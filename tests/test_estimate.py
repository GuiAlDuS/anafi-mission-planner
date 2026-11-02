import pytest

from planner.estimate import BatteryStatus, FlightModel, estimate_flight, leg_time_s
from planner.geo import LatLon
from planner.grid import SurveySettings, generate_grid
from planner.plan import plan_survey


def test_leg_time_profiles():
    assert leg_time_s(0, 10, 2) == 0
    assert leg_time_s(1000, 10, 2) == pytest.approx(100 + 5)  # cruise + accel/decel
    assert leg_time_s(8, 10, 2) == pytest.approx(4)  # never reaches cruise speed


def test_turn_time_grows_with_line_spacing():
    # Measured: ~17 s turns at 34 m spacing, ~19 s at 45 m (both at 10 m/s).
    m = FlightModel()
    turn = lambda s: 2 * 90 / m.yaw_rate_dps + leg_time_s(s, 10, m.accel_ms2) + 10 / m.accel_ms2  # noqa: E731
    assert 15 <= turn(33.8) <= 20
    assert 17 <= turn(45.4) <= 22
    assert turn(45.4) > turn(33.8)


def test_farther_home_costs_more(area):
    s = SurveySettings()
    g = generate_grid(area, s, 90)
    near = LatLon(9.9826743, -78.7006829)
    far = LatLon(9.9900, -78.7006829)  # ~800 m north
    e_near = estimate_flight(g.lines, near, s.altitude_m, s.speed_ms)
    e_far = estimate_flight(g.lines, far, s.altitude_m, s.speed_ms)
    extra_m = e_far.path_length_m - e_near.path_length_m
    assert extra_m > 800
    assert e_far.total_s - e_near.total_s == pytest.approx(extra_m / s.speed_ms, rel=0.1)


def test_battery_status_thresholds(area, home):
    s = SurveySettings()
    g = generate_grid(area, s, 90, home)
    fresh = FlightModel(endurance_min=25)
    assert estimate_flight(g.lines, home, s.altitude_m, s.speed_ms, fresh).status is BatteryStatus.OK
    # This is the 2023 survey's area: flown on an aged battery it hit the low-battery alert,
    # and with today's default endurance the planner says so.
    assert estimate_flight(g.lines, home, s.altitude_m, s.speed_ms).status is not BatteryStatus.OK
    short = FlightModel(endurance_min=12)
    assert estimate_flight(g.lines, home, s.altitude_m, s.speed_ms, short).status is BatteryStatus.EXCEEDS


def test_split_into_battery_sized_flights(area, home):
    plan = plan_survey(area, home, SurveySettings(altitude_m=50))
    assert len(plan.flights) >= 2
    assert all(f.estimate.status is BatteryStatus.OK for f in plan.flights)
    covered = [ln for f in plan.flights for ln in f.lines]
    assert covered == plan.grid.lines
    assert [f.first_line_index for f in plan.flights][0] == 0


def test_auto_angle_prefers_fewer_turns(area, home):
    plan = plan_survey(area, home, SurveySettings())
    assert plan.grid.angle_deg == pytest.approx(90, abs=0.1)  # long E-W lines, 9 instead of 14
