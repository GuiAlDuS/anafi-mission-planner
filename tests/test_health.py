import json
from types import SimpleNamespace

import pytest

from drone import health
from drone.health import FAIL, NA, OK, WARN, HealthReading, WatchSummary, assess, update_summary


def _healthy(**kw):
    d = dict(firmware="1.8.2", hardware="HW_03", flying_state="landed", motor_error="noError",
             motor_last_error="noError", motor_flights=60, motor_last_flight_s=95, motor_total_flight_s=36000,
             sensors={"IMU": True, "barometer": True, "GPS": True, "magnetometer": True},
             magneto_calibration_required=False, heading_lock="ok", vibration="ok",
             gimbal_calibration="ok", gimbal_errors=[], alarms=None, takeoff_blockers=None,
             piloting_alert="none", gps_fixed=True, satellites=14)
    d.update(kw)
    return HealthReading(**d)


def _by_name(checks):
    return {c.name: c for c in checks}


def test_healthy_drone_on_the_ground():
    checks = _by_name(assess(_healthy()))
    assert checks["motors"].status == OK and checks["sensors"].status == OK
    assert checks["compass"].status == OK and checks["gimbal"].status == OK
    assert checks["vibration"].status == NA and "--watch" in checks["vibration"].detail  # landed
    assert checks["alarms"].status == NA and checks["takeoff checklist"].status == NA
    assert "60 flights, 10.0 h" in checks["motor counter"].detail and "1:35" in checks["motor counter"].detail
    assert "no problems" in health.verdict(list(checks.values()))


@pytest.mark.parametrize("kw, name, status, word", [
    ({"motor_error": "errorCommLost", "motor_error_motors": ["back left"]}, "motors", FAIL, "back left"),
    ({"motor_last_error": "errorPropellerSecurity"}, "last motor error", WARN, "propeller was blocked"),
    ({"sensors": {"IMU": True, "magnetometer": False}}, "sensors", FAIL, "magnetometer"),
    ({"magneto_calibration_required": True}, "compass", FAIL, "calibrate the magnetometer"),
    ({"heading_lock": "warning"}, "compass", WARN, "magnetic disturbance"),
    ({"gimbal_errors": ["overload_error"]}, "gimbal", FAIL, "blocks the gimbal"),
    ({"gimbal_calibration": "required"}, "gimbal", FAIL, "calibrate the gimbal"),
    ({"flying_state": "hovering", "vibration": "warning"}, "vibration", WARN, "propellers"),
    ({"vibration": "critical"}, "vibration", FAIL, "arms"),
    ({"alarms": ["motor_cutout"]}, "alarms", FAIL, "motor_cutout"),
    ({"takeoff_blockers": ["gyro"]}, "takeoff checklist", FAIL, "gyro"),
    ({"takeoff_blockers": ["gps"]}, "takeoff checklist", WARN, "gps"),
    ({"piloting_alert": "cut_out"}, "alert", FAIL, "cut out"),
    ({"gps_fixed": False}, "GPS", WARN, "no fix"),
    ({"battery_problems": ["worn out"]}, "battery", FAIL, "worn out"),
])
def test_problems(kw, name, status, word):
    c = _by_name(assess(_healthy(**kw)))[name]
    assert c.status == status and word in c.detail


def test_nothing_reported():
    checks = assess(HealthReading())
    assert {c.name: c.status for c in checks if c.status != NA} == {"battery": OK}
    text = health.format_report(HealthReading(), checks)
    assert "firmware ?" in text and "n/a  motors" in text


def test_verdict_fail_beats_warn():
    checks = assess(_healthy(motor_error="errorMotorStalled", motor_error_motors=["front left"], gps_fixed=False))
    assert health.verdict(checks).startswith("DON'T FLY")
    assert health.verdict(assess(_healthy(gps_fixed=False))).startswith("check the warn")


