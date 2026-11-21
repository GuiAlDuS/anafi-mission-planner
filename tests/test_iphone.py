import contextlib
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
    monkeypatch.setenv(iphone.BACKEND_ENV, "afcclient")
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
    monkeypatch.setenv(iphone.BACKEND_ENV, "afcclient")
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


# --------------------------------------------------------------------------- pymobiledevice3 backend


class FakeAsyncAfc:
    """Stands in for pymobiledevice3's HouseArrestService rooted in FreeFlight's Documents."""

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.dirs = {iphone.PLANS_DIR, iphone.RUNS_DIR}

    async def listdir(self, path):
        prefix = path.rstrip("/") + "/"
        return sorted({p[len(prefix):].split("/")[0] for p in list(self.files) + list(self.dirs)
                       if p.startswith(prefix)})

    async def makedirs(self, path):
        self.dirs.add(path)

    async def set_file_contents(self, path, data):
        self.files[path] = data

    async def get_file_contents(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


@pytest.fixture
def pmd3(monkeypatch):
    monkeypatch.setenv(iphone.BACKEND_ENV, "pymobiledevice3")
    fake = FakeAsyncAfc({f"{iphone.PLANS_DIR}/AAAA/savedPlan.json": json.dumps({"title": "Prueba"}).encode()})

    @contextlib.asynccontextmanager
    async def documents():
        yield fake

    async def connected():
        return {"udid": "UDID1", "name": "My iPhone"}

    monkeypatch.setattr(iphone._PMD3, "_documents", documents)
    monkeypatch.setattr(iphone._PMD3, "_connected", connected)
    return fake


def test_pmd3_connected_and_list(pmd3):
    assert iphone.connected() == {"udid": "UDID1", "name": "My iPhone"}
    assert iphone.list_plans() == [{"uuid": "AAAA", "title": "Prueba"}]


def test_pmd3_send_plan_writes_all_files(pmd3):
    folder = iphone.plan_uuid("north", 1)
    files = {"savedPlan.json": b"{}", "flightPlan.mavlink": b"QGC WPL 120\n", "screenshot.png": b"png"}
    iphone.send_plan(folder, files)
    for name, data in files.items():
        assert pmd3.files[f"{iphone.PLANS_DIR}/{folder}/{name}"] == data


def test_pmd3_pull_runs_copies_only_new_logs(pmd3, tmp_path):
    pmd3.files[f"{iphone.RUNS_DIR}/a_0001.json"] = b"{}"
    pmd3.files[f"{iphone.RUNS_DIR}/a_0002.json"] = b'{"x": 1}'
    (tmp_path / "a_0001.json").write_text("{}")
    assert [p.name for p in iphone.pull_runs(tmp_path)] == ["a_0002.json"]
    assert (tmp_path / "a_0002.json").read_bytes() == b'{"x": 1}'


def test_pmd3_errors_become_phone_errors(monkeypatch):
    ex = pytest.importorskip("pymobiledevice3.exceptions")
    monkeypatch.setenv(iphone.BACKEND_ENV, "pymobiledevice3")

    @contextlib.asynccontextmanager
    async def documents():
        raise ex.AppNotInstalledError()
        yield

    monkeypatch.setattr(iphone._PMD3, "_documents", documents)
    with pytest.raises(iphone.PhoneError, match="isn't installed"):
        iphone.list_plans()


def test_backend_choice(monkeypatch):
    monkeypatch.setenv(iphone.BACKEND_ENV, "bogus")
    with pytest.raises(iphone.PhoneError, match="must be"):
        iphone.connected()
    monkeypatch.delenv(iphone.BACKEND_ENV)
    monkeypatch.setattr(iphone.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(iphone.shutil, "which", lambda tool: None)
    with pytest.raises(iphone.PhoneError, match="iphone"):
        iphone.connected()
    monkeypatch.setattr(iphone.shutil, "which", lambda tool: "/usr/bin/" + tool)
    assert iphone._backend() is iphone._AFCCLIENT
    monkeypatch.setattr(iphone.importlib.util, "find_spec", lambda name: object())
    assert iphone._backend() is iphone._PMD3
