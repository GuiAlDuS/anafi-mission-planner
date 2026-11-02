"""Flight time and battery estimate for a survey mission.

The model is kinematic: every straight leg between waypoints is flown with a
trapezoidal speed profile (accelerate, cruise, decelerate), and every corner
costs a yaw rotation. Takeoff, climb, descent and landing are added at home.
Defaults come from Pix4Dcapture survey flights (120 m and 150 m, 10 m/s)
where noted; the rest are educated guesses until real telemetry is logged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Sequence

from .geo import LatLon, distance_m, heading_deg
from .grid import SurveyLine


@dataclass(frozen=True)
class FlightModel:
    climb_rate_ms: float = 2.5  # measured: 5 survey flight logs, vertical climb over home
    descent_rate_ms: float = 2.0  # logs: 1.3-3.8 m/s; with landing_overhead gives 70 s from 120 m vs 72 s logged
    accel_ms2: float = 2.0  # guess; reproduces measured 16-21 s turns
    yaw_rate_dps: float = 45.0  # guess; reproduces measured 16-21 s turns
    takeoff_overhead_s: float = 3.0  # measured: motor start to liftoff plus settling, 5 flight logs
    landing_overhead_s: float = 10.0  # guess
    # Full battery to empty in survey flight. Rated 25; 4.1-4.6 %/min (22-24 min) in 2021-22 logs, but
    # 6.7 %/min (15 min) in the 2023 survey, which hit the low-battery alert. Measure yours: anafi-plan runs.
    endurance_min: float = 15.0
    reserve_frac: float = 0.25  # battery that must remain on landing
    wind_margin: float = 0.15  # extra time assumed for wind


class BatteryStatus(str, Enum):
    OK = "ok"  # fits with reserve and wind margin
    TIGHT = "tight"  # fits with reserve only if there's no wind
    EXCEEDS = "exceeds"  # does not fit with reserve


@dataclass
class FlightEstimate:
    phases_s: dict[str, float] = field(default_factory=dict)
    path_length_m: float = 0.0
    max_distance_from_home_m: float = 0.0
    battery_used_frac: float = 0.0
    status: BatteryStatus = BatteryStatus.OK

    @property
    def total_s(self) -> float:
        return sum(self.phases_s.values())


def leg_time_s(distance: float, speed: float, accel: float) -> float:
    """Time for a straight leg that starts and ends at rest."""
    if distance <= 0:
        return 0.0
    if distance >= speed * speed / accel:
        return distance / speed + speed / accel
    return 2 * math.sqrt(distance / accel)


def _turn_angle(h1: float, h2: float) -> float:
    return abs((h2 - h1 + 180) % 360 - 180)


def estimate_flight(
    lines: Sequence[SurveyLine],
    home: LatLon,
    altitude_m: float,
    speed_ms: float,
    model: FlightModel = FlightModel(),
) -> FlightEstimate:
    """Estimate one flight: takeoff at home, fly `lines` in order, return home and land."""
    if not lines:
        raise ValueError("no lines to fly")
    v, a = speed_ms, model.accel_ms2
    yaw = lambda h1, h2: _turn_angle(h1, h2) / model.yaw_rate_dps  # noqa: E731

    # Waypoint path at survey altitude: home -> line starts/ends -> home.
    path = [home]
    for ln in lines:
        path += [ln.start, ln.end]
    path.append(home)

    phases = {"takeoff_climb": model.takeoff_overhead_s + altitude_m / model.climb_rate_ms,
              "transit_out": 0.0, "lines": 0.0, "turns": 0.0, "transit_back": 0.0,
              "descent_land": altitude_m / model.descent_rate_ms + model.landing_overhead_s}
    length = 0.0
    prev_heading = None
    n = len(path) - 1
    for i in range(n):
        p, q = path[i], path[i + 1]
        d = distance_m(p, q)
        length += d
        h = heading_deg(p, q) if d > 0.01 else prev_heading
        corner = yaw(prev_heading, h) if prev_heading is not None and h is not None else 0.0
        t = leg_time_s(d, v, a)
        if i == 0:
            phases["transit_out"] += t + corner
        elif i == n - 1:
            phases["transit_back"] += t + corner
        elif i % 2 == 1:  # survey line (start -> end)
            phases["lines"] += t
            phases["turns"] += corner
        else:  # connector between line end and next line start
            phases["turns"] += t + corner
        prev_heading = h

    est = FlightEstimate(phases_s=phases, path_length_m=length,
                         max_distance_from_home_m=max(distance_m(home, p) for p in path))
    est.battery_used_frac = est.total_s / (model.endurance_min * 60)
    budget = 1 - model.reserve_frac
    if est.battery_used_frac * (1 + model.wind_margin) <= budget:
        est.status = BatteryStatus.OK
    elif est.battery_used_frac <= budget:
        est.status = BatteryStatus.TIGHT
    else:
        est.status = BatteryStatus.EXCEEDS
    return est
