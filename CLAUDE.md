# Anafi Mission Planner — Project Brief

A small desktop tool to plan and fly photogrammetry (mapping) missions with an original **Parrot Anafi**, replacing the discontinued Pix4Dcapture app.

> Developer notes and verified hardware findings, written while building the project (also read by Claude Code at the start of a session).

---

## 1. Background

- Pix4Dcapture was used to fly photogrammetry missions with this drone. It was pulled from the Apple App Store and Google Play in October 2023, and the newer Pix4D apps don't support the original Anafi.
- The drone works fine and still flies with FreeFlight 6 on iPhone.
- Goal: keep using the drone for mapping without depending on abandoned mobile apps.

## 2. Hardware and environment

| Item | Value |
|---|---|
| Drone | Parrot Anafi (original "4K" model) |
| Hardware version | HW_03 (no known SDK impact) |
| Firmware | 1.8.2 (appears to be the final release for this model) |
| Controller | Parrot Skycontroller 3 |
| Mobile app | FreeFlight 6 (iPhone), working |
| Dev machine | x86_64 Linux (Arch-based; Olympe only has x86_64 Linux builds) |

## 3. Key technical findings

### The drone already supports autonomous missions
- Anafi drones support **MAVLink v1** and can fly automatic flight plans.
- A flight plan is a **MAVLink text mission file** (QGroundControl WPL format, `QGC WPL 120` header) stored on the drone.
- FreeFlight 6 uses the same mission system, so firmware 1.8.2 supports it.

### Uploading a flight plan (plain HTTP, no SDK needed)
The drone runs a web server with a REST API. With a direct Wi-Fi connection to the drone (`192.168.42.1`):

- `GET  http://192.168.42.1/api/v1/upload/flightplans` lists the flight plans on the drone
- `GET  http://192.168.42.1/api/v1/upload/flightplan/{uid}` downloads one
- `PUT  http://192.168.42.1/api/v1/upload/flightplan` uploads a `.mavlink` file. **The response is the new plan's UID**, which is needed to start it.
- There's also a browser UI at `http://192.168.42.1/` with a "Flightplans" tab.
- Parrot has not formally documented this API. Expect some trial and error.

### Controlling the flight: the Olympe SDK
- **Olympe** is Parrot's free Python SDK (BSD-3 license). It supports Anafi, Anafi Thermal, Anafi USA, Anafi Ai and Anafi UKR.
- It can start, pause and stop missions, monitor drone state (battery, GPS, flying state) and trigger return-to-home.
- Start a mission with `common.Mavlink.Start(<uid>, type='flightPlan')`. Pause and stop are `common.Mavlink.Pause` and `common.Mavlink.Stop`.
- Install: `pip install parrot-olympe` inside a virtualenv. Pre-built packages exist **only for x86_64 Linux**. There is no ARM build, so an ARM Mac would need a Linux VM plus a source build, which is not recommended.
- Officially supported on Ubuntu 20.04+ and Debian 10+. On other distributions (tested on an Arch-based one), try the pip package first. If it fails, use an Ubuntu container (distrobox or Docker with host networking).
- Docs: https://developer.parrot.com/docs/olympe/
- Source: https://github.com/Parrot-Developers/olympe

### ⚠️ Known catch: uploading through the Skycontroller 3
- A Parrot engineer confirmed a firmware bug: the Skycontroller (`192.168.53.1`) **does not forward web-server traffic to the drone**, so REST uploads through the controller may not work. It's unknown whether this is fixed on our controller firmware.
- Direct Wi-Fi to the drone has short range, and uploads fail when the drone is far away.
- **Planned workflow:**
  1. With the drone **on the ground**, connect directly over Wi-Fi and upload the mission. Save the returned UID.
  2. Connect through the Skycontroller 3 (USB to the computer) for flight control and monitoring, then start the mission by UID.
- **Quick test to run first:** connect the laptop to the Skycontroller over USB and open `http://192.168.53.1/`. If the drone's Flightplans page appears, uploads through the controller work and the workflow gets simpler.

