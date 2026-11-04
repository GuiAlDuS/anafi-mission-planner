"""One mission flight on a connected drone, for any front end (terminal `fly`, the GUI).

The session owns what must happen while a plan flies, whoever is watching:
the CSV flight log, the note when a return home ends hovering, and finishing a
paused line's photos (PhotoResume). Front ends call step() about twice a second
and the commands (pause, resume, return home...) when the pilot asks, and show
`events`: short messages, oldest first.

Thread-safe: the GUI reads the drone from a background thread while its buttons
send commands from request threads.
"""

from __future__ import annotations

import csv
import datetime as dt
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import control
from .upload import FlightPlanClient, append_log, upload_and_verify

LOG_FIELDS = ["t_s", "battery_pct", "flying_state", "plan_state", "mission_item", "lat", "lon",
              "altitude_m", "speed_ms", "home_m", "rth_state", "voltage_mv"]


def ensure_uploaded(mission: control.Mission, path: Path, http_host: str, upload_log: str | Path,
                    say=print) -> bool:
    """True if the plan is on the drone, uploading it first if needed."""
    client = FlightPlanClient(http_host)
    if any(p.get("uid") == mission.uid for p in client.list()):
        return True
    say(f"Plan {mission.uid} isn't on the drone; uploading {path} ...")
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
            return f"Return home finished: the drone is hovering over home{where}. Land it."
        return None


class PhotoResume:
    """Finish the photos of a line paused halfway (seen in the Sphinx simulator, fw 1.10.4).

    A pause switches the photos off, and a resume doesn't switch them back on: the
    mission's next photo start (the next line) does. So on a resume inside a photo
    line we start them ourselves. The drone then skips that line's own photo stop
    (its flight plan logic thinks they're off), and the next line's start would
    toggle our photos off and stall 20 s, so we also stop them ourselves: when the
    plan passes the line's stop item, or stops playing (pause, stop, return home).
    """

    def __init__(self, items: list):
        self.lines = control.photo_lines(items)
        self.stop_at: int | None = None  # our photos are on until this item

    def on_resume(self, item: int | None) -> bool:
        """After a resume: True if the photos must be started now."""
        if item is None:
            return False
        for start, stop in self.lines:
            if start <= item < stop:
                self.stop_at = stop
                return True
        return False

    def update(self, s: control.Status) -> bool:
        """Every status read: True if our photos must be stopped now."""
        if self.stop_at is None:
            return False
        if s.plan_state != "playing" or (s.mission_item is not None and s.mission_item >= self.stop_at):
            self.stop_at = None
            return True
        return False


@dataclass
class Event:
    t_s: float  # seconds since the session started
    kind: str  # "info", "action" (a command we sent), "warning", "done"
    text: str


class FlightSession:
    """Start and watch one mission on a connected drone (`dev` from control.connect).

    `ctl` is the module that talks to the drone; tests pass a fake.
    """

    def __init__(self, dev, mission: control.Mission, log_dir: str | Path = "flight_logs",
                 ctl=control, clock=time.monotonic):
        self.dev, self.mission, self.ctl, self.clock = dev, mission, ctl, clock
        self.log_dir = Path(log_dir)
        self.log_path: Path | None = None
        self.events: list[Event] = []
        self.status: control.Status | None = None
        self.started = False
        self.airborne = False
        self.finished = False  # landed after flying
        self.start_battery: int | None = None
        self._t0 = clock()
        self._rth = RthWatch()
        self._photos = PhotoResume(mission.items)
        self._log_file = None
        self._log = None
        self._lock = threading.RLock()

    # ---- watching

    @property
    def elapsed_s(self) -> float:
        return self.clock() - self._t0

    def _event(self, kind: str, text: str) -> None:
        self.events.append(Event(round(self.elapsed_s, 1), kind, text))

    def step(self) -> control.Status:
        """Read the drone once: log it, and react (photos, return home note, landing)."""
        with self._lock:
            s = self.ctl.read_status(self.dev)
            self.status = s
            if not self.started:
                return s
            if self.start_battery is None:
                self.start_battery = s.battery_pct
            self.airborne = self.airborne or s.flying_state not in (None, "landed")
            self._write_log(s)
            if note := self._rth.update(s):
                self._event("info", note)
            if self._photos.update(s):
                self.ctl.stop_photos(self.dev)
            if self.airborne and s.flying_state == "landed" and not self.finished:
                self.finished = True
                self._event("done", f"Landed after {self.elapsed_s / 60:.1f} min, "
                                    f"battery {self.start_battery} % -> {s.battery_pct} %.")
            return s

    def _write_log(self, s: control.Status) -> None:
        if self._log is None:
            return
        pos = s.position or (None, None, None)
        self._log.writerow([f"{self.elapsed_s:.1f}", s.battery_pct, s.flying_state, s.plan_state,
                            s.mission_item, pos[0], pos[1], s.altitude_m, s.speed_ms,
                            s.distance_from_home_m(), s.rth_state, s.voltage_mv])
        self._log_file.flush()

    # ---- commands

    def start(self) -> None:
        """Take off and start the plan (preflight checks and the pilot's confirmation come first)."""
        with self._lock:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = self.log_dir / f"{dt.datetime.now():%Y%m%d_%H%M%S}_{self.mission.uid[:8]}.csv"
            self._log_file = open(self.log_path, "w", newline="")
            self._log = csv.writer(self._log_file)
            self._log.writerow(LOG_FIELDS)
            self._t0 = self.clock()
            self.ctl.start_plan(self.dev, self.mission.uid)
            self.started = True
            self._event("action", f"Started. Logging to {self.log_path}")

    def pause(self) -> None:
        with self._lock:
            self.ctl.pause_plan(self.dev)
            self._event("action", "Paused: the drone hovers.")

    def resume(self) -> None:
        with self._lock:
            item = self.status.mission_item if self.status else None
            self.ctl.start_plan(self.dev, self.mission.uid)
            self._event("action", "Resumed.")
            if self._photos.on_resume(item):
                self.ctl.start_photos(self.dev)
                self._event("info", "Photos back on for the rest of this line.")

    def toggle_pause(self) -> str | None:
        """Pause a playing plan or resume a paused one; returns what it did, or None."""
        with self._lock:
            state = self.status.plan_state if self.status else None
            if state == "playing":
                self.pause()
                return "paused"
            if state == "paused":
                self.resume()
                return "resumed"
            return None

    def stop(self) -> None:
        with self._lock:
            self.ctl.stop_plan(self.dev)
            self._event("action", "Flight plan stopped: the drone hovers. Fly it home or return home.")

    def return_home(self) -> None:
        with self._lock:
            self.ctl.return_home(self.dev)
            self._event("action", "Returning home.")

    def land(self) -> None:
        """Land where the drone is (a playing plan is paused first, so it doesn't fly on)."""
        with self._lock:
            self.ctl.land(self.dev)
            self._event("action", "Landing.")

    def close(self) -> None:
        """Stop watching. The plan keeps flying on the drone; photos we started are stopped,
        since nobody would stop them (the next line starts its own)."""
        with self._lock:
            if self._photos.stop_at is not None:
                self._photos.stop_at = None
                self.ctl.stop_photos(self.dev)
                self._event("info", "Stopped the photos started after the resume; the next line starts its own.")
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = self._log = None
