"""Drone health check: what the drone reports about its own hardware, judged and logged.

Meant for after a crash or a repair, and before trusting the drone with a
mission. Read only: it never starts the motors or sends a command.

Two parts:

- `check`: on the ground, motors off. Reads every state the Anafi (product
  0914, firmware 1.8.2) can report about its hardware at connection (from
  Olympe's arsdk XML): motor errors (current and last), motor flight counter,
  sensor states (IMU, barometer, GPS, magnetometer, ...), compass calibration
  and heading lock, gimbal calibration and errors, alarms and the takeoff
  checklist (newer firmware; may not be sent), the piloting alert, GPS and
  the battery (see battery.py).
- `gimbal_photo_test`: moves the camera to -90 deg and 0 deg, takes a photo at
  each and compares them. The gimbal's own report can't be trusted for this:
  after the 2026-11-01 crash it said "calibrated, no errors" and reported
  reaching every angle while its tilt motor was dead. Two near-identical
  photos mean the camera didn't move.
- `watch`: while you hover and fly the drone by hand. Follows the drone's own
  vibration level, heading lock, motor errors and alerts, and reports the
  worst seen. The vibration level is only measured in the air: it is the best
  check of propellers, motors and arms after a repair.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .upload import DroneError

LOG_FILE = "health_checks.json"
MOTORS = ["front left", "front right", "back right", "back left"]  # bit 0..3 of motorIds
LEVELS = ["ok", "warning", "critical"]  # vibration and heading lock, in order of severity
OK, WARN, FAIL, NA = "ok", "warn", "FAIL", "n/a"

MOTOR_ERROR_HELP = {
    "errorPropellerSecurity": "propeller cutout: a propeller was blocked (e.g. hit something)",
    "errorMotorStalled": "motor stalled: something blocked the motor",
    "errorCommLost": "lost communication with a motor: check the motor cable through the arm",
    "errorTemperature": "motor too hot",
    "errorBatteryVoltage": "battery voltage too low for the motors",
    "errorLipoCells": "battery cells problem",
    "errorMOSFET": "motor driver (MOSFET) failure",
    "errorEEPRom": "motor controller memory failure",
}
GIMBAL_ERROR_HELP = {
    "calibration_error": "calibration error: check nothing blocks the gimbal",
    "overload_error": "overload: check nothing blocks the gimbal",
    "comm_error": "communication error",
    "critical_error": "critical error: restart the drone",
}
# Takeoff checklist items that are expected indoors or right after power-on.
SOFT_TAKEOFF_CHECKS = {"gps", "update_ongoing"}


@dataclass
class HealthReading:
    time: str = ""
    firmware: str | None = None
    hardware: str | None = None
    flying_state: str | None = None
    motor_error: str | None = None  # current; noError when fine
    motor_error_motors: list[str] = field(default_factory=list)
    motor_last_error: str | None = None  # reminder of the last error, kept across boots
    motor_flights: int | None = None
    motor_last_flight_s: int | None = None
    motor_total_flight_s: int | None = None
    sensors: dict[str, bool] = field(default_factory=dict)  # name -> working
    magneto_calibration_required: bool | None = None
    heading_lock: str | None = None  # ok, warning, critical
    vibration: str | None = None  # ok, warning, critical (measured in flight)
    gimbal_calibration: str | None = None  # required, in_progress, ok
    gimbal_errors: list[str] = field(default_factory=list)
    alarms: list[str] | None = None  # alarms that are on; None: not sent by this firmware
    takeoff_blockers: list[str] | None = None  # takeoff checklist items that are on
    piloting_alert: str | None = None
    gps_fixed: bool | None = None
    satellites: int | None = None
    battery_problems: list[str] = field(default_factory=list)
    battery_notes: list[str] = field(default_factory=list)


@dataclass
class Check:
    name: str
    status: str  # ok, warn, FAIL, n/a
    detail: str


def _name(v) -> str:
    return getattr(v, "name", str(v))


def _items(st) -> list[dict]:
    """List messages come back as {key: {args}} (or as one item's args)."""
    if not st:
        return []
    return list(st.values()) if all(isinstance(v, dict) for v in st.values()) else [st]


def _motors(bits: int) -> list[str]:
    return [m for i, m in enumerate(MOTORS) if bits >> i & 1]


def _flags(v) -> list[str]:
    """Names of the set bits of an Olympe bitfield (iterating it yields the set flags) or an int."""
    if isinstance(v, int):
        return [n for i, n in enumerate(GIMBAL_ERROR_HELP) if v >> i & 1]
    return [_name(f) for f in v] if v else []


def _getter(dev):
    def get(msg):
        try:
            return dev.get_state(msg)
        except Exception:  # not received (not sent by this firmware, or not yet)
            return None
    return get


def _wait_for_drone(get, msg, wait_s: float) -> None:
    deadline = time.monotonic() + wait_s
    while get(msg) is None:
        if time.monotonic() >= deadline:
            raise DroneError(f"no data from the drone after {wait_s:.0f} s: is it on and linked to "
                             "the Skycontroller (controller LED solid)?")
        time.sleep(0.5)


def read_health(dev, with_battery: bool = True, wait_s: float = 45.0) -> HealthReading:
    """Read every hardware state the drone has reported. Read only."""
    from olympe.messages import alarms, gimbal
    from olympe.messages.ardrone3 import GPSSettingsState, GPSState, PilotingState, SettingsState
    from olympe.messages.common import CalibrationState, CommonState
    from olympe.messages.common import SettingsState as CommonSettings

    get = _getter(dev)
    _wait_for_drone(get, CommonState.BatteryStateChanged, wait_s)
    time.sleep(1.0)  # the rest of the connection burst
    r = HealthReading(time=dt.datetime.now().isoformat(timespec="seconds"))
    if st := get(CommonSettings.ProductVersionChanged):
        r.firmware, r.hardware = st["software"], st["hardware"]
    if st := get(PilotingState.FlyingStateChanged):
        r.flying_state = _name(st["state"])
    if st := get(SettingsState.MotorErrorStateChanged):
        r.motor_error = _name(st["motorError"])
        r.motor_error_motors = _motors(int(st["motorIds"]))
    if st := get(SettingsState.MotorErrorLastErrorChanged):
        r.motor_last_error = _name(st["motorError"])
    if st := get(SettingsState.MotorFlightsStatusChanged):
        r.motor_flights = st["nbFlights"]
        r.motor_last_flight_s = st["lastFlightDuration"]
        r.motor_total_flight_s = st["totalFlightDuration"]
    for it in _items(get(CommonState.SensorsStatesListChanged)):
        if "sensorName" in it:
            r.sensors[_name(it["sensorName"])] = bool(it["sensorState"])
    if st := get(CalibrationState.MagnetoCalibrationRequiredState):
        r.magneto_calibration_required = bool(st["required"])
    if st := get(PilotingState.HeadingLockedStateChanged):
        r.heading_lock = _name(st["state"])
    if st := get(PilotingState.VibrationLevelChanged):
        r.vibration = _name(st["state"])
    for it in _items(get(gimbal.calibration_state)):
        if "state" in it:
            r.gimbal_calibration = _name(it["state"])
    for it in _items(get(gimbal.alert)):
        if "error" in it:
            r.gimbal_errors += _flags(it["error"])
    if (st := get(alarms.alarms)) is not None:
        r.alarms = [_name(it["type"]) for it in _items(st) if "type" in it and _name(it["state"]) == "on"]
    if (st := get(alarms.takeoff_checklist)) is not None:
        r.takeoff_blockers = [_name(it["check"]) for it in _items(st)
                              if "check" in it and _name(it["state"]) == "on"]
    if st := get(PilotingState.AlertStateChanged):
        r.piloting_alert = _name(st["state"])
    if st := get(GPSSettingsState.GPSFixStateChanged):
        r.gps_fixed = bool(st["fixed"])
    if st := get(GPSState.NumberOfSatelliteChanged):
        r.satellites = st["numberOfSatellite"]
    if with_battery:
        from . import battery
        b = battery.read_battery(dev, seconds=3.0, wait_s=wait_s)
        r.battery_problems, r.battery_notes = battery.assess(b)
    return r


def assess(r: HealthReading) -> list[Check]:
    """One line per part of the drone. FAIL: don't fly until fixed."""
    checks = []

    def add(name, status, detail):
        checks.append(Check(name, status, detail))

    # Motors
    if r.motor_error is None:
        add("motors", NA, "the drone sent no motor error state")
    elif r.motor_error != "noError":
        where = ", ".join(r.motor_error_motors) or "unknown motor"
        add("motors", FAIL, f"{where}: {MOTOR_ERROR_HELP.get(r.motor_error, r.motor_error)}")
    else:
        add("motors", OK, "no motor error now")
    if r.motor_last_error and r.motor_last_error != "noError":
        add("last motor error", WARN, f"{MOTOR_ERROR_HELP.get(r.motor_last_error, r.motor_last_error)}. "
            "Kept until a new error replaces it, so it may be from an earlier crash")
    elif r.motor_last_error:
        add("last motor error", OK, "none")
    if r.motor_flights is not None:
        add("motor counter", OK, f"{r.motor_flights} flights, {(r.motor_total_flight_s or 0) / 3600:.1f} h "
            f"in total, last flight {_mmss(r.motor_last_flight_s)}")

    # Sensors
    if not r.sensors:
        add("sensors", NA, "the drone sent no sensor states")
    else:
        bad = [n for n, okay in r.sensors.items() if not okay]
        add("sensors", FAIL if bad else OK,
            f"not working: {', '.join(bad)}" if bad else f"all working ({', '.join(r.sensors)})")

    # Compass
    if r.magneto_calibration_required:
        add("compass", FAIL, "calibration required: calibrate the magnetometer in FreeFlight")
    elif r.heading_lock in ("warning", "critical"):
        add("compass", WARN if r.heading_lock == "warning" else FAIL,
            f"heading lock {r.heading_lock}: magnetic disturbance. Move away from metal, cars and "
            "rebar; if it stays, calibrate the magnetometer in FreeFlight")
    elif r.magneto_calibration_required is None and r.heading_lock is None:
        add("compass", NA, "the drone sent no compass state")
    else:
        add("compass", OK, "calibrated" + (f", heading lock {r.heading_lock}" if r.heading_lock else ""))

    # Gimbal
    if r.gimbal_errors:
        add("gimbal", FAIL, "; ".join(GIMBAL_ERROR_HELP.get(e, e) for e in r.gimbal_errors))
    elif r.gimbal_calibration == "required":
        add("gimbal", FAIL, "calibration required: calibrate the gimbal in FreeFlight")
    elif r.gimbal_calibration == "in_progress":
        add("gimbal", WARN, "calibrating; check again when it's done")
    elif r.gimbal_calibration is None:
        add("gimbal", NA, "the drone sent no gimbal state")
    else:
        add("gimbal", OK, "calibrated, no errors (its own report: run `health --gimbal` to see it move)")

    # Vibration: measured in flight only.
    airborne = r.flying_state in ("hovering", "flying", "takingoff", "landing")
    if r.vibration in ("warning", "critical"):
        add("vibration", WARN if r.vibration == "warning" else FAIL,
            f"{r.vibration}: check propellers (chips, bends, seated), motors and arms")
    elif r.vibration is None:
        add("vibration", NA, "not reported")
    else:
        add("vibration", OK if airborne else NA,
            "ok" if airborne else "only measured in flight: run `health --watch` while hovering")

    # Alarms and takeoff checklist (newer firmware)
    if r.alarms is None:
        add("alarms", NA, "not sent by this firmware")
    else:
        add("alarms", FAIL if r.alarms else OK, ", ".join(r.alarms) or "none on")
    if r.takeoff_blockers is None:
        add("takeoff checklist", NA, "not sent by this firmware")
    else:
        hard = [c for c in r.takeoff_blockers if c not in SOFT_TAKEOFF_CHECKS]
        add("takeoff checklist", FAIL if hard else WARN if r.takeoff_blockers else OK,
            f"blocking takeoff: {', '.join(r.takeoff_blockers)}" if r.takeoff_blockers else "ready")

    # Piloting alert
    alert = r.piloting_alert
    if alert in (None, "none"):
        add("alert", OK if alert else NA, "none" if alert else "not reported")
    elif alert in ("cut_out", "too_much_angle"):
        add("alert", FAIL, f"{alert}: motors cut out / drone tilted too far")
    else:
        add("alert", WARN, alert)

    # GPS
    sats = f", {r.satellites} satellites" if r.satellites is not None else ""
    if r.gps_fixed:
        add("GPS", OK, "fix" + sats)
    elif r.gps_fixed is False:
        add("GPS", WARN, "no fix" + sats + ": normal indoors; outdoors, wait a few minutes. No fix "
            "after 5 min in open sky can mean a damaged GPS antenna")
    else:
        add("GPS", NA, "not reported")

    # Battery
    if r.battery_problems:
        add("battery", FAIL, "; ".join(r.battery_problems))
    elif r.battery_notes:
        add("battery", WARN, "; ".join(r.battery_notes))
    else:
        add("battery", OK, "no problems (details: `anafi-drone battery`)")
    return checks


def _mmss(s: int | None) -> str:
    return "?" if s is None else f"{s // 60}:{s % 60:02d}"


def verdict(checks: list[Check]) -> str:
    if any(c.status == FAIL for c in checks):
        return "DON'T FLY until the FAIL lines are fixed"
    if any(c.status == WARN for c in checks):
        return "check the warn lines before flying"
    return "no problems reported (still inspect props and arms by eye)"


def format_report(r: HealthReading, checks: list[Check]) -> str:
    head = [f"drone        firmware {r.firmware or '?'}, hardware {r.hardware or '?'}, "
            f"{r.flying_state or '?'}"]
    rows = [f"{c.status:<4} {c.name:<18} {c.detail}" for c in checks]
    return "\n".join(head + rows + [f"verdict      {verdict(checks)}"])


# Gimbal photo test: does the camera really move?

GIMBAL_TEST_PITCHES = (-90.0, 0.0)
MIN_PHOTO_DIFFERENCE = 12.0  # mean grey-level difference (0-255); a still camera gives ~1-3
MEDIA_URL = "http://192.168.42.1"  # drone web server, also reachable through the Skycontroller route


def photo_difference(a: bytes, b: bytes, size: tuple[int, int] = (64, 48)) -> float:
    """Mean absolute grey-level difference between two photos, shrunk so noise averages out."""
    import io

    from PIL import Image, ImageChops, ImageStat

    def small(data: bytes):
        return Image.open(io.BytesIO(data)).convert("L").resize(size, Image.BILINEAR)

    return ImageStat.Stat(ImageChops.difference(small(a), small(b))).mean[0]


def judge_gimbal_photos(diff: float, reported: dict[float, float | None]) -> Check:
    said = ", ".join(f"{t:+.0f} -> {'?' if g is None else f'{g:+.1f}'}" for t, g in reported.items())
    if diff < MIN_PHOTO_DIFFERENCE:
        return Check("gimbal movement", FAIL,
                     f"the photos at -90 and 0 deg are the same (difference {diff:.1f}): the camera "
                     f"didn't move, whatever the gimbal reports ({said}). Check the gimbal cable "
                     "and the tilt motor")
    return Check("gimbal movement", OK, f"the camera moved: photos differ by {diff:.1f} (gimbal: {said})")


def _media_get(path: str, timeout: float) -> bytes:
    import urllib.request
    with urllib.request.urlopen(MEDIA_URL + path, timeout=timeout) as r:
        return r.read()


def gimbal_photo_test(dev, fetch=_media_get, settle_s: float = 25.0) -> Check:
    """Photo at each test pitch, then compare. Moves only the gimbal and the camera; saves 2 photos."""
    from olympe.messages import camera, gimbal

    def one(msg):
        try:
            st = dev.get_state(msg)
        except Exception:
            return {}
        return next(iter(st.values())) if st and all(isinstance(v, dict) for v in st.values()) else st

    # Single JPEG photos: FreeFlight may have left GPS lapse set, which would start a lapse.
    dev(camera.set_camera_mode(cam_id=0, value="photo")).wait(_timeout=5)
    dev(camera.set_photo_mode(cam_id=0, mode="single", format="rectilinear", file_format="jpeg",
                              burst="burst_14_over_1s", bracketing="preset_1ev",
                              capture_interval=0.0)).wait(_timeout=5)
    photos, reported = [], {}
    for target in GIMBAL_TEST_PITCHES:
        dev(gimbal.set_target(gimbal_id=0, control_mode="position",
                              yaw_frame_of_reference="none", yaw=0.0,
                              pitch_frame_of_reference="absolute", pitch=target,
                              roll_frame_of_reference="none", roll=0.0)).wait(_timeout=5)
        t0, got = time.monotonic(), None
        while time.monotonic() - t0 < settle_s:  # the gimbal turns at about 10 deg/s
            time.sleep(0.5)
            got = one(gimbal.attitude).get("pitch_absolute")
            if got is not None and abs(got - target) < 1.5:
                break
        reported[target] = got
        time.sleep(1.5)
        exp = dev(camera.take_photo(cam_id=0)
                  >> camera.photo_progress(cam_id=0, result="photo_saved", _policy="wait", _timeout=20)
                  ).wait(_timeout=25)
        media_id = one(camera.photo_progress).get("media_id") if exp.success() else None
        if not media_id:
            return Check("gimbal movement", NA, f"couldn't take a photo at {target:+.0f} deg "
                         "(SD card in? storage full?)")
        info = json.loads(fetch(f"/api/v1/media/medias/{media_id}", 10))
        res = next((r for r in info.get("resources", []) if r.get("format") == "JPG"),
                   (info.get("resources") or [{}])[0])
        if not res.get("url"):
            return Check("gimbal movement", NA, f"photo {media_id} has no downloadable file")
        photos.append(fetch(res["url"], 60))
    return judge_gimbal_photos(photo_difference(*photos), reported)


# Watch mode: while the drone flies by hand.

@dataclass
class WatchSummary:
    seconds_airborne: float = 0.0
    max_altitude_m: float = 0.0
    worst_vibration: str | None = None
    worst_heading_lock: str | None = None
    motor_errors: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)


