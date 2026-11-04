"""Local web app: plan on a satellite map, save missions, (later) fly.

Standard library only. Serves the page from ui/static, a JSON API under
/api, and map tiles under /tiles through a disk cache, so areas viewed once
at home stay available offline in the field.
"""

from __future__ import annotations

import importlib.util
import json
import mimetypes
import re
import threading
from dataclasses import asdict, dataclass
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from planner.camera import ANAFI_4K
from drone import battery, iphone
from drone.upload import DRONE_HOST, SKYCONTROLLER_HOST, DroneError
from planner.estimate import FlightModel
from planner.ff6 import plan_files, plan_zip
from shapely.geometry import Point, Polygon

from planner.geo import LatLon, LocalProjection, centroid
from planner.grid import SurveySettings
from planner.mavlink import format_mission, survey_mission, validate_mission
from planner.plan import plan_geojson, plan_survey
from planner.runs import LABELS_FILE, load_labels, read_run, save_labels

from .wms import WmsError, WmsStore
from .wms import base_url as wms_base_url
from .wms import fetch_capabilities as wms_fetch_capabilities

STATIC = Path(__file__).parent / "static"
TILE_CACHE = Path.home() / ".cache" / "anafi-mission-planner" / "tiles"
USER_AGENT = "anafi-mission-planner (+https://github.com/GuiAlDuS/anafi-mission-planner)"


@dataclass(frozen=True)
class TileSource:
    label: str
    url: str  # {z}/{x}/{y} template
    attribution: str
    max_zoom: int = 19  # deepest zoom the server has; the map scales up beyond it
    note: str = ""  # shown under the name in the base map list


# Base maps whose terms allow this use (fetching and caching the tiles viewed, with attribution).
TILE_SOURCES: dict[str, TileSource] = {
    "imagery": TileSource(
        "Esri satellite",
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Imagery © Esri, Maxar, Earthstar Geographics", note="Saved for offline use"),
    "osm": TileSource(
        "OpenStreetMap",
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        note="Streets and paths, saved for offline use"),
}


IMAGERY_INFO = ("https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/identify"
                "?geometry={lon},{lat}&geometryType=esriGeometryPoint&sr=4326&layers=all:4&tolerance=0"
                "&mapExtent={w},{s},{e},{n}&imageDisplay=400,400,96&returnGeometry=false&f=json")
SENSORS = {"WV01": "WorldView-1", "WV02": "WorldView-2", "WV03": "WorldView-3", "WV04": "WorldView-4",
           "GE01": "GeoEye-1", "QB02": "QuickBird", "IK02": "IKONOS", "LGN01": "Legion"}


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_imagery_info(resp: dict) -> list[dict]:
    """Esri's citations for one point: which capture is shown at which zoom levels."""
    out = []
    for r in resp.get("results", []):
        a = r.get("attributes", {})
        lo, hi = _num(a.get("FROM_CACHE_LEVEL")), _num(a.get("TO_CACHE_LEVEL"))
        if lo is None or hi is None:
            continue
        raw = str(a.get("DATE (YYYYMMDD)", ""))
        date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw) else None
        sensor = str(a.get("DESCRIPTION") or "")
        out.append({"from_zoom": int(lo), "to_zoom": int(hi), "date": date,
                    "resolution_m": _num(a.get("RESOLUTION (M)")), "accuracy_m": _num(a.get("ACCURACY (M)")),
                    "sensor": SENSORS.get(sensor, sensor if sensor not in ("", "Null") else None),
                    "provider": None if a.get("SOURCE") in (None, "Null") else a.get("SOURCE")})
    return sorted(out, key=lambda c: c["from_zoom"])


