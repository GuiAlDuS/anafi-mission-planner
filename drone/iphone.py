"""Copy flight plans into FreeFlight 6 on an iPhone over USB (no iCloud).

FreeFlight shares its Documents folder (it shows in the Files app), so its
plans can be written through Apple's file-sharing service (AFC "house
arrest"), the same one Finder and iTunes use. Plans live in
/Documents/.Contents/FlightPlan/<UUID>/ as savedPlan.json, flightPlan.mavlink
and screenshot.png; FreeFlight lists new folders after it's restarted.

Two ways to reach the phone, picked automatically:

- **pymobiledevice3** (pure Python; Linux, macOS and Windows): installed with
  the `iphone` extra (`uv sync --extra iphone`). On Windows it also needs
  Apple's device drivers, which come with iTunes or the Apple Devices app.
- **libimobiledevice's `afcclient`** (Linux): the `libimobiledevice` and
  `usbmuxd` packages.

Set ANAFI_IPHONE_BACKEND=pymobiledevice3 or =afcclient to force one. Either
way the iPhone must be plugged in, unlocked, and "Trust this computer"
accepted once.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid as uuidlib
from pathlib import Path

FREEFLIGHT_APP = "com.parrot.freeflight6"
PLANS_DIR = "/Documents/.Contents/FlightPlan"
RUNS_DIR = "/Documents/.Contents/runs"  # FreeFlight's flight logs, one JSON per flight
# Fixed namespace so a mission name and flight number always map to the same plan folder:
# sending an edited mission again replaces the plan in FreeFlight instead of adding a copy.
PLAN_NAMESPACE = uuidlib.UUID("6f0b6a52-2d55-4a52-9c3e-0d1f5e3a8a11")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

NOT_TRUSTED = "the iPhone is plugged in but not trusted yet: unlock it and tap Trust"
NO_PHONE = "no iPhone found: plug it in, unlock it and tap Trust"
NO_FREEFLIGHT = "FreeFlight 6 isn't installed on this iPhone"
BACKEND_ENV = "ANAFI_IPHONE_BACKEND"


class PhoneError(Exception):
    pass


def plan_uuid(mission: str, flight: int) -> str:
    return str(uuidlib.uuid5(PLAN_NAMESPACE, f"{mission}/{flight}")).upper()


# --------------------------------------------------------------------------- backend choice


def _install_hint() -> str:
    if sys.platform == "win32":
        return ("iPhone transfer needs the `iphone` extra (`uv sync --extra iphone`) and Apple's "
                "device drivers (install iTunes or the Apple Devices app)")
    if sys.platform == "darwin":
        return "iPhone transfer needs the `iphone` extra: `uv sync --extra iphone`"
    return ("iPhone transfer needs the `iphone` extra (`uv sync --extra iphone`) "
            "or the libimobiledevice and usbmuxd packages")


def _backend():
    """The transfer backend: forced by ANAFI_IPHONE_BACKEND, else pymobiledevice3, else afcclient."""
    forced = os.environ.get(BACKEND_ENV, "").strip().lower()
    if forced == "afcclient":
        return _AFCCLIENT
    if forced == "pymobiledevice3":
        return _PMD3
    if forced:
        raise PhoneError(f"{BACKEND_ENV} must be 'pymobiledevice3' or 'afcclient', not {forced!r}")
    if importlib.util.find_spec("pymobiledevice3") is not None:
        return _PMD3
    if shutil.which("afcclient") is not None:
        return _AFCCLIENT
    raise PhoneError(_install_hint())


# --------------------------------------------------------------------------- afcclient (Linux)


def _run(args: list[str], stdin: str | None = None, timeout: float = 30) -> str:
    tool = args[0]
    if shutil.which(tool) is None:
        raise PhoneError(f"`{tool}` not found: install libimobiledevice and usbmuxd")
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise PhoneError(f"`{tool}` didn't answer in {timeout:.0f} s; unplug and replug the iPhone") from e
    return ANSI.sub("", r.stdout + r.stderr)


def _afc(commands: list[str], timeout: float = 60) -> str:
    """Run afcclient commands in FreeFlight's Documents; raise on any error line."""
    # afcclient spins on end of input, so always finish with an explicit exit.
    out = _run(["afcclient", "--documents", FREEFLIGHT_APP], "\n".join(commands + ["exit"]) + "\n", timeout)
    if "Could not connect" in out or "No device found" in out:
        raise PhoneError(NO_PHONE)
    if "ApplicationLookupFailed" in out:
        raise PhoneError(NO_FREEFLIGHT)
    errors = [ln for ln in out.splitlines() if ln.startswith(("Error", "ERROR"))]
    if errors:
        raise PhoneError("; ".join(errors))
    return out


