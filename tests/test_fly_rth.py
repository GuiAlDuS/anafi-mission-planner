"""The live view's note when a return home ends hovering (seen in the Sphinx simulator)."""

from drone.control import Status
from drone.session import RthWatch

HOME = (48.879455, 2.370077, 2.0)


def _s(flying, rth, pos=HOME):
    return Status(flying_state=flying, rth_state=rth, position=pos, home=HOME)


def test_note_once_when_return_home_ends_hovering():
    w = RthWatch()
    seq = [_s("flying", "available"), _s("flying", "inProgress"), _s("flying", "inProgress"),
           _s("hovering", "available"), _s("hovering", "available")]
    notes = [w.update(s) for s in seq]
    assert notes[:3] == [None, None, None] and notes[4] is None
    assert "hovering over home (0 m from home)" in notes[3] and "Land it" in notes[3]


def test_no_note_for_a_pause_or_stop_without_return_home():
    w = RthWatch()
    assert all(w.update(_s(f, "available")) is None for f in ("flying", "hovering", "hovering"))


def test_landing_after_return_home_needs_no_note():
    w = RthWatch()
    w.update(_s("flying", "inProgress"))
    assert w.update(_s("landing", "unavailable")) is None
    assert w.update(_s("landed", "unavailable")) is None
