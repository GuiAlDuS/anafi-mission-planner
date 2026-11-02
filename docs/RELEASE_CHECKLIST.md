# Release checklist: sharing with the Anafi community

The goal: any original Anafi owner can plan a mapping mission on a computer,
copy it to FreeFlight 6 on their phone and fly it, without Pix4Dcapture.
Work through these before the first public release. Order matters: don't
publish before section 1 is done.

## 1. Prove it in the field

- [ ] Fly the tiny mission (`anafi-drone fly`): takeoff, waypoint, land work.
- [ ] Fly a small survey sent with **Send to FreeFlight** and flown from
      FreeFlight 6 with GPS lapse: photos every N metres, camera pointing down.
- [ ] Check the photos: count, spacing and overlap (`anafi-plan analyze`),
      and process them in photogrammetry software.
- [ ] A multi-battery mission: flight 2 resumes at the right line.
- [ ] Return home and pause/resume during a mission (FreeFlight and `anafi-drone fly`).
- [ ] Flight time vs estimate, from the logs (`anafi-plan runs`).
- [ ] Note every surprise in `docs/` and fix what's ours to fix.

## 2. Remove personal data

- [x] Replace the personal test site (the usual takeoff point, used in
      `tests/conftest.py` and several tests) with an invented one; keep the
      measured *numbers* (spacing, timings), drop the coordinates.
- [x] `tests/test_analyze.py` reads a local photo folder: ship a few small
      synthetic EXIF fixtures instead, or skip without them.
- [x] `CLAUDE.md`: keep it as developer notes but drop personal details
      (machines, places), or replace it with `CONTRIBUTING.md`.
- [x] `tests/data/ff6_prueba.mavlink`: already moved to made-up coordinates; recheck.
- [x] Grep for names, serial numbers, home coordinates, e-mail addresses.
- [x] Publish from a **fresh repository** (or rewritten history) so old
      commits with the real coordinates don't come along.

## 3. Phones and computers

### iPhone

- [ ] Replace the `afcclient` calls in `drone/iphone.py` with
      [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) (pure
      Python): one code path for Linux, macOS and Windows, no system packages.
      Windows needs Apple's device drivers (iTunes or Apple Devices app).
- [ ] Test on macOS and Windows: list, send, pull-runs.
- [ ] Test with a fresh iPhone that has never trusted the computer.

### Android (test with the old Android phone)

FreeFlight 6 for Android stores its plans somewhere else, and Android has no
"Trust" dialog: access goes through USB debugging (adb) or MTP. Steps to find
out what works:

1. On the phone: install FreeFlight 6, make and save a plan named "Prueba".
2. Enable developer options and USB debugging; on the PC `pacman -S android-tools`
   (adb), plug the phone in and accept the prompt. `adb devices` lists it.
3. Find FreeFlight's files (read only):
   ```
   adb shell ls /sdcard/Android/data/com.parrot.freeflight6/
   adb shell find /sdcard/Android/data/com.parrot.freeflight6/ -maxdepth 4
   adb shell find /sdcard -iname '*savedPlan*' 2>/dev/null
   ```
   (Android 11+ hides `Android/data` from other apps but usually not from adb;
   newer versions may block it.)
4. If the plan folder is reachable: `adb pull` it, compare with the iPhone
   format, then `adb push` a plan from `out/<name>/freeflight/` and see if
   FreeFlight lists it after a restart.
