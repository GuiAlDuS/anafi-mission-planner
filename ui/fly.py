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
from planner.geo import LatLon, distance_m
from planner.mavlink import format_mission, refly_line_mission
from drone.session import FlightSession, ensure_uploaded
from drone.upload import DRONE_HOST, SKYCONTROLLER_HOST, DroneError, NotReachable

READ_PERIOD_S = 0.5
MOVED_AFTER_PAUSE_M = 5.0  # flown away from the pause point by hand: offer to refly the line
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
    first_line: int = 1  # survey number of this flight's first line
    survey_lines: int | None = None  # lines in the whole survey


def route_legs(items: list) -> list[list]:
    """[lat, lon, alt, photos] per waypoint; photos: the leg flown to it takes photos."""
    from planner.mavlink import NAV_COMMANDS, Cmd
    out, photos = [], False
    for it in items:
        if it.command == Cmd.IMAGE_START_CAPTURE:
            photos = True
        elif it.command == Cmd.IMAGE_STOP_CAPTURE:
            photos = False
        elif it.command in NAV_COMMANDS:
            out.append([it.x, it.y, it.z, photos])
    return out


def _status_json(s: control.Status | None) -> dict | None:
    if s is None:
        return None
    return {
        "battery_pct": s.battery_pct, "voltage_mv": s.voltage_mv, "flying_state": s.flying_state,
        "gps_fixed": s.gps_fixed, "position": s.position, "home": s.home, "home_type": s.home_type,
        "home_m": s.distance_from_home_m(), "altitude_m": s.altitude_m, "speed_ms": s.speed_ms,
        "heading_deg": s.heading_deg,
        "plan_state": s.plan_state, "plan_available": s.plan_available,
        "mission_item": s.mission_item, "rth_state": s.rth_state, "alert": s.alert,
        "max_altitude_m": s.max_altitude_m, "max_distance_m": s.max_distance_m, "geofence": s.geofence,
        "rth_delay_s": s.rth_delay_s,
    }


