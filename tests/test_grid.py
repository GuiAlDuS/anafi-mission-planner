import math

import pytest
from shapely.geometry import LineString, Polygon

from planner.geo import LatLon, LocalProjection, centroid, distance_m
from planner.grid import SurveySettings, candidate_angles, generate_grid


def _angle_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def _xy(area, pts):
    proj = LocalProjection(centroid(area))
    return [proj.to_xy(p) for p in pts]


def test_settings_validation():
    with pytest.raises(ValueError):
        SurveySettings(front_overlap=1.0)
    with pytest.raises(ValueError):
        SurveySettings(speed_ms=12)


def test_rectangle_line_count_and_spacing(area):
    s = SurveySettings(side_overlap=0.8)
    g = generate_grid(area, s, 90)
    # Area is ~270 m tall; lines span edge to edge at <= max spacing.
    assert g.line_spacing_m <= s.max_line_spacing_m + 1e-6
    assert g.side_overlap_actual >= 0.8 - 1e-9
    assert len(g.lines) == math.ceil(270.6 / s.max_line_spacing_m) + 1
    for ln in g.lines:
        assert min(_angle_diff(ln.heading_deg, 90), _angle_diff(ln.heading_deg, 270)) < 0.01


def test_lines_alternate_direction(area):
    g = generate_grid(area, SurveySettings(), 90)
    for a, b in zip(g.lines, g.lines[1:]):
        assert abs((a.heading_deg - b.heading_deg) % 360 - 180) < 0.1
        # Consecutive lines connect at the same side.
        assert distance_m(a.end, b.start) < g.line_spacing_m + 1


def test_lines_stay_within_area_plus_overshoot(area):
    s = SurveySettings(overshoot_m=10)
    g = generate_grid(area, s, 33)
    poly = Polygon(_xy(area, area)).buffer(10 + s.max_line_spacing_m / 2 + 0.5)
    for ln in g.lines:
        assert poly.contains(LineString(_xy(area, [ln.start, ln.end])))


def test_rotation_invariance():
    # Same 200 x 100 m rectangle, axis-aligned vs rotated 30 deg: same line count.
    c = LatLon(10.0, -84.0)
    proj = LocalProjection(c)
    cx, cy = proj.to_xy(c)

    def rect(rot):
        r = math.radians(rot)
        pts = [(-100, -50), (100, -50), (100, 50), (-100, 50)]
        return [proj.to_latlon(cx + x * math.cos(r) - y * math.sin(r), cy + x * math.sin(r) + y * math.cos(r))
                for x, y in pts]

    s = SurveySettings(altitude_m=60)
    n0 = len(generate_grid(rect(0), s, 90).lines)
    n30 = len(generate_grid(rect(30), s, 60).lines)
    assert n0 == n30


def test_concave_l_shape_covers_both_arms():
    c = LatLon(10.0, -84.0)
    proj = LocalProjection(c)
    cx, cy = proj.to_xy(c)
    l_shape = [(0, 0), (300, 0), (300, 100), (100, 100), (100, 300), (0, 300)]
    area = [proj.to_latlon(cx + x, cy + y) for x, y in l_shape]
    g = generate_grid(area, SurveySettings(altitude_m=60, overshoot_m=0), 90)
    lengths = sorted(ln.length_m for ln in g.lines)
    # Lines through the foot are long, lines through the upright are short.
    assert lengths[0] < 150 and lengths[-1] > 250


def test_tiny_area_still_gets_a_line():
    c = LatLon(10.0, -84.0)
    proj = LocalProjection(c)
    cx, cy = proj.to_xy(c)
    area = [proj.to_latlon(cx + x, cy + y) for x, y in [(0, 0), (5, 0), (5, 5)]]
    assert len(generate_grid(area, SurveySettings(), 90).lines) >= 1


@pytest.mark.parametrize("lat,lon", [(64.1, -21.9), (-33.9, 18.4), (0.0, 179.9)])
def test_other_hemispheres_and_zones(lat, lon):
    area = [LatLon(lat, lon), LatLon(lat, lon + 0.002), LatLon(lat + 0.002, lon + 0.002), LatLon(lat + 0.002, lon)]
    g = generate_grid(area, SurveySettings(altitude_m=80), 0)
    assert g.lines
    for ln in g.lines:
        assert min(_angle_diff(ln.heading_deg, 0), _angle_diff(ln.heading_deg, 180)) < 0.05


def test_candidate_angles_rectangle(area):
    assert sorted(candidate_angles(area)) == pytest.approx([0.0, 90.0], abs=0.1)


def test_home_picks_nearest_corner_and_ends_there(area):
    corner = area[2]  # NE corner
    near = LatLon(corner.lat + 0.0005, corner.lon + 0.0005)
    g = generate_grid(area, SurveySettings(), 90, home=near)
    assert len(g.lines) % 2 == 1  # start and end on opposite sides: a tie on distance
    assert distance_m(near, g.lines[-1].end) < 120


def test_photo_points_match_count_and_spacing(area, home):
    from planner.geo import distance_m
    from planner.plan import plan_geojson, plan_survey

    plan = plan_survey(area, home)
    g = plan.grid
    for ln in g.lines:
        pts = ln.photo_points(g.photo_spacing_m)
        assert pts[0] == ln.start
        assert distance_m(pts[-1], ln.end) < g.photo_spacing_m
        for a, b in zip(pts, pts[1:]):
            assert abs(distance_m(a, b) - g.photo_spacing_m) < 0.5
    photos = [f for f in plan_geojson(plan)["features"] if f["properties"]["kind"] == "photos"]
    assert sum(len(f["geometry"]["coordinates"]) for f in photos) == g.photo_count
