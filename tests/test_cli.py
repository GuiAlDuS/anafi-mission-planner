import json

import pytest

from planner.__main__ import read_polygon


def _write(tmp_path, coords):
    p = tmp_path / "area.geojson"
    p.write_text(json.dumps({"type": "Polygon", "coordinates": [coords]}))
    return p


def test_read_polygon_wgs84(tmp_path):
    p = _write(tmp_path, [[-78.70, 9.98], [-78.69, 9.98], [-78.69, 9.99], [-78.70, 9.98]])
    pts = read_polygon(p)
    assert len(pts) == 3
    assert pts[0].lat == 9.98 and pts[0].lon == -78.70


def test_read_polygon_rejects_projected_coordinates(tmp_path):
    # Same corner in CRTM05 (EPSG:5367) metres.
    p = _write(tmp_path, [[423000, 1103000], [423400, 1103000], [423400, 1103300], [423000, 1103000]])
    with pytest.raises(ValueError, match="EPSG:4326"):
        read_polygon(p)
