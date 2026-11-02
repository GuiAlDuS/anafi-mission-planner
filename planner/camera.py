"""Pinhole camera model for nadir photogrammetry."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Camera:
    name: str
    image_width_px: int
    image_height_px: int
    focal_mm: float
    pixel_pitch_um: float

    @property
    def sensor_width_mm(self) -> float:
        return self.image_width_px * self.pixel_pitch_um / 1000

    @property
    def sensor_height_mm(self) -> float:
        return self.image_height_px * self.pixel_pitch_um / 1000

    @property
    def hfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.sensor_width_mm / (2 * self.focal_mm)))

    @property
    def vfov_deg(self) -> float:
        return math.degrees(2 * math.atan(self.sensor_height_mm / (2 * self.focal_mm)))

    def gsd_m(self, altitude_m: float) -> float:
        """Ground sampling distance (m/px) for a nadir photo over flat ground."""
        return altitude_m * self.pixel_pitch_um / 1000 / self.focal_mm

    def altitude_for_gsd(self, gsd_m: float) -> float:
        return gsd_m * self.focal_mm / (self.pixel_pitch_um / 1000)

    def footprint_m(self, altitude_m: float) -> tuple[float, float]:
        """Ground footprint (width, height) in metres along the image axes."""
        g = self.gsd_m(altitude_m)
        return self.image_width_px * g, self.image_height_px * g


# Measured from EXIF of 5 Pix4Dcapture flights (Anafi fw 1.8.2, rectilinear 16 MP mode):
# FocalLength 4.0 mm, FocalPlaneXResolution 7439.143084 px/cm -> 1.344241 um pixels.
ANAFI_4K = Camera(
    name="Parrot Anafi 4K (rectilinear 16 MP)",
    image_width_px=4608,
    image_height_px=3456,
    focal_mm=4.0,
    pixel_pitch_um=10_000 / 7439.143084,
)
