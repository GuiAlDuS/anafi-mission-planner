"""The GUI's link to the drone for flying: one Olympe connection for the whole server.

A background thread connects and reads the drone about twice a second (through
the FlightSession once a flight has started), so the page only asks the server:
closing or reloading the browser doesn't touch the flight. The page polls
state() and sends commands; the Skycontroller stays the primary way to take over.

If this server stops while the drone flies, the drone loses its controller: in
the simulator it either carried on with the plan or started its own return home.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import threading
from dataclasses import dataclass
from pathlib import Path

from drone import control
from drone.session import FlightSession, ensure_uploaded
from drone.upload import DRONE_HOST, SKYCONTROLLER_HOST, DroneError, NotReachable

READ_PERIOD_S = 0.5
MIN_BATTERY_PCT = 30
NO_OLYMPE = ("Flying from the computer needs Parrot's Olympe SDK, which only runs on x86_64 Linux. "
             "Install it with `uv sync --extra fly`, then restart anafi-gui. You can still fly the "
             "missions from FreeFlight on the phone.")
ROUTE_HINT = (f"Through the Skycontroller the drone's flight plan service is reached with a route: "
              f"sudo ip route add {DRONE_HOST}/32 via {SKYCONTROLLER_HOST}")


class FlyError(DroneError):
    """A request the link can't do now (shown to the pilot as is)."""


@dataclass
class Note:
    id: int
    time: str  # wall clock, HH:MM:SS
    kind: str  # info, action, warning, error, done
    text: str


@dataclass
class LoadedMission:
    name: str
    flight: int
    path: Path
    mission: control.Mission
    on_drone: bool


def _status_json(s: control.Status | None) -> dict | None:
    if s is None:
        return None
    return {
        "battery_pct": s.battery_pct, "voltage_mv": s.voltage_mv, "flying_state": s.flying_state,
        "gps_fixed": s.gps_fixed, "position": s.position, "home": s.home, "home_type": s.home_type,
        "home_m": s.distance_from_home_m(), "altitude_m": s.altitude_m, "speed_ms": s.speed_ms,
        "plan_state": s.plan_state, "plan_available": s.plan_available,
        "mission_item": s.mission_item, "rth_state": s.rth_state, "alert": s.alert,
        "max_altitude_m": s.max_altitude_m, "max_distance_m": s.max_distance_m, "geofence": s.geofence,
        "rth_delay_s": s.rth_delay_s,
    }


