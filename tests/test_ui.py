from pathlib import Path
import json

import pytest

from planner.mavlink import parse_mission, validate_mission
from ui.server import BadRequest, build_plan, save_plan

AREA = [[9.9862, -78.7031], [9.9866, -78.6985], [9.9838, -78.6972], [9.9829, -78.7018]]
HOME = [9.9826743, -78.7006829]


def test_build_plan():
    resp, missions, waypoints = build_plan({"area": AREA, "home": HOME, "altitude": "120", "angle": None})
    assert resp["photos"] > 0 and len(resp["flights"]) == len(missions) == len(waypoints) >= 1
    assert len(waypoints[0]) == 2 * (resp["flights"][0]["last_line"] - resp["flights"][0]["first_line"] + 1)
    assert all(f["problems"] == [] for f in resp["flights"])
    assert validate_mission(parse_mission(missions[0])) == []
    json.dumps(resp)  # the page gets it as JSON


def test_build_plan_without_home_uses_area_middle():
    resp, _, _ = build_plan({"area": AREA})
    assert resp["notes"] and resp["home"] != HOME


@pytest.mark.parametrize("req, msg", [
    ({"area": AREA[:2]}, "at least 3 corners"),
    ({"area": [[9, -85], [10, -85], [10, -84]]}, "km² limit"),
    ({"area": AREA, "home": [9.5, -78.7]}, "km from the area"),
    ({"area": AREA, "speed": "15"}, "1-10 m/s"),
    ({"area": AREA, "end": "fly away"}, "end must be"),
    ({"area": [[95, 0], [1, 1], [2, 2]]}, "out of range"),
])
def test_build_plan_rejects(req, msg):
    with pytest.raises(BadRequest, match=msg):
        build_plan(req)


def test_save_plan(tmp_path):
    res = save_plan({"area": AREA, "home": HOME, "name": "north field / v2"}, tmp_path)
    assert res["folder"] == str(tmp_path / "north_field_v2")
    assert (tmp_path / "north_field_v2" / "flight_1.mavlink").exists()
    assert len(res["freeflight"]) == len(res["files"]) and res["freeflight"][0].endswith(".zip")
    assert json.loads((tmp_path / "north_field_v2" / "request.json").read_text())["home"] == HOME
    with pytest.raises(BadRequest, match="name"):
        save_plan({"area": AREA, "name": "  "}, tmp_path)


def test_plan_from_line_keeps_grid_and_numbers():
    base = {"area": AREA, "home": HOME, "altitude": "40"}
    full, _, full_wps = build_plan(base)
    assert full["lines"] >= 4 and full["from_line"] == 1 and full["total_lines"] == full["lines"]
    part, _, part_wps = build_plan({**base, "from_line": 3})
    assert part["from_line"] == 3 and part["total_lines"] == full["lines"]
    assert part["lines"] == full["lines"] - 2
    assert part["flights"][0]["first_line"] == 3 and part["flights"][-1]["last_line"] == full["lines"]
    # Same grid: the remaining waypoints are the full plan's from line 3 on (2 per line).
    flat = lambda wps: [p for w in wps for p in w]  # noqa: E731
    assert flat(part_wps) == flat(full_wps)[4:]
    done = [f["properties"]["line"] for f in part["geojson"]["features"] if f["properties"]["kind"] == "done_line"]
    assert done == [1, 2]


def test_plan_from_line_out_of_range():
    with pytest.raises(BadRequest, match="start line must be"):
        build_plan({"area": AREA, "home": HOME, "altitude": "40", "from_line": 99})


def test_save_from_line_gets_its_own_name(tmp_path):
    base = {"area": AREA, "home": HOME, "altitude": "40", "name": "north"}
    full = save_plan(base, tmp_path)
    part = save_plan({**base, "from_line": 3}, tmp_path)
    assert part["folder"] == str(tmp_path / "north_from_line_3")
    assert Path(part["freeflight"][0]).name != Path(full["freeflight"][0]).name  # other plan on the phone
    req = json.loads((tmp_path / "north_from_line_3" / "request.json").read_text())
    assert req["name"] == "north_from_line_3" and req["from_line"] == 3
    again = save_plan(req, tmp_path)  # reopening and saving keeps one suffix
    assert again["folder"] == part["folder"]


def test_run_cache_and_labels(tmp_path):
    from tests.test_runs import write_log
    from ui.server import RunCache

    write_log(tmp_path / "a_0001.json", 10, 100, 40, date="2025-01-01T100000-0600")
    write_log(tmp_path / "a_0002.json", 2, 100, 95, date="2025-02-01T100000-0600")
    cache = RunCache(tmp_path)
    runs = cache.runs()
    assert [r["id"] for r in runs] == ["0001", "0002"]
    assert runs[0]["endurance"] == pytest.approx(100 / 6) and runs[1]["endurance"] is None
    assert runs[0]["curve"][0] == [0.0, 100] or runs[0]["curve"][0] == (0.0, 100)
    cache.label("0001", "A")
    assert cache.runs()[0]["battery"] == "A"
    cache.label("0001", "")
    assert cache.runs()[0]["battery"] is None
    with pytest.raises(BadRequest):
        cache.label("../x", "A")
    json.dumps(cache.runs())  # served as JSON


