"""WMS layers by URL: read a service's layer list, keep the chosen layers, and
fetch map tiles from them in the web map projection (EPSG:3857).

Tiles go through the same disk cache as the base maps, so areas viewed at home
stay available offline. Standard library only.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

WEB_MERCATOR = ("EPSG:3857", "EPSG:900913", "EPSG:102100", "EPSG:102113")
HALF = 20037508.342789244  # half the width of the EPSG:3857 world, metres
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


class WmsError(ValueError):
    pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _local(c.tag) == name), None)


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _text(el, name, default="") -> str:
    c = _child(el, name) if el is not None else None
    return (c.text or "").strip() if c is not None and c.text else default


def base_url(url: str) -> str:
    """The service URL without WMS request parameters (other parameters, e.g. map=, stay)."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise WmsError("the WMS address must start with http:// or https://")
    parts = urllib.parse.urlsplit(url)
    keep = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in ("service", "request", "version", "layers", "bbox", "width", "height",
                                 "format", "crs", "srs", "styles", "transparent")]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(keep), fragment=""))


def _with(url: str, params: dict) -> str:
    parts = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + list(params.items())
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(q)))


def parse_capabilities(xml: bytes, service_url: str) -> dict:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        raise WmsError("the address didn't answer with a WMS layer list (GetCapabilities)") from e
    if _local(root.tag) not in ("WMS_Capabilities", "WMT_MS_Capabilities"):
        if _local(root.tag) in ("ServiceExceptionReport", "ExceptionReport"):
            raise WmsError(f"the service refused: {' '.join(root.itertext()).strip()[:200]}")
        raise WmsError("the address didn't answer with a WMS layer list (GetCapabilities)")
    version = root.get("version", "1.3.0")
    service = _child(root, "Service")
    cap = _child(root, "Capability")
    request = _child(cap, "Request") if cap is not None else None
    getmap = _child(request, "GetMap") if request is not None else None
    formats = [f.text.strip() for f in _children(getmap, "Format") if f.text] if getmap is not None else []
    fmt = next((f for f in ("image/png", "image/jpeg") if f in formats), formats[0] if formats else "image/png")
    href = None
    for el in getmap.iter() if getmap is not None else []:  # DCPType/HTTP/Get/OnlineResource
        if _local(el.tag) == "OnlineResource":
            href = next((v for k, v in el.attrib.items() if _local(k) == "href"), None)
            if href:
                break
    get_url = base_url(href) if href and href.startswith(("http://", "https://")) else service_url

    layers = []

    def walk(el, inherited_crs, inherited_bbox):
        # CRS/SRS are inherited from parent layers; 1.1.1 may list several codes in one SRS element
        crs = inherited_crs | {code for c in el if _local(c.tag) in ("CRS", "SRS") and c.text
                               for code in c.text.upper().split()}
        bbox = inherited_bbox
        geo = _child(el, "EX_GeographicBoundingBox")
        if geo is not None:
            try:
                bbox = [float(_text(geo, "southBoundLatitude")), float(_text(geo, "westBoundLongitude")),
                        float(_text(geo, "northBoundLatitude")), float(_text(geo, "eastBoundLongitude"))]
            except ValueError:
                pass
        ll = _child(el, "LatLonBoundingBox")
        if ll is not None:
            try:
                bbox = [float(ll.get("miny")), float(ll.get("minx")), float(ll.get("maxy")), float(ll.get("maxx"))]
            except (TypeError, ValueError):
                pass
        name = _text(el, "Name")
        if name:
            code = next((c for c in WEB_MERCATOR if c in crs), None)
            layers.append({"name": name, "title": _text(el, "Title", name), "abstract": _text(el, "Abstract")[:400],
                           "crs": code, "bbox": bbox})
        for child in _children(el, "Layer"):
            walk(child, crs, bbox)

    for top in _children(cap, "Layer") if cap is not None else []:
        walk(top, set(), None)
    if not layers:
        raise WmsError("the service lists no layers that can be shown")
    return {"title": _text(service, "Title", "WMS"), "version": version, "format": fmt, "get_url": get_url,
            "layers": layers}


