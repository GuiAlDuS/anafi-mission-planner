import io
import json

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402

from ui.ortho import OrthoError, OrthoStore, copy_into, read_metadata  # noqa: E402

# A 400 x 300 px orthophoto at 5 cm/px near the test field, UTM 16N, RGBA like WebODM's.
ORIGIN = (752_050.0, 1_104_600.0)  # easting, northing of the top-left corner


def make_ortho(path, date=None, rgb=True, crs="EPSG:32617", dtype="uint8"):
    w, h = 400, 300
    bands = 4 if rgb else 1
    data = np.zeros((bands, h, w), dtype=dtype)
    data[0] = 200
    if rgb:
        data[1], data[2] = 120, 40
        data[3] = 255
    profile = dict(driver="GTiff", width=w, height=h, count=bands, dtype=dtype, crs=crs,
                   transform=from_origin(*ORIGIN, 0.05, 0.05))
    with rasterio.open(path, "w", **profile) as ds:
        ds.write(data)
        if date:
            ds.update_tags(TIFFTAG_DATETIME=date)
    return path


def test_read_metadata(tmp_path):
    m = read_metadata(make_ortho(tmp_path / "a.tif", date="2026:11:01 10:30:00"))
    s, w, n, e = m["bounds"]
    assert 9.97 < s < n < 9.99 and -78.71 < w < e < -78.69
    assert m["resolution_m"] == pytest.approx(0.05) and m["date"] == "2026-11-01" and m["date_kind"] == "image"
    assert 21 <= m["max_zoom"] <= 23
    m = read_metadata(make_ortho(tmp_path / "b.tif"), file_date="2026-10-30")
    assert m["date"] == "2026-10-30" and m["date_kind"] == "file"


@pytest.mark.parametrize("kw, msg", [({"rgb": False}, "8-bit RGB"), ({"crs": None}, "georeferencing")])
def test_read_metadata_rejects(tmp_path, kw, msg):
    with pytest.raises(OrthoError, match=msg):
        read_metadata(make_ortho(tmp_path / "x.tif", **kw))


def test_store_add_tile_remove(tmp_path):
    store = OrthoStore(tmp_path / "orthos")
    assert store.list() == []
    src = make_ortho(tmp_path / "Field 1 (WebODM).tif")
    data = src.read_bytes()
    meta = store.add(src.name, io.BytesIO(data), len(data), file_date="2026-11-01")
    assert meta["id"] == "Field_1_WebODM" and meta["date"] == "2026-11-01"
    assert [m["id"] for m in store.list()] == ["Field_1_WebODM"]
    assert not any(k.startswith("_") for k in store.list()[0])
    again = copy_into(store, src)  # same name again gets its own id
    assert again["id"] == "Field_1_WebODM_2"

    # a tile over the image is a PNG; one far away is None
    import morecantile
    tms = morecantile.tms.get("WebMercatorQuad")
    s, w, n, e = meta["bounds"]
    t = tms.tile((w + e) / 2, (s + n) / 2, 19)
    png = store.tile("Field_1_WebODM", t.z, t.x, t.y)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    far = tms.tile(0, 0, 19)
    assert store.tile("Field_1_WebODM", far.z, far.x, far.y) is None
    assert store.tile("nope", 19, 1, 1) is None

    store.remove("Field_1_WebODM_2")
    assert [m["id"] for m in store.list()] == ["Field_1_WebODM"]
    with pytest.raises(OrthoError):
        store.remove("Field_1_WebODM_2")


def test_store_refuses_bad_uploads(tmp_path):
    store = OrthoStore(tmp_path / "o")
    with pytest.raises(OrthoError, match="GeoTIFF"):
        store.add("photo.jpg", io.BytesIO(b"x"), 1)
    with pytest.raises(OrthoError, match="stopped"):
        store.add("a.tif", io.BytesIO(b"abc"), 10)
    bad = make_ortho(tmp_path / "gray.tif", rgb=False).read_bytes()
    with pytest.raises(OrthoError, match="8-bit RGB"):
        store.add("gray.tif", io.BytesIO(bad), len(bad))
    assert store.list() == [] and not list((tmp_path / "o").glob(".*part"))


