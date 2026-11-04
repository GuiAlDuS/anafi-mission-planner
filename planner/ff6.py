"""Flight plans in FreeFlight 6's own format, to fly our grids from the iPhone app.

FreeFlight 6 keeps each plan on the phone as a folder named by a UUID holding
`savedPlan.json`, `flightPlan.mavlink` and `screenshot.png` (see
docs/step2_upload_test.md). This writes the same three files, copying the
layout of plans the app saved itself.

The camera is set by actions in the plan, which override the app's camera
control once the plan starts. Given `photo_period_s`, the plan tilts the camera
straight down at its first waypoint and at every line start, and takes
time-lapse photos from the start of line 1 to the end of the last line (format from a plan FreeFlight re-saved,
flown in the simulator 2026-11-04: 104 photos at -90 deg). The tilt goes on the
first waypoint, not on takeoff: users report that a tilt in the takeoff actions
makes firmware 1.8.x hover instead of flying the plan. FreeFlight's plan
actions have no GPS-lapse (distance) mode, so photo spacing is speed x period.
Without `photo_period_s` the plan records video, like the app's default.

FreeFlight flies straight from one waypoint to the next, the first one
included, so a plan made of the survey lines alone climbs on a slant towards
line 1 after takeoff (seen in the field, 2026-11-01). Given `home`, the route
starts with a waypoint over home at survey altitude (vertical climb) and ends
with home at survey altitude, then home low (vertical descent).
"""

from __future__ import annotations

import io
import json
import struct
import time
import uuid as uuidlib
import zipfile
import zlib
from typing import Sequence

from .geo import LatLon, distance_m, heading_deg

ANAFI_PRODUCT_ID = 2324  # 0x0914, "Anafi" in FreeFlight's savedPlan.json
END_ALTITUDE_M = 5.0  # last waypoint, over home: low enough to land from, above GPS altitude drift
SAME_SPOT_M = 1.0  # waypoints closer than this are a vertical leg
NADIR_TILT_DEG = -90  # FreeFlight's Tilt angle is a change from the current tilt; -90 from level = straight down
TILT_SPEED_DEG_S = 180
JPEG_RECTILINEAR = 12.58291244506836  # FreeFlight's "resolution" code for rectilinear JPEG photos
VIDEO_START = {"cameraId": 0, "type": "VideoStartCapture", "fps": 30, "resolution": 0}


def home_route(points: Sequence[LatLon], altitude_m: float, home: LatLon,
               end_altitude_m: float = END_ALTITUDE_M) -> tuple[list[LatLon], list[float]]:
    """(points, altitudes): climb over home, fly `points`, back over home, descend over home."""
    if not 0 < end_altitude_m <= altitude_m:
        raise ValueError("end altitude must be above 0 and at most the survey altitude")
    pts = [home, *points, home, home]
    alts = [altitude_m] * (len(pts) - 1) + [end_altitude_m]
    return pts, alts


def _yaws(points: Sequence[LatLon]) -> list[float]:
    """FreeFlight's rule: each waypoint faces the next one; the last repeats the previous yaw.

    A vertical leg (next waypoint straight above or below) keeps the yaw it has.
    """
    yaws: list[float | None] = [heading_deg(a, b) if distance_m(a, b) >= SAME_SPOT_M else None
                                for a, b in zip(points, points[1:])]
    known = [y for y in yaws if y is not None]
    prev = known[0] if known else 0.0
    out = []
    for y in yaws:
        prev = prev if y is None else y
        out.append(prev)
    return out + [out[-1] if out else 0.0]


def _altitudes(points: Sequence[LatLon], altitude_m: float | Sequence[float]) -> list[float]:
    if isinstance(altitude_m, (int, float)):
        return [float(altitude_m)] * len(points)
    if len(altitude_m) != len(points):
        raise ValueError("one altitude per waypoint")
    return [float(a) for a in altitude_m]


def camera_actions(n_points: int, photo_period_s: float | None = None, first_line: int = 0,
                   last_line: int | None = None) -> tuple[list[dict], list[list[dict]]]:
    """(takeoff actions, actions per waypoint).

    Video (no period): start at takeoff, stop at the last waypoint. Photos: tilt
    down at waypoint 0 and again at every line start (`first_line`, then every
    other waypoint), time lapse from `first_line` to `last_line` (indexes of
    the first line's start and the last line's end).

    Simulator findings (2026-11-04, fw 1.10.4): a stick push pauses the plan,
    and play resumes the route without replaying earlier actions. The repeated
    tilt brings the camera back down after a manual tilt, at no extra hover
    time. Photo commands must NOT be repeated: a start while photos run stops
    them, and repeated stop/start pairs (at every waypoint or every line start)
    fell into a cycle of skipped starts, 20 s stop timeouts and a skipped final
    stop (whole lines without photos, camera still shooting after landing).
    After a pause, photos sometimes stopped and sometimes kept going.
    """
    per_wp: list[list[dict]] = [[] for _ in range(n_points)]
    if photo_period_s is None:
        per_wp[-1].append({"type": "VideoStopCapture"})
        return [dict(VIDEO_START)], per_wp
    if not photo_period_s > 0:
        raise ValueError("photo period must be positive")
    last_line = n_points - 1 if last_line is None else last_line
    if not 0 <= first_line < last_line < n_points:
        raise ValueError("photos must start before they stop, within the route")
    tilt = {"type": "Tilt", "angle": NADIR_TILT_DEG, "speed": TILT_SPEED_DEG_S}
    per_wp[0].append(dict(tilt))
    for i in range(first_line, last_line, 2):  # line starts: the route alternates line start, line end
        if i:
            per_wp[i].append(dict(tilt))
    per_wp[first_line].append({"type": "ImageStartCapture", "period": round(photo_period_s, 2),
                               "resolution": JPEG_RECTILINEAR, "nbOfPictures": 0})
    per_wp[last_line].append({"type": "ImageStopCapture"})
    return [], per_wp