def _worse(a: str | None, b: str | None) -> str | None:
    if b not in LEVELS:
        return a
    return b if a not in LEVELS or LEVELS.index(b) > LEVELS.index(a) else a


def update_summary(s: WatchSummary, sample: dict, dt_s: float) -> None:
    """Fold one sample ({flying_state, altitude, vibration, heading_lock, motor_error, alert}) in."""
    if sample.get("flying_state") in ("hovering", "flying"):
        s.seconds_airborne += dt_s
        s.worst_vibration = _worse(s.worst_vibration, sample.get("vibration"))
    s.max_altitude_m = max(s.max_altitude_m, sample.get("altitude") or 0.0)
    s.worst_heading_lock = _worse(s.worst_heading_lock, sample.get("heading_lock"))
    me = sample.get("motor_error")
    if me and me != "noError" and me not in s.motor_errors:
        s.motor_errors.append(me)
    al = sample.get("alert")
    if al and al != "none" and al not in s.alerts:
        s.alerts.append(al)


def format_summary(s: WatchSummary) -> str:
    lines = [f"airborne     {s.seconds_airborne:.0f} s, up to {s.max_altitude_m:.1f} m"]
    if s.seconds_airborne < 30:
        lines.append("note: hover at least 1 minute for a meaningful vibration reading")
    lines.append(f"vibration    worst {s.worst_vibration or 'not reported'}")
    lines.append(f"heading lock worst {s.worst_heading_lock or 'not reported'}")
    lines.append(f"motor errors {', '.join(MOTOR_ERROR_HELP.get(e, e) for e in s.motor_errors) or 'none'}")
    lines.append(f"alerts       {', '.join(s.alerts) or 'none'}")
    bad = s.worst_vibration in ("warning", "critical") or s.motor_errors or s.worst_heading_lock == "critical"
    lines.append("verdict      " + ("DON'T trust it for missions: find the cause (props, motor, arm)"
                                    if bad else "no problems seen" if s.seconds_airborne >= 30
                                    else "fly longer to judge"))
    return "\n".join(lines)


