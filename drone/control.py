"""Flight control through Parrot's Olympe SDK: drone state, pre-flight checks,
and flight plan commands (start, pause, stop, return home).

Olympe is optional (`uv sync --extra fly`, x86_64 Linux only), so it is
imported only when connecting. Only the command functions at the end move the
drone.

Connect to the drone directly (DRONE_HOST, over its Wi-Fi) or through the
Skycontroller 3 over USB (SKYCONTROLLER_HOST), which relays drone messages.
"""

from __future__ import annotations

import hashlib
import logging
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field

from planner.geo import LatLon, distance_m
from planner.mavlink import NAV_COMMANDS, Cmd, parse_mission, validate_mission

from .upload import DRONE_HOST, SKYCONTROLLER_HOST, DroneError

UNKNOWN_COORD = 500.0  # what the drone reports for lat/lon/alt before a GPS fix


@dataclass
class Status:
    battery_pct: int | None = None
    flying_state: str | None = None
    gps_fixed: bool | None = None
    position: tuple[float, float, float] | None = None  # lat, lon, alt
    home: tuple[float, float, float] | None = None
    plan_available: bool | None = None
    plan_components: dict[str, bool] = field(default_factory=dict)  # what a plan needs to start
    plan_state: str | None = None  # playing, stopped, paused, loaded
    plan_file: str | None = None
    max_altitude_m: float | None = None
    max_distance_m: float | None = None
    geofence: bool | None = None  # True: the drone won't fly beyond max_distance_m
    rth_delay_s: int | None = None
    home_type: str | None = None
    alert: str | None = None
    altitude_m: float | None = None  # above takeoff
    speed_ms: float | None = None  # horizontal
    mission_item: int | None = None  # index of the last executed mission item
    rth_state: str | None = None  # available, inProgress, unavailable, pending
    play_error: str | None = None  # why the last Start failed
    voltage_mv: int | None = None  # battery pack voltage (sag under load shows a tired pack)

    def distance_from_home_m(self) -> float | None:
        if self.position is None or self.home is None:
            return None
        return distance_m(LatLon(*self.home[:2]), LatLon(*self.position[:2]))


def _enum_name(v) -> str:
    return getattr(v, "name", str(v))


def _coords(st) -> tuple[float, float, float] | None:
    lat, lon, alt = st["latitude"], st["longitude"], st["altitude"]
    return None if UNKNOWN_COORD in (lat, lon) else (lat, lon, alt)


@contextmanager
def connect(host: str = DRONE_HOST):
    # Olympe 8.5 warns about its own docstrings and logs at INFO while importing.
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    logging.disable(logging.INFO)
    try:
        import olympe
    except ImportError as e:
        raise DroneError("Olympe isn't installed: run `uv sync --extra fly` (x86_64 Linux only)") from e
    finally:
        logging.disable(logging.NOTSET)
    olympe.log.update_config({"loggers": {"olympe": {"level": "ERROR"}}})
    # SkyController3 talks over the controller's USB network link; olympe.SkyController
    # is the Skycontroller 4's USB mux protocol and times out on a Skycontroller 3.
    dev = olympe.SkyController3(host) if host == SKYCONTROLLER_HOST else olympe.Drone(host)
    try:
        if not dev.connect(retry=2):
            raise DroneError(f"Olympe couldn't connect to {host}")
        time.sleep(1.0)  # let the initial state burst arrive
        yield dev
    finally:
        dev.disconnect()
        # destroy() logs a harmless "deferred cleanup still pending" error
        olympe.log.update_config({"loggers": {"olympe": {"level": "CRITICAL"}}})
        dev.destroy()