def _listing(out: str) -> list[str]:
    return [ln.split()[-1] for ln in out.splitlines() if ln and not ln.startswith(("afc:", "Error"))]


class _Afcclient:
    """libimobiledevice command-line tools; each call batches its commands into one afcclient run."""

    def connected(self) -> dict | None:
        udids = _run(["idevice_id", "-l"], timeout=10).split()
        if not udids:
            return None
        udid = udids[0]
        name = _run(["ideviceinfo", "-u", udid, "-k", "DeviceName"], timeout=10).strip()
        if "ERROR" in name or "Could not connect" in name:
            raise PhoneError(NOT_TRUSTED)
        return {"udid": udid, "name": name}

    def ls(self, path: str) -> list[str]:
        return _listing(_afc([f"ls {path}"]))

    def get(self, pairs: list[tuple[str, Path]], timeout: float = 60) -> None:
        if pairs:
            _afc([f"get {remote} {local}" for remote, local in pairs], timeout=timeout)

    def put(self, folder: str, files: dict[str, bytes]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cmds = [f"mkdir {folder}"]
            for name, data in files.items():
                Path(tmp, name).write_bytes(data)
                cmds.append(f"put -f {tmp}/{name} {folder}/{name}")
            _afc(cmds)


# --------------------------------------------------------------------------- pymobiledevice3


def _pmd3_errors():
    """pymobiledevice3 exception types -> PhoneError messages users can act on (first match wins)."""
    from pymobiledevice3 import exceptions as ex

    usbmuxd = "install iTunes or the Apple Devices app" if sys.platform == "win32" else "is usbmuxd running?"
    return [
        ((ex.ConnectionFailedToUsbmuxdError,), f"can't reach Apple's USB service: {usbmuxd}"),
        ((ex.NoDeviceConnectedError, ex.DeviceNotFoundError), NO_PHONE),
        ((ex.PairingError, ex.NotPairedError), NOT_TRUSTED),
        ((ex.AppNotInstalledError,), NO_FREEFLIGHT),
        ((ex.AfcFileNotFoundError,), "file not found on the iPhone"),
        ((ex.PyMobileDevice3Exception, ConnectionError, OSError), "iPhone transfer failed"),
    ]


def _pmd3_call(coro_fn, *args):
    """Run an async pymobiledevice3 operation to completion, translating its errors."""
    try:
        return asyncio.run(coro_fn(*args))
    except PhoneError:
        raise
    except Exception as e:
        for types, message in _pmd3_errors():
            if isinstance(e, types):
                raise PhoneError(f"{message} ({e})" if str(e) else message) from e
        raise


class _Pmd3:
    """pymobiledevice3: one USB session per call, rooted in FreeFlight's Documents."""

    @staticmethod
    async def _first_usb_device():
        from pymobiledevice3 import usbmux

        usb = [d for d in await usbmux.list_devices() if d.is_usb]
        return usb[0] if usb else None

    @contextlib.asynccontextmanager
    async def _documents(self):
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.house_arrest import HouseArrestService

        device = await self._first_usb_device()
        if device is None:
            raise PhoneError(NO_PHONE)
        async with await create_using_usbmux(serial=device.serial) as lockdown:
            async with await HouseArrestService.create(
                lockdown=lockdown, bundle_id=FREEFLIGHT_APP, documents_only=True,
            ) as afc:
                yield afc

    async def _connected(self) -> dict | None:
        from pymobiledevice3.lockdown import create_using_usbmux

        device = await self._first_usb_device()
        if device is None:
            return None
        async with await create_using_usbmux(serial=device.serial) as lockdown:
            name = lockdown.all_values.get("DeviceName") or "iPhone"
        return {"udid": device.serial, "name": name}

    def connected(self) -> dict | None:
        return _pmd3_call(self._connected)

    async def _ls(self, path: str) -> list[str]:
        async with self._documents() as afc:
            return list(await afc.listdir(path))

    def ls(self, path: str) -> list[str]:
        return _pmd3_call(self._ls, path)

    async def _get(self, pairs: list[tuple[str, Path]]) -> None:
        async with self._documents() as afc:
            for remote, local in pairs:
                Path(local).write_bytes(await afc.get_file_contents(remote))

    def get(self, pairs: list[tuple[str, Path]], timeout: float = 60) -> None:
        if pairs:
            _pmd3_call(self._get, pairs)

    async def _put(self, folder: str, files: dict[str, bytes]) -> None:
        async with self._documents() as afc:
            await afc.makedirs(folder)
            for name, data in files.items():
                await afc.set_file_contents(f"{folder}/{name}", data)

    def put(self, folder: str, files: dict[str, bytes]) -> None:
        _pmd3_call(self._put, folder, files)


_AFCCLIENT = _Afcclient()
_PMD3 = _Pmd3()


# --------------------------------------------------------------------------- public API


def connected() -> dict | None:
    """{'udid', 'name'} of the first paired iPhone, or None."""
    return _backend().connected()


def list_plans() -> list[dict]:
    """Plans in FreeFlight: [{'uuid', 'title'}], titles read from each savedPlan.json."""
    phone = _backend()
    folders = phone.ls(PLANS_DIR)
    if not folders:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        phone.get([(f"{PLANS_DIR}/{u}/savedPlan.json", Path(tmp, f"{u}.json")) for u in folders])
        plans = []
        for u in folders:
            f = Path(tmp, f"{u}.json")
            try:
                title = json.loads(f.read_text()).get("title", "") if f.exists() else ""
            except (json.JSONDecodeError, UnicodeDecodeError):
                title = ""
            plans.append({"uuid": u, "title": title})
    return plans


def send_plan(folder: str, files: dict[str, bytes]) -> None:
    """Create or replace the plan folder `folder` with `files`."""
    if not re.fullmatch(r"[0-9A-F-]{36}", folder):
        raise PhoneError(f"bad plan folder name {folder!r}")
    phone = _backend()
    dest = f"{PLANS_DIR}/{folder}"
    phone.put(dest, files)
    missing = set(files) - set(phone.ls(dest))
    if missing:
        raise PhoneError(f"copy incomplete, missing on the iPhone: {', '.join(sorted(missing))}")


def pull_plans(out_dir: str | Path) -> list[Path]:
    """Copy every FreeFlight plan folder to out_dir/<UUID>/."""
    phone = _backend()
    out = Path(out_dir)
    folders = phone.ls(PLANS_DIR)
    for u in folders:
        (out / u).mkdir(parents=True, exist_ok=True)
        phone.get([(f"{PLANS_DIR}/{u}/{n}", out / u / n)
                   for n in ("savedPlan.json", "flightPlan.mavlink", "screenshot.png")])
    return [out / u for u in folders]


def pull_runs(out_dir: str | Path) -> list[Path]:
    """Copy flight logs not yet in out_dir; returns the new files."""
    phone = _backend()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    new = [n for n in phone.ls(RUNS_DIR) if n.endswith(".json") and not (out / n).exists()]
    phone.get([(f"{RUNS_DIR}/{n}", out / n) for n in new], timeout=60 + 10 * len(new))
    return [out / n for n in new]
