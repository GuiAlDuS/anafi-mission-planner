import json

import pytest

from planner.__main__ import main
from planner.runs import endurance_estimate, read_run, read_runs

HEADERS = ["time", "battery_level", "flying_state", "alert_state", "product_gps_available",
           "product_gps_latitude", "product_gps_longitude", "speed_vx", "speed_vy", "altitude"]


def write_log(path, minutes, start, end, alt=120, speed=10.0, low_at=None, date="2025-03-09T081859-0600"):
    """A flight log like FreeFlight's: ramp, climb, survey, land, one row per second."""
    n = int(minutes * 60)
    rows = [[0, start, 7, 0, True, 9.98, -78.70, 0, 0, 0]]
    for i in range(n + 1):
        batt = round(start - (start - end) * i / n)
        alert = 4 if low_at is not None and batt <= low_at else 0
        rows.append([1000 + i * 1000, batt, 3, alert, True, 9.98 + 0.003 * i / n, -78.70, speed, 0.0, alt])
    rows.append([1000 + (n + 1) * 1000, end, 0, 0, True, 9.98, -78.70, 0, 0, 0])
    path.write_text(json.dumps({"date": date, "gps_latitude": 9.98, "gps_longitude": -78.70,
                                "details_headers": HEADERS, "details_data": rows}))


def test_read_run(tmp_path):
    f = tmp_path / "0914_2023-10-08T094809-0600_503340_0038.json"
    write_log(f, 12, 86, 7, low_at=17, date="2023-10-08T094809-0600")
    r = read_run(f)
    assert r.id == "0038" and r.date == "2023-10-08 09:48"
    assert r.airborne_s == pytest.approx(720) and (r.battery_start, r.battery_end) == (86, 7)
    assert r.drain_pct_per_min == pytest.approx(79 / 12)
    assert r.endurance_min == pytest.approx(100 / (79 / 12))
    assert r.survey and r.low_battery_at == 17 and 300 < r.max_distance_m < 360


def test_short_flights_and_no_takeoff(tmp_path):
    write_log(tmp_path / "a_0001.json", 2, 100, 92)
    assert read_run(tmp_path / "a_0001.json").endurance_min is None  # too short to measure
    (tmp_path / "a_0002.json").write_text(json.dumps({"details_headers": HEADERS, "details_data": [
        [0, 99, 0, 0, True, 9.98, -78.7, 0, 0, 0]]}))
    assert read_run(tmp_path / "a_0002.json") is None


def test_endurance_uses_worst_recent_flight(tmp_path):
    write_log(tmp_path / "a_0001.json", 10, 100, 60, date="2021-01-01T100000-0600")  # 25 min, old
    write_log(tmp_path / "a_0002.json", 10, 100, 40, date="2025-01-01T100000-0600")  # ~17 min
    write_log(tmp_path / "a_0003.json", 10, 100, 33, date="2025-02-01T100000-0600")  # ~15 min
    write_log(tmp_path / "a_0004.json", 10, 100, 55, date="2025-03-01T100000-0600")  # ~22 min
    minutes, used = endurance_estimate(read_runs(tmp_path))
    assert [r.id for r in used] == ["0002", "0003", "0004"]
    assert minutes == pytest.approx(100 / 6.7)


def test_cli_labels_batteries(tmp_path, capsys):
    write_log(tmp_path / "a_0001.json", 10, 100, 40, date="2025-01-01T100000-0600")
    write_log(tmp_path / "a_0002.json", 10, 100, 55, date="2025-02-01T100000-0600")
    assert main(["runs", str(tmp_path), "--label", "0001=A", "--label", "0002=B"]) == 0
    out = capsys.readouterr().out
    assert "Battery A: plan with an endurance of 16 min" in out
    assert "Battery B: plan with an endurance of 22 min" in out
    assert json.loads((tmp_path / "batteries.json").read_text()) == {"0001": "A", "0002": "B"}
