import pytest

from planner.camera import ANAFI_4K


def test_anafi_intrinsics_match_exif():
    assert ANAFI_4K.pixel_pitch_um == pytest.approx(1.3442, abs=1e-4)
    assert ANAFI_4K.sensor_width_mm == pytest.approx(6.194, abs=1e-3)
    assert ANAFI_4K.sensor_height_mm == pytest.approx(4.646, abs=1e-3)
    assert ANAFI_4K.hfov_deg == pytest.approx(75.5, abs=0.1)


def test_gsd_and_footprint_at_120m():
    assert ANAFI_4K.gsd_m(120) == pytest.approx(0.0403, abs=1e-4)
    w, h = ANAFI_4K.footprint_m(120)
    assert w == pytest.approx(185.8, abs=0.2)
    assert h == pytest.approx(139.4, abs=0.2)


def test_altitude_gsd_roundtrip():
    for alt in (30, 60, 120, 150):
        assert ANAFI_4K.altitude_for_gsd(ANAFI_4K.gsd_m(alt)) == pytest.approx(alt)
