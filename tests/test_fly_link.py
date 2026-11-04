"""The GUI's drone link (ui/fly.py) against a fake drone."""

import time
from contextlib import contextmanager

import pytest

from drone import control
from drone.control import Status
from planner.geo import LatLon
from planner.grid import SurveySettings
from planner.mavlink import format_mission, survey_mission
from planner.plan import plan_survey
from ui.fly import FlyError, FlyLink

HOME = (48.879455, 2.370077, 2.0)
AREA = [LatLon(48.879590, 2.369737), LatLon(48.879590, 2.370417), LatLon(48.879950, 2.370417),
        LatLon(48.879950, 2.369737)]


def _write_mission(out_root):
    home = LatLon(*HOME[:2])
    plan = plan_survey(AREA, home, SurveySettings(altitude_m=25, speed_ms=5))
    f = plan.flights[0]
    text = format_mission(survey_mission(f.lines, home, 25, 5, plan.grid.photo_spacing_m))
    (out_root / "sim").mkdir(parents=True)
    (out_root / "sim" / "flight_1.mavlink").write_text(text)


def _ready(**kw):
    base = dict(battery_pct=95, flying_state="landed", gps_fixed=True, position=HOME, home=HOME,
                plan_available=True, plan_state="stopped", max_altitude_m=120, max_distance_m=300, geofence=True)
    return Status(**{**base, **kw})


class FakeDrone:
    """drone.control with the drone replaced: scripted status, recorded commands."""

    load_mission = staticmethod(control.load_mission)
    preflight_problems = staticmethod(control.preflight_problems)

    def __init__(self, status):
        self.now = status
        self.calls = []

    def read_status(self, dev):
        return self.now

    def __getattr__(self, name):  # start_plan, pause_plan, return_home, land, ...
        return lambda dev, *a: self.calls.append(name)


@contextmanager
def _connector(host):
    yield object()


def _link(tmp_path, status=None, uploaded=True):
    fake = FakeDrone(status or _ready())
    uploads = []

    def uploader(mission, path, http_host, log, say):
        uploads.append(http_host)
        return uploaded

    link = FlyLink(tmp_path / "out", log_dir=tmp_path / "logs", ctl=fake, connector=_connector,
                   uploader=uploader, read_period_s=0.01)
    _write_mission(tmp_path / "out")
    return link, fake, uploads


def _wait(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def _connected(link, how="other", host="10.202.0.1"):
    link.connect(how, host)
    _wait(lambda: link.link == "connected" and link.status is not None)


def test_checks_walk_the_pilot_through(tmp_path):
    link, _, _ = _link(tmp_path)
    assert link.checks() == ["connect to the drone"]
    _connected(link)
    assert link.checks() == ["choose a mission"]
    link.select_mission("sim", 1)
    assert link.checks() == []
    st = link.state()
    assert st["link"]["state"] == "connected" and st["mission"]["on_drone"]
    assert st["mission"]["photo_lines"] and st["mission"]["route"] and st["status"]["home_m"] == 0
    link.shutdown()


def test_upload_goes_to_the_drone_address(tmp_path):
    link, _, uploads = _link(tmp_path)
    _connected(link, "skycontroller")  # through the controller: the drone's own address, via the route
    link.select_mission("sim", 1)
    link.shutdown()
    link2, _, uploads2 = _link(tmp_path / "b")
    _connected(link2, "other", "10.202.0.1")
    link2.select_mission("sim", 1)
    link2.shutdown()
    assert uploads == ["192.168.42.1"] and uploads2 == ["10.202.0.1"]


def test_start_refuses_on_fresh_problems(tmp_path):
    link, fake, _ = _link(tmp_path)
    _connected(link)
    link.select_mission("sim", 1)
    fake.now = _ready(battery_pct=20, gps_fixed=False)  # changed since the last poll
    with pytest.raises(FlyError, match="battery 20 %.*no GPS fix"):
        link.start()
    assert fake.calls == []
    link.shutdown()


def test_start_then_commands_and_notes(tmp_path):
    link, fake, _ = _link(tmp_path)
    _connected(link)
    link.select_mission("sim", 1)
    link.start()
    fake.now = _ready(flying_state="flying", plan_state="playing", mission_item=8)
    _wait(lambda: link.state()["flight"]["airborne"])
    for action in ("pause", "resume", "rth", "land"):
        link.command(action)
    assert fake.calls[:3] == ["start_plan", "pause_plan", "start_plan"]
    assert fake.calls[-2:] == ["return_home", "land"]
    texts = [n["text"] for n in link.state()["notes"]]
    assert any(t.startswith("Started.") for t in texts) and "Landing." in texts
    with pytest.raises(FlyError, match="flying"):
        link.disconnect()  # not while it flies
    with pytest.raises(FlyError, match="already in progress"):
        link.start()
    link.shutdown()


def test_return_home_and_land_work_without_our_flight(tmp_path):
    link, fake, _ = _link(tmp_path, _ready(flying_state="hovering"))
    _connected(link)
    link.command("rth")
    link.command("land")
    with pytest.raises(FlyError, match="no flight in progress"):
        link.command("pause")
    assert fake.calls == ["return_home", "land"]
    link.shutdown()


def test_notes_since(tmp_path):
    link, _, _ = _link(tmp_path)
    _connected(link)
    first = link.state()["notes"]
    assert [n["id"] for n in first] == [1, 2]
    assert link.state(since=2)["notes"] == []
    link.shutdown()


def test_without_olympe_it_says_so(tmp_path):
    link = FlyLink(tmp_path, can_fly=False)
    assert "Olympe" in link.state()["no_olympe"]
    with pytest.raises(FlyError, match="Olympe"):
        link.connect("wifi")
