"""Fly a mission from the terminal: check, confirm, start, then watch it live.

Keys while flying: r return home, p pause/resume, s stop (hover), q quit the
view (the flight plan keeps running on the drone). The Skycontroller stays the
primary way to take over.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import select
import sys
import termios
import time
import tty
from contextlib import contextmanager
from pathlib import Path

from . import control
from .upload import DRONE_HOST, DroneError, FlightPlanClient, NotReachable, append_log, upload_and_verify

KEYS = "[r] return home  [p] pause/resume  [s] stop (hover)  [q] quit view"
LOG_FIELDS = ["t_s", "battery_pct", "flying_state", "plan_state", "mission_item", "lat", "lon",
              "altitude_m", "speed_ms", "home_m", "rth_state", "voltage_mv"]


@contextmanager
def _keys():
    """Read single key presses without Enter; yields a function(timeout) -> key or ''."""
    if not sys.stdin.isatty():
        yield lambda timeout: (time.sleep(timeout), "")[1]
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    try:
        def read(timeout: float) -> str:
            ready, _, _ = select.select([sys.stdin], [], [], timeout)
            return os.read(fd, 1).decode(errors="ignore").lower() if ready else ""
        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _ensure_uploaded(mission: control.Mission, path: Path, http_host: str, upload_log: str) -> bool:
    client = FlightPlanClient(http_host)
    if any(p.get("uid") == mission.uid for p in client.list()):
        return True
    print(f"Plan {mission.uid} isn't on the drone; uploading {path} ...")
    rec = upload_and_verify(client, path)
    append_log(rec, upload_log)
    return rec.uid == mission.uid and rec.readback != "mismatch" and rec.listed


class RthWatch:
    """Notice when a return home ends with the drone hovering over home.

    The Anafi doesn't land after a return home (by default it hovers about 2 m above
    home), so without a note the live view just keeps waiting for "landed".
    """

    def __init__(self):
        self.returning = False

    def update(self, s: control.Status) -> str | None:
        if s.rth_state in ("inProgress", "pending"):
            self.returning = True
            return None
        if self.returning and s.flying_state == "hovering":
            self.returning = False
            dist = s.distance_from_home_m()
            where = f" ({dist:.0f} m from home)" if dist is not None else ""
            return (f"Return home finished: the drone is hovering over home{where}. "
                    "Land it with the Skycontroller.")
        return None


def _confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() == "yes"
    except EOFError:
        return False


def fly(host: str, path: str | Path, min_battery_pct: int = 30, http_host: str = DRONE_HOST,
        log_dir: str | Path = "flight_logs", upload_log: str = "uploads.json") -> int:
    path = Path(path)
    mission = control.load_mission(path.read_bytes())
    try:
        on_drone = _ensure_uploaded(mission, path, http_host, upload_log)
    except NotReachable as e:
        raise DroneError(f"{e}\nCan't check the plan is on the drone. Through the Skycontroller, add:\n"
                         f"  sudo ip route add {DRONE_HOST}/32 via {control.SKYCONTROLLER_HOST}") from e

    with control.connect(host) as dev:
        s = control.read_status(dev)
        print(control.format_status(s))
        print(f"mission      {path.name}: {mission.waypoints} waypoints, max altitude {mission.max_altitude_m:.0f} m, "
              f"up to {mission.max_distance_m(s.home):.0f} m from home")
        problems = control.preflight_problems(s, mission, on_drone, min_battery_pct)
        if problems:
            print("\nNOT STARTING:\n  " + "\n  ".join(problems))
            return 1
        print("\nAll checks passed. Skycontroller in hand, area clear?")
        if not _confirm("Type 'yes' to take off and start the mission: "):
            print("Not started.")
            return 1

        control.start_plan(dev, mission.uid)
        return _watch(dev, mission, Path(log_dir))


def _watch(dev, mission: control.Mission, log_dir: Path) -> int:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{mission.uid[:8]}.csv"
    t0 = time.monotonic()
    start_battery = None
    airborne = False
    quit_armed_at = 0.0
    rth = RthWatch()
    print(f"Started. Logging to {log_path}\n{KEYS}")
    with open(log_path, "w", newline="") as f, _keys() as read_key:
        log = csv.writer(f)
        log.writerow(LOG_FIELDS)
        while True:
            s = control.read_status(dev)
            t = time.monotonic() - t0
            start_battery = start_battery if start_battery is not None else s.battery_pct
            airborne = airborne or s.flying_state not in (None, "landed")
            pos = s.position or (None, None, None)
            log.writerow([f"{t:.1f}", s.battery_pct, s.flying_state, s.plan_state, s.mission_item, pos[0], pos[1],
                          s.altitude_m, s.speed_ms, s.distance_from_home_m(), s.rth_state, s.voltage_mv])
            f.flush()
            sys.stdout.write("\r\033[K" + control.format_telemetry(s, t, len(mission.items)))
            sys.stdout.flush()

            if note := rth.update(s):
                print("\n" + note)

            if airborne and s.flying_state == "landed":
                print(f"\nLanded after {t / 60:.1f} min, battery {start_battery} % -> {s.battery_pct} %.")
                return 0

            key = read_key(0.5)
            if key == "r":
                print("\nRETURN HOME")
                control.return_home(dev)
            elif key == "p":
                if s.plan_state == "playing":
                    print("\nPausing (drone hovers). Press p again to resume.")
                    control.pause_plan(dev)
                elif s.plan_state == "paused":
                    print("\nResuming.")
                    control.start_plan(dev, mission.uid)
            elif key == "s":
                print("\nStopping the flight plan; the drone hovers. Fly it home with the Skycontroller or press r.")
                control.stop_plan(dev)
            elif key == "q":
                if not airborne or s.flying_state == "landed" or time.monotonic() - quit_armed_at < 3:
                    print("\nLeaving the view." + (" The flight plan continues on the drone; "
                                                   "use the Skycontroller to take over." if airborne else ""))
                    return 0
                quit_armed_at = time.monotonic()
                print("\nThe drone is flying. Press q again within 3 s to leave the view anyway.")
