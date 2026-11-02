"""Polygon -> lawnmower survey lines."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from shapely import affinity
from shapely.geometry import Polygon, box

from .camera import ANAFI_4K, Camera
from .geo import LatLon, LocalProjection, centroid, distance_m, heading_deg


@dataclass(frozen=True)
class SurveySettings:
    altitude_m: float = 120.0
    front_overlap: float = 0.80
    side_overlap: float = 0.80
    speed_ms: float = 10.0
    camera: Camera = ANAFI_4K
    # Line heading in degrees from true north; None lets the planner choose.
    angle_deg: float | None = None
    # Extra distance flown past the area at each end of a line; None = one photo spacing.
    overshoot_m: float | None = None

    def __post_init__(self):
        if not 0 <= self.front_overlap < 1 or not 0 <= self.side_overlap < 1:
            raise ValueError("overlaps must be fractions in [0, 1)")
        if self.altitude_m <= 0:
            raise ValueError("altitude must be positive")
        if not 1 <= self.speed_ms <= 10:
            raise ValueError("Anafi flight plans accept speeds of 1-10 m/s")

    @property
    def footprint_across_m(self) -> float:
        # The long image side runs across track (as Pix4Dcapture flew it).
        return self.camera.footprint_m(self.altitude_m)[0]

    @property
    def footprint_along_m(self) -> float:
        return self.camera.footprint_m(self.altitude_m)[1]

    @property
    def photo_spacing_m(self) -> float:
        return self.footprint_along_m * (1 - self.front_overlap)

    @property
    def max_line_spacing_m(self) -> float:
        return self.footprint_across_m * (1 - self.side_overlap)


@dataclass(frozen=True)
class SurveyLine:
    start: LatLon
    end: LatLon

    @property
    def length_m(self) -> float:
        return distance_m(self.start, self.end)

    @property
    def heading_deg(self) -> float:
        return heading_deg(self.start, self.end)

    def reversed(self) -> SurveyLine:
        return SurveyLine(self.end, self.start)

    def photo_points(self, spacing_m: float) -> list[LatLon]:
        """Where distance-triggered photos fall: at the line start, then every spacing_m.

        Linear in lat/lon, which is well under a metre off over survey line lengths.
        """
        length = self.length_m
        n = int(length // spacing_m) + 1
        fracs = [k * spacing_m / length for k in range(n)] if length > 0 else [0.0]
        return [LatLon(self.start.lat + f * (self.end.lat - self.start.lat),
                       self.start.lon + f * (self.end.lon - self.start.lon)) for f in fracs]


@dataclass
class SurveyGrid:
    area: list[LatLon]
    settings: SurveySettings
    angle_deg: float
    line_spacing_m: float
    lines: list[SurveyLine] = field(default_factory=list)

    @property
    def photo_spacing_m(self) -> float:
        return self.settings.photo_spacing_m

    @property
    def gsd_m(self) -> float:
        return self.settings.camera.gsd_m(self.settings.altitude_m)

    @property
    def side_overlap_actual(self) -> float:
        return 1 - self.line_spacing_m / self.settings.footprint_across_m

    @property
    def photo_count(self) -> int:
        return sum(len(ln.photo_points(self.photo_spacing_m)) for ln in self.lines)

    @property
    def line_length_m(self) -> float:
        return sum(ln.length_m for ln in self.lines)


def candidate_angles(area: Sequence[LatLon]) -> list[float]:
    """Line headings (0..180) parallel to the convex hull edges.

    The minimum-width direction of a polygon is always parallel to a hull edge,
    so these include the heading that needs the fewest lines.
    """
    origin = centroid(area)
    proj = LocalProjection(origin)
    conv = proj.convergence_deg(origin)
    hull = Polygon([proj.to_xy(p) for p in area]).convex_hull
    xs, ys = hull.exterior.coords.xy
    angles: list[float] = []
    for i in range(len(xs) - 1):
        dx, dy = xs[i + 1] - xs[i], ys[i + 1] - ys[i]
        if math.hypot(dx, dy) < 1e-6:
            continue
        a = round((math.degrees(math.atan2(dx, dy)) - conv) % 180, 1)
        if all(abs(a - b) > 0.5 and abs(abs(a - b) - 180) > 0.5 for b in angles):
            angles.append(a)
    return angles


def generate_grid(
    area: Sequence[LatLon],
    settings: SurveySettings,
    angle_deg: float | None = None,
    home: LatLon | None = None,
) -> SurveyGrid:
    """Cover `area` with parallel lines at `angle_deg` (true-north heading).

    Lines are spread evenly from one edge of the area to the other, so the
    actual spacing is never wider than the side overlap allows. With `home`,
    the start corner and direction are chosen to shorten the transit legs.
    """
    if len(area) < 3:
        raise ValueError("survey area needs at least 3 vertices")
    if angle_deg is None:
        angle_deg = settings.angle_deg if settings.angle_deg is not None else 90.0
    area = list(area)
    origin = centroid(area)
    proj = LocalProjection(origin)
    ox, oy = proj.to_xy(origin)
    poly = Polygon([(x - ox, y - oy) for x, y in (proj.to_xy(p) for p in area)])
    if not poly.is_valid:
        poly = poly.buffer(0)

    # Rotate so the flight lines run along +x.
    theta = 90.0 - (angle_deg + proj.convergence_deg(origin))
    rpoly = affinity.rotate(poly, -theta, origin=(0, 0))
    minx, miny, maxx, maxy = rpoly.bounds
    height = maxy - miny
    max_spacing = settings.max_line_spacing_m
    n = 1 if height < 1e-6 else math.ceil(height / max_spacing - 1e-9) + 1
    spacing = height / (n - 1) if n > 1 else 0.0
    overshoot = settings.photo_spacing_m if settings.overshoot_m is None else settings.overshoot_m
    half_band = max(spacing, max_spacing) / 2

    segments: list[tuple[float, float, float]] = []
    for i in range(n):
        y = miny + i * spacing if n > 1 else (miny + maxy) / 2
        band = rpoly.intersection(box(minx - 1, y - half_band, maxx + 1, y + half_band))
        if band.is_empty:
            continue
        bx0, _, bx1, _ = band.bounds
        segments.append((y, bx0 - overshoot, bx1 + overshoot))

    c, s = math.cos(math.radians(theta)), math.sin(math.radians(theta))

    def to_latlon(x: float, y: float) -> LatLon:
        return proj.to_latlon(x * c - y * s + ox, x * s + y * c + oy)

    lines = []
    for k, (y, x0, x1) in enumerate(segments):
        a, b = to_latlon(x0, y), to_latlon(x1, y)
        lines.append(SurveyLine(a, b) if k % 2 == 0 else SurveyLine(b, a))

    if home is not None and lines:
        lines = _best_order(lines, home)
    return SurveyGrid(area=area, settings=settings, angle_deg=angle_deg % 180,
                      line_spacing_m=spacing, lines=lines)


def _best_order(lines: list[SurveyLine], home: LatLon) -> list[SurveyLine]:
    """Pick the corner and direction that minimise home->first + last->home.

    Reversing the line order or flipping every line keeps the lawnmower
    alternation intact, so the four variants are all valid paths. Ties go to
    the variant that ends nearest home: the last leg is flown on the lowest
    battery.
    """
    variants = []
    for seq in (lines, lines[::-1]):
        for flip in (False, True):
            v = [ln.reversed() if flip else ln for ln in seq]
            back = distance_m(v[-1].end, home)
            variants.append((round(distance_m(home, v[0].start) + back, 1), back, v))
    return min(variants, key=lambda c: c[:2])[2]