def fetch_capabilities(url: str, opener=None) -> dict:
    service_url = base_url(url)
    full = _with(service_url, {"SERVICE": "WMS", "REQUEST": "GetCapabilities", "VERSION": "1.3.0"})
    open_ = opener or (lambda req: urllib.request.urlopen(req, timeout=20))
    try:
        with open_(urllib.request.Request(full, headers={"User-Agent": "anafi-mission-planner"})) as r:
            data = r.read(20_000_000)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise WmsError(f"couldn't reach the WMS service ({getattr(e, 'reason', e)})") from e
    caps = parse_capabilities(data, service_url)
    caps["url"] = service_url
    return caps


def tile_bbox(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    size = 2 * HALF / 2**z
    minx, maxy = -HALF + x * size, HALF - y * size
    return minx, maxy - size, minx + size, maxy


def getmap_url(layer: dict, z: int, x: int, y: int) -> str:
    v13 = layer["version"].startswith("1.3")
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": layer["version"], "LAYERS": layer["name"],
              "STYLES": "", "FORMAT": layer["format"], "TRANSPARENT": "TRUE", "WIDTH": "256", "HEIGHT": "256",
              ("CRS" if v13 else "SRS"): layer["crs"],
              "BBOX": ",".join(f"{v:.3f}" for v in tile_bbox(z, x, y))}
    return _with(layer["get_url"], params)


class WmsStore:
    """The WMS layers you added, kept in a small JSON file."""

    def __init__(self, path: str | Path, cache: Path, opener=None):
        self.path = Path(path)
        self.cache = Path(cache)
        self._open = opener or (lambda req: urllib.request.urlopen(req, timeout=15))
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def _save(self, layers: list[dict]) -> None:
        self.path.write_text(json.dumps(layers, indent=2) + "\n")

    def add(self, caps: dict, name: str) -> dict:
        layer = next((lyr for lyr in caps.get("layers", []) if lyr["name"] == name), None)
        if layer is None:
            raise WmsError(f"no layer {name!r} in that service")
        if not layer["crs"]:
            raise WmsError("that layer isn't offered in the web map projection (EPSG:3857), so it can't be shown")
        ident = "w" + hashlib.sha1(f"{caps['get_url']}|{name}".encode()).hexdigest()[:10]
        item = {"id": ident, "title": layer["title"], "name": name, "abstract": layer["abstract"],
                "bbox": layer["bbox"], "service": caps["title"], "url": caps["url"], "get_url": caps["get_url"],
                "version": caps["version"], "format": caps["format"], "crs": layer["crs"]}
        with self._lock:
            layers = [lyr for lyr in self.list() if lyr["id"] != ident] + [item]
            self._save(layers)
        return item

    def remove(self, ident: str) -> None:
        with self._lock:
            layers = self.list()
            if not any(lyr["id"] == ident for lyr in layers):
                raise WmsError(f"no WMS layer {ident!r}")
            self._save([lyr for lyr in layers if lyr["id"] != ident])

    def tile(self, ident: str, z: int, x: int, y: int) -> tuple[bytes, str] | None:
        layer = next((lyr for lyr in self.list() if lyr["id"] == ident), None)
        if layer is None:
            return None
        if layer.get("bbox"):  # outside the layer: nothing to ask for
            s, w, n, e = layer["bbox"]
            k = 2.0 ** z
            tw, te = x / k * 360 - 180, (x + 1) / k * 360 - 180
            if te < w or tw > e:
                return None
            lat = lambda yy: math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / k))))  # noqa: E731
            if lat(y) < s or lat(y + 1) > n:
                return None
        folder = self.cache / "wms" / ident / str(z) / str(x)
        for ctype, ext in IMAGE_TYPES.items():
            if (folder / f"{y}{ext}").exists():
                return (folder / f"{y}{ext}").read_bytes(), ctype
        try:
            req = urllib.request.Request(getmap_url(layer, z, x, y), headers={"User-Agent": "anafi-mission-planner"})
            with self._open(req) as r:
                data, ctype = r.read(), r.headers.get_content_type()
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
        ext = IMAGE_TYPES.get(ctype)
        if ext is None:  # a ServiceException (XML), not an image
            return None
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{y}{ext}").write_bytes(data)
        return data, ctype

