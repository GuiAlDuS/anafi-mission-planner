"""Top-level planning: choose line angle, split into battery-sized flights."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .estimate import BatteryStatus, FlightEstimate, FlightModel, estimate_flight
from .geo import LatLon
from .grid import SurveyGrid, SurveyLine, SurveySettings, candidate_angles, generate_grid


@dataclass
class Flight:
    lines: list[SurveyLine]
    first_line_index: int
    estimate: FlightEstimate


@dataclass
class SurveyPlan:
    grid: SurveyGrid
    home: LatLon
    model: FlightModel
    flights: list[Flight] = field(default_factory=list)

    @property
    def total_s(self) -> float:
        return sum(f.estimate.total_s for f in self.flights)


def plan_survey(
    area: Sequence[LatLon],
    home: LatLon,
    settings: SurveySettings = SurveySettings(),
    model: FlightModel = FlightModel(),
) -> SurveyPlan:
    """Build the grid (trying candidate angles unless one is fixed) and split it into flights."""
    if settings.angle_deg is not None:
        angles = [settings.angle_deg]
    else:
        angles = candidate_angles(area)
    best: SurveyPlan | None = None
    for angle in angles:
        grid = generate_grid(area, settings, angle, home)
        plan = SurveyPlan(grid=grid, home=home, model=model, flights=split_flights(grid, home, model))
        if best is None or (len(plan.flights), plan.total_s) < (len(best.flights), best.total_s):
            best = plan
    assert best is not None
    return best


def split_flights(grid: SurveyGrid, home: LatLon, model: FlightModel) -> list[Flight]:
    """Greedily pack consecutive lines into flights whose estimate stays OK.

    Each later flight resumes at the next line, with its own transit from and
    back to home. A single line too long for one battery still gets a flight,
    marked with its EXCEEDS/TIGHT status.
    """
    s = grid.settings
    est = lambda lines: estimate_flight(lines, home, s.altitude_m, s.speed_ms, model)  # noqa: E731
    flights: list[Flight] = []
    i = 0
    while i < len(grid.lines):
        j = i + 1
        e = est(grid.lines[i:j])
        while j < len(grid.lines):
            e_next = est(grid.lines[i:j + 1])
            if e_next.status is not BatteryStatus.OK:
                break
            j, e = j + 1, e_next
        flights.append(Flight(lines=grid.lines[i:j], first_line_index=i, estimate=e))
        i = j
    return flights


def plan_geojson(plan: SurveyPlan) -> dict:
    feats = [
        {"type": "Feature", "properties": {"kind": "area"},
         "geometry": {"type": "Polygon", "coordinates": [[[p.lon, p.lat] for p in plan.grid.area + plan.grid.area[:1]]]}},
        {"type": "Feature", "properties": {"kind": "home"},
         "geometry": {"type": "Point", "coordinates": [plan.home.lon, plan.home.lat]}},
    ]
    for n, f in enumerate(plan.flights, 1):
        pts = [plan.home] + [p for ln in f.lines for p in (ln.start, ln.end)] + [plan.home]
        feats.append({"type": "Feature", "properties": {"kind": "flight", "flight": n},
                      "geometry": {"type": "LineString", "coordinates": [[p.lon, p.lat] for p in pts]}})
        photos = [p for ln in f.lines for p in ln.photo_points(plan.grid.photo_spacing_m)]
        feats.append({"type": "Feature", "properties": {"kind": "photos", "flight": n, "count": len(photos)},
                      "geometry": {"type": "MultiPoint", "coordinates": [[p.lon, p.lat] for p in photos]}})
    return {"type": "FeatureCollection", "features": feats}
