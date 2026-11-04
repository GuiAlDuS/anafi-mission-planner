import io
import json
import zipfile
from pathlib import Path

import pytest

from planner.ff6 import flight_plan_mavlink, plan_files, plan_zip
from planner.geo import LatLon

FF6_PLAN = Path(__file__).parent / "data" / "ff6_prueba.mavlink"


def _rows(text):
    return [r.split("\t") for r in text.splitlines()[1:]]


def test_mavlink_matches_freeflight_layout():
    ref = _rows(FF6_PLAN.read_text())
    points = [LatLon(float(r[8]), float(r[9])) for r in ref if r[3] == "16"]
    ours = _rows(flight_plan_mavlink(points, 3, 5))
    assert len(ours) == len(ref)
    for i, (a, b) in enumerate(zip(ours, ref)):
        for col in range(12):
            if col == 7 and a[3] == "16":  # yaw: FreeFlight computes it slightly differently
                assert abs(float(a[col]) - float(b[col])) < 1.0
            elif i == 0 and col == 5:  # FreeFlight's first speed item says 6; meaning unknown
                continue
            else:
                assert a[col] == b[col], (i, col, a, b)
    assert FF6_PLAN.read_text().splitlines()[0] == "QGC WPL 120"


def test_plan_files_and_zip():
    pts = [LatLon(9.983, -78.701), LatLon(9.984, -78.701), LatLon(9.984, -78.700)]
    folder, files = plan_files("Import test", pts, 60, 8, date_s=1792855328)
    assert len(folder) == 36 and folder == folder.upper()
    plan = json.loads(files["savedPlan.json"])
    assert plan["title"] == "Import test" and plan["uuid"] == folder and plan["date"] == "1792855328"
    wps = plan["plan"]["wayPoints"]
    assert [w["altitude"] for w in wps] == [60, 60, 60] and wps[0]["lastYaw"] == 360
    assert wps[1]["lastYaw"] == wps[0]["yaw"] and wps[-1]["yaw"] == wps[-2]["yaw"]
    assert wps[-1]["actions"] == [{"type": "VideoStopCapture"}]
    assert b" : " in files["savedPlan.json"]  # same JSON style as the app writes
    assert files["screenshot.png"].startswith(b"\x89PNG")
    with zipfile.ZipFile(io.BytesIO(plan_zip(files))) as z:
        assert sorted(z.namelist()) == ["flightPlan.mavlink", "savedPlan.json", "screenshot.png"]


def test_plan_climbs_and_descends_over_home():
    from planner.ff6 import END_ALTITUDE_M

    home = LatLon(9.9826, -78.7007)
    pts = [LatLon(9.983, -78.701), LatLon(9.984, -78.701), LatLon(9.984, -78.700)]
    _, files = plan_files("Home test", pts, 100, 10, date_s=1792855328, home=home)
    wps = json.loads(files["savedPlan.json"])["plan"]["wayPoints"]
    where = [(w["latitude"], w["longitude"], w["altitude"]) for w in wps]
    assert where[0] == (home.lat, home.lon, 100)  # vertical climb over home first
    assert where[1:4] == [(p.lat, p.lon, 100) for p in pts]
    assert where[4] == (home.lat, home.lon, 100)  # back over home at survey altitude
    assert where[5] == (home.lat, home.lon, END_ALTITUDE_M)  # then straight down
    # Vertical legs keep the heading instead of spinning to an arbitrary yaw.
    assert wps[4]["yaw"] == wps[3]["yaw"] and wps[5]["yaw"] == wps[4]["yaw"]
    assert wps[-1]["actions"] == [{"type": "VideoStopCapture"}]
    rows = _rows(files["flightPlan.mavlink"].decode())
    wp_rows = [r for r in rows if r[3] == "16"]
    assert [(float(r[8]), float(r[9]), float(r[10])) for r in wp_rows] == pytest.approx(where)


def test_plan_without_home_is_unchanged():
    pts = [LatLon(9.983, -78.701), LatLon(9.984, -78.701)]
    _, files = plan_files("No home", pts, 60, 8, date_s=1792855328)
    assert len(json.loads(files["savedPlan.json"])["plan"]["wayPoints"]) == 2


FF6_PHOTOS = Path(__file__).parent / "data" / "ff6_sim_photos.mavlink"  # FreeFlight re-saved our photo plan
SIM_HOME = LatLon(48.879455, 2.370077)


def _photo_plan():
    ref = _rows(FF6_PHOTOS.read_text())
    wps = [LatLon(float(r[8]), float(r[9])) for r in ref if r[3] == "16"]
    lines = wps[1:-2]  # the route is home, the survey lines, home, home low
    return ref, plan_files("Photos", lines, 25, 5, date_s=1793802217, home=SIM_HOME, photo_period_s=5.8 / 5)


def test_photo_plan_matches_freeflight():
    ref, (_, files) = _photo_plan()
    ours = _rows(files["flightPlan.mavlink"].decode())
    # FreeFlight's reference tilts once, over home; ours tilts again at every line start.
    # Without those extra tilts the commands are the same.
    extra = [i for i, r in enumerate(ours) if r[3] == "2800" and i > 3]
    assert len(extra) == 8  # one per line of the 8-line reference plan
    trimmed = [r for i, r in enumerate(ours) if i not in extra]
    assert [r[3] for r in trimmed] == [r[3] for r in ref]  # same commands in the same order
    for a, b in zip(trimmed, ref):
        if a[3] in ("2800", "2000", "2001", "50000"):
            assert a[4:8] == b[4:8], (a, b)


def test_photo_plan_tilts_down_and_starts_photos_once():
    _, (_, files) = _photo_plan()
    plan = json.loads(files["savedPlan.json"])["plan"]
    wps = plan["wayPoints"]
    tilt = {"type": "Tilt", "angle": -90, "speed": 180}
    assert plan["takeoff"] == []  # a tilt at takeoff makes firmware 1.8.x hover instead of flying
    assert wps[0]["actions"] == [tilt]  # over home, before line 1
    assert wps[1]["actions"][0] == tilt and wps[1]["actions"][1]["type"] == "ImageStartCapture"
    assert wps[1]["actions"][1]["period"] == 1.16
    for i in range(3, len(wps) - 3, 2):  # every later line start: tilt back down, nothing else
        assert wps[i]["actions"] == [tilt], i
    for i in range(2, len(wps) - 3, 2):  # line ends
        assert wps[i]["actions"] == [], i
    assert wps[-3]["actions"] == [{"type": "ImageStopCapture"}]  # end of the last line
    assert wps[-2]["actions"] == wps[-1]["actions"] == []
    kinds = [a["type"] for w in wps for a in w["actions"]]
    # Photo commands exactly once each: repeated starts toggle and jam the camera (simulator).
    assert kinds.count("ImageStartCapture") == 1 and kinds.count("ImageStopCapture") == 1
    assert not any("Video" in k for k in kinds)


def test_photo_period_must_be_positive():
    pts = [LatLon(9.983, -78.701), LatLon(9.984, -78.701)]
    with pytest.raises(ValueError):
        plan_files("Bad", pts, 60, 8, photo_period_s=0)
