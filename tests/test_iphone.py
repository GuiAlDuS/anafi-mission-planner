import json
from pathlib import Path

import pytest

from drone import iphone


class FakeAfc:
    """Stands in for afcclient: a dict of path -> bytes for FreeFlight's Documents."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.dirs = {iphone.PLANS_DIR}

    def __call__(self, args, stdin=None, timeout=30):
        if args[0] == "idevice_id":
            return "UDID1\n"
        if args[0] == "ideviceinfo":
            return "My iPhone\n"
        out = []
        for cmd in (stdin or "").splitlines():
            op, *rest = cmd.split()
            if op == "ls":
                prefix = rest[0].rstrip("/") + "/"
                names = {p[len(prefix):].split("/")[0] for p in list(self.files) + list(self.dirs)
                         if p.startswith(prefix)}
                out += [f"-rw-r--r-- 1 mobile mobile 0 24 Oct 2026 10:00:00 {n}" for n in sorted(names)]
            elif op == "mkdir":
                self.dirs.add(rest[0])
            elif op == "put":
                self.files[rest[-1]] = Path(rest[-2]).read_bytes()
            elif op == "get":
                if rest[0] in self.files:
                    Path(rest[1]).write_bytes(self.files[rest[0]])
                else:
                    out.append(f"Error: Failed to get {rest[0]}: Object not found (8)")
        return "\n".join(out)


@pytest.fixture
def afc(monkeypatch):
    fake = FakeAfc({f"{iphone.PLANS_DIR}/AAAA/savedPlan.json": json.dumps({"title": "Prueba"}).encode()})
    monkeypatch.setattr(iphone, "_run", fake)
    monkeypatch.setattr(iphone.shutil, "which", lambda tool: "/usr/bin/" + tool)
    return fake


def test_plan_uuid_is_stable_per_mission_and_flight():
    assert iphone.plan_uuid("north_field", 1) == iphone.plan_uuid("north_field", 1)
    assert iphone.plan_uuid("north_field", 1) != iphone.plan_uuid("north_field", 2)
    assert len(iphone.plan_uuid("x", 1)) == 36 and iphone.plan_uuid("x", 1).isupper()


def test_connected_and_list(afc):
    assert iphone.connected() == {"udid": "UDID1", "name": "My iPhone"}
    assert iphone.list_plans() == [{"uuid": "AAAA", "title": "Prueba"}]


def test_send_plan_writes_all_files(afc):
    folder = iphone.plan_uuid("north", 1)
    files = {"savedPlan.json": b"{}", "flightPlan.mavlink": b"QGC WPL 120\n", "screenshot.png": b"png"}
    iphone.send_plan(folder, files)
    for name, data in files.items():
        assert afc.files[f"{iphone.PLANS_DIR}/{folder}/{name}"] == data


def test_send_plan_rejects_odd_folder_names(afc):
    with pytest.raises(iphone.PhoneError):
        iphone.send_plan("../../evil", {})


def test_errors_are_raised(monkeypatch):
    monkeypatch.setattr(iphone.shutil, "which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(iphone, "_run", lambda args, stdin=None, timeout=30: "ERROR: ApplicationLookupFailed")
    with pytest.raises(iphone.PhoneError, match="isn't installed"):
        iphone.list_plans()


def test_pull_runs_copies_only_new_logs(afc, tmp_path):
    afc.files[f"{iphone.RUNS_DIR}/a_0001.json"] = b"{}"
    afc.files[f"{iphone.RUNS_DIR}/a_0002.json"] = b"{}"
    (tmp_path / "a_0001.json").write_text("{}")
    assert [p.name for p in iphone.pull_runs(tmp_path)] == ["a_0002.json"]
    assert iphone.pull_runs(tmp_path) == []
