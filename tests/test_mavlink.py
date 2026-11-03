from pathlib import Path

import pytest

from planner.geo import LatLon, heading_deg
from planner.grid import SurveySettings, generate_grid
from planner.mavlink import (Cmd, Frame, MissionItem, format_mission, parse_mission, survey_mission,
                             tiny_mission, validate_mission)


@pytest.fixture
def mission(area, home):
    s = SurveySettings()
    g = generate_grid(area, s, 90, home)
    return survey_mission(g.lines, home, s.altitude_m, s.speed_ms, g.photo_spacing_m), g


def test_header_and_sequence(mission):
    items, _ = mission
    rows = format_mission(items).splitlines()
    assert rows[0] == "QGC WPL 120"
    for seq, row in enumerate(rows[1:]):
        f = row.split("\t")
        assert len(f) == 12
        assert int(f[0]) == seq
        assert f[1] == "0"


def test_roundtrip(mission):
    items, _ = mission
    parsed = parse_mission(format_mission(items))
    assert len(parsed) == len(items)
    for a, b in zip(items, parsed):
        assert a.command == b.command and a.frame == b.frame
        assert (a.x, a.y, a.z) == pytest.approx((b.x, b.y, b.z), abs=1e-7)
        assert (a.p1, a.p2, a.p3, a.p4) == pytest.approx((b.p1, b.p2, b.p3, b.p4), abs=1e-5)


def test_survey_mission_structure(mission):
    items, g = mission
    cmds = [it.command for it in items]
    assert cmds[0] == Cmd.NAV_TAKEOFF
    assert cmds[-1] == Cmd.NAV_RETURN_TO_LAUNCH
    assert cmds.count(Cmd.IMAGE_START_CAPTURE) == cmds.count(Cmd.IMAGE_STOP_CAPTURE) == len(g.lines)
    still = next(it for it in items if it.command == Cmd.SET_STILL_CAPTURE_MODE)
    assert still.p1 == 1 and still.p2 == pytest.approx(g.photo_spacing_m)  # GPS-position capture
    mount = next(it for it in items if it.command == Cmd.DO_MOUNT_CONTROL)
    assert mount.p1 == -90 and mount.z == 2
    for it in items:
        if it.command in (Cmd.NAV_WAYPOINT, Cmd.NAV_TAKEOFF):
            assert it.frame == Frame.GLOBAL_RELATIVE_ALT and it.z == 120
    assert validate_mission(items) == []


def test_validate_catches_known_rejections(home):
    no_wp = [MissionItem(Cmd.NAV_TAKEOFF, Frame.GLOBAL_RELATIVE_ALT, x=home.lat, y=home.lon, z=10),
             MissionItem(Cmd.NAV_LAND)]
    assert any("NAV_WAYPOINT" in p for p in validate_mission(no_wp))
    no_alt = no_wp[:1] + [MissionItem(Cmd.NAV_WAYPOINT, Frame.GLOBAL_RELATIVE_ALT, x=home.lat, y=home.lon, z=0)]
    assert any("altitude" in p for p in validate_mission(no_alt))
    fast = no_alt[:1] + [MissionItem(Cmd.DO_CHANGE_SPEED, p2=15)]
    assert any("speed" in p for p in validate_mission(fast))


def test_tiny_mission_golden(home):
    target = LatLon(9.9827643, -78.7006829)  # ~10 m north
    text = format_mission(tiny_mission(home, target, 10))
    assert text == (
        "QGC WPL 120\n"
        "0\t0\t3\t22\t0.000000\t0.000000\t0.000000\t0.000000\t9.98267430\t-78.70068290\t10.000000\t1\n"
        "1\t0\t3\t16\t0.000000\t3.000000\t0.000000\t0.000000\t9.98267430\t-78.70068290\t10.000000\t1\n"
        "2\t0\t3\t16\t0.000000\t3.000000\t0.000000\t0.000000\t9.98276430\t-78.70068290\t10.000000\t1\n"
        "3\t0\t3\t21\t0.000000\t0.000000\t0.000000\t0.000000\t0.00000000\t0.00000000\t0.000000\t1\n"
    )


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_mission("hello\n1 2 3")


FF6_PLAN = Path(__file__).parent / "data" / "ff6_prueba.mavlink"


