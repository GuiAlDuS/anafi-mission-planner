"""Battery use per flight from FreeFlight 6's flight logs.

FreeFlight keeps a JSON log of every flight it sees, Pix4Dcapture flights
included: about 10 rows a second of battery %, altitude, speed, GPS and flying
state. Copy them off the iPhone with `anafi-drone freeflight pull-runs`.

The logs don't say which battery flew, so labels (flight id -> battery name)
are kept in batteries.json next to the logs.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

AIRBORNE = {1, 2, 3, 4}  # takingoff, hovering, flying, landing
LOW_BATTERY_ALERTS = {3, 4, 6}  # critical, low, almost empty
MIN_AIRBORNE_S = 300  # shorter flights say little about endurance
CURVE_STEP_S = 15  # discharge curve resolution
LABELS_FILE = "batteries.json"


@dataclass
class Run:
    id: str  # the log's flight counter, e.g. "0038"
    date: str
    airborne_s: float
    battery_start: int
    battery_end: int
    max_altitude_m: float
    max_distance_m: float
    cruise_ms: float  # 90th percentile horizontal speed in the air
    low_battery_at: int | None  # battery % when the drone first raised a low-battery alert
    battery: str | None = None
    low_battery_min: float | None = None  # minutes into the flight of that alert
    curve: list[tuple[float, int]] = field(default_factory=list)  # (minutes in the air, battery %)

    @property
    def drain_pct_per_min(self) -> float:
        return (self.battery_start - self.battery_end) / (self.airborne_s / 60) if self.airborne_s else 0.0

    @property
    def endurance_min(self) -> float | None:
        """Full battery to empty at this flight's rate (the battery % is the drone's own estimate)."""
        if self.airborne_s < MIN_AIRBORNE_S or self.drain_pct_per_min <= 0:
            return None
        return 100 / self.drain_pct_per_min

    @property
    def survey(self) -> bool:
        return self.max_altitude_m >= 60 and self.cruise_ms >= 8


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    return 6371000 * math.hypot((lo2 - lo1) * math.cos((la1 + la2) / 2), la2 - la1)


def read_run(path: str | Path) -> Run | None:
    """Summary of one log, or None if the drone never took off."""
    path = Path(path)
    d = json.loads(path.read_text())
    head = d["details_headers"]
    rows = [dict(zip(head, r)) for r in d["details_data"]]
    air = [r for r in rows if r["flying_state"] in AIRBORNE]
    if len(air) < 2:
        return None
    home = (d.get("gps_latitude"), d.get("gps_longitude"))
    fixes = [(r["product_gps_latitude"], r["product_gps_longitude"]) for r in air
             if r.get("product_gps_available") and abs(r["product_gps_latitude"]) <= 90]
    speeds = sorted(math.hypot(r["speed_vx"], r["speed_vy"]) for r in air)
    low = next((r for r in air if r["alert_state"] in LOW_BATTERY_ALERTS), None)
    t0 = air[0]["time"]
    curve, next_t = [], 0.0
    for r in air:
        if r["time"] - t0 >= next_t * 1000:
            curve.append((round((r["time"] - t0) / 60000, 3), r["battery_level"]))
            next_t += CURVE_STEP_S
    curve.append((round((air[-1]["time"] - t0) / 60000, 3), air[-1]["battery_level"]))
    return Run(
        id=path.stem.rsplit("_", 1)[-1],
        date=re.sub(r"^(\d{4}-\d\d-\d\d)T(\d\d)(\d\d).*", r"\1 \2:\3", d.get("date", "")),
        airborne_s=(air[-1]["time"] - air[0]["time"]) / 1000,
        battery_start=air[0]["battery_level"],
        battery_end=air[-1]["battery_level"],
        max_altitude_m=max(r["altitude"] for r in air),
        max_distance_m=max((_distance_m(home, f) for f in fixes), default=0.0) if home[0] is not None else 0.0,
        cruise_ms=speeds[int(len(speeds) * 0.9)],
        low_battery_at=low["battery_level"] if low else None,
        low_battery_min=round((low["time"] - t0) / 60000, 2) if low else None,
        curve=curve,
    )


def load_labels(log_dir: str | Path) -> dict[str, str]:
    p = Path(log_dir) / LABELS_FILE
    return json.loads(p.read_text()) if p.exists() else {}


def save_labels(log_dir: str | Path, labels: dict[str, str]) -> None:
    (Path(log_dir) / LABELS_FILE).write_text(json.dumps(dict(sorted(labels.items())), indent=2) + "\n")


def read_runs(log_dir: str | Path) -> list[Run]:
    """All flights in log_dir, oldest first, with battery labels applied."""
    labels = load_labels(log_dir)
    runs = [r for f in sorted(Path(log_dir).glob("*.json")) if f.name != LABELS_FILE
            and (r := read_run(f)) is not None]
    for r in runs:
        r.battery = labels.get(r.id)
    return sorted(runs, key=lambda r: r.date)


def endurance_estimate(runs: list[Run], last: int = 3) -> tuple[float, list[Run]] | None:
    """Worst endurance among the most recent flights long enough to measure.

    Recent, because batteries lose capacity with age (2021 logs: ~24 min; 2023-25: 15-19 min).
    Worst, because the logs don't say which battery flew unless labelled, and planning
    with the weakest one is the safe choice.
    """
    recent = [r for r in runs if r.endurance_min is not None][-last:]
    if not recent:
        return None
    return min(r.endurance_min for r in recent), recent