def imagery_info(lat: float, lon: float, cache: Path = TILE_CACHE.parent / "imagery_info") -> dict:
    """Capture date, sharpness and accuracy of Esri's imagery around a point, by zoom level.

    Kept on disk per ~100 m cell, so places looked at at home still show it offline."""
    if not (-85 < lat < 85 and -180 <= lon <= 180):
        raise BadRequest("position out of range")
    f = cache / f"{lat:.3f}_{lon:.3f}.json"
    if f.exists():
        return json.loads(f.read_text())
    d = 0.0005
    url = IMAGERY_INFO.format(lat=lat, lon=lon, w=lon - d, s=lat - d, e=lon + d, n=lat + d)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            info = {"captures": parse_imagery_info(json.loads(r.read()))}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"captures": [], "offline": True}
    if info["captures"]:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(info))
    return info


def parse_tile_source(spec: str) -> tuple[str, TileSource]:
    """`Label=https://server/{z}/{x}/{y}.png` -> (key, source), for a base map of your own."""
    label, sep, url = spec.partition("=")
    label = label.strip()
    if not sep or not label or not all(f"{{{k}}}" in url for k in "zxy") or not url.startswith(("http://", "https://")):
        raise ValueError(f"tile source must look like 'Name=https://server/{{z}}/{{x}}/{{y}}.png', got {spec!r}")
    key = "user_" + re.sub(r"\W+", "_", label.lower()).strip("_")
    return key, TileSource(label, url.strip(), f"{label} (added with --tiles)", 21, note="Added with --tiles")
MAX_BODY = 1_000_000
# A full battery covers roughly 0.3-0.5 km2 at 120 m, so this is about ten flights.
# Bigger areas are almost always a mis-click at a zoomed-out view, and slow to plan.
MAX_AREA_KM2 = 5.0
MAX_HOME_DISTANCE_M = 3000.0  # takeoff to area; the Skycontroller 3 link is rated about 4 km
ALTITUDES_M = [40, 60, 80, 100, 120, 150]  # 150 m is the drone's altitude limit as set
SPEEDS_MS = [4, 6, 8, 10]  # flight plans accept 1-10 m/s


def config(tiles: dict[str, TileSource] | None = None) -> dict:
    tiles = TILE_SOURCES if tiles is None else tiles
    return {
        "tiles": [{"id": k, "label": t.label, "attribution": t.attribution, "max_zoom": t.max_zoom,
                   "note": t.note} for k, t in tiles.items()],
        "altitudes": [{"m": a, "gsd_cm": ANAFI_4K.gsd_m(a) * 100} for a in ALTITUDES_M],
        "speeds": SPEEDS_MS,
        "endurance_min": FlightModel.endurance_min,
        "reserve_pct": FlightModel.reserve_frac * 100,
    }


class BadRequest(ValueError):
    pass


def _latlon(v) -> LatLon:
    try:
        lat, lon = float(v[0]), float(v[1])
    except (TypeError, ValueError, IndexError) as e:
        raise BadRequest(f"bad coordinate {v!r}") from e
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise BadRequest(f"coordinate out of range {v!r}")
    return LatLon(lat, lon)


