"""Battery check on the ground: what the drone's smart battery reports, judged and logged.

The Anafi (product 0914) sends these through Olympe at connection, without
flying or starting the motors (from Olympe's arsdk/xml/battery.xml and
gauge_fw_updater.xml):

- battery.serial         identifies the pack
- battery.health         state of health, % (the battery gauge's own estimate)
- battery.cycle_count    charge cycles
- battery.voltage        pack voltage in mV, sent regularly (firmware >= 1.6.2)
- battery.alert          too hot/cold, low power, gauge too old, authentication failure, lost comm
- gauge_fw_updater.status  whether the gauge firmware can be updated (read only; we never update)
- common.BatteryStateChanged  charge %

Every check is appended to a JSON log so each pack can be followed over time,
and packs can be given short names (A, B, C) by serial.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .upload import DroneError

CELLS = 2  # Anafi battery: 2-cell high-voltage LiPo, 7.6 V nominal, 8.8 V full
CELL_MIN_V = 3.3  # below this at rest the pack has been over-discharged
CELL_MAX_V = 4.4  # above the 4.35 V limit of HV LiPo cells (plus measuring margin)
FULL_CELL_MIN_V = 4.1  # a full HV cell rests near 4.3 V (a bit less with the drone's own draw)
HEALTH_WARN_PCT = 80  # usual end-of-life mark for LiPo packs
HEALTH_BAD_PCT = 70
LOG_FILE = "battery_checks.json"


@dataclass
class BatteryReading:
    time: str = ""
    serial: str | None = None
    name: str | None = None
    charge_pct: int | None = None
    health_pct: int | None = None
    cycles: int | None = None
    voltage_mv: list[int] = field(default_factory=list)  # samples over the check
    alerts: dict[str, str] = field(default_factory=dict)  # active alerts: name -> warning/critical
    gauge_fw: str | None = None  # up_to_date, updatable, cannot_update

    @property
    def voltage_v(self) -> float | None:
        return sum(self.voltage_mv) / len(self.voltage_mv) / 1000 if self.voltage_mv else None

    @property
    def cell_v(self) -> float | None:
        v = self.voltage_v
        return None if v is None else v / CELLS


def _name(v) -> str:
    return getattr(v, "name", str(v))


def _alerts(st) -> dict[str, str]:
    """battery.alert is a list message: get_state returns {key: {alert, level, ...}} (or one item)."""
    if not st:
        return {}
    items = st.values() if all(isinstance(v, dict) for v in st.values()) else [st]
    out = {}
    for it in items:
        if "alert" in it and _name(it.get("level")) not in ("none", "None"):
            out[_name(it["alert"])] = _name(it["level"])
    return out


def read_battery(dev, seconds: float = 5.0, wait_s: float = 45.0) -> BatteryReading:
    """Read the battery states, sampling the voltage for `seconds`. Read only."""
    from olympe.messages import battery, gauge_fw_updater
    from olympe.messages.common.CommonState import BatteryStateChanged

    def get(msg):
        try:
            return dev.get_state(msg)
        except Exception:  # not received (not sent by this firmware, or not yet)
            return None

    # Through the Skycontroller the connection opens even before the drone has
    # linked to it (e.g. right after switching the drone on): wait for its data.
    deadline = time.monotonic() + wait_s
    while get(BatteryStateChanged) is None and get(battery.serial) is None:
        if time.monotonic() >= deadline:
            raise DroneError(f"no battery data from the drone after {wait_s:.0f} s: is it on and "
                             "linked to the Skycontroller (controller LED solid)?")
        time.sleep(0.5)

    r = BatteryReading(time=dt.datetime.now().isoformat(timespec="seconds"))
    end = time.monotonic() + seconds
    while True:
        if st := get(battery.voltage):
            r.voltage_mv.append(int(st["voltage"]))
        if time.monotonic() >= end:
            break
        time.sleep(0.5)
    if st := get(battery.serial):
        r.serial = st["serial"] or None
    if st := get(battery.health):
        r.health_pct = st["state_of_health"]
    if st := get(battery.cycle_count):
        r.cycles = st["count"]
    if st := get(BatteryStateChanged):
        r.charge_pct = st["percent"]
    r.alerts = _alerts(get(battery.alert))
    if st := get(gauge_fw_updater.status):
        r.gauge_fw = _name(st["diag"])
    return r


def assess(r: BatteryReading, previous: list[BatteryReading] = ()) -> tuple[list[str], list[str]]:
    """(problems, notes). Problems mean: don't fly this pack until you know why."""
    problems, notes = [], []
    for a, level in r.alerts.items():
        (problems if level == "critical" or a in ("authentication_failure", "lost_comm") else notes
         ).append(f"battery alert {a} ({level})")
    if r.health_pct is not None:
        if r.health_pct < HEALTH_BAD_PCT:
            problems.append(f"state of health {r.health_pct} % (below {HEALTH_BAD_PCT} %): worn out")
        elif r.health_pct < HEALTH_WARN_PCT:
            notes.append(f"state of health {r.health_pct} % (below {HEALTH_WARN_PCT} %): expect short flights")
    if (c := r.cell_v) is not None:
        if c < CELL_MIN_V:
            problems.append(f"{c:.2f} V per cell at rest: over-discharged, may be damaged")
        elif c > CELL_MAX_V:
            problems.append(f"{c:.2f} V per cell: above the cell limit, overcharged or bad reading")
    if c is not None and r.charge_pct is not None and r.charge_pct >= 95 and CELL_MIN_V <= c < FULL_CELL_MIN_V:
        notes.append(f"{r.charge_pct} % charge but only {c:.2f} V per cell: the gauge is off or the "
                     "cells no longer charge fully")
    if len(r.voltage_mv) >= 2 and max(r.voltage_mv) - min(r.voltage_mv) > 100:
        notes.append(f"voltage moved {max(r.voltage_mv) - min(r.voltage_mv)} mV while idle")
    if r.gauge_fw == "updatable":
        notes.append("battery gauge firmware can be updated (FreeFlight does this)")
    if not r.voltage_mv and r.health_pct is None and r.cycles is None:
        notes.append("the drone sent no battery details (only charge %); this firmware may not report them")
    prev = [p for p in previous if p.serial and p.serial == r.serial]
    if prev:
        last = prev[-1]
        if last.health_pct is not None and r.health_pct is not None and r.health_pct < last.health_pct:
            notes.append(f"health down {last.health_pct - r.health_pct} points since {last.time[:10]}")
        if last.cycles is not None and r.cycles is not None and r.cycles != last.cycles:
            notes.append(f"{r.cycles - last.cycles} cycles since {last.time[:10]}")
    return problems, notes


