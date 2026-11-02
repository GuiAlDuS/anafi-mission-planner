import io
import json
import urllib.parse
from email.message import Message

import pytest

from ui.wms import WmsError, WmsStore, base_url, getmap_url, parse_capabilities, tile_bbox

CAPS_130 = b"""<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms" xmlns:xlink="http://www.w3.org/1999/xlink">
 <Service><Title>Ortofotos</Title></Service>
 <Capability>
  <Request><GetMap><Format>image/jpeg</Format><Format>image/png</Format>
   <DCPType><HTTP><Get><OnlineResource xlink:href="https://maps.example/wms?map=orto.map&amp;"/></Get></HTTP></DCPType>
  </GetMap></Request>
  <Layer><Title>Root</Title><CRS>EPSG:5367</CRS><CRS>EPSG:3857</CRS>
   <EX_GeographicBoundingBox><westBoundLongitude>-86</westBoundLongitude><eastBoundLongitude>-82.5</eastBoundLongitude>
    <southBoundLatitude>8.3</southBoundLatitude><northBoundLatitude>11.2</northBoundLatitude></EX_GeographicBoundingBox>
   <Layer><Name>Mosaico5000</Name><Title>Mosaico 2005-2007</Title><Abstract>Ortofotos 1:5000</Abstract></Layer>
   <Layer><Name>Local</Name><Title>Local only</Title></Layer>
  </Layer>
 </Capability>
</WMS_Capabilities>"""

CAPS_111 = b"""<?xml version="1.0"?>
<WMT_MS_Capabilities version="1.1.1"><Service><Title>Old</Title></Service>
 <Capability><Request><GetMap><Format>image/jpeg</Format></GetMap></Request>
  <Layer><Title>Root</Title><SRS>EPSG:4326 EPSG:900913</SRS>
   <Layer><Name>roads</Name><Title>Roads</Title><LatLonBoundingBox minx="-85" miny="9" maxx="-84" maxy="10"/></Layer>
  </Layer></Capability></WMT_MS_Capabilities>"""


def test_parse_capabilities_130_inherits_crs_and_bbox():
    c = parse_capabilities(CAPS_130, "https://maps.example/wms")
    assert c["title"] == "Ortofotos" and c["version"] == "1.3.0" and c["format"] == "image/png"
    assert c["get_url"] == "https://maps.example/wms?map=orto.map"
    layers = {lyr["name"]: lyr for lyr in c["layers"]}
    assert layers["Mosaico5000"]["crs"] == "EPSG:3857" and layers["Mosaico5000"]["bbox"] == [8.3, -86, 11.2, -82.5]
    assert layers["Mosaico5000"]["abstract"] == "Ortofotos 1:5000"


def test_parse_capabilities_111():
    c = parse_capabilities(CAPS_111, "http://old.example/wms")
    [lyr] = c["layers"]
    assert c["format"] == "image/jpeg" and c["get_url"] == "http://old.example/wms"
    assert lyr["crs"] == "EPSG:900913" and lyr["bbox"] == [9.0, -85.0, 10.0, -84.0]


@pytest.mark.parametrize("xml, msg", [
    (b"<html>nope</html>", "layer list"),
    (b"not xml", "layer list"),
    (b"<ServiceExceptionReport><ServiceException>Bad map</ServiceException></ServiceExceptionReport>", "Bad map"),
])
def test_parse_capabilities_errors(xml, msg):
    with pytest.raises(WmsError, match=msg):
        parse_capabilities(xml, "http://x")


def test_base_url_strips_wms_params():
    assert base_url(" http://h/cgi?map=a.map&SERVICE=WMS&request=GetCapabilities&version=1.1.1 ") == "http://h/cgi?map=a.map"
    with pytest.raises(WmsError):
        base_url("ftp://h/wms")


def test_getmap_url():
    assert tile_bbox(0, 0, 0) == pytest.approx((-20037508.34, -20037508.34, 20037508.34, 20037508.34))
    lyr = {"version": "1.3.0", "name": "L", "format": "image/png", "crs": "EPSG:3857", "get_url": "https://m/wms?map=o"}
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(getmap_url(lyr, 1, 1, 0)).query))
    assert q["map"] == "o" and q["CRS"] == "EPSG:3857" and q["LAYERS"] == "L" and q["WIDTH"] == "256"
    assert q["BBOX"] == "0.000,0.000,20037508.343,20037508.343"
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(getmap_url({**lyr, "version": "1.1.1"}, 1, 1, 0)).query))
    assert "SRS" in q and "CRS" not in q


def tile_of(lon, lat, z):
    import math
    n = 2**z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return z, x, y


class Resp(io.BytesIO):
    def __init__(self, data, ctype):
        super().__init__(data)
        self.headers = Message()
        self.headers["Content-Type"] = ctype

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def test_store_add_tile_remove(tmp_path):
    calls = []

    def opener(req):
        calls.append(req.full_url)
        return Resp(b"PNGDATA", "image/png") if "LAYERS=Mosaico5000" in req.full_url else Resp(b"<x/>", "text/xml")

    caps = parse_capabilities(CAPS_130, "https://maps.example/wms")
    caps["url"] = "https://maps.example/wms"
    store = WmsStore(tmp_path / "w.json", tmp_path / "cache", opener)
    item = store.add(caps, "Mosaico5000")
    assert item["id"].startswith("w") and item["title"] == "Mosaico 2005-2007"
    assert store.add(caps, "Mosaico5000")["id"] == item["id"] and len(store.list()) == 1  # no duplicates
    # tile inside the layer's bounding box: fetched once, then from the disk cache
    z, x, y = tile_of(-84.0, 10.0, 15)
    assert store.tile(item["id"], z, x, y) == (b"PNGDATA", "image/png")
    assert store.tile(item["id"], z, x, y) == (b"PNGDATA", "image/png") and len(calls) == 1
    assert store.tile(item["id"], *tile_of(10, 50, 15)) is None and len(calls) == 1  # outside: not asked
    with pytest.raises(WmsError, match="no layer"):
        store.add(caps, "Nope")
    store.remove(item["id"])
    assert store.list() == []
    with pytest.raises(WmsError):
        store.remove(item["id"])


def test_store_refuses_layer_without_web_mercator(tmp_path):
    caps = parse_capabilities(CAPS_130.replace(b"<CRS>EPSG:3857</CRS>", b""), "https://m")
    caps["url"] = "https://m"
    with pytest.raises(WmsError, match="EPSG:3857"):
        WmsStore(tmp_path / "w.json", tmp_path / "c").add(caps, "Mosaico5000")


def test_service_error_tile_not_cached(tmp_path):
    caps = parse_capabilities(CAPS_130, "https://maps.example/wms")
    caps["url"] = "https://maps.example/wms"
    store = WmsStore(tmp_path / "w.json", tmp_path / "cache", lambda req: Resp(b"<ServiceException/>", "text/xml"))
    item = store.add(caps, "Mosaico5000")
    assert store.tile(item["id"], 15, 8483, 15421) is None
    assert not (tmp_path / "cache" / "wms").exists()
    json.dumps(store.list())