def build_plan(req: dict) -> tuple[dict, list[str], list[list[LatLon]]]:
    """Plan request -> (response for the page, .mavlink text per flight, waypoints per flight)."""
    area = [_latlon(p) for p in req.get("area") or []]
    if len(area) < 3:
        raise BadRequest("draw an area with at least 3 corners")
    proj = LocalProjection(centroid(area))
    poly = Polygon([proj.to_xy(p) for p in area])
    km2 = poly.area / 1e6
    if km2 > MAX_AREA_KM2:
        raise BadRequest(f"the area is {km2:,.1f} km², larger than the {MAX_AREA_KM2:.0f} km² limit. "
                         "Zoom in and draw a smaller area, or split it into several missions.")
    notes = []
    if req.get("home"):
        home = _latlon(req["home"])
        away = poly.distance(Point(proj.to_xy(home)))
        if away > MAX_HOME_DISTANCE_M:
            raise BadRequest(f"the takeoff point is {away / 1000:,.1f} km from the area; "
                             f"set it within {MAX_HOME_DISTANCE_M / 1000:.0f} km")
    else:
        home = centroid(area)
        notes.append("Takeoff point not set: using the middle of the area.")
    try:
        settings = SurveySettings(
            altitude_m=float(req.get("altitude", 120)), front_overlap=float(req.get("front", 80)) / 100,
            side_overlap=float(req.get("side", 80)) / 100, speed_ms=float(req.get("speed", 10)),
            angle_deg=None if req.get("angle") in (None, "") else float(req["angle"]))
        model = replace(FlightModel(), endurance_min=float(req.get("endurance", FlightModel.endurance_min)),
                        reserve_frac=float(req.get("reserve", FlightModel.reserve_frac * 100)) / 100)
    except ValueError as e:
        raise BadRequest(str(e)) from e
    end = req.get("end", "rth")
    if end not in ("rth", "land"):
        raise BadRequest("end must be 'rth' or 'land'")

    plan = plan_survey(area, home, settings, model)
    g = plan.grid
    missions, waypoints, flights = [], [], []
    for n, f in enumerate(plan.flights, 1):
        items = survey_mission(f.lines, home, settings.altitude_m, settings.speed_ms, g.photo_spacing_m, end_action=end)
        missions.append(format_mission(items))
        waypoints.append([p for ln in f.lines for p in (ln.start, ln.end)])
        e = f.estimate
        flights.append({
            "n": n, "first_line": f.first_line_index + 1, "last_line": f.first_line_index + len(f.lines),
            "total_s": e.total_s, "battery_frac": e.battery_used_frac, "status": e.status.value,
            "max_distance_m": e.max_distance_from_home_m, "phases_s": e.phases_s,
            "problems": validate_mission(items),
        })
    return {
        "home": [home.lat, home.lon],
        "gsd_cm": g.gsd_m * 100,
        "footprint_m": [settings.footprint_across_m, settings.footprint_along_m],
        "lines": len(g.lines), "angle_deg": g.angle_deg, "line_spacing_m": g.line_spacing_m,
        "side_overlap": g.side_overlap_actual, "photo_spacing_m": g.photo_spacing_m, "photos": g.photo_count,
        "total_s": plan.total_s, "flights": flights, "geojson": plan_geojson(plan), "notes": notes,
    }, missions, waypoints


def _save(req: dict, out_root: Path) -> tuple[dict, list[tuple[str, str, dict[str, bytes]]]]:
    """Write the mission files; return (result for the page, FreeFlight plans as (folder, title, files))."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(req.get("name") or "").strip()).strip("_")
    if not name:
        raise BadRequest("give the mission a name")
    resp, missions, waypoints = build_plan(req)
    home = LatLon(*resp["home"])
    out = out_root / name
    (out / "freeflight").mkdir(parents=True, exist_ok=True)
    files, zips, plans = [], [], []
    altitude, speed = float(req.get("altitude", 120)), float(req.get("speed", 10))
    # FreeFlight plans take time-lapse photos: the interval that gives the planned spacing at survey speed.
    photo_period = resp["photo_spacing_m"] / speed
    for n, (text, points) in enumerate(zip(missions, waypoints), 1):
        p = out / f"flight_{n}.mavlink"
        p.write_text(text)
        files.append(str(p))
        # FreeFlight 6 plan. Its folder on the phone is named by the plan's UUID, fixed per
        # mission name and flight so that sending again replaces the plan.
        title = name if len(missions) == 1 else f"{name} {n}"
        folder, ff6_files = plan_files(title, points, altitude, speed,
                                       plan_uuid=iphone.plan_uuid(name, n), home=home,
                                       photo_period_s=photo_period)
        z = out / "freeflight" / f"{folder}.zip"
        z.write_bytes(plan_zip(ff6_files))
        zips.append(str(z))
        plans.append((folder, title, ff6_files))
    (out / "plan.geojson").write_text(json.dumps(resp["geojson"]))
    (out / "request.json").write_text(json.dumps(req, indent=2))  # reopen or re-plan later
    return {"folder": str(out), "files": files, "freeflight": zips}, plans


def list_missions(out_root: Path) -> list[dict]:
    """Saved missions (folders with a request.json), newest first."""
    out = []
    for req_file in Path(out_root).glob("*/request.json") if Path(out_root).exists() else []:
        try:
            req = json.loads(req_file.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(req, dict) or len(req.get("area") or []) < 3:
            continue
        out.append({"name": req_file.parent.name, "altitude": req.get("altitude"), "speed": req.get("speed"),
                    "flights": len(list(req_file.parent.glob("flight_*.mavlink"))),
                    "saved": req_file.stat().st_mtime})
    return sorted(out, key=lambda m: -m["saved"])


def load_mission(out_root: Path, name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise BadRequest(f"bad mission name {name!r}")
    f = Path(out_root) / name / "request.json"
    if not f.exists():
        raise BadRequest(f"no saved mission {name!r}")
    return {**json.loads(f.read_text()), "name": name}


def delete_mission(out_root: Path, name: str, trash=None) -> None:
    """Move a saved mission's folder to the system trash (restorable from there)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise BadRequest(f"bad mission name {name!r}")
    src = Path(out_root) / name
    if not (src / "request.json").exists():
        raise BadRequest(f"no saved mission {name!r}")
    if trash is None:
        from send2trash import send2trash as trash
    try:
        trash(str(src))
    except Exception as e:  # no trash on this system or drive: say so, never delete for good instead
        raise BadRequest(f"couldn't move {name} to the trash ({e}); nothing was deleted") from e