def _reading(serial="S1", charge=68, health=96, mv=7800):
    from drone.battery import BatteryReading
    return BatteryReading(time="2026-10-31T12:00:00", serial=serial, charge_pct=charge, health_pct=health,
                          cycles=2, voltage_mv=[mv], gauge_fw="up_to_date")


def test_battery_store(tmp_path):
    from drone.upload import DroneError
    from ui.server import BatteryStore

    seen = []
    queue = [_reading(), _reading("S2", 100, 55, 8000), _reading(health=94)]

    def reader(host):
        seen.append(host)
        return queue.pop(0)

    store = BatteryStore(tmp_path / "checks.json", reader)
    assert store.state()["packs"] == []
    st = store.check("skycontroller")
    assert seen == ["192.168.53.1"] and st["checked"] == "S1"
    st = store.check("wifi")
    assert seen[-1] == "192.168.42.1"
    b = next(p for p in st["packs"] if p["serial"] == "S2")
    assert b["problems"] and "worn out" in b["problems"][0] and b["cell_v"] == pytest.approx(4.0)
    store.name("S1", "A")
    st = store.check("skycontroller")
    a = st["packs"][0]
    assert a["name"] == "A" and [h["health_pct"] for h in a["history"]] == [96, 94]
    assert "health down 2 points" in " ".join(a["notes"])
    assert len(st["checks"]) == 3 and st["limits"]["cell_min"] == 3.3
    json.dumps(st)

    with pytest.raises(BadRequest, match="link must be"):
        store.check("bluetooth")
    with pytest.raises(BadRequest, match="no check"):
        store.name("NOPE", "X")

    def failing(host):
        raise DroneError("no battery data")
    with pytest.raises(DroneError):
        BatteryStore(tmp_path / "checks.json", failing).check("skycontroller")
    assert len(store.state()["checks"]) == 3  # a failed check logs nothing


def test_battery_store_without_olympe(tmp_path):
    from ui.server import BatteryStore
    store = BatteryStore(tmp_path / "checks.json", can_check=False)
    assert store.state()["can_check"] is False
    with pytest.raises(BadRequest, match="x86_64 Linux"):
        store.check("skycontroller")


def test_config_lists_base_maps():
    from ui.server import config
    tiles = {t["id"]: t for t in config()["tiles"]}
    assert set(tiles) == {"imagery", "osm"}
    assert "OpenStreetMap" in tiles["osm"]["attribution"]
    assert not any("google" in t["label"].lower() for t in tiles.values())


def test_parse_tile_source():
    from ui.server import parse_tile_source
    key, src = parse_tile_source("My Ortho=https://example.org/t/{z}/{x}/{y}.png")
    assert key == "user_my_ortho" and src.label == "My Ortho" and src.url.endswith("{y}.png")
    for bad in ["no-url", "X=https://example.org/{z}/{x}.png", "X=file:///{z}/{x}/{y}", "=https://e/{z}/{x}/{y}"]:
        with pytest.raises(ValueError):
            parse_tile_source(bad)


def test_fetch_tile_caches_by_type(tmp_path, monkeypatch):
    import io
    import urllib.request
    from email.message import Message

    from ui import server
    calls = []

    class Resp(io.BytesIO):
        def __init__(self, data, ctype):
            super().__init__(data)
            self.headers = Message()
            self.headers["Content-Type"] = ctype
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    replies = {"osm": (b"PNG", "image/png"), "bad": (b"<html>", "text/html")}

    def urlopen(req, timeout):
        calls.append((req.full_url, req.get_header("User-agent")))
        return Resp(*replies["bad" if "bad" in req.full_url else "osm"])

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    src = server.TILE_SOURCES["osm"]
    assert server.fetch_tile(src, "osm", 3, 4, 5, cache=tmp_path) == (b"PNG", "image/png")
    assert (tmp_path / "osm" / "3" / "4" / "5.png").exists()
    assert calls[0][0] == "https://tile.openstreetmap.org/3/4/5.png" and "github.com" in calls[0][1]
    assert server.fetch_tile(src, "osm", 3, 4, 5, cache=tmp_path) == (b"PNG", "image/png")
    assert len(calls) == 1  # second time from the cache
    bad = server.TileSource("Bad", "https://bad.example/{z}/{x}/{y}", "")
    assert server.fetch_tile(bad, "bad", 1, 1, 1, cache=tmp_path) is None  # error pages aren't cached


