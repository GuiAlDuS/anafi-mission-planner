"""Reconstruct flown survey parameters from Anafi photo EXIF/XMP (needs exiftool)."""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics as st
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .camera import ANAFI_4K, Camera
from .geo import LatLon, LocalProjection, distance_m

_TAGS = ["SubSecDateTimeOriginal", "GPSLatitude", "GPSLongitude", "GPSAltitude",
         "AboveGroundAltitude", "DroneYawDegree", "PhotoMode", "Flight_uid", "RunDate"]


@dataclass
class Photo:
    path: str
    time: dt.datetime
    pos: LatLon
    agl_m: float
    yaw_deg: float
    photo_mode: str
    flight_uid: str
    run_date: dt.datetime | None


@dataclass
class FlightStats:
    flight_uid: str
    photos: int
    photo_mode: str
    altitude_agl_m: float
    speed_ms: float
    photo_spacing_m: float
    line_spacing_m: float | None
    lines: int
    turn_gaps_s: list[float]
    front_overlap: float
    side_overlap: float | None
    run_to_first_photo_s: float | None
    first_photo_from_home_m: float | None
    last_photo_from_home_m: float | None
    max_from_home_m: float | None


def _parse_time(s: str) -> dt.datetime:
    # "2023:11:12 13:58:05.150-06:00" -> naive local time
    return dt.datetime.fromisoformat(s.replace(":", "-", 2)).replace(tzinfo=None)


def read_photos(directory: str | Path) -> list[Photo]:
    out = subprocess.run(
        ["exiftool", "-q", "-n", "-json", "-ext", "jpg", *(f"-{t}" for t in _TAGS), str(directory)],
        check=True, capture_output=True, text=True,
    ).stdout
    photos = []
    for r in json.loads(out or "[]"):
        if "GPSLatitude" not in r or "SubSecDateTimeOriginal" not in r:
            continue
        photos.append(Photo(
            path=r["SourceFile"],
            time=_parse_time(str(r["SubSecDateTimeOriginal"])),
            pos=LatLon(float(r["GPSLatitude"]), float(r["GPSLongitude"])),
            agl_m=float(r.get("AboveGroundAltitude", "nan")),
            yaw_deg=float(r.get("DroneYawDegree", "nan")),
            photo_mode=str(r.get("PhotoMode", "")),
            flight_uid=str(r.get("Flight_uid", "")),
            run_date=_parse_time(str(r["RunDate"])) if r.get("RunDate") else None,
        ))
    return sorted(photos, key=lambda p: p.time)


def split_lines(photos: list[Photo], max_turn_deg: float = 45, max_gap_s: float = 20,
                min_step_m: float = 1.0) -> list[list[Photo]]:
    """Group photos into survey lines by the direction of travel between them.

    A line's direction is set by its first two photos; a step that turns more than
    max_turn_deg from it (the sideways hop to the next line) or a gap of max_gap_s
    starts a new line. The drone's own heading isn't used: the Anafi takes the first
    photo of a line before it has turned onto it, still facing the hop.
    """
    proj = LocalProjection(photos[0].pos)
    lines = [[photos[0]]]
    heading = None  # direction of travel of the current line, degrees
    for a, b in zip(photos, photos[1:]):
        (ax, ay), (bx, by) = proj.to_xy(a.pos), proj.to_xy(b.pos)
        step = math.hypot(bx - ax, by - ay)
        if (b.time - a.time).total_seconds() >= max_gap_s:
            lines.append([b])
            heading = None
            continue
        if step < min_step_m:  # hovering: too short to tell a direction
            lines[-1].append(b)
            continue
        brg = math.degrees(math.atan2(bx - ax, by - ay))
        if heading is None:
            lines[-1].append(b)
            heading = brg
        elif abs((brg - heading + 180) % 360 - 180) < max_turn_deg:
            lines[-1].append(b)
        else:
            lines.append([b])
            heading = None
    return lines


def analyze_flight(photos: list[Photo], home: LatLon | None = None, camera: Camera = ANAFI_4K) -> FlightStats:
    proj = LocalProjection(photos[0].pos)
    xy = {id(p): proj.to_xy(p.pos) for p in photos}
    lines = split_lines(photos)
    full = [ln for ln in lines if len(ln) >= 4]

    spacings, speeds = [], []
    for ln in full:
        # Skip the first photos of a line: they are taken while turning/accelerating.
        for a, b in zip(ln[2:], ln[3:]):
            d = math.dist(xy[id(a)], xy[id(b)])
            spacings.append(d)
            speeds.append(d / max((b.time - a.time).total_seconds(), 1e-3))

    line_spacing = None
    if len(full) >= 2:
        def centre(ln):
            return st.fmean(xy[id(p)][0] for p in ln), st.fmean(xy[id(p)][1] for p in ln)
        x0, y0 = xy[id(full[0][0])]
        x1, y1 = xy[id(full[0][-1])]
        ux, uy = (x1 - x0, y1 - y0)
        norm = math.hypot(ux, uy) or 1.0
        ux, uy = ux / norm, uy / norm
        gaps = []
        for a, b in zip(full, full[1:]):
            (ax, ay), (bx, by) = centre(a), centre(b)
            gaps.append(abs(-(bx - ax) * uy + (by - ay) * ux))
        line_spacing = st.median(gaps)

    agl = st.median(p.agl_m for p in photos)
    fw, fh = camera.footprint_m(agl)
    photo_spacing = st.median(spacings) if spacings else float("nan")
    run = photos[0].run_date
    dists = [distance_m(home, p.pos) for p in photos] if home else None
    return FlightStats(
        flight_uid=photos[0].flight_uid,
        photos=len(photos),
        photo_mode=st.mode(p.photo_mode for p in photos),
        altitude_agl_m=agl,
        speed_ms=st.median(speeds) if speeds else float("nan"),
        photo_spacing_m=photo_spacing,
        line_spacing_m=line_spacing,
        lines=len(lines),
        turn_gaps_s=[(b[0].time - a[-1].time).total_seconds() for a, b in zip(lines, lines[1:])],
        front_overlap=1 - photo_spacing / fh,
        side_overlap=1 - line_spacing / fw if line_spacing else None,
        run_to_first_photo_s=(photos[0].time - run).total_seconds() if run else None,
        first_photo_from_home_m=dists[0] if dists else None,
        last_photo_from_home_m=dists[-1] if dists else None,
        max_from_home_m=max(dists) if dists else None,
    )


def analyze_directory(directory: str | Path, home: LatLon | None = None) -> list[FlightStats]:
    photos = read_photos(directory)
    flights: dict[str, list[Photo]] = {}
    for p in photos:
        flights.setdefault(p.flight_uid, []).append(p)
    return [analyze_flight(ps, home) for ps in flights.values()]