def test_broken_file_in_folder_is_listed_with_error(tmp_path):
    d = tmp_path / "o"
    d.mkdir()
    (d / "junk.tif").write_bytes(b"not a tiff")
    [m] = OrthoStore(d).list()
    assert m["id"] == "junk" and "error" in m
    json.dumps(m)


def test_server_orthophoto_endpoints(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    import morecantile

    from ui import server

    srv = server.serve(port=0, battery_log=tmp_path / "b.json", orthophotos=tmp_path / "orthos")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, data=None, headers=None):
        req = urllib.request.Request(base + path, data=data, headers=headers or {})
        with urllib.request.urlopen(req) as r:
            return r.read(), r.headers.get_content_type()

    try:
        assert json.loads(call("/api/orthophotos")[0]) == {"available": True, "missing": server.ORTHO_MISSING,
                                                          "items": []}
        body = make_ortho(tmp_path / "ortho.tif").read_bytes()
        meta = json.loads(call("/api/orthophotos/upload", body, {"X-Filename": "My%20field.tif",
                                                                 "X-File-Date": "2026-11-01"})[0])
        assert meta["id"] == "My_field" and meta["date"] == "2026-11-01"
        s, w, n, e = meta["bounds"]
        t = morecantile.tms.get("WebMercatorQuad").tile((w + e) / 2, (s + n) / 2, 20)
        png, ctype = call(f"/tiles/ortho/My_field/{t.z}/{t.x}/{t.y}")
        assert ctype == "image/png" and png[:4] == b"\x89PNG"
        with pytest.raises(urllib.error.HTTPError) as err:
            call("/api/orthophotos/upload", b"not a tiff", {"X-Filename": "x.tif"})
        assert err.value.code == 400
        items = json.loads(call("/api/orthophotos/remove", json.dumps({"id": "My_field"}).encode(),
                                {"Content-Type": "application/json"})[0])["items"]
        assert items == []
    finally:
        srv.shutdown()
        srv.server_close()


def test_cloud_optimized_geotiff_is_accepted_untouched(tmp_path):
    """WebODM writes COGs; GDAL refuses to update them, and they already have overviews."""
    plain = make_ortho(tmp_path / "plain.tif")
    cog = tmp_path / "cog.tif"
    with rasterio.open(plain) as src:
        profile = src.profile | {"driver": "COG", "blockxsize": None, "blockysize": None, "tiled": None}
        with rasterio.open(cog, "w", **{k: v for k, v in profile.items() if v is not None}) as dst:
            dst.write(src.read())
    before = cog.read_bytes()
    store = OrthoStore(tmp_path / "o")
    meta = store.add("cog.tif", io.BytesIO(before), len(before))
    assert meta["id"] == "cog" and "error" not in meta
    assert (tmp_path / "o" / "cog.tif").read_bytes() == before  # not rewritten


def test_overviews_added_to_plain_big_file(tmp_path):
    from ui.ortho import ensure_overviews
    path = tmp_path / "big.tif"
    data = np.full((4, 1024, 1024), 128, dtype="uint8")
    with rasterio.open(path, "w", driver="GTiff", width=1024, height=1024, count=4, dtype="uint8",
                       crs="EPSG:32617", transform=from_origin(*ORIGIN, 0.05, 0.05)) as ds:
        ds.write(data)
    assert ensure_overviews(path)
    with rasterio.open(path) as ds:
        assert ds.overviews(1) == [2, 4]


@pytest.mark.parametrize("name, date", [
    ("2022_03_27_orthomosaic.tif", "2022-03-27"), ("ortho-2026-11-01.tif", "2026-11-01"),
    ("field_20250309.tif", "2025-03-09"), ("odm_orthophoto.tif", None), ("2022_13_40.tif", None),
    ("v12345678.tif", None),
])
def test_date_in_name(name, date):
    from ui.ortho import date_in_name
    assert date_in_name(name) == date


def test_upload_uses_date_in_original_name(tmp_path):
    data = make_ortho(tmp_path / "x.tif").read_bytes()
    meta = OrthoStore(tmp_path / "o").add("2022_03_27_orthomosaic.tif", io.BytesIO(data), len(data), "2022-03-28")
    assert meta["date"] == "2022-03-27" and meta["date_kind"] == "name"
    [listed] = OrthoStore(tmp_path / "o").list()
    assert listed["date"] == "2022-03-27"  # kept in the sidecar
