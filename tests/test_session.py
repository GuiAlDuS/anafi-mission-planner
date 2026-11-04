"""FlightSession against a fake drone: what any front end (terminal, GUI) gets."""

import csv

from drone.control import Mission, Status
from drone.session import LOG_FIELDS, FlightSession
from planner.mavlink import Cmd, MissionItem

HOME = (48.879455, 2.370077, 2.0)


def _mission():
    c = [Cmd.NAV_TAKEOFF, Cmd.NAV_WAYPOINT,
         Cmd.NAV_WAYPOINT, Cmd.IMAGE_START_CAPTURE, Cmd.NAV_WAYPOINT, Cmd.IMAGE_STOP_CAPTURE,
         Cmd.NAV_WAYPOINT, Cmd.IMAGE_START_CAPTURE, Cmd.NAV_WAYPOINT, Cmd.IMAGE_STOP_CAPTURE,
         Cmd.NAV_RETURN_TO_LAUNCH]
    return Mission("d909283af263c7d0f28d95a674f244eb", [MissionItem(x) for x in c], [])


class FakeDrone:
    """Stands in for drone.control: replays statuses, records the commands sent."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []

    def read_status(self, dev):
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def __getattr__(self, name):  # start_plan, pause_plan, stop_photos, ...
        return lambda dev, *a: self.calls.append(name)


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        self.t += 1.0
        return self.t


def _s(flying="flying", plan="playing", item=None, battery=90, rth="available"):
    return Status(flying_state=flying, plan_state=plan, mission_item=item, battery_pct=battery, rth_state=rth,
                  position=HOME, home=HOME)


def _session(tmp_path, statuses):
    fake = FakeDrone(statuses)
    return FlightSession(None, _mission(), tmp_path, ctl=fake, clock=Clock()), fake


def test_before_start_it_only_reads(tmp_path):
    sess, fake = _session(tmp_path, [_s("landed", "stopped")])
    sess.step()
    assert fake.calls == [] and sess.log_path is None and not sess.events


def test_flight_is_logged_and_landing_ends_it(tmp_path):
    sess, fake = _session(tmp_path, [_s("takingoff", battery=90), _s("flying", item=3, battery=80),
                                     _s("landed", "stopped", battery=70)])
    sess.start()
    for _ in range(3):
        sess.step()
    sess.close()
    assert fake.calls == ["start_plan"]
    assert sess.finished and sess.events[-1].kind == "done" and "90 % -> 70 %" in sess.events[-1].text
    rows = list(csv.reader(open(sess.log_path)))
    assert rows[0] == LOG_FIELDS and len(rows) == 4 and rows[2][4] == "3"


def test_resume_inside_a_line_finishes_its_photos(tmp_path):
    sess, fake = _session(tmp_path, [_s(item=3), _s("hovering", "paused", item=3),
                                     _s(item=4), _s(item=5), _s(item=7)])
    sess.start()
    sess.step()
    assert sess.toggle_pause() == "paused"
    sess.step()
    assert sess.toggle_pause() == "resumed"
    for _ in range(3):
        sess.step()
    assert fake.calls == ["start_plan", "pause_plan", "start_plan", "start_photos", "stop_photos"]
    assert any("Photos back on" in e.text for e in sess.events)


def test_resume_between_lines_leaves_photos_to_the_mission(tmp_path):
    sess, fake = _session(tmp_path, [_s("hovering", "paused", item=5), _s(item=6)])
    sess.start()
    sess.step()
    sess.resume()
    sess.step()
    assert fake.calls == ["start_plan", "start_plan"]


def test_closing_mid_line_stops_the_photos_we_started(tmp_path):
    sess, fake = _session(tmp_path, [_s("hovering", "paused", item=7), _s(item=8)])
    sess.start()
    sess.step()
    sess.resume()
    sess.step()
    sess.close()
    assert fake.calls[-2:] == ["start_photos", "stop_photos"]


def test_return_home_note_and_commands(tmp_path):
    sess, fake = _session(tmp_path, [_s(rth="inProgress"), _s("hovering", "stopped")])
    sess.start()
    sess.return_home()
    sess.step()
    sess.step()
    sess.land()
    sess.stop()
    assert fake.calls == ["start_plan", "return_home", "land", "stop_plan"]
    assert any("hovering over home" in e.text for e in sess.events)


def test_toggle_does_nothing_when_no_plan_runs(tmp_path):
    sess, fake = _session(tmp_path, [_s("hovering", "stopped")])
    sess.start()
    sess.step()
    assert sess.toggle_pause() is None and fake.calls == ["start_plan"]
