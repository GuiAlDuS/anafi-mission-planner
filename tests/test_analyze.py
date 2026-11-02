import os
import shutil
from pathlib import Path

import pytest

from planner.analyze import analyze_directory

# The 133 photos of the reference Pix4Dcapture flight (not shipped): point this at them to run.
PHOTOS = Path(os.environ.get("ANAFI_REFERENCE_PHOTOS", "reference_photos"))
pytestmark = pytest.mark.skipif(not PHOTOS.is_dir() or not shutil.which("exiftool"),
                                reason="reference photos or exiftool not available")


def test_analyze_reference_flight(home):
    (s,) = analyze_directory(PHOTOS, home)
    assert s.photos == 133 and s.photo_mode == "GPSLapse"
    assert s.altitude_agl_m == pytest.approx(120, abs=0.5)
    assert s.speed_ms == pytest.approx(10, abs=0.2)
    assert s.photo_spacing_m == pytest.approx(26.0, rel=0.02)
    assert s.line_spacing_m == pytest.approx(33.8, rel=0.02)
    assert s.run_to_first_photo_s == pytest.approx(98, abs=2)
    assert s.first_photo_from_home_m == pytest.approx(335, abs=5)
