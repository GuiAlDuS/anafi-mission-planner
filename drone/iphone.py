"""Copy flight plans into FreeFlight 6 on an iPhone over USB (no iCloud).

FreeFlight shares its Documents folder (it shows in the Files app), so its
plans can be written with libimobiledevice's `afcclient --documents`, the same
file-sharing service Finder uses. Plans live in
/Documents/.Contents/FlightPlan/<UUID>/ as savedPlan.json, flightPlan.mavlink
and screenshot.png; FreeFlight lists new folders after it's restarted.

Needs the libimobiledevice and usbmuxd packages, the iPhone plugged in and
"Trust this computer" accepted once.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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


class PhoneError(Exception):
    pass


def plan_uuid(mission: str, flight: int) -> str:
    return str(uuidlib.uuid5(PLAN_NAMESPACE, f"{mission}/{flight}")).upper()


def _run(args: list[str], stdin: str | None = None, timeout: float = 30) -> str:
    tool = args[0]
    if shutil.which(tool) is None:
        raise PhoneError(f"`{tool}` not found: install libimobiledevice and usbmuxd")
    try:
        r = subprocess.run(args, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise PhoneError(f"`{tool}` didn't answer in {timeout:.0f} s; unplug and replug the iPhone") from e
    return ANSI.sub("", r.stdout + r.stderr)


def connected() -> dict | None:
    """{'udid', 'name'} of the first paired iPhone, or None."""
    udids = _run(["idevice_id", "-l"], timeout=10).split()
    if not udids:
        return None
    udid = udids[0]
    name = _run(["ideviceinfo", "-u", udid, "-k", "DeviceName"], timeout=10).strip()
    if "ERROR" in name or "Could not connect" in name:
        raise PhoneError("the iPhone is plugged in but not trusted yet: unlock it and tap Trust")
    return {"udid": udid, "name": name}


def _afc(commands: list[str], timeout: float = 60) -> str:
    """Run afcclient commands in FreeFlight's Documents; raise on any error line."""
    # afcclient spins on end of input, so always finish with an explicit exit.
    out = _run(["afcclient", "--documents", FREEFLIGHT_APP], "\n".join(commands + ["exit"]) + "\n", timeout)
    if "Could not connect" in out or "No device found" in out:
        raise PhoneError("no iPhone found: plug it in, unlock it and tap Trust")
    if "ApplicationLookupFailed" in out:
        raise PhoneError("FreeFlight 6 isn't installed on this iPhone")
    errors = [ln for ln in out.splitlines() if ln.startswith(("Error", "ERROR"))]
    if errors:
        raise PhoneError("; ".join(errors))
    return out


def _listing(out: str) -> list[str]:
    return [ln.split()[-1] for ln in out.splitlines() if ln and not ln.startswith(("afc:", "Error"))]


def list_plans() -> list[dict]:
    """Plans in FreeFlight: [{'uuid', 'title'}], titles read from each savedPlan.json."""
    folders = _listing(_afc([f"ls {PLANS_DIR}"]))
    if not folders:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        _afc([f"get {PLANS_DIR}/{u}/savedPlan.json {tmp}/{u}.json" for u in folders])
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
    dest = f"{PLANS_DIR}/{folder}"
    with tempfile.TemporaryDirectory() as tmp:
        cmds = [f"mkdir {dest}"]
        for name, data in files.items():
            Path(tmp, name).write_bytes(data)
            cmds.append(f"put -f {tmp}/{name} {dest}/{name}")
        _afc(cmds)
    present = set(_listing(_afc([f"ls {dest}"])))
    missing = set(files) - present
    if missing:
        raise PhoneError(f"copy incomplete, missing on the iPhone: {', '.join(sorted(missing))}")


def pull_plans(out_dir: str | Path) -> list[Path]:
    """Copy every FreeFlight plan folder to out_dir/<UUID>/."""
    out = Path(out_dir)
    folders = _listing(_afc([f"ls {PLANS_DIR}"]))
    for u in folders:
        (out / u).mkdir(parents=True, exist_ok=True)
        _afc([f"get {PLANS_DIR}/{u}/{n} {out / u / n}" for n in ("savedPlan.json", "flightPlan.mavlink",
                                                                    "screenshot.png")])
    return [out / u for u in folders]


def pull_runs(out_dir: str | Path) -> list[Path]:
    """Copy flight logs not yet in out_dir; returns the new files."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    new = [n for n in _listing(_afc([f"ls {RUNS_DIR}"])) if n.endswith(".json") and not (out / n).exists()]
    if new:
        _afc([f"get {RUNS_DIR}/{n} {out / n}" for n in new], timeout=60 + 10 * len(new))
    return [out / n for n in new]