### Lessons from other developers (Parrot forum)
- The drone **silently rejects** a mission file that's missing details. Known causes:
  - waypoints without altitudes,
  - a mission with no waypoint command (`16`) at all.
- When `Mavlink.Start` fails with a starting error, check the mission file first.
- The Sphinx simulator may not offer firmware 1.8.2, so use it for logic testing but expect differences from the real drone.

### Why not reverse engineer Pix4Dcapture
Not worth it. The app was built on Parrot's SDK and almost certainly generated the same kind of MAVLink missions. Decompiling it is slow, likely against its license, and would mostly rediscover what Parrot already documents. The only part worth borrowing is the **survey grid logic**, which is standard photogrammetry math we can write ourselves.

## 4. Alternatives to keep in mind
- **Legacy Pix4Dcapture APK on an old Android phone.** Pix4D's support site has offered legacy APKs. It might still work with firmware 1.8.2, but this is unverified.
- **QGroundControl as the planner.** Its "Survey" tool generates grids, and its `.plan` files (JSON) can be converted to Anafi `.mavlink` files. Reference converter (written for Anafi Ai, same format): https://github.com/mgr098/anafi-ai-survival-kit
- **FreeFlight 6 for flying: works for planning-side import (2026-10-24).** `planner/ff6.py` writes FreeFlight's plan folders; `drone/iphone.py` copies them over USB into `/Documents/.Contents/FlightPlan/<UUID>/` of `com.parrot.freeflight6` with `afcclient`; FreeFlight lists and opens them after a restart. The plan's actions set the camera and override the app's camera control: plans tilt −90° at the first waypoint and every line start and take time-lapse photos started once at line 1 (period = spacing ÷ speed; plan actions have no GPS lapse; never repeat photo starts, they toggle and jam the camera), verified in the simulator 2026-11-04. A stick push pauses the plan; play resumes it but photos stay off. **First flight (2026-11-01) failed:** plans had no home waypoint, so the drone climbed on a slant toward line 1 and hit branches. Fixed 2026-11-02: plans climb and descend vertically over home (`home_route`). Never generate a route that changes altitude while moving sideways near the ground. Details in `docs/freeflight_import.md`.
- **FreeFlight 6 for flying (original idea).** Plan on the desktop and upload over Wi-Fi, then start and monitor from FreeFlight 6 on the iPhone, which already has battery display and return-to-home. **Unverified:** whether FreeFlight 6 lists plans uploaded through the REST API. Test this early, because if it works the field laptop isn't needed.

## 5. Platform decision
- **Develop on x86_64 Linux.** Olympe has x86_64 Linux packages.
- **Avoid Olympe on Apple Silicon (ARM).** There's no pre-built Olympe for it.
- **Field problem:** the machine running Olympe must be next to the drone during the flight, and a desktop can't go to the field. Options:
  1. a cheap used x86_64 laptop running Linux (most reliable),
  2. the FreeFlight 6 path above (no laptop needed if it works),
  3. a Linux VM on a Mac (last resort).

## 6. Proposed architecture

Python project. Keep planning code separate from drone code so most of it can be built and tested without the drone.

```
anafi-mission-planner/
├── planner/            # pure Python, no drone dependency
│   ├── camera.py       # Anafi camera model (sensor, FOV, resolution)
│   ├── grid.py         # polygon → lawnmower survey lines
│   └── mavlink.py      # waypoints → Anafi .mavlink file (QGC WPL 120)
├── drone/              # talks to the drone
│   ├── upload.py       # REST upload/list over HTTP (requests)
│   └── control.py      # Olympe: connect, start by UID, telemetry, RTH
├── ui/                 # desktop GUI with a map
├── tests/              # unit tests for planner (grid math, file format)
└── CLAUDE.md
```