def test_watch_summary_tracks_worst_while_airborne():
    s = WatchSummary()
    update_summary(s, {"flying_state": "landed", "vibration": "critical"}, 0.5)  # ignored on the ground
    for state, vib in [("hovering", "ok"), ("hovering", "warning"), ("flying", "ok")]:
        update_summary(s, {"flying_state": state, "vibration": vib, "altitude": 2.5, "heading_lock": "ok",
                           "motor_error": "noError", "alert": "none"}, 20.0)
    assert s.worst_vibration == "warning" and s.seconds_airborne == 60 and s.max_altitude_m == 2.5
    assert s.motor_errors == [] and s.alerts == []
    assert "DON'T trust it" in health.format_summary(s)
    update_summary(s, {"flying_state": "flying", "motor_error": "errorMotorStalled", "alert": "too_much_angle"}, 1)
    assert s.motor_errors == ["errorMotorStalled"] and s.alerts == ["too_much_angle"]


def test_short_watch_says_fly_longer():
    s = WatchSummary()
    update_summary(s, {"flying_state": "hovering", "vibration": "ok"}, 10)
    text = health.format_summary(s)
    assert "at least 1 minute" in text and "fly longer" in text


def test_helpers():
    assert health._motors(0b1001) == ["front left", "back left"]
    assert health._flags(0b0010) == ["overload_error"]
    assert health._flags([SimpleNamespace(name="comm_error")]) == ["comm_error"]
    assert health._items({"IMU": {"sensorName": "IMU"}, "GPS": {"sensorName": "GPS"}}) == [
        {"sensorName": "IMU"}, {"sensorName": "GPS"}]
    assert health._items({"sensorName": "IMU", "sensorState": 1}) == [{"sensorName": "IMU", "sensorState": 1}]


def test_log_appends(tmp_path):
    p = tmp_path / "h.json"
    r = _healthy()
    health.append_log(p, r, assess(r))
    health.append_log(p, r, assess(r), WatchSummary(seconds_airborne=90, worst_vibration="ok"))
    log = json.loads(p.read_text())
    assert len(log["checks"]) == 2 and "watch" not in log["checks"][0]
    assert log["checks"][1]["watch"]["worst_vibration"] == "ok"
    assert log["checks"][0]["reading"]["firmware"] == "1.8.2"


def test_read_health_parses_olympe_states(monkeypatch):
    olympe = pytest.importorskip("olympe")  # noqa: F841 (only with the `fly` extra)
    from olympe.enums import ardrone3 as e3, common as ec, gimbal as eg
    from olympe.messages import gimbal
    from olympe.messages.ardrone3 import GPSSettingsState, GPSState, PilotingState, SettingsState
    from olympe.messages.common import CalibrationState, CommonState
    from olympe.messages.common import SettingsState as CommonSettings

    sensor = ec.CommonState.SensorsStatesListChanged_SensorName
    states = {
        CommonState.BatteryStateChanged: {"percent": 80},
        CommonSettings.ProductVersionChanged: {"software": "1.8.2", "hardware": "HW_03"},
        PilotingState.FlyingStateChanged: {"state": e3.PilotingState.FlyingStateChanged_State.landed},
        SettingsState.MotorErrorStateChanged: {
            "motorIds": 0b0100, "motorError": e3.SettingsState.MotorErrorStateChanged_MotorError.errorCommLost},
        SettingsState.MotorErrorLastErrorChanged: {
            "motorError": e3.SettingsState.MotorErrorLastErrorChanged_MotorError.errorPropellerSecurity},
        SettingsState.MotorFlightsStatusChanged: {"nbFlights": 61, "lastFlightDuration": 125,
                                                  "totalFlightDuration": 40000},
        CommonState.SensorsStatesListChanged: {
            sensor.IMU: {"sensorName": sensor.IMU, "sensorState": 1},
            sensor.magnetometer: {"sensorName": sensor.magnetometer, "sensorState": 0}},
        CalibrationState.MagnetoCalibrationRequiredState: {"required": 1},
        PilotingState.HeadingLockedStateChanged: {"state": e3.PilotingState.HeadingLockedStateChanged_State.ok},
        gimbal.calibration_state: {"gimbal_id": 0, "state": eg.calibration_state.ok},
        gimbal.alert: {"gimbal_id": 0, "error": eg.error._bitfield_type_(0b0010)},
        PilotingState.AlertStateChanged: {"state": e3.PilotingState.AlertStateChanged_State.none},
        GPSSettingsState.GPSFixStateChanged: {"fixed": 0},
        GPSState.NumberOfSatelliteChanged: {"numberOfSatellite": 3},
    }

    class Dev:
        def get_state(self, msg):
            if msg not in states:
                raise RuntimeError("not received")
            return states[msg]

    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    r = health.read_health(Dev(), with_battery=False)
    assert (r.firmware, r.flying_state, r.motor_error, r.motor_error_motors) == (
        "1.8.2", "landed", "errorCommLost", ["back right"])
    assert r.motor_last_error == "errorPropellerSecurity" and r.motor_flights == 61
    assert r.sensors == {"IMU": True, "magnetometer": False}
    assert r.magneto_calibration_required is True and r.heading_lock == "ok"
    assert r.gimbal_calibration == "ok" and r.gimbal_errors == ["overload_error"]
    assert r.alarms is None and r.takeoff_blockers is None and r.vibration is None
    assert r.gps_fixed is False and r.satellites == 3 and r.piloting_alert == "none"