def save_plan(req: dict, out_root: Path) -> dict:
    return _save(req, out_root)[0]


def send_to_iphone(req: dict, out_root: Path) -> dict:
    """Save the mission, then copy its FreeFlight plans onto the plugged-in iPhone."""
    phone = iphone.connected()
    if phone is None:
        raise BadRequest("no iPhone found: plug it in with its cable, unlock it and tap Trust")
    result, plans = _save(req, out_root)
    for folder, _, files in plans:
        iphone.send_plan(folder, files)
    return {**result, "phone": phone["name"], "titles": [t for _, t, _ in plans]}


def phone_status() -> dict:
    try:
        phone = iphone.connected()
    except iphone.PhoneError as e:
        return {"connected": False, "message": str(e)}
    return {"connected": phone is not None, "name": phone and phone["name"]}


class RunCache:
    """Flight log summaries, re-read only when a log file changes (the logs are large)."""

    def __init__(self, log_dir: Path):
        self.dir = Path(log_dir)
        self._runs: dict[Path, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def runs(self) -> list[dict]:
        with self._lock:
            files = [f for f in sorted(self.dir.glob("*.json")) if f.name != LABELS_FILE] if self.dir.exists() else []
            for f in files:
                mtime = f.stat().st_mtime
                if f not in self._runs or self._runs[f][0] != mtime:
                    self._runs[f] = (mtime, read_run(f))
            labels = load_labels(self.dir)
            out = []
            for f in files:
                run = self._runs[f][1]
                if run is None:
                    continue
                d = asdict(run)
                d.update(battery=labels.get(run.id), drain=run.drain_pct_per_min,
                         endurance=run.endurance_min, survey=run.survey)
                out.append(d)
            return sorted(out, key=lambda r: r["date"])

    def label(self, flight: str, battery: str | None) -> None:
        if not re.fullmatch(r"[0-9A-Fa-f]{1,8}", flight or ""):
            raise BadRequest(f"bad flight id {flight!r}")
        battery = (battery or "").strip()[:20]
        with self._lock:
            labels = load_labels(self.dir)
            if battery:
                labels[flight] = battery
            else:
                labels.pop(flight, None)
            self.dir.mkdir(parents=True, exist_ok=True)
            save_labels(self.dir, labels)


NO_OLYMPE = ("The pack check needs Parrot's Olympe SDK, which only runs on x86_64 Linux. "
             "Install it with `uv sync --extra fly`, then restart anafi-gui.")


class BatteryStore:
    """Battery health checks (drone/battery.py): the log, and running a check through Olympe."""

    HOSTS = {"skycontroller": SKYCONTROLLER_HOST, "wifi": DRONE_HOST}

    def __init__(self, log_path: Path, reader=None, can_check: bool | None = None):
        self.path = Path(log_path)
        self._reader = reader or self._read_from_drone
        # Olympe only ships for x86_64 Linux; elsewhere the page explains instead of offering the button.
        if can_check is None:
            can_check = reader is not None or importlib.util.find_spec("olympe") is not None
        self.can_check = can_check
        self._lock = threading.Lock()  # one check at a time, and no log write during one

    @staticmethod
    def _read_from_drone(host: str) -> battery.BatteryReading:
        from drone import control
        with control.connect(host) as dev:
            return battery.read_battery(dev)

    def state(self, checked: str | None = None) -> dict:
        log = battery.load_log(self.path)
        rows = battery.readings(log)
        packs, checks = {}, []
        for i, r in enumerate(rows):
            problems, notes = battery.assess(r, rows[:i])
            d = asdict(r)
            d.update(name=log["names"].get(r.serial) or r.name, cell_v=r.cell_v, voltage_v=r.voltage_v,
                     problems=problems, notes=notes)
            checks.append(d)
            if r.serial:
                hist = packs.get(r.serial, {}).get("history", [])
                hist.append({"time": r.time, "health_pct": r.health_pct, "cell_v": r.cell_v,
                             "charge_pct": r.charge_pct})
                packs[r.serial] = {**d, "history": hist}
        order = sorted(packs.values(), key=lambda p: (p["name"] is None, p["name"] or "", p["serial"]))
        return {"log": str(self.path), "can_check": self.can_check, "names": log["names"], "packs": order, "checks": checks,
                "checked": checked, "limits": {"cell_min": battery.CELL_MIN_V, "cell_max": battery.CELL_MAX_V,
                                                "full_cell_min": battery.FULL_CELL_MIN_V,
                                                "health_warn": battery.HEALTH_WARN_PCT,
                                                "health_bad": battery.HEALTH_BAD_PCT}}

    def check(self, link: str) -> dict:
        if not self.can_check:
            raise BadRequest(NO_OLYMPE)
        if link not in self.HOSTS:
            raise BadRequest(f"link must be one of {', '.join(self.HOSTS)}")
        if not self._lock.acquire(blocking=False):
            raise BadRequest("a battery check is already running")
        try:
            r = self._reader(self.HOSTS[link])
            log = battery.load_log(self.path)
            battery.record(log, r)
            battery.save_log(self.path, log)
        finally:
            self._lock.release()
        return self.state(checked=r.serial)

    def name(self, serial: str, name: str) -> dict:
        name = (name or "").strip()[:20]
        with self._lock:
            log = battery.load_log(self.path)
            if not any(c.get("serial") == serial for c in log["checks"]):
                raise BadRequest(f"no check of a pack with serial {serial!r}")
            if name:
                log["names"][serial] = name
            else:
                log["names"].pop(serial, None)
            battery.save_log(self.path, log)
        return self.state()


TILE_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def fetch_tile(source: TileSource, key: str, z: int, x: int, y: int,
               cache: Path = TILE_CACHE) -> tuple[bytes, str] | None:
    """A map tile and its content type, from the disk cache or the tile server (then cached)."""
    folder = cache / key / str(z) / str(x)
    for ext, ctype in TILE_TYPES.items():
        if (folder / f"{y}{ext}").exists():
            return (folder / f"{y}{ext}").read_bytes(), ctype
    try:
        req = urllib.request.Request(source.url.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            data, ctype = r.read(), r.headers.get_content_type()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None  # offline and not cached
    ext = next((e for e, t in TILE_TYPES.items() if t == ctype), None)
    if ext is None:
        return None  # an error page, not an image
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{y}{ext}").write_bytes(data)
    return data, ctype


ORTHO_MISSING = ("Loading your own orthophotos needs the optional map tools. Install them with "
                 "`uv sync --all-extras`, then restart anafi-gui.")


def ortho_store(folder: Path):
    """The orthophoto store, or None when rio-tiler (the `ortho` extra) isn't installed."""
    if importlib.util.find_spec("rio_tiler") is None:
        return None
    from .ortho import OrthoStore
    return OrthoStore(folder)


def make_handler(out_root: Path, runs: RunCache, batteries: BatteryStore,
                 tiles: dict[str, TileSource] = TILE_SOURCES, orthos=None, wms: WmsStore | None = None):
    wms_caps: dict[str, dict] = {}  # service URL -> its layer list, between "find layers" and "add"
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the terminal quiet
            pass

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj):
            self._send(status, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/api/config":
                return self._json(200, config(tiles))
            if path == "/api/missions":
                return self._json(200, {"missions": list_missions(out_root)})
            if m := re.fullmatch(r"/api/missions/([A-Za-z0-9_-]+)", path):
                try:
                    return self._json(200, load_mission(out_root, m[1]))
                except BadRequest as e:
                    return self._json(404, {"error": str(e)})
            if path == "/api/iphone":
                return self._json(200, phone_status())
            if path == "/api/runs":
                return self._json(200, {"dir": str(runs.dir), "runs": runs.runs(),
                                        "planner_endurance": FlightModel.endurance_min})
            if path == "/api/battery":
                return self._json(200, batteries.state())
            if path == "/api/orthophotos":
                return self._json(200, {"available": orthos is not None, "missing": ORTHO_MISSING,
                                        "items": orthos.list() if orthos else []})
            if m := re.fullmatch(r"/tiles/ortho/([\w-]+)/(\d+)/(\d+)/(\d+)", path):
                png = orthos.tile(m[1], int(m[2]), int(m[3]), int(m[4])) if orthos else None
                return self._send(200, png, "image/png") if png else self._send(404, b"", "text/plain")
            if path == "/api/wms":
                return self._json(200, {"items": wms.list() if wms else []})
            if m := re.fullmatch(r"/tiles/wms/(w[0-9a-f]+)/(\d+)/(\d+)/(\d+)", path):
                tile = wms.tile(m[1], int(m[2]), int(m[3]), int(m[4])) if wms else None
                return self._send(200, tile[0], tile[1], cache=True) if tile else self._send(404, b"", "text/plain")
            if path == "/api/imagery":
                q = urllib.parse.parse_qs(self.path.partition("?")[2])
                try:
                    lat, lon = float(q["lat"][0]), float(q["lon"][0])
                    return self._json(200, imagery_info(lat, lon))
                except (KeyError, ValueError, BadRequest) as e:
                    return self._json(400, {"error": f"need lat and lon in range ({e})"})
            if m := re.fullmatch(r"/tiles/(\w+)/(\d+)/(\d+)/(\d+)", path):
                if m[1] not in tiles:
                    return self._send(404, b"", "text/plain")
                tile = fetch_tile(tiles[m[1]], m[1], int(m[2]), int(m[3]), int(m[4]))
                return self._send(200, tile[0], tile[1], cache=True) if tile else self._send(404, b"", "text/plain")
            rel = "index.html" if path == "/" else path.lstrip("/")
            f = (STATIC / rel).resolve()
            if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            self._send(200, f.read_bytes(), ctype, cache="vendor" in f.parts)

        def _upload_ortho(self):
            from .ortho import OrthoError
            if orthos is None:
                return self._json(400, {"error": ORTHO_MISSING})
            name = urllib.parse.unquote(self.headers.get("X-Filename", ""))
            date = self.headers.get("X-File-Date", "")
            date = date if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) else None
            try:
                meta = orthos.add(name, self.rfile, int(self.headers.get("Content-Length", 0)), date)
            except (OrthoError, ValueError) as e:
                self.close_connection = True  # the rest of the body may be unread
                return self._json(400, {"error": str(e)})
            except Exception as e:  # say what went wrong instead of dropping the connection
                self.close_connection = True
                return self._json(500, {"error": f"couldn't prepare the file ({type(e).__name__}: {e})"})
            return self._json(200, meta)

        def do_POST(self):
            if self.path == "/api/orthophotos/upload":
                return self._upload_ortho()
            try:
                n = int(self.headers.get("Content-Length", 0))
                if n > MAX_BODY:
                    raise BadRequest("request too large")
                req = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/plan":
                    return self._json(200, build_plan(req)[0])
                if self.path == "/api/save":
                    return self._json(200, save_plan(req, out_root))
                if self.path == "/api/missions/delete":
                    delete_mission(out_root, str(req.get("name", "")))
                    return self._json(200, {"missions": list_missions(out_root)})
                if self.path == "/api/iphone/send":
                    return self._json(200, send_to_iphone(req, out_root))
                if self.path == "/api/runs/label":
                    runs.label(str(req.get("flight", "")), req.get("battery"))
                    return self._json(200, {"ok": True})
                if self.path == "/api/battery/check":
                    return self._json(200, batteries.check(str(req.get("link", "skycontroller"))))
                if self.path == "/api/battery/name":
                    return self._json(200, batteries.name(str(req.get("serial", "")), req.get("name")))
                if self.path in ("/api/wms/capabilities", "/api/wms/add", "/api/wms/remove"):
                    try:
                        if self.path == "/api/wms/capabilities":
                            caps = wms_fetch_capabilities(str(req.get("url", "")))
                            wms_caps[caps["url"]] = caps
                            return self._json(200, caps)
                        if self.path == "/api/wms/add":
                            url = wms_base_url(str(req.get("url", "")))
                            caps = wms_caps.get(url) or wms_fetch_capabilities(url)
                            return self._json(200, wms.add(caps, str(req.get("name", ""))))
                        wms.remove(str(req.get("id", "")))
                        return self._json(200, {"items": wms.list()})
                    except WmsError as e:
                        raise BadRequest(str(e)) from e
                if self.path == "/api/orthophotos/remove":
                    from .ortho import OrthoError
                    if orthos is None:
                        raise BadRequest(ORTHO_MISSING)
                    try:
                        orthos.remove(str(req.get("id", "")))
                    except OrthoError as e:
                        raise BadRequest(str(e)) from e
                    return self._json(200, {"items": orthos.list()})
                if self.path == "/api/runs/pull":
                    if iphone.connected() is None:
                        raise BadRequest("no iPhone found: plug it in with its cable, unlock it and tap Trust")
                    return self._json(200, {"new": [p.name for p in iphone.pull_runs(runs.dir)]})
                self._json(404, {"error": "unknown endpoint"})
            except (BadRequest, json.JSONDecodeError, iphone.PhoneError, DroneError) as e:
                self._json(400, {"error": str(e)})
            except Exception as e:  # report planner failures to the page instead of dropping the connection
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8765, out_root: str | Path = "out",
          runs_dir: str | Path = "freeflight_runs", battery_log: str | Path = battery.LOG_FILE,
          battery_reader=None, extra_tiles: list[str] = (),
          orthophotos: str | Path = "orthophotos", wms_layers: str | Path = "wms_layers.json",
          tile_cache: Path = TILE_CACHE) -> ThreadingHTTPServer:
    tiles = dict(TILE_SOURCES)
    tiles.update(parse_tile_source(spec) for spec in extra_tiles)
    return ThreadingHTTPServer((host, port), make_handler(Path(out_root), RunCache(Path(runs_dir)),
                                                          BatteryStore(Path(battery_log), battery_reader), tiles,
                                                          ortho_store(Path(orthophotos)),
                                                          WmsStore(Path(wms_layers), tile_cache)))