### Features (MVP)
1. **Plan:** draw or enter a polygon on a map and set altitude, front/side overlap and speed. Generate a lawnmower grid and preview the lines, photo count, estimated flight time and battery use.
2. **Export:** write a `.mavlink` file. The mission includes takeoff, gimbal pointing straight down, photo capture by time or distance along each line, waypoints with altitudes, and return-to-launch or land at the end.
3. **Upload:** upload over HTTP to `192.168.42.1` and save the returned UID.
4. **Fly:** connect through the Skycontroller, start the mission by UID, and show battery %, mission progress and GPS position.
5. **Return home:** a large, always-visible **Return to Home** button, plus pause/resume.
6. **Safety:** warn when the estimated flight time exceeds the battery. The drone's own failsafes (low-battery and lost-link return-to-home) still apply, so the button is a convenience, not the only safeguard.
7. **Map layers (built 2026-10-31):** Esri satellite / OpenStreetMap base maps with the
   image's capture date per zoom (Esri citation layer), your own orthophotos (`ui/ortho.py`,
   rio-tiler as the optional `ortho` extra because rasterio's wheels need Python 3.12+ and
   macOS 15+) and WMS layers by URL (`ui/wms.py`, stdlib). Google imagery rejected: paid API,
   no offline caching allowed. Check GUI changes in a real browser: unit tests don't run app.js.

