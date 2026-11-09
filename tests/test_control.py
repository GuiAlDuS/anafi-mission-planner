import hashlib

from drone.control import Status, format_status, format_telemetry, load_mission, preflight_problems
from planner.geo import LatLon
from planner.mavlink import format_mission, tiny_mission


def test_format_status_before_gps_fix():
    s = Status(battery_pct=74, flying_state="landed", gps_fixed=False, plan_available=False,
               plan_components={"GPS": False, "Calibration": True, "TakeOff": True}, plan_state="stopped",
               max_altitude_m=150.0, max_distance_m=100.0, geofence=False, rth_delay_s=1, home_type="TAKEOFF",
               alert="none")
    assert format_status(s).splitlines() == [
        "battery      74 %",
        "state        landed",
        "GPS          no fix, position unknown",
        "home         unknown, type TAKEOFF",
        "link loss    return home after 1 s; during a flight plan: not reported",
        "flight plan  NOT ready (GPS MISSING, Calibration ok, TakeOff ok); stopped",
        "limits       max altitude 150 m, max distance 100 m (geofence off)",
    ]


def test_format_status_ready_and_unknowns():
    s = Status(gps_fixed=True, position=(9.98, -78.7, 1.5), plan_available=True, plan_state="playing",
               plan_file="abc", alert="power_level")
    text = format_status(s)
    assert "battery      ?" in text
    assert "alert: power_level" in text
    assert "position 9.980000, -78.700000 (alt 1.5 m)" in text
    assert "flight plan  ready; playing abc" in text
    assert "during a flight plan: not reported" in text


def test_format_status_rth_on_disconnect():
    s = Status(rth_delay_s=1, rth_on_disconnect=True, rth_on_disconnect_fixed=True)
    assert "link loss    return home after 1 s; during a flight plan: return home (fixed)" in format_status(s)
    s = Status(rth_on_disconnect=False, rth_on_disconnect_fixed=False)
    assert "during a flight plan: keep flying the plan (settable)" in format_status(s)


HOME = (9.9826743, -78.7006829, 0.0)


def _tiny(alt=10):
    return load_mission(format_mission(tiny_mission(LatLon(*HOME[:2]), LatLon(9.9828090, -78.7006829), alt)).encode())


def _ready(**kw):
    s = dict(battery_pct=90, flying_state="landed", gps_fixed=True, home=HOME, position=HOME,
             plan_available=True, plan_components={"GPS": True}, max_altitude_m=150.0, max_distance_m=100.0,
             geofence=False)
    s.update(kw)
    return Status(**s)


def test_load_mission():
    m = _tiny()
    data = format_mission(tiny_mission(LatLon(*HOME[:2]), LatLon(9.9828090, -78.7006829), 10)).encode()
    assert m.uid == hashlib.md5(data).hexdigest()
    assert m.waypoints == 3 and m.max_altitude_m == 10 and m.problems == []
    assert 14 < m.max_distance_m(HOME) < 16


def test_preflight_ok():
    assert preflight_problems(_ready(), _tiny(), on_drone=True, min_battery_pct=30) == []


def test_preflight_refuses():
    m = _tiny(alt=160)
    s = _ready(battery_pct=20, flying_state="hovering", gps_fixed=False, home=None, plan_available=False,
               plan_components={"GPS": False, "Calibration": True})
    text = "\n".join(preflight_problems(s, m, on_drone=False, min_battery_pct=30))
    for needle in ["not on the drone", "not landed", "battery 20 %", "no GPS fix", "no home point",
                   "missing GPS", "altitude 160 m"]:
        assert needle in text


def test_preflight_geofence_only_when_on():
    far = load_mission(format_mission(tiny_mission(LatLon(*HOME[:2]), LatLon(9.9846, -78.7006829), 30)).encode())
    assert preflight_problems(_ready(), far, True, 30) == []
    assert "geofence" in preflight_problems(_ready(geofence=True), far, True, 30)[0]


def test_format_telemetry():
    s = _ready(flying_state="flying", altitude_m=29.6, speed_ms=8.04, mission_item=4, plan_state="playing",
               position=(9.9828090, -78.7006829, 30.0))
    assert format_telemetry(s, 75.4, 12) == (
        "01:15  battery 90%  flying  alt 30 m  8.0 m/s  home 15 m  item 5/12  plan playing")
