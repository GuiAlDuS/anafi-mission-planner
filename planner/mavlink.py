"""Anafi flight plan files: QGC WPL 120 text, MAVLink v1 commands.

Command parameters follow Parrot's flight plan docs:
https://developer.parrot.com/docs/mavlink-flightplan/messages_v1.html
https://developer.parrot.com/docs/mavlink-flightplan/enums.html

Where the docs leave room, the layout copies plans FreeFlight 6 writes for
firmware 1.8.2 (tests/data/ff6_prueba.mavlink): frame 3 and current = 0 on
every line, and speed as p1 = 0, p3 = -1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Sequence

from .geo import LatLon, heading_deg
from .grid import SurveyLine

HEADER = "QGC WPL 120"


class Cmd(IntEnum):
    NAV_WAYPOINT = 16  # p2 acceptance radius (m, >0), p4 yaw (deg)
    NAV_RETURN_TO_LAUNCH = 20  # ends the flight plan, starts RTH
    NAV_LAND = 21  # lands at current position
    NAV_TAKEOFF = 22  # all params ignored
    NAV_DELAY = 93  # p1 delay (s)
    DO_CHANGE_SPEED = 178  # p2 speed m/s (1..10); docs say p1 = 1, FreeFlight 6 writes p1 0, p3 -1
    DO_SET_ROI = 201
    DO_MOUNT_CONTROL = 205  # p1 pitch (deg, -90..90), p7 mode = 2 (MAVLINK_TARGETING)
    IMAGE_START_CAPTURE = 2000  # p1 interval s (0 = use still capture mode), p2 count (0 = until stop), p3 format
    IMAGE_STOP_CAPTURE = 2001
    VIDEO_START_CAPTURE = 2500
    VIDEO_STOP_CAPTURE = 2501
    SET_VIEW_MODE = 50000  # Parrot: p1 MAV_VIEW_MODE_TYPE (0 absolute: turn to the yaw on reaching the waypoint)
    SET_STILL_CAPTURE_MODE = 50001  # Parrot: p1 mode (0 timelapse, 1 GPS position), p2 interval (s or m)


class Frame(IntEnum):
    GLOBAL = 0
    MISSION = 2
    GLOBAL_RELATIVE_ALT = 3


NAV_COMMANDS = {Cmd.NAV_WAYPOINT, Cmd.NAV_TAKEOFF}
VIEW_MODE_ABSOLUTE = 0
STILL_CAPTURE_TIMELAPSE = 0
STILL_CAPTURE_GPS_POSITION = 1
FORMAT_RECTILINEAR_JPEG = 12
MOUNT_MODE_MAVLINK_TARGETING = 2


@dataclass(frozen=True)
class MissionItem:
    command: int
    frame: int = Frame.GLOBAL_RELATIVE_ALT
    p1: float = 0.0
    p2: float = 0.0
    p3: float = 0.0
    p4: float = 0.0
    x: float = 0.0  # latitude
    y: float = 0.0  # longitude
    z: float = 0.0  # altitude (m, relative to takeoff for frame 3)
    autocontinue: int = 1


def waypoint(p: LatLon, alt: float, yaw: float, radius: float = 3.0) -> MissionItem:
    return MissionItem(Cmd.NAV_WAYPOINT, Frame.GLOBAL_RELATIVE_ALT, p2=radius, p4=yaw, x=p.lat, y=p.lon, z=alt)


def change_speed(speed_ms: float) -> MissionItem:
    return MissionItem(Cmd.DO_CHANGE_SPEED, p2=speed_ms, p3=-1)


def format_mission(items: Sequence[MissionItem]) -> str:
    rows = [HEADER]
    for seq, it in enumerate(items):
        rows.append("\t".join([
            str(seq), "0", str(int(it.frame)), str(int(it.command)),
            *(f"{v:.6f}" for v in (it.p1, it.p2, it.p3, it.p4)),
            f"{it.x:.8f}", f"{it.y:.8f}", f"{it.z:.6f}", str(it.autocontinue),
        ]))
    return "\n".join(rows) + "\n"


def parse_mission(text: str) -> list[MissionItem]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].startswith("QGC WPL"):
        raise ValueError("not a QGC WPL mission file")
    items = []
    for ln in lines[1:]:
        f = ln.split()
        if len(f) != 12:
            raise ValueError(f"expected 12 fields, got {len(f)}: {ln!r}")
        vals = [float(v) for v in f[4:11]]
        items.append(MissionItem(int(f[3]), int(f[2]), *vals, autocontinue=int(f[11])))
    return items


def validate_mission(items: Sequence[MissionItem]) -> list[str]:
    """Return problems known to make the Anafi reject or misfly a plan."""
    problems = []
    if not any(it.command == Cmd.NAV_WAYPOINT for it in items):
        problems.append("no NAV_WAYPOINT (16): the drone rejects plans without one")
    for i, it in enumerate(items):
        if it.command in NAV_COMMANDS:
            if it.frame != Frame.GLOBAL_RELATIVE_ALT:
                problems.append(f"item {i}: navigation item should use frame 3 (relative altitude)")
            if not it.z > 0:
                problems.append(f"item {i}: missing altitude (the drone silently rejects these)")
            if not (-90 <= it.x <= 90 and -180 <= it.y <= 180) or (it.x == 0 and it.y == 0):
                problems.append(f"item {i}: invalid coordinates {it.x}, {it.y}")
        if it.command == Cmd.DO_CHANGE_SPEED and not 1 <= it.p2 <= 10:
            problems.append(f"item {i}: speed {it.p2} outside 1-10 m/s")
        if it.command == Cmd.DO_MOUNT_CONTROL and not -90 <= it.p1 <= 90:
            problems.append(f"item {i}: gimbal pitch {it.p1} outside -90..90")
        if it.command == Cmd.SET_STILL_CAPTURE_MODE and not it.p2 > 0:
            problems.append(f"item {i}: capture interval must be positive")
    return problems


def survey_mission(
    lines: Sequence[SurveyLine],
    home: LatLon,
    altitude_m: float,
    speed_ms: float,
    photo_spacing_m: float,
    end_action: str = "rth",
) -> list[MissionItem]:
    """Mission for one flight over `lines`, photos triggered by distance.

    Takeoff, climb vertically over home, fly each line with capture on, then
    fly back over home at survey altitude and descend there with RTH (`rth`),
    or land on the last line (`land`).
    """
    lines = list(lines)
    if not lines:
        raise ValueError("no lines")
    if end_action not in ("rth", "land"):
        raise ValueError("end_action must be 'rth' or 'land'")
    items = [
        MissionItem(Cmd.NAV_TAKEOFF, Frame.GLOBAL_RELATIVE_ALT, x=home.lat, y=home.lon, z=altitude_m),
        change_speed(speed_ms),
        MissionItem(Cmd.DO_MOUNT_CONTROL, p1=-90, z=MOUNT_MODE_MAVLINK_TARGETING),
        MissionItem(Cmd.SET_VIEW_MODE, p1=VIEW_MODE_ABSOLUTE),
        MissionItem(Cmd.SET_STILL_CAPTURE_MODE, p1=STILL_CAPTURE_GPS_POSITION, p2=photo_spacing_m),
        waypoint(home, altitude_m, heading_deg(home, lines[0].start)),
    ]
    # In absolute view mode the drone turns to a waypoint's yaw once it reaches
    # it and holds it for the next leg, so each yaw points along the leg ahead.
    for i, ln in enumerate(lines):
        h = ln.heading_deg
        if i + 1 < len(lines):
            h_next = heading_deg(ln.end, lines[i + 1].start)
        else:
            h_next = heading_deg(ln.end, home) if end_action == "rth" else h
        items += [
            waypoint(ln.start, altitude_m, h),
            MissionItem(Cmd.IMAGE_START_CAPTURE, p1=0, p2=0, p3=FORMAT_RECTILINEAR_JPEG),
            waypoint(ln.end, altitude_m, h_next),
            MissionItem(Cmd.IMAGE_STOP_CAPTURE),
        ]
    if end_action == "rth":
        # Back over home at survey altitude first, so the drone's RTH only has to descend
        # vertically (RTH alone would first climb to its minimum altitude if that is higher).
        items += [waypoint(home, altitude_m, h_next), MissionItem(Cmd.NAV_RETURN_TO_LAUNCH)]
    else:
        items.append(MissionItem(Cmd.NAV_LAND))
    return items


def tiny_mission(home: LatLon, target: LatLon, altitude_m: float = 10.0) -> list[MissionItem]:
    """Smallest useful plan for first tests: takeoff, climb, one waypoint, land.

    The Anafi's takeoff only lifts it to about 1 m, so the plan climbs straight up
    over home before flying to the target; going straight to the target would climb
    on a slant (seen in the Sphinx simulator). It lands vertically at the target.
    """
    h = heading_deg(home, target)
    return [
        MissionItem(Cmd.NAV_TAKEOFF, Frame.GLOBAL_RELATIVE_ALT, x=home.lat, y=home.lon, z=altitude_m),
        waypoint(home, altitude_m, h),
        waypoint(target, altitude_m, h),
        MissionItem(Cmd.NAV_LAND),
    ]


def refly_line_mission(items: Sequence[MissionItem], line: int, here: tuple[float, float]) -> list[MissionItem]:
    """A mission for a drone in the air: fly photo line `line` (0-based) again from its start, then the rest.

    For a plan paused and flown away from by hand: the drone itself would only resume
    straight to the line's end. It keeps the original's setup (speed, gimbal, view and
    photo mode), then a waypoint at `here` (lat, lon) at the line's altitude, so any
    climb or descent happens on the spot before flying sideways, then everything
    from the line's start waypoint on.
    """
    starts = [i for i, it in enumerate(items) if it.command == Cmd.IMAGE_START_CAPTURE]
    if not 0 <= line < len(starts):
        raise ValueError(f"no photo line {line + 1} in this mission")
    navs = [i for i in range(starts[line]) if items[i].command == Cmd.NAV_WAYPOINT]
    if not navs:
        raise ValueError("the photo line has no start waypoint")
    line_start = navs[-1]
    first_line_start = max(i for i in range(starts[0]) if items[i].command == Cmd.NAV_WAYPOINT)
    setup = [it for it in items[:first_line_start]
             if it.command not in NAV_COMMANDS
             and it.command not in (Cmd.IMAGE_START_CAPTURE, Cmd.IMAGE_STOP_CAPTURE)]
    w = items[line_start]
    on_the_spot = MissionItem(Cmd.NAV_WAYPOINT, frame=w.frame, p1=w.p1, p2=w.p2, p3=w.p3, p4=w.p4,
                              x=here[0], y=here[1], z=w.z)
    return [*setup, on_the_spot, *items[line_start:]]