### Things to verify early (don't trust from memory)
- [x] Which MAVLink commands the Anafi actually supports in flight plans (waypoint, takeoff, land, return-to-launch, change speed, gimbal/mount control, image start/stop capture, delay, yaw). Check Parrot's MAVLink documentation and forum examples.
  - Documented (https://developer.parrot.com/docs/mavlink-flightplan/messages_v1.html, enums.html); table in `planner/mavlink.py`. Distance-triggered photos: `50001` SET_STILL_CAPTURE_MODE p1=1 (GPS position), p2=metres, then `2000` with p1=0. Speed limited to 1-10 m/s. **Not yet flight-tested.**
- [ ] Exact `.mavlink` file format and fields accepted (frame, altitude reference, params).
  - Mostly known (2026-10-24, from plans FreeFlight 6 writes on fw 1.8.2): tab-separated QGC WPL 120, frame 3 and current = 0 on every line, altitude relative to takeoff, speed `178` p1=0 p3=-1, waypoint p4 = yaw toward next waypoint. Writer now copies this; reference file `tests/data/ff6_prueba.mavlink`, details in `docs/step2_upload_test.md`. Still unverified on 1.8.2: photo commands (`50001`, `2000`/`2001`) and takeoff/land/RTL, since FreeFlight writes none of them. Field-day checks listed in the same doc.
- [x] Anafi camera specs needed for the grid math: sensor size, focal length, photo resolution and horizontal/vertical field of view in rectilinear photo mode.
  - From EXIF of 5 Pix4Dcapture flights: 4608x3456, f 4.0 mm, 1.3442 um pixels, sensor 6.194x4.646 mm, HFOV 75.5 deg. Pix4Dcapture used GPSLapse (distance) capture.
- [x] Battery endurance and climb/descent from real flights (2026-10-24).
  - FreeFlight 6 keeps Parrot flight logs (JSON, ~10 Hz) for every flight in `/Documents/.Contents/runs` on the iPhone; `anafi-drone freeflight pull-runs` copies them, `anafi-plan runs` reports battery use. Climb 2.5 m/s vertical over home (5 surveys). Endurance fell from ~24 min (2021) to ~15 min (2023-25); default now 15 min. 3 batteries came with the drone, one is dead: label flights per battery.
- [ ] Olympe message names and signatures for the installed version: `Mavlink.Start`, battery state, mission playing state, return-to-home, and connecting through the Skycontroller (`192.168.53.1`).
  - Read side verified on the drone (`drone/control.py`): `CommonState.BatteryStateChanged`, `PilotingState.FlyingStateChanged`/`PositionChanged`/`AlertStateChanged`, `GPSSettingsState.GPSFixStateChanged`/`HomeChanged`, `FlightPlanState.AvailabilityStateChanged`/`ComponentStateListChanged`, `MavlinkState.MavlinkFilePlayingStateChanged`, max altitude/distance/geofence. Positions read 500 before a GPS fix.
  - `common.Mavlink.Start(filepath, type)`: takes a "file path from the mavlink ftp root" (pass the UID, per the forum; unverified); types `flightPlan`, `mapMyHouse`, `flightPlanV2`. Needs calibration, outdoor mode, GPS fix. RTH is `ardrone3.Piloting.NavigateHome(start=1)`.
  - Through the Skycontroller 3 works (2026-10-24) with `olympe.SkyController3("192.168.53.1")`. `olympe.SkyController` is the Skycontroller 4 USB mux protocol and times out. No firewall change needed (default zone `block`). While the controller holds the drone, the drone refuses direct SDK connections (handshake status -1).
  - `anafi-drone fly` (drone/fly.py) built 2026-10-24: pre-flight checks, typed confirmation, Start, live telemetry with keys r/p/s/q, CSV log. RTH pauses a playing plan first (NavigateHome waits for hovering). Indoor dry run refused correctly (no GPS). Still to test in the field: Start/Pause/Stop/RTH, see `docs/step3_field_test.md`.
- [x] Whether Olympe installs directly on an Arch-based distribution or needs an Ubuntu container.
  - Directly (2026-10-24): `parrot-olympe` 8.5.0 in the project's Python 3.12 venv, as the `fly` extra (`uv sync --extra fly`). 8.5.0 also needs `certifi` and its `rendering` extra at import time. Connected over the drone's Wi-Fi and read state.
- [x] Whether REST upload works through the Skycontroller (`http://192.168.53.1/` test).
  - Yes, indirectly (2026-10-24, SC3 fw 1.8.1): the controller has no upload API itself, but routes to the drone. `sudo ip route add 192.168.42.1/32 via 192.168.53.1`, then use `192.168.42.1`. Upload verified (read-back identical); UID = MD5 of the file. Details in `docs/step2_upload_test.md`.
- [x] Whether FreeFlight 6 shows plans uploaded through the REST API.
  - No (2026-10-24): its Flight Plan mode only lists plans saved in the app. Olympe stays the main path; an import into FreeFlight is untested.

## 7. Suggested build order
1. **Planner core** (no drone needed): camera model → grid generator → `.mavlink` writer, with unit tests. Sanity-check output by loading it into QGroundControl's map view.
2. **Simple upload script:** upload a tiny test mission (takeoff → one waypoint → land) over direct Wi-Fi and confirm the UID.
3. **Olympe control script:** connect, read battery, start the test mission by UID, trigger return-to-home. Test in Sphinx if possible, then in a safe open area.
4. **Field test of the FreeFlight 6 path:** does an uploaded plan appear and run?
5. **GUI:** map, parameters, preview, upload, fly and monitor, Return to Home button.
6. **Real survey test:** small area, conservative altitude, then process the photos in your photogrammetry software and tune overlap.

## 8. Field safety checklist
- Fly in an open area within line of sight, following local drone regulations.
- Test every new mission feature with a tiny mission first.
- Keep the Skycontroller in hand so you can take over manually at any time.
- Check the battery estimate before every flight. Leave margin for wind and the return trip.
- Confirm the home point is set (GPS lock) before starting a mission.

## 9. References
- Olympe docs: https://developer.parrot.com/docs/olympe/
- Olympe installation (x86_64 only): https://developer.parrot.com/docs/olympe/installation_procedures/installation.html
- Olympe on GitHub: https://github.com/Parrot-Developers/olympe
- Parrot forum, flight plans with Olympe (REST API, `Mavlink.Start`): https://forum.developer.parrot.com/t/flightplan-with-olympe/9541
- Parrot forum, Skycontroller upload bug: https://forum.developer.parrot.com/t/controlling-anafi-thermals-speed-without-the-use-of-optical-flow/13872
- Parrot forum, mission rejected (missing altitudes and waypoints): https://forum.developer.parrot.com/t/anafi-olympe-mavlink-start-get-startingerrorevent/14394
- Parrot open-source tools (MAVLink support): https://www.parrot.com/en/open-source-development-tools-2
- QGC → Anafi `.mavlink` converter: https://github.com/mgr098/anafi-ai-survival-kit
- Pix4Dcapture legacy info: https://support.pix4d.com/hc/en-us/articles/360000240783