def test_parse_imagery_info():
    from ui.server import parse_imagery_info
    resp = {"results": [
        {"layerId": 4, "attributes": {"FROM_CACHE_LEVEL": "19", "TO_CACHE_LEVEL": "19", "DATE (YYYYMMDD)": "20200304",
                                      "RESOLUTION (M)": "0.46", "ACCURACY (M)": "10.16", "DESCRIPTION": "GE01",
                                      "SOURCE": "Vantor"}},
        {"layerId": 4, "attributes": {"FROM_CACHE_LEVEL": "12", "TO_CACHE_LEVEL": "18", "DATE (YYYYMMDD)": "20241211",
                                      "RESOLUTION (M)": "0.31", "ACCURACY (M)": "5", "DESCRIPTION": "WV03",
                                      "SOURCE": "Vantor"}},
        {"layerId": 4, "attributes": {"FROM_CACHE_LEVEL": "0", "TO_CACHE_LEVEL": "11", "DATE (YYYYMMDD)": "Null",
                                      "RESOLUTION (M)": "15", "DESCRIPTION": "TerraColor NextGen", "SOURCE": "Null"}},
        {"layerId": 4, "attributes": {"DATE (YYYYMMDD)": "20200101"}},  # no zoom range: skipped
    ]}
    caps = parse_imagery_info(resp)
    assert [c["from_zoom"] for c in caps] == [0, 12, 19]
    assert caps[0]["date"] is None and caps[0]["provider"] is None and caps[0]["accuracy_m"] is None
    assert caps[1] == {"from_zoom": 12, "to_zoom": 18, "date": "2024-12-11", "resolution_m": 0.31,
                       "accuracy_m": 5.0, "sensor": "WorldView-3", "provider": "Vantor"}
    assert caps[2]["sensor"] == "GeoEye-1"


def test_imagery_info_cached_and_offline(tmp_path, monkeypatch):
    import io
    import urllib.request

    from ui import server
    body = json.dumps({"results": [{"attributes": {"FROM_CACHE_LEVEL": "12", "TO_CACHE_LEVEL": "18",
                                                   "DATE (YYYYMMDD)": "20241211"}}]}).encode()
    calls = []

    def urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) > 1:
            raise OSError("offline")
        return io.BytesIO(body)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    a = server.imagery_info(9.98271, -78.70068, cache=tmp_path)
    assert a["captures"][0]["date"] == "2024-12-11" and "geometry=-78.70068,9.98271" in calls[0]
    assert server.imagery_info(9.98274, -78.70071, cache=tmp_path) == a  # same ~100 m cell, from disk
    assert server.imagery_info(10.5, -84.0, cache=tmp_path) == {"captures": [], "offline": True}
    with pytest.raises(server.BadRequest):
        server.imagery_info(95, 0, cache=tmp_path)


def test_list_and_load_missions(tmp_path):
    import os
    from ui.server import list_missions, load_mission
    save_plan({"area": AREA, "home": HOME, "name": "first", "altitude": "100", "speed": "6"}, tmp_path)
    save_plan({"area": AREA, "home": HOME, "name": "second", "altitude": "120"}, tmp_path)
    os.utime(tmp_path / "first" / "request.json", (1, 1))  # older
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "request.json").write_text("not json")
    ms = list_missions(tmp_path)
    assert [m["name"] for m in ms] == ["second", "first"]
    assert ms[1]["altitude"] == "100" and ms[1]["speed"] == "6" and ms[1]["flights"] >= 1
    m = load_mission(tmp_path, "first")
    assert m["name"] == "first" and m["home"] == HOME and m["area"] == AREA
    for bad in ["../etc", "nope", ""]:
        with pytest.raises(BadRequest):
            load_mission(tmp_path, bad)
    assert list_missions(tmp_path / "missing") == []


def test_delete_mission_goes_to_the_trash(tmp_path):
    from ui.server import delete_mission, list_missions
    save_plan({"area": AREA, "home": HOME, "name": "keep"}, tmp_path)
    save_plan({"area": AREA, "home": HOME, "name": "gone"}, tmp_path)
    trashed = []

    def fake_trash(path):  # stands in for the system trash
        trashed.append(path)
        Path(path).rename(tmp_path.parent / f"{tmp_path.name}_trash_can")  # outside the missions folder

    delete_mission(tmp_path, "gone", trash=fake_trash)
    assert trashed == [str(tmp_path / "gone")]
    assert [m["name"] for m in list_missions(tmp_path)] == ["keep"]
    for bad in ["gone", "../keep", ""]:
        with pytest.raises(BadRequest):
            delete_mission(tmp_path, bad, trash=fake_trash)

    def no_trash(path):
        raise OSError("no trash here")

    with pytest.raises(BadRequest, match="nothing was deleted"):
        delete_mission(tmp_path, "keep", trash=no_trash)
    assert (tmp_path / "keep" / "request.json").exists()
