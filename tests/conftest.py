import pytest

from planner.geo import LatLon

# Reference site: the 2023-11-12 Pix4Dcapture flight (photo-centre bounding box), moved to
# made-up coordinates (6 deg east, same latitude: distances and headings are unchanged)
# and the usual takeoff point reported by the pilot.
REF_HOME = LatLon(9.9826743, -78.7006829)
REF_120M_AREA = [
    LatLon(9.9819621, -78.7019918), LatLon(9.9819621, -78.6980126),
    LatLon(9.9843955, -78.6980126), LatLon(9.9843955, -78.7019918),
]
REF_150M_AREA = [
    LatLon(9.9815177, -78.7036670), LatLon(9.9815177, -78.6980712),
    LatLon(9.9855830, -78.6980712), LatLon(9.9855830, -78.7036670),
]


@pytest.fixture
def home():
    return REF_HOME


@pytest.fixture
def area():
    return list(REF_120M_AREA)


@pytest.fixture
def area150():
    return list(REF_150M_AREA)