def watch(dev, minutes: float = 10.0, out=print) -> WatchSummary:
    """Follow the drone while it's flown by hand; print changes. Read only. Ctrl+C ends."""
    from olympe.messages.ardrone3 import PilotingState, SettingsState

    get = _getter(dev)
    _wait_for_drone(get, PilotingState.FlyingStateChanged, 45.0)

    def sample() -> dict:
        d = {}
        for key, msg, arg in [("flying_state", PilotingState.FlyingStateChanged, "state"),
                              ("vibration", PilotingState.VibrationLevelChanged, "state"),
                              ("heading_lock", PilotingState.HeadingLockedStateChanged, "state"),
                              ("motor_error", SettingsState.MotorErrorStateChanged, "motorError"),
                              ("alert", PilotingState.AlertStateChanged, "state")]:
            if st := get(msg):
                d[key] = _name(st[arg])
        if st := get(PilotingState.AltitudeChanged):
            d["altitude"] = float(st["altitude"])
        return d

    s = WatchSummary()
    out("Watching (read only). Take off with the Skycontroller and hover 1-2 minutes at 2 m, then "
        "10 m, then fly gently. Ctrl+C or land to finish.")
    last_line, last_t, end = None, time.monotonic(), time.monotonic() + minutes * 60
    was_airborne = False
    try:
        while time.monotonic() < end:
            time.sleep(0.5)
            now = time.monotonic()
            d = sample()
            update_summary(s, d, now - last_t)
            last_t = now
            line = (f"{d.get('flying_state', '?'):<10} alt {d.get('altitude', 0):5.1f} m  "
                    f"vibration {d.get('vibration', '?'):<8} heading {d.get('heading_lock', '?'):<8} "
                    f"motors {d.get('motor_error', '?')}  alert {d.get('alert', '?')}")
            if line != last_line:
                out(line)
                last_line = line
            airborne = d.get("flying_state") in ("hovering", "flying", "takingoff")
            if was_airborne and d.get("flying_state") == "landed":
                break
            was_airborne = was_airborne or airborne
    except KeyboardInterrupt:
        pass
    return s


# Log: {"checks": [{"reading": ..., "checks": [...]}, ...]}

def append_log(path: str | Path, r: HealthReading, checks: list[Check],
               summary: WatchSummary | None = None) -> None:
    p = Path(path)
    log = json.loads(p.read_text()) if p.exists() else {"checks": []}
    entry = {"reading": asdict(r), "checks": [asdict(c) for c in checks]}
    if summary is not None:
        entry["watch"] = asdict(summary)
    log["checks"].append(entry)
    p.write_text(json.dumps(log, indent=2) + "\n")
