"""Flight plan upload/download through the drone's web server.

Endpoints (https://developer.parrot.com/docs/webserver-api/module_upload.html):
  PUT /api/v1/upload/flightplan             body = .mavlink file, 200 -> "<uid>"
  GET /api/v1/upload/flightplans            -> [{"uid": ..., "date": ...}]
  GET /api/v1/upload/flightplan/<uid>       -> file content
  GET /api/v1/upload/flightplan/current/info -> {"uid": ..., "date": ...} or 404
There is no delete; the drone drops the oldest plans when it has too many.
The docs target newer firmware, so uploads try a couple of header variants.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from planner.mavlink import parse_mission, validate_mission

DRONE_HOST = "192.168.42.1"  # direct Wi-Fi to the drone
SKYCONTROLLER_HOST = "192.168.53.1"  # Skycontroller 3 over USB

API = "/api/v1/upload"

# Tried in order until the drone accepts one. The second is what
# anafi-ai-survival-kit uses successfully with an Anafi Ai.
UPLOAD_VARIANTS: list[tuple[str, dict[str, str]]] = [
    ("raw", {"Content-Type": "application/octet-stream"}),
    ("survival-kit", {
        "Accept": "application/json, text/javascript, text/plain */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Content-type": "application/json; charset=UTF-8; application/gzip",
    }),
]


class DroneError(Exception):
    pass


class NotReachable(DroneError):
    pass


class Rejected(DroneError):
    def __init__(self, status: int, body: bytes, what: str):
        self.status = status
        self.body = body
        detail = body.decode(errors="replace").strip()[:200]
        super().__init__(f"{what}: HTTP {status}" + (f" ({detail})" if detail else ""))


@dataclass
class UploadResult:
    uid: str
    variant: str


class FlightPlanClient:
    def __init__(self, host: str = DRONE_HOST, timeout: float = 5.0):
        self.host = host
        self.timeout = timeout
        self.base = f"http://{host}"

    def _request(self, method: str, path: str, body: bytes | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            reason = getattr(e, "reason", e)
            raise NotReachable(f"drone not reachable at {self.host}: {reason}") from e

    def _get(self, path: str, what: str) -> bytes:
        status, body = self._request("GET", path)
        if status != 200:
            raise Rejected(status, body, what)
        return body

    def list(self) -> list[dict]:
        return json.loads(self._get(f"{API}/flightplans", "list flight plans") or b"[]")

    def download(self, uid: str) -> bytes:
        return self._get(f"{API}/flightplan/{urllib.parse.quote(uid, safe='')}", f"download {uid}")

    def current(self) -> dict | None:
        status, body = self._request("GET", f"{API}/flightplan/current/info")
        if status == 404:
            return None
        if status != 200:
            raise Rejected(status, body, "current flight plan")
        return json.loads(body)

    def upload(self, data: bytes) -> UploadResult:
        last: Rejected | None = None
        for name, headers in UPLOAD_VARIANTS:
            status, body = self._request("PUT", f"{API}/flightplan", data, headers)
            if status == 200:
                return UploadResult(uid=parse_uid(body), variant=name)
            last = Rejected(status, body, "upload")
            if status not in (400, 415):
                break
        assert last is not None
        raise last


def parse_uid(body: bytes) -> str:
    text = body.decode().strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = text.strip('"')
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise DroneError(f"unexpected upload response: {text[:200]!r}")
    return value


@dataclass
class UploadRecord:
    uid: str
    file: str
    sha256: str
    size: int
    host: str
    date: str
    variant: str
    readback: str  # identical | equivalent | mismatch
    listed: bool


def upload_and_verify(client: FlightPlanClient, path: str | Path, force: bool = False) -> UploadRecord:
    """Validate, upload, read back and confirm the plan is listed."""
    path = Path(path)
    data = path.read_bytes()
    items = parse_mission(data.decode())
    problems = validate_mission(items)
    if problems and not force:
        raise DroneError("mission has problems (use --force to upload anyway):\n  " + "\n  ".join(problems))

    result = client.upload(data)
    back = client.download(result.uid)
    if back == data:
        readback = "identical"
    else:
        try:
            readback = "equivalent" if parse_mission(back.decode()) == items else "mismatch"
        except (ValueError, UnicodeDecodeError):
            readback = "mismatch"
    listed = any(p.get("uid") == result.uid for p in client.list())
    return UploadRecord(
        uid=result.uid, file=str(path), sha256=hashlib.sha256(data).hexdigest(), size=len(data),
        host=client.host, date=dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        variant=result.variant, readback=readback, listed=listed,
    )


def append_log(record: UploadRecord, log_path: str | Path) -> None:
    log_path = Path(log_path)
    records = json.loads(log_path.read_text()) if log_path.exists() else []
    records.append(asdict(record))
    log_path.write_text(json.dumps(records, indent=2) + "\n")


def pull_all(client: FlightPlanClient, out_dir: str | Path) -> list[Path]:
    """Download every flight plan on the drone into out_dir (skips files already there)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plans = client.list()
    (out / "index.json").write_text(json.dumps(plans, indent=2) + "\n")
    written = []
    for p in plans:
        dest = out / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', p['uid'])}.mavlink"
        if not dest.exists():
            dest.write_bytes(client.download(p["uid"]))
            written.append(dest)
    return written
