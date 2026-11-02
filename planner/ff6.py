"""Flight plans in FreeFlight 6's own format, to fly our grids from the iPhone app.

FreeFlight 6 keeps each plan on the phone as a folder named by a UUID holding
`savedPlan.json`, `flightPlan.mavlink` and `screenshot.png` (see
docs/step2_upload_test.md). This writes the same three files, copying the
layout of plans the app saved itself. The camera mode (video, GPS lapse) is
not in these files: set it on the plan in the app.

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


def saved_plan(title: str, points: Sequence[LatLon], altitude_m: float | Sequence[float], speed_ms: float,
               plan_uuid: str, date_s: int) -> dict:
    yaws = _yaws(points)
    alts = _altitudes(points, altitude_m)
    waypoints = []
    for i, (p, yaw, alt) in enumerate(zip(points, yaws, alts)):
        waypoints.append({
            "speed": speed_ms,
            "yaw": yaw,
            "lastYaw": yaws[i - 1] if i else 360,
            "radius": 0,
            "poi": -1,
            "actions": [{"type": "VideoStopCapture"}] if i == len(points) - 1 else [],
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
            "takeoff": [{"cameraId": 0, "type": "VideoStartCapture", "fps": 30, "resolution": 0}],
            "wayPoints": waypoints,
            "poi": [],
            "buckled": False,
        },
    }


def flight_plan_mavlink(points: Sequence[LatLon], altitude_m: float | Sequence[float], speed_ms: float) -> str:
    """The .mavlink FreeFlight generates for such a plan (same commands, frames and number format)."""
    def row(cmd: int, p1=0.0, p2=0.0, p3=0.0, p4=0.0, x=0.0, y=0.0, z=0.0) -> list[str]:
        return ["0", "3", str(cmd), *(f"{v:.6f}" for v in (p1, p2, p3, p4, x, y, z)), "1"]

    rows = [row(178, p2=speed_ms, p3=-1), row(2500, p2=30), row(178, p2=speed_ms, p3=-1)]
    for i, (p, yaw, alt) in enumerate(zip(points, _yaws(points), _altitudes(points, altitude_m))):
        rows.append(row(16, p2=5, p4=yaw, x=p.lat, y=p.lon, z=alt))
        if i == 0:
            rows.append(row(50000, p1=1))
    rows.append(row(2501))
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
               home: LatLon | None = None) -> tuple[str, dict[str, bytes]]:
    """(folder name, {file name: content}) for one FreeFlight 6 plan.

    With `home`, the plan climbs and descends vertically over it (see home_route).
    """
    if len(points) < 2:
        raise ValueError("a FreeFlight plan needs at least 2 waypoints")
    alts: float | list[float] = altitude_m
    if home is not None:
        points, alts = home_route(points, altitude_m, home)
    if not 1 <= speed_ms <= 10:
        raise ValueError("speed must be 1-10 m/s")
    plan_uuid = plan_uuid or str(uuidlib.uuid4()).upper()
    date_s = int(time.time()) if date_s is None else date_s
    plan = saved_plan(title, points, alts, speed_ms, plan_uuid, date_s)
    return plan_uuid, {
        "savedPlan.json": json.dumps(plan, indent=2, separators=(",", " : ")).encode(),
        "flightPlan.mavlink": flight_plan_mavlink(points, alts, speed_ms).encode(),
        "screenshot.png": placeholder_png(),
    }


def plan_zip(files: dict[str, bytes]) -> bytes:
    """Zip with the files at the top level, like the Files app makes from a plan folder."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("screenshot.png", "savedPlan.json", "flightPlan.mavlink"):
            z.writestr(name, files[name])
    return buf.getvalue()
