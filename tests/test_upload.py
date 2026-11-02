"""Upload client against a fake drone web server that follows Parrot's documented API."""

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from drone.__main__ import main as drone_main
from drone.upload import (FlightPlanClient, NotReachable, Rejected, append_log, parse_uid, pull_all,
                          upload_and_verify)
from planner.geo import LatLon
from planner.mavlink import format_mission, tiny_mission

API = "/api/v1/upload"


class FakeDrone:
    def __init__(self):
        self.plans: dict[str, bytes] = {}
        self.current: str | None = None
        self.accept_content_type: str | None = None  # only accept uploads with this header
        self.reject_all = False
        self.rewrite = None  # optional callable applied to stored bytes
        self.requests: list[tuple[str, str, dict]] = []


def _handler(drone: FakeDrone):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body=b""):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            drone.requests.append(("GET", self.path, dict(self.headers)))
            if self.path == f"{API}/flightplans":
                lst = [{"uid": u, "date": "20261021T120000-0600"} for u in drone.plans]
                return self._send(200, json.dumps(lst).encode())
            if self.path == f"{API}/flightplan/current/info":
                if drone.current is None:
                    return self._send(404)
                return self._send(200, json.dumps({"uid": drone.current, "date": "x"}).encode())
            prefix = f"{API}/flightplan/"
            if self.path.startswith(prefix):
                uid = self.path[len(prefix):]
                if uid in drone.plans:
                    return self._send(200, drone.plans[uid])
                return self._send(404)
            self._send(400)

        def do_PUT(self):
            drone.requests.append(("PUT", self.path, dict(self.headers)))
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path != f"{API}/flightplan":
                return self._send(400)
            ctype = self.headers.get("Content-Type", "")
            if drone.reject_all or (drone.accept_content_type and drone.accept_content_type not in ctype):
                return self._send(415)
            uid = uuid.uuid4().hex
            drone.plans[uid] = drone.rewrite(body) if drone.rewrite else body
            self._send(200, json.dumps(uid).encode())

    return H


@pytest.fixture
def fake():
    drone = FakeDrone()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(drone))
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    drone.host = f"127.0.0.1:{server.server_address[1]}"
    yield drone
    server.shutdown()
    server.server_close()


@pytest.fixture
def mission_file(tmp_path, home):
    p = tmp_path / "tiny.mavlink"
    p.write_text(format_mission(tiny_mission(home, LatLon(home.lat + 0.0001, home.lon), 10)))
    return p


def test_parse_uid():
    assert parse_uid(b'"68238cd792d215bdfdddc7bbb6d10db4"') == "68238cd792d215bdfdddc7bbb6d10db4"
    assert parse_uid(b"68238cd7\n") == "68238cd7"
    with pytest.raises(Exception):
        parse_uid(b"")
    with pytest.raises(Exception):
        parse_uid(b'{"error": 1}')


def test_upload_and_verify(fake, mission_file):
    c = FlightPlanClient(fake.host)
    rec = upload_and_verify(c, mission_file)
    assert rec.uid in fake.plans
    assert rec.variant == "raw"
    assert rec.readback == "identical" and rec.listed
    assert fake.plans[rec.uid] == mission_file.read_bytes()


def test_header_fallback(fake, mission_file):
    fake.accept_content_type = "application/json"
    rec = upload_and_verify(FlightPlanClient(fake.host), mission_file)
    assert rec.variant == "survival-kit"
    assert [r[0] for r in fake.requests].count("PUT") == 2


def test_rejected_upload(fake, mission_file):
    fake.reject_all = True
    with pytest.raises(Rejected) as e:
        upload_and_verify(FlightPlanClient(fake.host), mission_file)
    assert e.value.status == 415


def test_readback_equivalent_when_drone_reformats(fake, mission_file):
    fake.rewrite = lambda b: b.replace(b"\t", b"  ").replace(b"\n", b"\r\n")
    rec = upload_and_verify(FlightPlanClient(fake.host), mission_file)
    assert rec.readback == "equivalent"


def test_readback_mismatch(fake, mission_file):
    fake.rewrite = lambda b: b.replace(b"10.000000", b"99.000000")
    rec = upload_and_verify(FlightPlanClient(fake.host), mission_file)
    assert rec.readback == "mismatch"


def test_invalid_mission_refused_before_upload(fake, tmp_path):
    p = tmp_path / "bad.mavlink"
    p.write_text("QGC WPL 120\n0\t1\t3\t22\t0\t0\t0\t0\t9.98\t-78.70\t10\t1\n1\t0\t2\t21\t0\t0\t0\t0\t0\t0\t0\t1\n")
    with pytest.raises(Exception, match="NAV_WAYPOINT"):
        upload_and_verify(FlightPlanClient(fake.host), p)
    assert not fake.plans
    assert upload_and_verify(FlightPlanClient(fake.host), p, force=True).uid in fake.plans


def test_not_reachable():
    with pytest.raises(NotReachable):
        FlightPlanClient("127.0.0.1:1", timeout=1).list()


def test_current_and_pull_all(fake, mission_file, tmp_path):
    c = FlightPlanClient(fake.host)
    assert c.current() is None
    uid = upload_and_verify(c, mission_file).uid
    fake.current = uid
    assert c.current()["uid"] == uid
    out = tmp_path / "plans"
    assert len(pull_all(c, out)) == 1
    assert (out / f"{uid}.mavlink").read_bytes() == mission_file.read_bytes()
    assert json.loads((out / "index.json").read_text())[0]["uid"] == uid
    assert pull_all(c, out) == []  # already downloaded


def test_log_appends(fake, mission_file, tmp_path):
    c = FlightPlanClient(fake.host)
    log = tmp_path / "uploads.json"
    for _ in range(2):
        append_log(upload_and_verify(c, mission_file), log)
    records = json.loads(log.read_text())
    assert len(records) == 2 and records[0]["uid"] != records[1]["uid"]
    assert records[0]["file"] == str(mission_file)


def test_cli(fake, mission_file, tmp_path, capsys):
    log = tmp_path / "uploads.json"
    assert drone_main(["--host", fake.host, "ping"]) == 0
    assert drone_main(["--host", fake.host, "upload", str(mission_file), "--log", str(log)]) == 0
    uid = json.loads(log.read_text())[0]["uid"]
    assert f"UID: {uid}" in capsys.readouterr().out
    assert drone_main(["--host", fake.host, "list"]) == 0
    assert uid in capsys.readouterr().out
    assert drone_main(["--host", "127.0.0.1:1", "--timeout", "1", "ping"]) == 2
    assert "Wi-Fi" in capsys.readouterr().err
