"""Fly a mission from the terminal: check, confirm, start, then watch it live.

Keys while flying: r return home, p pause/resume, s stop (hover), l land,
q quit the view (the flight plan keeps running on the drone). The
Skycontroller stays the primary way to take over.

What happens during the flight (log, photos after a resume, return home note)
is in session.FlightSession, shared with the GUI.
"""

from __future__ import annotations

import os
import select
import sys
import termios
import time
import tty
from contextlib import contextmanager
from pathlib import Path

from . import control
from .session import FlightSession, ensure_uploaded
from .upload import DRONE_HOST, DroneError, NotReachable

KEYS = "[r] return home  [p] pause/resume  [s] stop (hover)  [l] land  [q] quit view"


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
        on_drone = ensure_uploaded(mission, path, http_host, upload_log)
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

        session = FlightSession(dev, mission, log_dir)
        session.start()
        try:
            return _watch(session)
        finally:
            shown = len(session.events)  # the live view printed these already
            session.close()
            _print_events(session, shown)


def _print_events(session: FlightSession, since: int = 0) -> int:
    for e in session.events[since:]:
        print("\n" + e.text)
    return len(session.events)


def _watch(session: FlightSession) -> int:
    shown = _print_events(session)
    print(KEYS)
    quit_armed_at = 0.0
    with _keys() as read_key:
        while True:
            s = session.step()
            sys.stdout.write("\r\033[K" + control.format_telemetry(s, session.elapsed_s, len(session.mission.items)))
            sys.stdout.flush()
            shown = _print_events(session, shown)
            if session.finished:
                return 0

            key = read_key(0.5)
            if key == "r":
                session.return_home()
            elif key == "p":
                session.toggle_pause()
            elif key == "s":
                session.stop()
            elif key == "l":
                session.land()
            elif key == "q":
                if not session.airborne or s.flying_state == "landed" or time.monotonic() - quit_armed_at < 3:
                    print("\nLeaving the view." + (" The flight plan continues on the drone; "
                                                   "use the Skycontroller to take over." if session.airborne else ""))
                    return 0
                quit_armed_at = time.monotonic()
                print("\nThe drone is flying. Press q again within 3 s to leave the view anyway.")
            shown = _print_events(session, shown)