def read_status(dev) -> Status:
    """Collect the drone's last reported state. Missing states stay None."""
    from olympe.messages.ardrone3 import GPSSettingsState, PilotingSettingsState, PilotingState
    from olympe.messages.common import CommonState, FlightPlanState, MavlinkState

    def get(msg):
        try:
            return dev.get_state(msg)
        except Exception:  # state not received (yet)
            return None

    s = Status()
    if st := get(CommonState.BatteryStateChanged):
        s.battery_pct = st["percent"]
    if st := get(PilotingState.FlyingStateChanged):
        s.flying_state = _enum_name(st["state"])
    if st := get(GPSSettingsState.GPSFixStateChanged):
        s.gps_fixed = bool(st["fixed"])
    if st := get(PilotingState.PositionChanged):
        s.position = _coords(st)
    if st := get(GPSSettingsState.HomeChanged):
        s.home = _coords(st)
    if st := get(FlightPlanState.AvailabilityStateChanged):
        s.plan_available = bool(st["AvailabilityState"])
    if st := get(FlightPlanState.ComponentStateListChanged):
        s.plan_components = {_enum_name(c): bool(v["State"]) for c, v in st.items()}
    if st := get(MavlinkState.MavlinkFilePlayingStateChanged):
        s.plan_state = _enum_name(st["state"])
        s.plan_file = st["filepath"] or None
    if st := get(PilotingSettingsState.MaxAltitudeChanged):
        s.max_altitude_m = st["current"]
    if st := get(PilotingSettingsState.MaxDistanceChanged):
        s.max_distance_m = st["current"]
    if st := get(PilotingSettingsState.NoFlyOverMaxDistanceChanged):
        s.geofence = bool(st["shouldNotFlyOver"])
    if st := get(GPSSettingsState.ReturnHomeDelayChanged):
        s.rth_delay_s = st["delay"]
    if st := get(GPSSettingsState.HomeTypeChanged):
        s.home_type = _enum_name(st["type"])
    if st := get(PilotingState.AlertStateChanged):
        s.alert = _enum_name(st["state"])
    if st := get(PilotingState.AltitudeChanged):
        s.altitude_m = st["altitude"]
    if st := get(PilotingState.SpeedChanged):
        s.speed_ms = (st["speedX"] ** 2 + st["speedY"] ** 2) ** 0.5
    if st := get(MavlinkState.MissionItemExecuted):
        s.mission_item = st["idx"]
    if st := get(PilotingState.NavigateHomeStateChanged):
        s.rth_state = _enum_name(st["state"])
    if st := get(MavlinkState.MavlinkPlayErrorStateChanged):
        s.play_error = _enum_name(st["error"])
    try:
        from olympe.messages.battery import voltage
        if st := get(voltage):
            s.voltage_mv = st["voltage"]
    except ImportError:
        pass
    return s


def _fmt(v, unit: str = "", fmt: str = "") -> str:
    return "?" if v is None else f"{v:{fmt}}{unit}"


def _fmt_pos(p) -> str:
    return "unknown" if p is None else f"{p[0]:.6f}, {p[1]:.6f} (alt {p[2]:.1f} m)"


def format_status(s: Status) -> str:
    comps = ", ".join(f"{k} {'ok' if v else 'MISSING'}" for k, v in s.plan_components.items())
    plan = {True: "ready", False: "NOT ready", None: "?"}[s.plan_available]
    geofence = {True: "on", False: "off", None: "?"}[s.geofence]
    return "\n".join([
        f"battery      {_fmt(s.battery_pct, ' %')}",
        f"state        {_fmt(s.flying_state)}" + (f", alert: {s.alert}" if s.alert not in (None, "none") else ""),
        f"GPS          {'fixed' if s.gps_fixed else 'no fix' if s.gps_fixed is False else '?'}"
        f", position {_fmt_pos(s.position)}",
        f"home         {_fmt_pos(s.home)}, type {_fmt(s.home_type)}, return home after {_fmt(s.rth_delay_s, ' s')} link loss",
        f"flight plan  {plan}" + (f" ({comps})" if comps else "")
        + f"; {_fmt(s.plan_state)}" + (f" {s.plan_file}" if s.plan_file else ""),
        f"limits       max altitude {_fmt(s.max_altitude_m, ' m', '.0f')}, "
        f"max distance {_fmt(s.max_distance_m, ' m', '.0f')} (geofence {geofence})",
    ])


@dataclass
class Mission:
    uid: str  # what the drone calls the plan: MD5 of the file
    items: list
    problems: list[str]

    @property
    def nav_points(self) -> list[tuple[float, float, float]]:
        return [(it.x, it.y, it.z) for it in self.items if it.command in NAV_COMMANDS]

    @property
    def waypoints(self) -> int:
        return len(self.nav_points)

    @property
    def max_altitude_m(self) -> float:
        return max((p[2] for p in self.nav_points), default=0.0)

    def max_distance_m(self, home: tuple[float, float, float] | None) -> float:
        """Furthest point from home (or from the first point if home is unknown)."""
        pts = self.nav_points
        if not pts:
            return 0.0
        origin = LatLon(*(home or pts[0])[:2])
        return max(distance_m(origin, LatLon(p[0], p[1])) for p in pts)