def _yaw_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def test_ff6_plan_parses_and_validates():
    items = parse_mission(FF6_PLAN.read_text())
    assert len(items) == 9
    assert validate_mission(items) == []


def test_layout_matches_ff6(mission):
    """Same frame, current flag and speed layout as FreeFlight 6 writes."""
    ff6 = FF6_PLAN.read_text().splitlines()[1:]
    ours = format_mission(mission[0]).splitlines()[1:]
    for row in ff6 + ours:
        f = row.split("\t")
        assert (f[1], f[2]) == ("0", "3")
    for rows in (ff6, ours):
        speed = next(r.split("\t") for r in rows if r.split("\t")[3] == "178")
        assert float(speed[4]) == 0 and float(speed[6]) == -1


def _check_yaw_points_ahead(items, tol):
    wps = [it for it in items if it.command == Cmd.NAV_WAYPOINT]
    for a, b in zip(wps, wps[1:]):
        if (a.x, a.y) != (b.x, b.y):
            assert _yaw_diff(a.p4, heading_deg(LatLon(a.x, a.y), LatLon(b.x, b.y))) < tol


def test_ff6_yaw_points_to_next_waypoint():
    _check_yaw_points_ahead(parse_mission(FF6_PLAN.read_text()), tol=1.0)


def test_survey_yaw_points_along_leg_ahead(mission):
    items, g = mission
    _check_yaw_points_ahead(items, tol=0.01)
    *_, last_line_end, over_home = [it for it in items if it.command == Cmd.NAV_WAYPOINT]
    assert _yaw_diff(last_line_end.p4, heading_deg(g.lines[-1].end, LatLon(over_home.x, over_home.y))) < 0.01
    assert over_home.p4 == last_line_end.p4  # no turn over home before descending


def test_survey_mission_climbs_and_returns_over_home(mission, home):
    items, g = mission
    wps = [it for it in items if it.command == Cmd.NAV_WAYPOINT]
    # First waypoint straight above home: vertical climb before heading out.
    assert (wps[0].x, wps[0].y) == (home.lat, home.lon) and wps[0].z == 120
    assert (wps[1].x, wps[1].y) == (g.lines[0].start.lat, g.lines[0].start.lon)
    # Back over home at survey altitude before RTH, so RTH only descends.
    assert items[-1].command == Cmd.NAV_RETURN_TO_LAUNCH
    assert items[-2] == wps[-1] and (wps[-1].x, wps[-1].y) == (home.lat, home.lon) and wps[-1].z == 120


def _slanted_legs(items, home):
    """Legs that change altitude while moving sideways, walking the mission as the
    Anafi flies it: takeoff only lifts it to about 1 m over home, waypoints are flown
    in a straight line, LAND descends where the drone is, and RTL flies home at
    return-home altitude (so it must start over home to descend vertically)."""
    from planner.geo import distance_m
    pos, alt, bad = home, 0.0, []
    for i, it in enumerate(items):
        if it.command == Cmd.NAV_TAKEOFF:
            alt = 1.0
        elif it.command == Cmd.NAV_WAYPOINT:
            nxt = LatLon(it.x, it.y)
            if abs(it.z - alt) > 0.5 and distance_m(pos, nxt) > 1.0:
                bad.append(f"item {i}: {alt:.0f} -> {it.z:.0f} m over {distance_m(pos, nxt):.0f} m")
            pos, alt = nxt, it.z
        elif it.command == Cmd.NAV_RETURN_TO_LAUNCH and distance_m(pos, home) > 1.0:
            bad.append(f"item {i}: return home starts {distance_m(pos, home):.0f} m from home")
    return bad


@pytest.mark.parametrize("kind", ["tiny", "survey_rth", "survey_land"])
def test_no_mission_climbs_or_descends_while_moving_sideways(kind, area, home):
    # The rule from the first field test, where a slanted climb hit branches.
    if kind == "tiny":
        items = tiny_mission(home, LatLon(home.lat + 0.0002, home.lon + 0.0002), 10)
    else:
        s = SurveySettings()
        g = generate_grid(area, s, 90, home)
        items = survey_mission(g.lines, home, s.altitude_m, s.speed_ms, g.photo_spacing_m,
                               end_action=kind.split("_")[1])
    assert _slanted_legs(items, home) == []