def _jpeg(fill):
    import io
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (640, 480), (90, 60, 40))
    fill(ImageDraw.Draw(img))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _room(d):  # walls above, floor below: the view at 0 deg
    d.rectangle([0, 0, 640, 200], fill=(230, 225, 215))
    d.rectangle([400, 40, 520, 200], fill=(150, 110, 70))


def _floor(d):  # floor only: the view at -90 deg
    for x in range(0, 640, 80):
        d.line([x, 0, x, 480], fill=(60, 40, 25), width=6)


def test_photo_difference_tells_still_from_moved_camera():
    pytest.importorskip("PIL")
    room = _jpeg(_room)
    assert health.photo_difference(room, _jpeg(_room)) < 3  # same view (2026-11-01: dead gimbal)
    assert health.photo_difference(room, _jpeg(_floor)) > health.MIN_PHOTO_DIFFERENCE


def test_judge_gimbal_photos():
    dead = health.judge_gimbal_photos(1.2, {-90.0: -90.0, 0.0: -1.0})  # reports success, didn't move
    assert dead.status == FAIL and "didn't move" in dead.detail and "-90 -> -90.0" in dead.detail
    assert health.judge_gimbal_photos(40.0, {-90.0: -90.0, 0.0: 0.0}).status == OK


def test_gimbal_photo_test_with_fake_drone(monkeypatch):
    pytest.importorskip("olympe")
    pytest.importorskip("PIL")
    from olympe.messages import camera, gimbal

    class Exp:
        def wait(self, _timeout=None):
            return self

        def success(self):
            return True

    class Dev:
        pitch, n = 0.0, 0

        def __call__(self, cmd):
            if "set_target" in str(cmd):
                Dev.pitch = cmd.args["pitch"] if hasattr(cmd, "args") else Dev.pitch
            return Exp()

        def get_state(self, msg):
            if msg is gimbal.attitude:
                return {0: {"pitch_absolute": Dev.pitch}}
            if msg is camera.photo_progress:  # read once per photo
                Dev.n += 1
                return {"media_id": f"m{Dev.n}"}
            raise RuntimeError

    files = {"/f/m1.jpg": _jpeg(_floor), "/f/m2.jpg": _jpeg(_room)}

    def fetch(path, timeout):
        if path.startswith("/api/v1/media/medias/"):
            mid = path.rsplit("/", 1)[1]
            return json.dumps({"resources": [{"format": "JPG", "url": f"/f/{mid}.jpg"}]}).encode()
        return files[path]

    monkeypatch.setattr(health.time, "sleep", lambda s: None)
    check = health.gimbal_photo_test(Dev(), fetch=fetch, settle_s=0.01)
    assert check.status == OK, check.detail
    files["/f/m1.jpg"] = files["/f/m2.jpg"]  # camera stuck: both photos the same
    Dev.n = 0
    assert health.gimbal_photo_test(Dev(), fetch=fetch, settle_s=0.01).status == FAIL