class FlyLink:
    HOSTS = {"skycontroller": SKYCONTROLLER_HOST, "wifi": DRONE_HOST}

    def __init__(self, out_root: Path, log_dir: str | Path = "flight_logs", upload_log: str | Path = "uploads.json",
                 min_battery_pct: int = MIN_BATTERY_PCT, connector=None, ctl=control, uploader=ensure_uploaded,
                 can_fly: bool | None = None, read_period_s: float = READ_PERIOD_S, book=None):
        self.out_root = Path(out_root)
        self.log_dir, self.upload_log = Path(log_dir), Path(upload_log)
        self.min_battery_pct = min_battery_pct
        self.ctl, self.uploader = ctl, uploader
        self.connector = connector or ctl.connect
        if can_fly is None:
            can_fly = connector is not None or importlib.util.find_spec("olympe") is not None
        self.can_fly = can_fly
        self.read_period_s = read_period_s
        # The saved missions (server.MissionBook): line numbers, and "continue from line N" plans.
        self.book = book
        self.resume: dict | None = None  # {"line", "total"}: where an unfinished survey stopped
        self._pause: dict | None = None  # where the plan paused: {"pos", "item"}
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
                            self._track_pause()
                            if self.session.finished:
                                self._after_flight()
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

    def video_url(self) -> str:
        """The drone's live video (RTSP). Through the Skycontroller it's reached by the same route as uploads."""
        if self.link != "connected" or not self.host:
            raise FlyError("not connected to the drone")
        return f"rtsp://{DRONE_HOST if self.host in self.HOSTS.values() else self.host}/live"

    def _require_dev(self):
        if self.link != "connected" or self._dev is None:
            raise FlyError("not connected to the drone")
        return self._dev

    # ---- mission

    def _flying_now(self) -> bool:
        return self.session is not None and self.session.started and not self.session.finished

    def _track_pause(self) -> None:
        """Remember where the plan paused (a stick push, Pause, return home): the line, the spot."""
        s = self.status
        if s.plan_state == "paused" and self._pause is None:
            self._pause = {"pos": s.position, "item": s.mission_item}
        elif s.plan_state != "paused":
            self._pause = None

    def _paused_line(self) -> int | None:
        """0-based photo line of the loaded mission the plan paused on, if it paused on one."""
        if self._pause is None or self.mission is None or self._pause["item"] is None:
            return None
        for i, (start, stop) in enumerate(control.photo_lines(self.mission.mission.items)):
            if start <= self._pause["item"] < stop:
                return i
        return None

    def _paused_json(self) -> dict | None:
        s = self.status
        if self._pause is None or s is None or not self._flying_now():
            return None
        moved = None
        if self._pause["pos"] and s.position:
            moved = distance_m(LatLon(*self._pause["pos"][:2]), LatLon(*s.position[:2]))
        line = self._paused_line()
        return {"moved_m": moved, "line": None if line is None else self.mission.first_line + line,
                "offer_refly": line is not None and moved is not None and moved > MOVED_AFTER_PAUSE_M}

    def refly(self) -> dict:
        """Paused on a line and flown away by hand: fly that line again from its start, then the rest.

        Resuming would fly straight from here to the line's end: the part of the line not
        yet photographed would be skipped, and the photos on the way would be off the line.
        """
        with self._lock:
            self._require_dev()
            s, m, line = self.status, self.mission, self._paused_line()
            if not self._flying_now() or s is None or s.plan_state != "paused" or line is None or not s.position:
                raise FlyError("only for a plan paused on a photo line, in the air")
            number = m.first_line + line
            items = refly_line_mission(m.mission.items, line, (s.position[0], s.position[1]))
            path = m.path.with_name(f"{m.path.stem}_refly_line_{number}.mavlink")
            text = format_mission(items)
            path.write_text(text)
            mission = self.ctl.load_mission(text.encode())
            http_host = DRONE_HOST if self.host == SKYCONTROLLER_HOST else self.host
            self._note("action", f"Uploading a plan to fly line {number} again from its start ...")
        try:  # outside the lock: the drone keeps being read while the plan uploads
            ok = self.uploader(mission, path, http_host, self.upload_log, say=lambda text: None)
        except NotReachable as e:
            raise FlyError(f"{e}. The drone still hovers, paused: resume, return home or land") from e
        with self._lock:
            if not ok:
                raise FlyError("the plan to refly the line didn't upload; the drone still hovers, paused")
            if self.status is None or self.status.plan_state != "paused":
                raise FlyError("the plan isn't paused any more; not switching")
            self.session.switch_mission(mission, f"Reflying line {number} from its start: altitude first, "
                                                 "on the spot, then the line.")
            self.mission = LoadedMission(m.name, m.flight, path, mission, True, number, m.survey_lines)
            self._pause = None
            self._pull_session_events()
        return self.state()

    def _after_flight(self) -> None:
        """Landed: if the survey isn't finished, remember the line it stopped on.

        Taking off again can't simply resume the plan: from the ground the drone would fly
        straight to its next waypoint while climbing. A new plan from that line climbs over
        home first; the interrupted line is flown again whole (some photos twice).
        """
        m, s = self.mission, self.status
        self.resume = None
        if m is None or s is None or self.book is None:
            return
        lines = control.photo_lines(m.mission.items)
        item = s.mission_item if s.mission_item is not None else -1
        if not lines or item < lines[0][0]:
            return  # no line was started: the same mission flies it all again
        left = [i for i, (_, stop) in enumerate(lines) if item < stop]
        if not left:
            return  # every line flown
        first, total = m.first_line, m.survey_lines or len(lines)
        line = first + left[0]
        self.resume = {"line": line, "total": total}
        self._note("info", f"The survey stopped on line {line} of {total}. Continue from line {line} "
                           "to fly the rest: carry the drone back to the takeoff point (H) first.")

    def continue_survey(self) -> dict:
        """Make and load a plan from the line the last flight stopped on."""
        with self._lock:
            if self.resume is None or self.mission is None:
                raise FlyError("no unfinished survey to continue")
            if self._flying_now():
                raise FlyError("a flight is in progress")
            line = self.resume["line"]
            name = self.book.continue_from(self.mission.name, line)
            self._note("action", f"Saved {name}: the survey from line {line}.")
        return self.select_mission(name, 1)

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
            first, total = 1, None
            if self.book is not None:
                try:
                    first, total = self.book.first_line(name, int(flight))
                except Exception:  # a mission file without its request.json: number its own lines
                    pass
            self.mission = LoadedMission(name, int(flight), path, mission, on_drone, first, total)
            self.session, self._copied, self.resume = None, 0, None
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

    COMMANDS = ("pause", "resume", "refly", "stop", "rth", "land")

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
            if action == "refly":
                return self.refly()
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
                    "first_line": m.first_line,
                    "survey_lines": m.survey_lines or len(control.photo_lines(m.mission.items)),
                    "route": route_legs(m.mission.items),
                },
                "checks": self.checks(),
                "resume": self.resume,
                "paused": self._paused_json(),
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
