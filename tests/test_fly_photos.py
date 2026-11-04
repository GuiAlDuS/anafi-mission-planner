"""Photos after a resume (`anafi-drone fly`), from what the Sphinx simulator showed (fw 1.10.4)."""

from drone.control import Status, photo_lines
from drone.session import PhotoResume
from planner.mavlink import Cmd, MissionItem


def _mission():
    # takeoff, climb, two photo lines (start, line end waypoint, stop), return home
    c = [Cmd.NAV_TAKEOFF, Cmd.NAV_WAYPOINT,
         Cmd.NAV_WAYPOINT, Cmd.IMAGE_START_CAPTURE, Cmd.NAV_WAYPOINT, Cmd.IMAGE_STOP_CAPTURE,
         Cmd.NAV_WAYPOINT, Cmd.IMAGE_START_CAPTURE, Cmd.NAV_WAYPOINT, Cmd.IMAGE_STOP_CAPTURE,
         Cmd.NAV_RETURN_TO_LAUNCH]
    return [MissionItem(cmd) for cmd in c]


def _s(item, plan="playing"):
    return Status(plan_state=plan, mission_item=item)


def test_photo_lines():
    assert photo_lines(_mission()) == [(3, 5), (7, 9)]


def test_resume_inside_a_line_starts_then_stops_at_its_stop_item():
    w = PhotoResume(_mission())
    assert w.on_resume(3)  # paused after line 1's photo start
    assert not w.update(_s(3)) and not w.update(_s(4))
    assert w.update(_s(5))  # line 1's stop item: the drone skips it, we stop
    assert w.stop_at is None and not w.update(_s(7))  # line 2 starts its own photos


def test_resume_between_lines_or_before_photos_does_nothing():
    w = PhotoResume(_mission())
    assert not w.on_resume(5) and not w.on_resume(6)  # line 1 done, line 2 not started
    assert not w.on_resume(1) and not w.on_resume(None)
    assert not w.update(_s(6))


def test_our_photos_stop_when_the_plan_stops_playing():
    for plan in ("paused", "stopped"):  # a second pause, a stop, or return home (which pauses)
        w = PhotoResume(_mission())
        assert w.on_resume(7)
        assert w.update(_s(8, plan)) and w.stop_at is None
