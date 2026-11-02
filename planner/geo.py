"""Coordinate helpers: WGS84 lat/lon <-> local UTM metres, headings."""

from __future__ import annotations

import math
from typing import NamedTuple, Sequence

from pyproj import Geod, Transformer

_GEOD = Geod(ellps="WGS84")


class LatLon(NamedTuple):
    lat: float
    lon: float


def utm_epsg(point: LatLon) -> int:
    zone = int((point.lon + 180) // 6) + 1
    return (32600 if point.lat >= 0 else 32700) + zone


class LocalProjection:
    """Projects WGS84 coordinates to the UTM zone of a reference point (metres)."""

    def __init__(self, reference: LatLon):
        self.epsg = utm_epsg(reference)
        self._fwd = Transformer.from_crs(4326, self.epsg, always_xy=True)
        self._inv = Transformer.from_crs(self.epsg, 4326, always_xy=True)

    def to_xy(self, p: LatLon) -> tuple[float, float]:
        return self._fwd.transform(p.lon, p.lat)

    def to_latlon(self, x: float, y: float) -> LatLon:
        lon, lat = self._inv.transform(x, y)
        return LatLon(lat, lon)

    def convergence_deg(self, at: LatLon) -> float:
        """Grid heading of true north at `at` (add it to a true heading to get a grid heading)."""
        x0, y0 = self.to_xy(at)
        x1, y1 = self.to_xy(LatLon(at.lat + 1e-4, at.lon))
        return math.degrees(math.atan2(x1 - x0, y1 - y0))


def centroid(points: Sequence[LatLon]) -> LatLon:
    return LatLon(sum(p.lat for p in points) / len(points), sum(p.lon for p in points) / len(points))


def heading_deg(a: LatLon, b: LatLon) -> float:
    """True-north heading from a to b, 0..360 clockwise."""
    az, _, _ = _GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    return az % 360


def distance_m(a: LatLon, b: LatLon) -> float:
    _, _, d = _GEOD.inv(a.lon, a.lat, b.lon, b.lat)
    return d
