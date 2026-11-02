"""The planner must reproduce what Pix4Dcapture actually flew at the reference site.

Measured from photo EXIF (see planner.analyze):
  120 m: photo spacing 26.0 m, line spacing 33.8 m, 9 lines, takeoff -> first photo ~99 s
         at 335 m from home, takeoff -> start of 9th line ~592 s.
  150 m: photo spacing 32.1 m, line spacing 45.4 m.
"""

import pytest

from planner.estimate import FlightModel, estimate_flight, leg_time_s
from planner.grid import SurveySettings, generate_grid

PIX4D_120 = SurveySettings(altitude_m=120, front_overlap=0.813, side_overlap=0.818, overshoot_m=0)
PIX4D_150 = SurveySettings(altitude_m=150, front_overlap=0.816, side_overlap=0.804, overshoot_m=0)


def test_120m_grid_matches_flown(area):
    g = generate_grid(area, PIX4D_120, 90)
    assert g.photo_spacing_m == pytest.approx(26.0, rel=0.02)
    assert g.line_spacing_m == pytest.approx(33.8, rel=0.02)
    assert len(g.lines) == 9


def test_150m_grid_matches_flown(area150):
    g = generate_grid(area150, PIX4D_150, 90)
    assert g.photo_spacing_m == pytest.approx(32.1, rel=0.02)
    assert g.line_spacing_m == pytest.approx(45.4, rel=0.03)


def test_takeoff_to_first_photo():
    m = FlightModel()
    for alt, dist, measured in ((120, 335, 99), (150, 342, 112)):
        t = m.takeoff_overhead_s + alt / m.climb_rate_ms + leg_time_s(dist, 10, m.accel_ms2)
        assert t == pytest.approx(measured, rel=0.10)


def test_120m_survey_time_within_10_percent(area, home):
    # Pix4D's transit (335 m) differs from our optimised start, so compare only the
    # survey part: first line start -> start of the 9th line, measured 592 - 99 = 493 s.
    g = generate_grid(area, PIX4D_120, 90, home)
    e = estimate_flight(g.lines, home, 120, 10)
    last_line = leg_time_s(g.lines[-1].length_m, 10, FlightModel().accel_ms2)
    survey = e.phases_s["lines"] + e.phases_s["turns"] - last_line
    assert survey == pytest.approx(493, rel=0.10)