def _f(v, unit="", fmt=""):
    return "?" if v is None else f"{v:{fmt}}{unit}"


def format_reading(r: BatteryReading, problems: list[str], notes: list[str]) -> str:
    who = (f"{r.name} " if r.name else "") + f"(serial {r.serial or '?'})"
    lines = [
        f"battery      {who}",
        f"charge       {_f(r.charge_pct, ' %')}",
        f"health       {_f(r.health_pct, ' %')}, {_f(r.cycles)} cycles",
        f"voltage      {_f(r.voltage_v, ' V', '.2f')} ({_f(r.cell_v, ' V', '.2f')} per cell, "
        f"{len(r.voltage_mv)} samples)",
        f"alerts       {', '.join(f'{a} {lv}' for a, lv in r.alerts.items()) or 'none'}",
        f"gauge fw     {_f(r.gauge_fw)}",
    ]
    lines += [f"PROBLEM: {p}" for p in problems] + [f"note: {n}" for n in notes]
    lines.append("verdict      " + ("DON'T FLY this pack until checked" if problems else "OK to fly"))
    return "\n".join(lines)


# Log: {"names": {serial: name}, "checks": [reading, ...]}

def load_log(path: str | Path) -> dict:
    p = Path(path)
    d = json.loads(p.read_text()) if p.exists() else {}
    return {"names": d.get("names", {}), "checks": d.get("checks", [])}


def save_log(path: str | Path, log: dict) -> None:
    Path(path).write_text(json.dumps(log, indent=2) + "\n")


def readings(log: dict) -> list[BatteryReading]:
    return [BatteryReading(**c) for c in log["checks"]]


def record(log: dict, r: BatteryReading, name: str | None = None) -> BatteryReading:
    """Name the pack (remembered by serial; a known pack keeps its name), then log the reading."""
    known = log["names"].get(r.serial) if r.serial else None
    if name and r.serial and not known:
        log["names"][r.serial] = name
    r.name = known or name
    log["checks"].append(asdict(r))
    return r


def format_history(log: dict) -> str:
    rows = readings(log)
    if not rows:
        return "no battery checks yet"
    out = [f"{'date':<16} {'pack':<6} {'serial':<20} {'charge':>6} {'health':>6} {'cycles':>6} "
           f"{'V/cell':>6}  alerts"]
    for r in rows:
        name = log["names"].get(r.serial) or r.name or "-"
        out.append(f"{r.time[:16].replace('T', ' '):<16} {name:<6} {(r.serial or '?'):<20} "
                   f"{_f(r.charge_pct, '%'):>6} {_f(r.health_pct, '%'):>6} {_f(r.cycles):>6} "
                   f"{_f(r.cell_v, '', '.2f'):>6}  {', '.join(r.alerts) or '-'}")
    return "\n".join(out)