class FlyLink:
    HOSTS = {"skycontroller": SKYCONTROLLER_HOST, "wifi": DRONE_HOST}

    def __init__(self, out_root: Path, log_dir: str | Path = "flight_logs", upload_log: str | Path = "uploads.json",
                 min_battery_pct: int = MIN_BATTERY_PCT, connector=None, ctl=control, uploader=ensure_uploaded,
                 can_fly: bool | None = None, read_period_s: float = READ_PERIOD_S):
        self.out_root = Path(out_root)
        self.log_dir, self.upload_log = Path(log_dir), Path(upload_log)
        self.min_battery_pct = min_battery_pct
        self.ctl, self.uploader = ctl, uploader
        self.connector = connector or ctl.connect
        if can_fly is None:
            can_fly = connector is not None or importlib.util.find_spec("olympe") is not None
        self.can_fly = can_fly
        self.read_period_s = read_period_s
        self.link = "disconnected"  # disconnected, connecting, connected, lost
        self.host: str | None = None
        self.error: str | None = None
        self.status: control.Status | None = None
        self.mission: LoadedMission | None = None
        self.session: FlightSession | None = None
        self._session_wall: dt.datetime | None = None
        self._copied = 0  # session events already copied into the notes
        self._notes: list[Note] = []
        self._dev = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    # ---- messages

    def _note(self, kind: str, text: str) -> None:
        self._notes.append(Note(len(self._notes) + 1, f"{dt.datetime.now():%H:%M:%S}", kind, text))

    def _pull_session_events(self) -> None:
        """Copy new FlightSession events into the notes, with wall-clock times."""
        s = self.session
        if s is None:
            return
        for e in s.events[self._copied:]:
            when = self._session_wall + dt.timedelta(seconds=e.t_s)
            self._notes.append(Note(len(self._notes) + 1, f"{when:%H:%M:%S}", e.kind, e.text))
        self._copied = len(s.events)

    # ---- connection

    def connect(self, link: str, host: str | None = None) -> dict:
        if not self.can_fly:
            raise FlyError(NO_OLYMPE)
        if link == "other":
            if not host:
                raise FlyError("give the drone's address, e.g. 10.202.0.1 for the simulator")
        elif link in self.HOSTS:
            host = self.HOSTS[link]
        else:
            raise FlyError(f"link must be one of {', '.join([*self.HOSTS, 'other'])}")
        with self._lock:
            if self.link in ("connecting", "connected"):
                raise FlyError(f"already {self.link} to {self.host}")
            self.link, self.host, self.error, self.status = "connecting", host, None, None
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, args=(host,), name="fly-link", daemon=True)
            self._thread.start()
            self._note("action", f"Connecting to {host} ...")
        return self.state()

    def _run(self, host: str) -> None:
        try:
            with self.connector(host) as dev:
                with self._lock:
                    self._dev = dev
                    self.link = "connected"
                    self._note("info", f"Connected to {host}.")
                while not self._stop.is_set():
                    with self._lock:
                        if self.session is not None and not self.session.finished:
                            self.status = self.session.step()
                            self._pull_session_events()
                        else:
                            self.status = self.ctl.read_status(dev)
                    self._stop.wait(self.read_period_s)
        except Exception as e:  # the link is gone: say why, keep what we knew
            with self._lock:
                self.error = str(e) or type(e).__name__
                self.link = "lost" if self.link == "connected" else "disconnected"
                self._note("error", f"Lost the link to {host}: {self.error}" if self.link == "lost"
                           else f"Couldn't connect to {host}: {self.error}")
        else:
            with self._lock:
                self.link = "disconnected"
                self._note("info", f"Disconnected from {host}.")
        finally:
            with self._lock:
                if self.session is not None:
                    self.session.close()
                    self._pull_session_events()
                self._dev = None

    def disconnect(self, force: bool = False) -> dict:
        with self._lock:
            flying = self.status is not None and self.status.flying_state not in (None, "landed")
            if flying and not force:
                raise FlyError("the drone is flying: land it first. Disconnecting now leaves it without "
                               "this controller (it carries on with the plan or returns home)")
            self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        return self.state()

    def _require_dev(self):
        if self.link != "connected" or self._dev is None:
            raise FlyError("not connected to the drone")
        return self._dev

    # ---- mission

    def _flying_now(self) -> bool:
        return self.session is not None and self.session.started and not self.session.finished

    def select_mission(self, name: str, flight: int = 1) -> dict:
        path = self.out_root / name / f"flight_{int(flight)}.mavlink"
        if not path.is_file():
            raise FlyError(f"no flight {flight} in mission {name!r}")
        with self._lock:
            if self._flying_now():
                raise FlyError("a flight is in progress: land before choosing another mission")
            self._require_dev()
            mission = self.ctl.load_mission(path.read_bytes())
            http_host = DRONE_HOST if self.host == SKYCONTROLLER_HOST else self.host
            try:
                on_drone = self.uploader(mission, path, http_host, self.upload_log,
                                         say=lambda text: self._note("action", text))
            except NotReachable as e:
                raise FlyError(f"{e}. {ROUTE_HINT}" if self.host == SKYCONTROLLER_HOST else str(e)) from e
            self.mission = LoadedMission(name, int(flight), path, mission, on_drone)
            self.session, self._copied = None, 0
            self._note("info" if on_drone else "warning",
                       f"Mission {name}, flight {flight}: " + ("on the drone." if on_drone else
                                                               "upload didn't verify; see the checks."))
        return self.state()

    def checks(self) -> list[str]:
        if self.link != "connected":
            return ["connect to the drone"]
        if self.mission is None:
            return ["choose a mission"]
        if self.status is None:
            return ["waiting for the drone's state"]
        m = self.mission
        return self.ctl.preflight_problems(self.status, m.mission, m.on_drone, self.min_battery_pct)

    # ---- flying

    def start(self) -> dict:
        """Take off and start the chosen mission. The page asks for a press-and-hold first."""
        with self._lock:
            dev = self._require_dev()
            if self._flying_now():
                raise FlyError("a flight is already in progress")
            self.status = self.ctl.read_status(dev)  # decide on fresh data, not the last poll
            problems = self.checks()
            if problems:
                raise FlyError("not starting: " + "; ".join(problems))
            self.session = FlightSession(dev, self.mission.mission, self.log_dir, ctl=self.ctl)
            self._session_wall = dt.datetime.now()
            self._copied = 0
            self.session.start()
            self._pull_session_events()
        return self.state()

    COMMANDS = ("pause", "resume", "stop", "rth", "land")

    def command(self, action: str) -> dict:
        if action not in self.COMMANDS:
            raise FlyError(f"action must be one of {', '.join(self.COMMANDS)}")
        with self._lock:
            dev = self._require_dev()
            s = self.session
            if action in ("rth", "land") and (s is None or not s.started or s.finished):
                # Also without a flight of ours (flown by hand, or after a restart of this server).
                (self.ctl.return_home if action == "rth" else self.ctl.land)(dev)
                self._note("action", "Returning home." if action == "rth" else "Landing.")
                return self.state()
            if s is None or not s.started:
                raise FlyError("no flight in progress")
            {"pause": s.pause, "resume": s.resume, "stop": s.stop, "rth": s.return_home, "land": s.land}[action]()
            self._pull_session_events()
        return self.state()

    # ---- for the page

    def state(self, since: int = 0) -> dict:
        with self._lock:
            self._pull_session_events()
            m, s = self.mission, self.session
            return {
                "can_fly": self.can_fly,
                "no_olympe": None if self.can_fly else NO_OLYMPE,
                "link": {"state": self.link, "host": self.host, "error": self.error},
                "status": _status_json(self.status),
                "mission": None if m is None else {
                    "name": m.name, "flight": m.flight, "uid": m.mission.uid, "on_drone": m.on_drone,
                    "items": len(m.mission.items), "waypoints": m.mission.waypoints,
                    "max_altitude_m": m.mission.max_altitude_m,
                    "photo_lines": [list(x) for x in control.photo_lines(m.mission.items)],
                    "route": [[p[0], p[1], p[2]] for p in m.mission.nav_points],
                },
                "checks": self.checks(),
                "min_battery_pct": self.min_battery_pct,
                "flight": None if s is None else {
                    "started": s.started, "airborne": s.airborne, "finished": s.finished,
                    "elapsed_s": s.elapsed_s if s.started and not s.finished else None,
                    "start_battery": s.start_battery, "log": str(s.log_path) if s.log_path else None,
                    "photos_restarted": s._photos.stop_at is not None,
                },
                "notes": [vars(n) for n in self._notes if n.id > since][-50:],
            }

    def shutdown(self) -> None:
        """Server stopping: let go of the drone (flying or not)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