5. Look for the flight logs (the iPhone's `runs` folder) the same way.
6. If FreeFlight keeps its files in private storage (`/data/data/...`), adb
   can't reach them without root: look for an import option in the app, or
   a share-to-FreeFlight intent, instead.
7. Write the result in `docs/freeflight_import.md`.

- [ ] Android: plans readable / writable via adb? Which Android versions?
- [ ] Android: flight logs readable?
- [ ] `drone/android.py` with the same functions as `drone/iphone.py`, and
      the GUI's Send button picking whichever phone is plugged in.

## 4. Install and run

- [ ] One-command install: `uv tool install` / `pipx install` from GitHub, then PyPI.
- [ ] The GUI is the main entry point; a desktop launcher would help non-developers.
- [ ] Olympe flight control stays an optional extra (`fly`), Linux x86_64 only;
      say so clearly, and that the FreeFlight path doesn't need it.
- [x] The GUI's pack check says when Olympe isn't available instead of offering a
      button that fails (the flight-log battery charts work everywhere).
- [ ] Tests that need a drone, phone or local data are skipped cleanly elsewhere.

### A standalone program (no Python needed)

Details and the trial build in [packaging.md](packaging.md). A PyInstaller
trial on Linux works (2026-10-31: 97 MB download; planning, maps, WMS and
orthophotos all ran from the built program). In this order:

- [ ] Store missions, orthophotos, WMS layers and battery checks in the
      per-user data folder (e.g. with `platformdirs`), keep today's folders for
      developers, add an "Open data folder" button.
- [ ] Tile requests answer with an error instead of dropping the connection
      when something fails.
- [ ] iPhone transfer with `pymobiledevice3` instead of `afcclient` (also in
      section 3), so the app needs no extra installs.
- [ ] Optional: the planner in its own window with `pywebview`.
- [ ] GitHub Actions: build Windows, macOS (Apple Silicon and Intel) and Linux
      versions on a version tag, smoke-test each built program, attach them to a
      GitHub release.
- [ ] The packaged app shows the Olympe features (pack check, laptop flying)
      greyed out with an explanation.
- [ ] Decide on signing (macOS notarization, Windows certificate) or ship
      unsigned with instructions in the README.
- [ ] Optional, later: installers (`.msi`, `.dmg`) with Briefcase.

### Battery check on every computer

The pack check (`anafi-drone battery`, GUI Batteries view) reads the battery
through Olympe today, so it only works on x86_64 Linux. Most Anafi owners use
a Mac, Windows or only a phone.

- [ ] Our own small read-only client for Parrot's drone protocol (ARSDK) in
      plain Python, over the drone's Wi-Fi: discovery handshake, then listen
      for `battery.serial/health/cycle_count/voltage/alert` and
      `common.CommonState.BatteryStateChanged`. No Olympe, any OS. First piece
      of a client that could later also start flight plans from a Mac.
- [ ] Use it in `drone/battery.py` when Olympe isn't installed; test on macOS
      and Windows.
- [ ] Test the check with other owners' drones and packs (other firmware, older
      and newer packs). So far only our three packs on firmware 1.8.2 have been
      read.
- [ ] Tune the limits from real data: health 70 % (don't fly) / 80 % (note) and
      3.3-4.4 V per cell at rest are LiPo rules of thumb, not Parrot figures.
      The cycle counts our packs report (2-4 after years of use) look
      meaningless; find out whether that's true for other packs too.
- [ ] CI (GitHub Actions) running the tests on Linux, macOS and Windows.
- [ ] A browser smoke test of the GUI (Playwright: load the page, draw an area,
      check there are no page errors and the route is drawn). Unit tests missed
      a deleted block of map code once (fixed in aa6b2cb).
- [ ] Orthophotos are an optional extra because rasterio's ready-made packages
      need Python 3.12+ and macOS 15+. Say so in the install guide; check the
      GUI explains it when the extra is missing (it does today).
- [x] "Load your own orthophoto" with a real orthomosaic (93 MB Cloud Optimized
      GeoTIFF, 11 cm/px): loads unchanged, date from the file name.
- [ ] Try a very large orthophoto (several GB) and one without overviews.
- [ ] WMS layers: try services from other countries (1.1.1 and 1.3.0), and one
      that needs a login or has no EPSG:3857 (should explain, not fail).

## 5. Settings that are personal today

- [ ] Altitude choices: 150 m is above the 120 m / 400 ft limit in many
      countries. Default to 120 m max with a setting to allow more, plus a
      note to check local rules.
- [ ] Units: metres and m/s, optionally feet and mph.
- [ ] Other models: Anafi Thermal and Anafi USA have other cameras and
      FreeFlight product IDs (we write 2324, Anafi 4K). Gate by model, test if
      someone has one.
- [ ] Map imagery: make the tile source configurable; note Esri's terms
      (attribution shown; caching for personal offline use).
- [ ] Battery endurance default and labels: explain `anafi-plan runs` / the
      Batteries tab in the user guide.

## 6. Documentation

- [ ] A user guide with screenshots: install, plan, send to FreeFlight, set GPS
      lapse, fly, measure batteries.
- [ ] Troubleshooting: "Trust this computer", restart FreeFlight to see new
      plans, phone not found, plan not listed.
- [ ] What's tested and what isn't (keep the README status table honest).
- [ ] A short "how it works" for developers (file formats, the phone transfer).

## 7. Licence and safety

- [ ] Choose a licence: BSD-3-Clause matches Olympe; MIT is also fine.
- [ ] Third-party notices: Leaflet (BSD-2) and Barlow (OFL) are vendored with
      their licences; libimobiledevice / pymobiledevice3 are called, not shipped.
- [ ] Safety and liability notice in the README and the GUI's first run:
      experimental software flying a real aircraft, follow local rules, keep
      the Skycontroller in hand.
- [ ] No affiliation with Parrot or Pix4D; don't use their logos.

## 8. Launch

- [ ] Version 0.1.0 tagged, changelog started.
- [ ] Announce where Anafi owners are: Parrot developer forum, drone mapping
      communities (OpenDroneMap forum), Anafi user groups.
- [ ] Issue templates asking for drone model, firmware, phone OS and FreeFlight version.
- [ ] Ask early users for flight logs (opt-in) to improve the time and battery models.
