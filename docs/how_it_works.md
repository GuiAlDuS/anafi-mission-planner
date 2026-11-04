# How it works

Technical notes: what we learned about the hardware, the file formats, the
time model, the code layout and the tests.

## What we learned about the hardware

Found while building this, on an Anafi 4K (fw 1.8.2), Skycontroller 3 (fw
1.8.1) and iPhone with FreeFlight 6:

- **Skycontroller over USB** appears as a network link (RNDIS) at
  `192.168.53.1`. It has no flight plan API of its own but routes to the drone,
  so the drone's API works through it at `192.168.42.1`. Olympe needs
  `olympe.SkyController3` for it (`olympe.SkyController` is the Skycontroller 4
  protocol). macOS has no RNDIS driver, so this link only works on Linux/Windows.
- **The drone** forgets uploaded plans when switched off; a plan's UID is the
  MD5 of the file; `Mavlink.Start` takes it as the file path.
- **FreeFlight 6** keeps plans on the phone in
  `/Documents/.Contents/FlightPlan/<UUID>/` (`savedPlan.json`,
  `flightPlan.mavlink`, `screenshot.png`) and flight logs in
  `/Documents/.Contents/runs/`, both reachable over USB. It lists plan folders
  it didn't create after a restart. The plan's actions set the camera (tilt,
  time-lapse photos) and override the app's camera control.
- **Flight logs** of past surveys: vertical climb over home at 2.5 m/s; flight
  time model within 2 %; battery endurance fell from ~24 min (2021) to ~15 min
  (2023-25), most likely because of one worn pack (55 % health in the pack
  check) rather than all of them.
- **Return home settings** are readable and settable through Olympe's `rth`
  feature: return-home altitude (`min_altitude`, 20-100 m; ours was 30 m, too
  low for hilly ground with trees, now 60 m), what it does on arrival (hovers
  2 m above home by default), auto return on low battery, home type, and the
  link-loss delay (1 s). Return home after a lost link isn't optional on the
  Anafi; FreeFlight has no switch for it.

## Two ways to fly, two ways to take photos

Both paths fly the same grid and use the photo spacing the planner computes
from altitude and front overlap, but the drone is told to take photos in
different ways (all verified in the simulator, 2026-11-03/04):

| | Laptop: `anafi-drone fly` (our `.mavlink`) | Phone: Send to FreeFlight (FreeFlight plan) |
|---|---|---|
| Runs the mission | our program, through Olympe | FreeFlight 6 on the iPhone |
| Photo trigger | **GPS lapse**: every N metres (`50001` mode 1 + `2000`) | **time lapse**: every N seconds, N = photo spacing ÷ speed |
| If the drone flies slower or faster than planned | spacing stays at the planned metres | spacing follows the real speed (5.5 m for 5.8 m planned at 4.7 m/s) |
| Photos switched on/off | at every line start and end | once: on at line 1, off at the end of the last line (repeating them jams the camera) |
| Camera tilt | `205`, absolute −90°, after the climb | `Tilt` (sent as `2800`), relative, at the first waypoint and every line start |
| After a pause and resume | nothing lost: `fly` switches the photos back on for the rest of the paused line (and off at its end); later lines start their own | photos stay off for the rest of the plan: fly the rest with "Start at line" |

FreeFlight plans can't use GPS lapse: the only photo action seen in its plan
files takes a time interval. The app's own camera control (photo/video, GPS
lapse, Single) doesn't matter for either path, because the mission's camera
commands override it once the plan starts.

## Coordinates, files and timing

**Coordinates.** WGS84 latitude/longitude in and out. The grid is built in
metres in the area's UTM zone; distances and headings are geodesic. Altitudes
are relative to the takeoff point (MAVLink frame 3).

**Mission file** (laptop path). A `QGC WPL 120` text file:

```
TAKEOFF
DO_CHANGE_SPEED            speed (1-10 m/s)
DO_MOUNT_CONTROL           gimbal -90° (straight down)
SET_VIEW_MODE              absolute (turn to each waypoint's yaw on arrival, hold it)
SET_STILL_CAPTURE_MODE     GPS position, every N meters
WAYPOINT                   above home at survey altitude (vertical climb)
for each line:
  WAYPOINT line start  ->  IMAGE_START_CAPTURE  ->  WAYPOINT line end  ->  IMAGE_STOP_CAPTURE
WAYPOINT                   above home at survey altitude, then RETURN_TO_LAUNCH (descends vertically)
(or LAND on the last line)
```

Commands follow
[Parrot's flight plan MAVLink docs](https://developer.parrot.com/docs/mavlink-flightplan/messages_v1.html);
where they leave room, the layout copies plans FreeFlight 6 writes for firmware
1.8.2 (see `tests/data/`). Each waypoint's yaw points along the leg leaving it,
so the heading is steady along photo lines.

**FreeFlight plans** (FreeFlight path). The same survey lines written in
FreeFlight's own format (`planner/ff6.py`), checked against plans the app
saved itself. The plan's folder UUID comes from the mission name and flight
number, so re-sending replaces it. FreeFlight flies straight from waypoint to
waypoint, the first one included, so the plan starts with a waypoint above
home at survey altitude (vertical climb) and ends above home at survey
altitude, then above home at 5 m (vertical descent). Land from there with the
controller. Without them the drone climbs on a slant toward line 1 after
takeoff, as it did on the first field test.

**Time model.** Each straight leg uses a trapezoidal speed profile and every
corner costs a yaw rotation, reproducing measured turns (about 17 s at 34 m
line spacing, 19 s at 45 m, at 10 m/s). Climb rate and takeoff time are
measured from flight logs; descent and landing match the logs within seconds.
Battery use is time over endurance, with a reserve and a wind margin.

## Project layout

```
planner/            pure Python, no drone needed
  camera.py         Anafi camera model
  geo.py            WGS84 <-> local UTM, geodesic distance and heading
  grid.py           polygon -> survey lines and photo positions
  estimate.py       flight time and battery estimate
  plan.py           line angle choice, split into battery-sized flights
  mavlink.py        .mavlink writer, parser and validator
  ff6.py            FreeFlight 6 plan folders (savedPlan.json + .mavlink)
  runs.py           battery use from FreeFlight 6 flight logs
  analyze.py        reconstruct flown missions from photo EXIF
drone/
  upload.py         flight plan web API client (stdlib only)
  iphone.py         FreeFlight 6 plans and logs on a USB-connected iPhone
  control.py        Olympe: drone state, pre-flight checks, start/pause/stop/RTH
  battery.py        pack check: read, judge and log what the battery reports
  session.py        one mission flight for any front end: log, photos after a
                    resume, return home note, commands (shared by fly and the GUI)
  fly.py            terminal front end: checks, typed confirmation, live telemetry, keys
ui/
  server.py         local web app (standard library): API, tile cache, imagery date
  fly.py            the Fly tab's drone link: one Olympe connection, background reads, commands
  video.py          the Fly tab's live camera: ffmpeg from the drone's RTSP to MJPEG, on demand
  ortho.py          your orthophotos: store, metadata, map tiles (rio-tiler, optional)
  wms.py            WMS layers: read a service's layer list, fetch and cache tiles
  static/           page, map and battery charts; Leaflet and fonts stored locally
tests/              unit tests, including checks against real flights
docs/               field test procedures, FreeFlight import, release checklist
```

## Tests

```bash
uv run pytest
```

The planner is checked against missions flown by Pix4Dcapture (line count,
line spacing, photo spacing, duration) and against FreeFlight's own plan
files. The upload client is tested against a fake drone web server, the
iPhone transfer against a fake phone, and the log reader against synthetic
flight logs.
