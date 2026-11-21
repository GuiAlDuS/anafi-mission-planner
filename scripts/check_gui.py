"""Smoke check of the GUI in a headless browser: the unit tests don't run ui/static/*.js.

    uv run --with playwright==1.64.1 python scripts/check_gui.py [--shots DIR]

Starts `anafi-gui` on a free port with empty temporary folders (your missions,
logs and layers are not touched), then in Chromium: draws an area, checks the
plan, route and photo-spacing note, changes settings, saves and reopens a
mission, and opens the Batteries and Fly tabs. Fails on any page error.
Screenshots go to --shots (default: a temporary folder) for a visual
comparison before and after a change. Needs Playwright's Chromium
(`uv run --with playwright==1.64.1 playwright install chromium`, once).
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

# A small field near the simulator's lake spawn point, so no real site is involved.
VIEW = [48.8800, 2.3701, 17]
CORNERS = [(-120, -80), (120, -80), (120, 80), (-120, 80)]  # pixels from the map centre


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(tmp: Path) -> tuple[subprocess.Popen, str]:
    port = free_port()
    args = [sys.executable, "-m", "ui", "--no-browser", "--port", str(port), "--out", str(tmp / "out"),
            "--runs", str(tmp / "runs"), "--battery-log", str(tmp / "battery.json"),
            "--orthophotos", str(tmp / "orthos"), "--wms-layers", str(tmp / "wms.json")]
    proc = subprocess.Popen(args, cwd=Path(__file__).resolve().parent.parent,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            return proc, url
        except OSError:
            if proc.poll() is not None:
                sys.exit(f"anafi-gui didn't start:\n{proc.stderr.read()}")
            time.sleep(0.2)
    proc.kill()
    sys.exit("anafi-gui didn't answer")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--shots", help="folder for screenshots")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="check_gui_"))
    shots = Path(args.shots) if args.shots else tmp / "shots"
    shots.mkdir(parents=True, exist_ok=True)
    proc, url = start_server(tmp)
    errors: list[str] = []
    failures: list[str] = []

    def check(ok: bool, what: str) -> None:
        print(("ok    " if ok else "FAIL  ") + what)
        if not ok:
            failures.append(what)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.on("pageerror", lambda e: errors.append(f"page error: {e}"))
            page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text
                    and errors.append(f"console: {m.text}"))
            page.goto(url)
            page.wait_for_timeout(1500)
            page.evaluate("localStorage.clear()")
            page.reload()
            page.wait_for_timeout(1500)
            page.screenshot(path=shots / "1_empty.png")
            check(page.input_value("#photo_gap") == "2.2", "photo gap default comes from the server (2.2 s)")

            # Draw an area.
            page.evaluate(f"map.setView([{VIEW[0]}, {VIEW[1]}], {VIEW[2]})")
            page.wait_for_timeout(800)
            box = page.locator("#map").bounding_box()
            cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.click("#draw")
            for dx, dy in CORNERS:
                page.mouse.click(cx + dx, cy + dy)
                page.wait_for_timeout(200)
            page.click("#draw")  # closes the area; the next click sets the takeoff point
            page.wait_for_timeout(300)
            page.mouse.click(cx, cy + 130)
            page.wait_for_timeout(2500)
            readout = " ".join(page.locator("#readout").inner_text().split())
            check("flight time" in readout and "Draw an area" not in readout, f"plan readout: {readout}")
            check(page.locator("#map path").count() > 20, f"route drawn ({page.locator('#map path').count()} paths)")
            page.screenshot(path=shots / "2_plan.png")

            # Low and fast: the planner must slow down and say so; 0 turns the limit off.
            page.locator("#altitude label:has-text('40')").first.click()
            page.locator("#speed label:has-text('10')").first.click()
            page.wait_for_timeout(2000)
            check("Speed lowered" in page.locator("#notes").inner_text(), "speed lowered note at 40 m, 10 m/s")
            check("slowed down" in page.locator("#summary").inner_text(), "summary says slowed down")
            page.fill("#photo_gap", "0")
            page.dispatch_event("#photo_gap", "input")
            page.wait_for_timeout(2000)
            check("Speed lowered" not in page.locator("#notes").inner_text(), "no note with the limit off")
            page.fill("#photo_gap", "3")  # saved with the mission, checked after reopening
            page.dispatch_event("#photo_gap", "input")
            page.wait_for_timeout(2000)

            # Save, then reopen it.
            page.fill("#name", "check gui")
            page.click("#save")
            page.wait_for_timeout(2000)
            check((tmp / "out" / "check_gui" / "flight_1.mavlink").exists(), "mission saved")
            check("check_gui" in page.locator("#missions").inner_text(), "mission listed")
            page.click("#clear")
            page.fill("#photo_gap", "2.2")
            page.dispatch_event("#photo_gap", "input")
            page.wait_for_timeout(500)
            page.locator("#missions button[aria-label='Open check_gui']").click()
            page.wait_for_timeout(2500)
            reopened = page.locator("#readout").inner_text()
            check("flight time" in reopened and "Draw an area" not in reopened, "reopened mission plans again")
            check(page.input_value("#photo_gap") == "3", "reopening restores the mission's settings")
            page.screenshot(path=shots / "3_reopened.png")

            # Leaflet repeats the world sideways: an area drawn on a copy (longitude off by 360)
            # must still plan, both when drawn there and when restored from a saved session.
            page.click("#clear")
            page.evaluate(f"map.setView([{VIEW[0]}, {VIEW[1] - 360}], {VIEW[2]})")
            page.wait_for_timeout(800)
            page.click("#draw")
            for dx, dy in CORNERS:
                page.mouse.click(cx + dx, cy + dy)
                page.wait_for_timeout(200)
            page.click("#draw")
            page.wait_for_timeout(300)
            page.mouse.click(cx, cy + 130)
            page.wait_for_timeout(2500)
            lngs = page.evaluate("state.area.map((p) => p[1]).concat(state.home ? [state.home[1]] : [])")
            check(all(-180 <= x <= 180 for x in lngs), f"points drawn on a world copy are wrapped ({lngs[0]:.4f})")
            readout = " ".join(page.locator("#readout").inner_text().split())
            check("flight time" in readout, f"area drawn on a world copy plans: {readout}")
            page.evaluate("""() => {
                const s = JSON.parse(localStorage.getItem(STORE));
                s.area = s.area.map(([a, b]) => [a, b - 360]);
                s.home = [s.home[0], s.home[1] - 360];
                s.view = [s.view[0], s.view[1] - 360, s.view[2]];
                localStorage.setItem(STORE, JSON.stringify(s));
            }""")
            page.reload()
            page.wait_for_timeout(3000)
            readout = " ".join(page.locator("#readout").inner_text().split())
            check("flight time" in readout, f"saved session on a world copy plans after reload: {readout}")

            # The other tabs.
            for view in ("batteries", "fly", "plan"):
                page.evaluate(f"location.hash = '#{view}'")
                page.wait_for_timeout(1200)
                page.screenshot(path=shots / f"4_{view}.png")
            check(True, "Batteries and Fly tabs opened")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    for e in errors:
        print("FAIL  " + e)
    print(f"screenshots: {shots}")
    ok = not failures and not errors
    print("GUI check passed" if ok else "GUI check FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