def saved_plan(title: str, points: Sequence[LatLon], altitude_m: float | Sequence[float], speed_ms: float,
               plan_uuid: str, date_s: int, takeoff: list[dict] | None = None,
               actions: list[list[dict]] | None = None) -> dict:
    yaws = _yaws(points)
    alts = _altitudes(points, altitude_m)
    if actions is None:
        takeoff, actions = camera_actions(len(points))
    waypoints = []
    for i, (p, yaw, alt) in enumerate(zip(points, yaws, alts)):
        waypoints.append({
            "speed": speed_ms,
            "yaw": yaw,
            "lastYaw": yaws[i - 1] if i else 360,
            "radius": 0,
            "poi": -1,
            "actions": actions[i],
            "longitude": p.lon,
            "latitude": p.lat,
            "landingAltitude": 0,
            "landing": 0,
            "continue": True,
            "altitude": alt,
            "followPoi": False,
            "follow": 1,
        })
    return {
        "product": "Anafi",
        "productId": ANAFI_PRODUCT_ID,
        "title": title,
        "date": str(date_s),
        "uuid": plan_uuid,
        "version": 1,
        "dirty": False,
        "plan": {
            "takeoff": takeoff or [],
            "wayPoints": waypoints,
            "poi": [],
            "buckled": False,
        },
    }


def flight_plan_mavlink(points: Sequence[LatLon], altitude_m: float | Sequence[float], speed_ms: float,
                        takeoff: list[dict] | None = None, actions: list[list[dict]] | None = None) -> str:
    """The .mavlink FreeFlight generates for such a plan (same commands, frames and number format).

    Each waypoint's actions follow it, as FreeFlight writes them.
    """
    def row(cmd: int, p1=0.0, p2=0.0, p3=0.0, p4=0.0, x=0.0, y=0.0, z=0.0) -> list[str]:
        return ["0", "3", str(cmd), *(f"{v:.6f}" for v in (p1, p2, p3, p4, x, y, z)), "1"]

    def action_rows(acts: list[dict]) -> list[list[str]]:
        out = []
        for a in acts:
            kind = a["type"]
            if kind == "VideoStartCapture":
                out.append(row(2500, p2=a.get("fps", 30)))
            elif kind == "VideoStopCapture":
                out.append(row(2501))
            elif kind == "Tilt":  # FreeFlight sends a tilt as a panorama move: p2 vertical angle, p4 its speed
                out.append(row(2800, p2=a["angle"], p4=a["speed"]))
            elif kind == "ImageStartCapture":
                out.append(row(2000, p1=a["period"], p2=a.get("nbOfPictures", 0), p3=a["resolution"]))
            elif kind == "ImageStopCapture":
                out.append(row(2001))
            else:
                raise ValueError(f"unknown FreeFlight action {kind!r}")
        return out

    if actions is None:
        takeoff, actions = camera_actions(len(points))
    rows = [row(178, p2=speed_ms, p3=-1), *action_rows(takeoff or []), row(178, p2=speed_ms, p3=-1)]
    for i, (p, yaw, alt) in enumerate(zip(points, _yaws(points), _altitudes(points, altitude_m))):
        rows.append(row(16, p2=5, p4=yaw, x=p.lat, y=p.lon, z=alt))
        rows.extend(action_rows(actions[i]))
        if i == 0:
            rows.append(row(50000, p1=1))
    return "QGC WPL 120\n" + "".join("\t".join([str(i), *r]) + "\n" for i, r in enumerate(rows))


def placeholder_png(width: int = 64, height: int = 48) -> bytes:
    """A plain grey PNG for the plan's map thumbnail."""
    raw = b"".join(b"\x00" + b"\x88" * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def plan_files(title: str, points: Sequence[LatLon], altitude_m: float, speed_ms: float,
               plan_uuid: str | None = None, date_s: int | None = None,
               home: LatLon | None = None, photo_period_s: float | None = None) -> tuple[str, dict[str, bytes]]:
    """(folder name, {file name: content}) for one FreeFlight 6 plan.

    With `home`, the plan climbs and descends vertically over it (see home_route).
    With `photo_period_s`, it takes nadir time-lapse photos along `points`
    instead of recording video (see camera_actions).
    """
    if len(points) < 2:
        raise ValueError("a FreeFlight plan needs at least 2 waypoints")
    alts: float | list[float] = altitude_m
    first, last = 0, len(points) - 1
    if home is not None:
        points, alts = home_route(points, altitude_m, home)
        first, last = 1, len(points) - 3  # the survey points sit between the climb and the return
    if not 1 <= speed_ms <= 10:
        raise ValueError("speed must be 1-10 m/s")
    plan_uuid = plan_uuid or str(uuidlib.uuid4()).upper()
    date_s = int(time.time()) if date_s is None else date_s
    takeoff, actions = camera_actions(len(points), photo_period_s, first, last)
    plan = saved_plan(title, points, alts, speed_ms, plan_uuid, date_s, takeoff, actions)
    return plan_uuid, {
        "savedPlan.json": json.dumps(plan, indent=2, separators=(",", " : ")).encode(),
        "flightPlan.mavlink": flight_plan_mavlink(points, alts, speed_ms, takeoff, actions).encode(),
        "screenshot.png": placeholder_png(),
    }


def plan_zip(files: dict[str, bytes]) -> bytes:
    """Zip with the files at the top level, like the Files app makes from a plan folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("screenshot.png", "savedPlan.json", "flightPlan.mavlink"):
            z.writestr(name, files[name])
    return buf.getvalue()