def photo_lines(items: list) -> list[tuple[int, int]]:
    """(start, stop) item indexes of each photo stretch: an image start and the stop after it."""
    out, start = [], None
    for i, it in enumerate(items):
        if it.command == Cmd.IMAGE_START_CAPTURE and start is None:
            start = i
        elif it.command == Cmd.IMAGE_STOP_CAPTURE and start is not None:
            out.append((start, i))
            start = None
    return out


def load_mission(data: bytes) -> Mission:
    items = parse_mission(data.decode())
    return Mission(hashlib.md5(data).hexdigest(), items, validate_mission(items))


def preflight_problems(s: Status, m: Mission, on_drone: bool, min_battery_pct: int) -> list[str]:
    """Reasons not to start `m` now. Empty means go."""
    p = [f"mission file: {x}" for x in m.problems]
    if not on_drone:
        p.append(f"plan {m.uid} is not on the drone (upload it after switching the drone on)")
    if s.flying_state != "landed":
        p.append(f"drone is not landed (state: {s.flying_state or '?'})")
    if s.battery_pct is None or s.battery_pct < min_battery_pct:
        p.append(f"battery {_fmt(s.battery_pct, ' %')} is below the {min_battery_pct} % minimum")
    if not s.gps_fixed:
        p.append("no GPS fix")
    if s.home is None:
        p.append("no home point yet (wait for GPS)")
    if s.plan_available is not True:
        missing = [k for k, ok in s.plan_components.items() if not ok]
        p.append("drone says the flight plan can't start" + (f": missing {', '.join(missing)}" if missing else ""))
    if s.max_altitude_m is not None and m.max_altitude_m > s.max_altitude_m:
        p.append(f"mission altitude {m.max_altitude_m:.0f} m is above the drone's max altitude "
                 f"{s.max_altitude_m:.0f} m")
    if s.geofence and s.max_distance_m is not None and m.max_distance_m(s.home) > s.max_distance_m:
        p.append(f"mission goes {m.max_distance_m(s.home):.0f} m from home, beyond the geofence "
                 f"({s.max_distance_m:.0f} m)")
    return p


def format_telemetry(s: Status, elapsed_s: float, items: int) -> str:
    dist = s.distance_from_home_m()
    item = "-" if s.mission_item is None else f"{s.mission_item + 1}/{items}"
    m, sec = divmod(int(elapsed_s), 60)
    return (f"{m:02d}:{sec:02d}  battery {_fmt(s.battery_pct, '%')}  {_fmt(s.flying_state)}  "
            f"alt {_fmt(s.altitude_m, ' m', '.0f')}  {_fmt(s.speed_ms, ' m/s', '.1f')}  "
            f"home {_fmt(dist, ' m', '.0f')}  item {item}  plan {_fmt(s.plan_state)}"
            + (f"  RTH {s.rth_state}" if s.rth_state in ("inProgress", "pending") else ""))


# Commands that move the drone.

def start_plan(dev, uid: str, timeout_s: float = 10.0) -> None:
    """Start (or resume after pause) the flight plan stored on the drone as `uid`."""
    from olympe.messages.common.Mavlink import Start
    from olympe.messages.common.MavlinkState import MavlinkFilePlayingStateChanged

    ok = dev(Start(filepath=uid, type="flightPlan")
             >> MavlinkFilePlayingStateChanged(state="playing", _timeout=timeout_s)).wait().success()
    if not ok:
        err = read_status(dev).play_error
        raise DroneError("the drone didn't start the flight plan"
                         + (f" (error: {err})" if err and err != "none" else
                            "; check the mission file: the drone rejects bad plans silently"))


def pause_plan(dev) -> None:
    from olympe.messages.common.Mavlink import Pause
    dev(Pause()).wait(5)


def start_photos(dev) -> bool:
    """Start taking photos in the camera's current mode (the mission's GPS lapse)."""
    from olympe.messages.camera import take_photo
    return dev(take_photo(cam_id=0)).wait(5).success()


def stop_photos(dev) -> bool:
    from olympe.messages.camera import stop_photo
    return dev(stop_photo(cam_id=0)).wait(5).success()


def stop_plan(dev) -> None:
    """Stop the flight plan; the drone hovers where it is."""
    from olympe.messages.common.Mavlink import Stop
    dev(Stop()).wait(5)


def return_home(dev) -> None:
    """Pause a running plan (the drone starts RTH only once hovering), then return home."""
    from olympe.messages.ardrone3.Piloting import NavigateHome

    if read_status(dev).plan_state == "playing":
        pause_plan(dev)
    dev(NavigateHome(start=1)).wait(5)

