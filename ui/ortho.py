"""Your own orthophotos (e.g. WebODM's orthophoto.tif) as map layers over the base map.

Files live in one folder (default `orthophotos/`): load them from the GUI or
drop GeoTIFFs there. Each gets a sidecar `<name>.json` with what the map needs
(bounds, date, sharpness, zoom range), read once. Tiles are cut on request
with rio-tiler; overviews are added to our copy if the file has none, so
zoomed-out views stay fast on big files.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
import threading
from pathlib import Path

SUFFIXES = (".tif", ".tiff")
MAX_BYTES = 8 * 1024**3


class OrthoError(ValueError):
    pass


def _ident(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:60]
    return stem or "orthophoto"


def _max_zoom(res_m: float, lat: float) -> int:
    """Deepest web-map zoom worth asking for: where a screen pixel matches a photo pixel."""
    z = math.log2(156543.03 * math.cos(math.radians(lat)) / max(res_m, 0.005))
    return max(12, min(23, round(z)))


def date_in_name(name: str) -> str | None:
    """A date written in a file name, e.g. 2022_03_27_orthomosaic.tif or ortho-2026-11-01.tif."""
    for m in re.finditer(r"(?<!\d)((?:19|20)\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])(?!\d)", name):
        try:
            return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            continue
    return None


def read_metadata(path: Path, file_date: str | None = None, name: str | None = None) -> dict:
    import rasterio
    from rasterio.warp import transform_bounds

    try:
        with rasterio.open(path) as ds:
            if ds.crs is None:
                raise OrthoError("this file has no georeferencing (GeoTIFF with a coordinate system needed)")
            if ds.count < 3 or ds.dtypes[0] != "uint8":
                raise OrthoError("expected an 8-bit RGB GeoTIFF, like WebODM's orthophoto")
            w, s, e, n = transform_bounds(ds.crs, "EPSG:4326", *ds.bounds)
            lat = (s + n) / 2
            if ds.crs.is_geographic:
                res = abs(ds.transform.a) * 111_320 * math.cos(math.radians(lat))
            else:
                res = abs(ds.transform.a) * (ds.crs.linear_units_factor[1] if ds.crs.linear_units_factor else 1)
            stamp = ds.tags().get("TIFFTAG_DATETIME", "")
    except rasterio.RasterioIOError as e:
        raise OrthoError(f"not a readable GeoTIFF ({e})") from e
    m = re.match(r"(\d{4})[:-](\d{2})[:-](\d{2})", stamp)
    if m:
        date, kind = f"{m[1]}-{m[2]}-{m[3]}", "image"
    elif named := date_in_name(name or path.name):
        date, kind = named, "name"
    else:
        date = file_date or dt.date.fromtimestamp(path.stat().st_mtime).isoformat()
        kind = "file"
    return {"bounds": [s, w, n, e], "resolution_m": round(res, 4), "date": date, "date_kind": kind,
            "max_zoom": _max_zoom(res, lat), "size_mb": round(path.stat().st_size / 1e6, 1)}


def ensure_overviews(path: Path) -> bool:
    """Add zoomed-out copies (overviews) if the file has none. Files that already have them,
    such as Cloud Optimized GeoTIFFs from WebODM, are left untouched. True if the file has them."""
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(path) as ds:  # look first, read-only: GDAL refuses to update a COG
        if ds.overviews(1):
            return True
        factors = [f for f in (2, 4, 8, 16, 32, 64, 128) if max(ds.width, ds.height) / f >= 256]
    if not factors:
        return True  # small enough without
    try:
        with rasterio.open(path, "r+", IGNORE_COG_LAYOUT_BREAK="YES") as ds:
            ds.build_overviews(factors, Resampling.average)
        return True
    except rasterio.RasterioIOError:
        return False  # still usable, just slower when zoomed out


class OrthoStore:
    def __init__(self, folder: str | Path):
        self.dir = Path(folder)
        self._lock = threading.Lock()

    def _files(self) -> dict[str, Path]:
        if not self.dir.exists():
            return {}
        return {_ident(p.name): p for p in sorted(self.dir.iterdir())
                if p.suffix.lower() in SUFFIXES and not p.name.startswith(".")}

    def _meta(self, ident: str, path: Path) -> dict | None:
        side = path.with_suffix(".json")
        stat = path.stat()
        if side.exists():
            meta = json.loads(side.read_text())
            if meta.get("_mtime") == stat.st_mtime and meta.get("_size") == stat.st_size:
                return meta
        try:
            meta = read_metadata(path)
        except OrthoError as e:
            return {"id": ident, "name": path.stem, "error": str(e)}
        meta.update(id=ident, name=path.stem, _mtime=path.stat().st_mtime, _size=path.stat().st_size)
        side.write_text(json.dumps(meta))
        return meta

    def list(self) -> list[dict]:
        with self._lock:
            out = [self._meta(i, p) for i, p in self._files().items()]
        return [{k: v for k, v in m.items() if not k.startswith("_")} for m in out if m]

    def add(self, filename: str, stream, length: int, file_date: str | None = None) -> dict:
        if Path(filename).suffix.lower() not in SUFFIXES:
            raise OrthoError("load a GeoTIFF (.tif): in WebODM, download the orthophoto as GeoTIFF")
        if not 0 < length <= MAX_BYTES:
            raise OrthoError("file is empty or larger than 8 GB")
        self.dir.mkdir(parents=True, exist_ok=True)
        ident = _ident(filename)
        with self._lock:
            taken = self._files()
            base, n = ident, 2
            while ident in taken:
                ident, n = f"{base}_{n}", n + 1
        tmp = self.dir / f".{ident}.part"
        final = self.dir / f"{ident}.tif"
        try:
            left = length
            with open(tmp, "wb") as f:
                while left:
                    chunk = stream.read(min(left, 1 << 20))
                    if not chunk:
                        raise OrthoError("the upload stopped before the end of the file")
                    f.write(chunk)
                    left -= len(chunk)
            meta = read_metadata(tmp, file_date, filename)  # refuse non-orthophotos before keeping them
            ensure_overviews(tmp)
            tmp.rename(final)
        finally:
            tmp.unlink(missing_ok=True)
        with self._lock:
            final.with_suffix(".json").unlink(missing_ok=True)
            out = self._meta(ident, final)
            if meta["date_kind"] != out.get("date_kind") or meta["date"] != out.get("date"):
                out.update(date=meta["date"], date_kind=meta["date_kind"])  # from the upload: original name/date
                final.with_suffix(".json").write_text(json.dumps(out))
        return {k: v for k, v in out.items() if not k.startswith("_")}

    def remove(self, ident: str) -> None:
        with self._lock:
            path = self._files().get(ident)
            if path is None:
                raise OrthoError(f"no orthophoto {ident!r}")
            path.unlink()
            path.with_suffix(".json").unlink(missing_ok=True)

    def tile(self, ident: str, z: int, x: int, y: int) -> bytes | None:
        """PNG tile (transparent outside the image), or None outside it."""
        from rio_tiler.errors import TileOutsideBounds
        from rio_tiler.io import Reader

        path = self._files().get(ident)
        if path is None:
            return None
        meta = self._meta(ident, path)
        if not meta or "bounds" not in meta:
            return None
        s, w, n, e = meta["bounds"]
        k = 2.0 ** z  # tile edges in degrees: skip tiles that don't touch the image
        tw, te = x / k * 360 - 180, (x + 1) / k * 360 - 180
        lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / k))))  # noqa: E731
        tn, ts = lat(y), lat(y + 1)
        if te < w or tw > e or tn < s or ts > n:
            return None
        try:
            with Reader(str(path)) as src:
                img = src.tile(x, y, z, indexes=(1, 2, 3))
        except TileOutsideBounds:
            return None
        return img.render(img_format="PNG")


def copy_into(store: OrthoStore, src: Path) -> dict:
    """Add a file already on disk (used by tests and scripts)."""
    with open(src, "rb") as f:
        return store.add(src.name, f, src.stat().st_size)

