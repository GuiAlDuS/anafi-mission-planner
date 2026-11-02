import pytest

from drone import battery
from drone.battery import BatteryReading, assess, format_history, format_reading, load_log, record, save_log


def _r(**kw):
    d = dict(time="2026-10-31T20:00:00", serial="PS123", charge_pct=95, health_pct=92, cycles=40,
             voltage_mv=[8500, 8510], gauge_fw="up_to_date")
    d.update(kw)
    return BatteryReading(**d)


def test_healthy_pack():
    r = _r()
    assert r.voltage_v == pytest.approx(8.505)
    assert r.cell_v == pytest.approx(4.2525)
    problems, notes = assess(r)
    assert problems == [] and notes == []
    text = format_reading(r, problems, notes)
    assert "health       92 %, 40 cycles" in text
    assert "verdict      OK to fly" in text


@pytest.mark.parametrize("kw, word", [
    ({"health_pct": 65}, "worn out"),
    ({"voltage_mv": [6400]}, "over-discharged"),
    ({"voltage_mv": [9000]}, "above the cell limit"),
    ({"alerts": {"authentication_failure": "warning"}}, "authentication_failure"),
    ({"alerts": {"too_hot": "critical"}}, "too_hot"),
])
def test_problems(kw, word):
    problems, _ = assess(_r(**kw))
    assert len(problems) == 1 and word in problems[0]
    assert "DON'T FLY" in format_reading(_r(**kw), problems, [])


def test_notes():
    _, notes = assess(_r(health_pct=75, gauge_fw="updatable", alerts={"too_cold": "warning"},
                         voltage_mv=[8300, 8500]))
    text = " | ".join(notes)
    assert "expect short flights" in text and "can be updated" in text
    assert "too_cold" in text and "moved 200 mV" in text


def test_firmware_sends_nothing():
    r = BatteryReading(charge_pct=80)
    problems, notes = assess(r)
    assert problems == [] and "only charge %" in notes[0]
    assert "voltage      ? (? per cell, 0 samples)" in format_reading(r, problems, notes)


def test_compare_with_previous_check_of_same_pack():
    prev = [_r(time="2026-01-01T10:00:00", health_pct=95, cycles=30), _r(serial="OTHER", health_pct=99)]
    _, notes = assess(_r(), prev)
    assert "health down 3 points since 2026-01-01" in notes
    assert "10 cycles since 2026-01-01" in notes


def test_log_names_by_serial(tmp_path):
    path = tmp_path / "checks.json"
    log = load_log(path)
    record(log, _r(), "A")
    save_log(path, log)
    log = load_log(path)
    r = record(log, _r(time="2026-11-01T09:00:00", health_pct=91))  # name remembered
    assert r.name == "A" and log["names"] == {"PS123": "A"}
    assert len(battery.readings(log)) == 2
    hist = format_history(log).splitlines()
    assert hist[1].startswith("2026-10-31 20:00 A      PS123")
    assert "91%" in hist[2]


def test_alerts_from_list_state():
    class E:
        def __init__(self, name):
            self.name = name
    st = {E("too_hot"): {"alert": E("too_hot"), "level": E("warning")},
          E("power_level"): {"alert": E("power_level"), "level": E("none")}}
    assert battery._alerts(st) == {"too_hot": "warning"}
    assert battery._alerts({"alert": E("lost_comm"), "level": E("critical")}) == {"lost_comm": "critical"}
    assert battery._alerts(None) == {}


def test_full_but_low_voltage():
    _, notes = assess(_r(charge_pct=100, voltage_mv=[8000]))
    assert any("100 % charge but only 4.00 V" in n for n in notes)


class _FakeDev:
    def __init__(self, states):
        self.states = states

    def get_state(self, msg):
        if msg not in self.states:
            raise RuntimeError("not received")
        return self.states[msg]


def test_read_battery_waits_for_drone_then_gives_up(monkeypatch):
    pytest.importorskip("olympe")
    from drone.upload import DroneError
    monkeypatch.setattr(battery.time, "sleep", lambda s: None)
    with pytest.raises(DroneError, match="no battery data"):
        battery.read_battery(_FakeDev({}), seconds=0, wait_s=0)


def test_read_battery(monkeypatch):
    pytest.importorskip("olympe")
    from olympe.messages import battery as b
    from olympe.messages.common.CommonState import BatteryStateChanged
    monkeypatch.setattr(battery.time, "sleep", lambda s: None)
    dev = _FakeDev({BatteryStateChanged: {"percent": 68}, b.serial: {"serial": "S1"},
                    b.health: {"state_of_health": 96}, b.cycle_count: {"count": 2},
                    b.voltage: {"voltage": 7800}})
    r = battery.read_battery(dev, seconds=0, wait_s=1)
    assert (r.serial, r.charge_pct, r.health_pct, r.cycles, r.voltage_mv) == ("S1", 68, 96, 2, [7800])


def test_known_pack_keeps_its_name():
    log = {"names": {"PS123": "A"}, "checks": []}
    assert record(log, _r(), "C").name == "A"
    assert log["names"] == {"PS123": "A"}
