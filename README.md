# Anafi Mission Planner

Plan and fly photogrammetry (mapping) missions with the original **Parrot Anafi**,
replacing the discontinued Pix4Dcapture app.

Pix4Dcapture was pulled from the app stores in 2023, and newer Pix4D apps don't
support the original Anafi. The drone itself still flies fine and supports
autonomous MAVLink flight plans. This project plans survey grids on a
computer and gets them onto the drone in one of two ways:

1. **Through FreeFlight 6 (no laptop in the field).** Plan on the computer,
   copy the plans to FreeFlight 6 on an iPhone over its USB cable, and fly from
   the phone with the Skycontroller as usual.
2. **From a Linux laptop.** Upload the plan over the drone's web API and start,
   watch and stop it with Parrot's Olympe SDK through the Skycontroller.

> [!CAUTION]
> **Experimental software. Use it at your own risk.** No mission made by this
> tool has completed successfully yet: the only real mission so far crashed
> (see [What happened on the first field test](#what-happened-on-the-first-field-test)).
> The software is provided "as is", without warranty of any kind (see
> [LICENSE](LICENSE)). You are the pilot: you are responsible for every flight,
> for following your local drone regulations, and for keeping the controller in
> hand, ready to take over.

> **Help wanted.** The author's drone was damaged in that first test (broken
> arm, dead gimbal), so development has stopped short of the field tests. If
> you have a working original Anafi and want to carry it on, issues and pull
> requests are welcome.

> [!WARNING]
> **How a mission climbs and descends.** Every mission takes off and climbs
> **straight up over the takeoff point** to the survey altitude. Only then
> does it fly to the first line. At the end it flies back **over the takeoff
> point at survey altitude** and comes **straight down** there. It never
> climbs or descends while travelling sideways. This behaviour is fixed in the
> code and covered by tests, but has **not been flown yet**.
>
> The vertical climb happens over the takeoff point *you set on the map*, not
> wherever the drone actually is. Take off on that exact spot, with nothing
> overhead, or the climb is slanted by the offset.

## What happened on the first field test

Told in full because it's the most useful thing this project has learned.

1. **The mission climbed on a slant.** The FreeFlight plans then started at the
   first survey line. FreeFlight flies straight from one waypoint to the next,
   the first one included, so after takeoff the drone climbed diagonally
   toward line 1 instead of going straight up. At low height it flew into
   tree branches near the takeoff point. The mission was abandoned and the
   drone came down. *Fix:* every plan now starts with a waypoint straight above
   the takeoff point at survey altitude, and ends with the takeoff point at
   survey altitude, then the takeoff point at 5 m (`planner/ff6.py`,
   `home_route`; the laptop path's `.mavlink` files work the same way).
2. **The second takeoff broke the arm.** After the crash the drone looked
   fine, so a short test flight followed. One arm had cracked inside and broke
   apart on that takeoff; the drone stopped after 6 seconds with a "motor
   stalled" error. *Lesson:* after any impact, inspect before flying again
   (see [Safety](#safety)). The `anafi-drone health` command came out of this.
3. **The gimbal lied.** After the crash the gimbal reported "calibrated, no
   errors" and reported reaching every angle it was sent to. Two photos taken
   at -90° and 0° were identical: its tilt motor was dead. *Lesson:* check the
   camera really moves (`anafi-drone health --gimbal` compares two photos);
   don't trust the gimbal's own report.

| Step | What | State |
|---|---|---|
| 1 | Planner core: camera model, survey grid, time/battery estimate, `.mavlink` writer | ✅ done |
| 2 | Upload flight plans to the drone over HTTP | ✅ works on the real drone (fw 1.8.2) |
| 3 | Olympe control: status, pre-flight checks, start, pause, stop, return home | 🔧 built; start/stop await the field test |
| 4 | FreeFlight 6 path: send plans to the iPhone over USB | ⚠️ FreeFlight lists and opens the plans; the first flight climbed on a slant and crashed; fixed, not flown since |
| 5 | GUI: plan on a map, send to FreeFlight, battery health | ✅ Plan and Batteries tabs; Fly tab later |
| – | Map layers: base maps, imagery date, your orthophotos, WMS layers | ✅ tried with a real WebODM orthomosaic and Costa Rica's SNIT WMS |
| – | Battery pack check: health, charge, voltage, alerts read from the pack | ✅ read from 3 real packs (x86_64 Linux only for now) |
| – | Drone health check after a crash or repair, incl. a photo test of the gimbal | ✅ ground check run on the real drone; `--watch` and `--gimbal` not yet |
| 6 | Real survey test and overlap tuning | 🙋 help wanted ([docs/step3_field_test.md](docs/step3_field_test.md)) |

## Features

- **Map planner (GUI):** draw or load the survey area on satellite imagery, set
  the takeoff point, pick altitude and speed, and see the grid, photo count,
  flight time and battery split update as you go. Works offline in the field
  for areas viewed at home.
- **Area editing:** drag corners, add one with the + in the middle of a side,
  remove one with a right-click, Undo (Ctrl+Z); a help bar on the map lists
  what works while you draw and edit.
- **Missions:** save, reopen, and delete (to the system trash) your missions
  from the GUI, each with its area, takeoff and settings.
- **Map layers:** Esri satellite or OpenStreetMap as the base map, with the
  date, satellite and sharpness of the image you're planning on; your own
  orthophotos (e.g. from WebODM) and WMS layers (e.g. national orthophotos)
  on top, each with an on/off switch.
- **Readable routes:** photo lines vs transit legs, direction arrows,
  numbered line starts in flying order, a dot for every photo, hover details,
  per-flight highlight and an animated route preview.
- **Send to FreeFlight:** one click copies the plans into FreeFlight 6 on a
  USB-connected iPhone, no iCloud; sending an edited mission again replaces it.
- **Battery health from the flight logs:** reads FreeFlight's flight logs (every
  flight since 2021, Pix4Dcapture flights included) and shows per battery how
  long a full charge lasts, how that changed over the years, and the charge
  curve of each flight.
- **Pack check on the ground:** with a pack in the drone, read what the smart
  battery reports about itself (state of health, charge, voltage, cycle count,
  alerts, serial) and get an "OK to fly" or "Don't fly" verdict. Packs are
  recognised by serial and every check is kept, so you can watch them age. In
  the GUI each pack gets a battery gauge showing its charge and the capacity
  it has lost. Needs Olympe (x86_64 Linux) for now.
- **Survey grid:** lawnmower lines over any polygon (concave too), at any
  heading or automatically at the fastest one; the start corner and direction
  are chosen from the takeoff point to shorten transit.
- **Camera model from real data:** Anafi intrinsics measured from the EXIF of
  five Pix4Dcapture flights (4608×3456 px, 4.0 mm, 1.344 µm pixels, 75.5° HFOV).
- **Distance-triggered photos:** Parrot's GPS-position capture mode, the same
  mode Pix4Dcapture used, so photo spacing doesn't depend on speed.
- **Time and battery estimate** per phase, checked against real flight logs
  (724 s predicted vs 708 s flown for a logged survey).
- **Multi-battery missions:** large areas are split into flights that each fit
  one battery, resuming at the next line.
- **Mission validation:** catches what makes the drone silently reject a plan.
- **Laptop flight control (Olympe):** status, pre-flight checks that refuse to
  start when something's missing, live telemetry with single-key return home,
  pause and stop.
- **Flight analysis:** reconstructs altitude, speed, overlaps and turn times
  from the photos of any past flight.

## Requirements

- Python 3.10+ and [uv](https://docs.astral.sh/uv/)
- Parrot Anafi (original 4K model), firmware 1.8.2, Skycontroller 3
- **FreeFlight path:** an iPhone with FreeFlight 6, and on the computer
  `libimobiledevice` and `usbmuxd` (Arch: `pacman -S libimobiledevice usbmuxd`).
  Tested on Linux.
- **Laptop path and pack check:** x86_64 Linux for Olympe (Parrot ships no
  other builds). On other systems the GUI says so and everything else works.
- `exiftool`, only for `anafi-plan analyze`.

Planning runs anywhere Python does, including macOS on Apple Silicon.

## Install

```bash
git clone https://github.com/GuiAlDuS/anafi-mission-planner.git
cd anafi-mission-planner
uv sync               # planning, GUI, FreeFlight transfer, battery logs
uv sync --all-extras  # plus your own orthophotos on the map, and on x86_64 Linux
                      # flight control and the pack check (Olympe)
```

## Quick start: plan, send to FreeFlight, fly

```bash
uv run anafi-gui
```

1. **Plan tab:** draw the area (or load a GeoJSON in WGS84 / EPSG:4326), set
   the takeoff point (the drone climbs and descends vertically over it, so put
   it exactly where you'll take off, in a clear spot with open sky), pick altitude (40–150 m, with the ground resolution each
   gives) and speed. Check the readout: flight time, photos, batteries. Mind
   your legal height limit: the altitude is above the *takeoff point*, so the
   drone is higher above ground that lies lower than where you take off.
2. **Save:** in the **Missions** section, name the mission and press **Save
   mission**. It writes `out/<name>/flight_N.mavlink` plus FreeFlight copies in
   `out/<name>/freeflight/`. **Saved missions** below lists them, newest first:
   **Open** brings one back with its area, takeoff and settings (saving under the
   same name replaces it), and **Delete** moves it to the system trash, where you
   can restore it. Plans already sent to FreeFlight stay on the phone.
3. **Send to FreeFlight:** plug the iPhone in (unlock it, tap "Trust" the first
   time) and press **Send to FreeFlight**. It saves what's on the map at that
   moment, so open the mission you mean to send first.
4. **On the phone:** close FreeFlight 6 and reopen it, open each plan in Flight
   Plan mode and **set the camera to GPS lapse** (the camera mode isn't stored
   in plan files). Fly as usual.
5. **After flying:** plug the iPhone in, open the **Batteries** tab, press
   "Copy new logs from iPhone" and pick the battery that flew each new flight.

Details: [docs/freeflight_import.md](docs/freeflight_import.md).

### The map

Solid magenta lines take photos, thin dashed ones are transit; arrows show the
direction and numbered badges mark where each line starts, in flying order
across all batteries. White dots mark each photo (one at the start of a line,
then every photo spacing; they're also in `plan.geojson` for QGIS). Hover a
line for its heading, length and photo count, hover a flight in the list to
highlight it, or press "Preview route" to watch a dot fly it.

**Drawing and editing the area:** **Draw area**, then click the corners;
Backspace removes the last one, and clicking the first corner, Enter or Esc
closes the area. Afterwards drag a corner to move it, drag or click the yellow
**+** in the middle of a side to add a corner, and double-click or right-click
a corner to remove it. **Undo** (Ctrl+Z) reverses changes, including Clear. A
bar at the bottom of the map lists these while you draw and edit; its × hides
it and **Help** in the Area section brings it back.

**Map key** at the bottom left explains the marks and folds away.

### Map layers

The button under the zoom buttons shows what's on the map (e.g. "Esri
satellite + yours") and opens a menu with three groups:

- **Base map** (pick one): **Esri satellite** or **OpenStreetMap** (streets and
  paths). Tiles you have viewed are cached in
  `~/.cache/anafi-mission-planner/tiles`, so look over the area at home and the
  map works offline in the field.
- **Your layers** (any number, on top of the base map), each with an **On/Off
  switch** and **Remove**:
  - **Your orthophotos**, e.g. WebODM's orthophoto: plan on a recent picture of
    your own field, with satellite imagery around it.
  - **WMS layers**, e.g. a national mapping agency's orthophotos.
- **Add a layer:** **Load your own orthophoto…** or **Add a WMS layer…**.

**Image info:** a small note under the button says what you're looking at, for
the survey area (or the map centre before you draw one):

- **Esri satellite:** when the image was taken and how old it is, the
  satellite, its sharpness (metres per pixel) and how accurately it's placed.
  Esri mixes captures by zoom level and the closest zoom is often older, so the
  note follows the zoom. Images 3 years or older get a warning to check for new
  trees, buildings or wires. The details are saved, so they show offline too.
- **Your orthophoto:** its date and sharpness. The date comes from the
  GeoTIFF, or from the file name (`2022_03_27_orthomosaic.tif`), or else from
  the file's date.
- **A WMS layer:** its name, the service and its description.

Its × folds it into an **Image info** button; it opens again by itself when
something new appears (another base map, a layer switched on or added).

**Loading an orthophoto:** in WebODM, download the orthophoto as GeoTIFF
(Cloud Optimized GeoTIFFs work as they are), then **Load your own
orthophoto…**. The file is copied into `orthophotos/` unchanged (you can also
drop GeoTIFFs there); Remove deletes only that copy. Large files without
built-in overviews get them added to the copy, so zoomed-out views stay fast.
This needs the optional map tools, `uv sync --all-extras`, which come as
ready-made packages for Windows, macOS 15+ and Linux with Python 3.12+; without
them the menu says how to add them.

**Adding a WMS layer:** **Add a WMS layer…**, paste the service's address,
**Find layers**, pick one and **Add layer**. The layer must be offered in the
web map projection (EPSG:3857); the list marks those that aren't. Viewed areas
are saved like the base maps, so they work offline. Your layers are kept in
`wms_layers.json`. For Costa Rica, the SNIT orthophotos (2005-2007, 1:5000):

```
http://geos0.snitcr.go.cr/cgi-bin/web?map=ortofoto.map
```

**A base map of your own** (an XYZ tile service), when you start the GUI:

```bash
uv run anafi-gui --tiles "Orthophoto=https://server.example/{z}/{x}/{y}.png"
```

Use only sources whose terms allow it. Google's imagery isn't offered: it
needs a paid API key and its terms forbid saving tiles for offline use.

### The Batteries tab

- A figure per battery to **plan with**: the worst of its last 3 measurable
  flights. "Use in planner" copies it to the Plan tab's battery setting.
- **How long a full battery lasts, flight by flight**, against the planner's
  setting: a falling line is a battery losing capacity.
- **Charge during each flight**, one panel per battery on the same scales: a
  steeper line drains faster. Low-battery alerts are marked.
- **The flight table**, where you pick the battery that flew each flight.
- **Pack check** (x86_64 Linux with Olympe): put a pack in the drone, switch it
  on, connect through the Skycontroller's USB or the drone's Wi-Fi and press
  **Check the pack in the drone**. Each pack gets a card with its health, a
  battery gauge (charge, usable capacity, and capacity lost to wear against a
  new pack), its cell voltage against the safe range, a verdict and notes, and
  a health trend once it has checks on different days. A chart of resting
  voltage against charge shows every check: healthy packs follow one curve,
  and a pack that reads full but sits low on voltage stands out. Name a pack
  once (A, B, C) and the same colour follows it into the flight charts.

The planner's default endurance is **15 min**. The logs show about 24 min per
battery in 2021, but some flights since 2023 drained at 6.6-6.8 %/min (about
15 min). Our pack check later found one pack at 55 % health, which matches
that drain, while the other two read 96-100 % and one still flew 24 min in
2025. Label your flights per pack and plan with each pack's own figure.

## Command line

### Plan a survey

```bash
uv run anafi-plan plan area.geojson --home "LAT,LON" --alt 120 --front 80 --side 80 --speed 10 -o out/
```

```
GSD 4.03 cm/px | footprint 186 x 139 m
Lines: 9 at 90.0 deg, spacing 33.7 m (side overlap 82%), photo every 27.9 m, ~162 photos
Flights: 2, total 15:40
  Flight 1: lines 1-5, 8:33, battery ~57% [ok], max 373 m from home
            takeoff_climb 51s, transit_out 42s, lines 271s, turns 52s, transit_back 27s, descent_land 70s
            -> out/flight_1.mavlink
  Flight 2: lines 6-9, 7:07, battery ~48% [ok], max 330 m from home
            takeoff_climb 51s, transit_out 22s, lines 217s, turns 40s, transit_back 27s, descent_land 70s
            -> out/flight_2.mavlink
Preview: out/plan.geojson
```

With the 15 min battery default this ~12 min survey takes two batteries: as
one flight it would leave less than the 25 % reserve. One `flight_N.mavlink`
per battery and a `plan.geojson` preview. `anafi-plan
plan -h` lists all options: line heading, overshoot, end action (`rth` or
`land`), battery endurance and reserve.

A tiny test mission (takeoff, one waypoint, land) for first flights:

```bash
uv run anafi-plan tiny "HOME_LAT,HOME_LON" "WP_LAT,WP_LON" --alt 10 -o out/tiny.mavlink
```

### FreeFlight 6 on the iPhone

```bash
uv run anafi-drone freeflight list                          # plans on the phone
uv run anafi-drone freeflight send out/<name>/freeflight/*.zip
uv run anafi-drone freeflight pull -o freeflight_plans/     # copy all plans back
uv run anafi-drone freeflight pull-runs                     # copy new flight logs
```

### Battery use from the flight logs

```bash
uv run anafi-plan runs                      # battery use per flight
uv run anafi-plan runs --label 0042=B       # note which battery flew flight 0042
```

```
flight date             battery   air    charge %/min full lasts  alt  far  notes
0038   2023-10-08 09:48 ?       11.7m  86->  7%   6.7     15 min  120  336  survey, low-battery alert at 17%
...
All flights: plan with an endurance of 14 min or less (worst of the last 3 measurable flights: ...).
```

Labels are kept in `freeflight_runs/batteries.json`.

### Battery health check (on the ground)

Put a pack in the drone, switch it on, plug in the Skycontroller and run:

```bash
uv run anafi-drone --host 192.168.53.1 battery --name A   # name is remembered by the pack's serial
uv run anafi-drone battery --history                       # all past checks
```

It reads what the smart battery reports through Olympe: serial, state of health,
cycle count, voltage (sampled for 5 s), alerts (too hot/cold, authentication
failure, ...) and whether the gauge firmware is outdated. Motors never start.
It says "DON'T FLY" (exit code 1) for an active critical or authentication
alert, health under 70 % or a resting cell voltage outside 3.3-4.4 V, and notes
health under 80 %, an outdated gauge, and changes since the pack's last check.
Checks go to `battery_checks.json`. Flight logs from `anafi-drone fly` also
record the voltage, which shows sag under load.

The same check is in the GUI (`anafi-gui`, Batteries view, **Check the pack in the drone**):
one card per pack with a battery gauge (charge, usable capacity, capacity lost to wear),
the cell voltage against its safe range, the verdict, and a chart of resting voltage
against charge for every check, where a tired pack sits low on the right.

Still do the physical check: a swollen pack (doesn't sit flat, latch tight),
damaged contacts, or a pack that gets hot while charging stays on the ground.

### Drone health check (after a crash or a repair)

```bash
uv run anafi-drone --host 192.168.53.1 health            # on the ground, motors off
uv run anafi-drone --host 192.168.53.1 health --gimbal   # also prove the camera really tilts (2 photos)
uv run anafi-drone --host 192.168.53.1 health --watch    # then while you hover and fly by hand
```

Reads what the drone reports about its own hardware through Olympe, through
the Skycontroller or the drone's Wi-Fi. It never starts the motors or sends a
command:

| Part | What it reads | Says FAIL when |
|---|---|---|
| motors | current motor error and which motor, last error, flight counter | a motor error is active (the last error is only a warning: it stays until a new one, e.g. "propeller cutout" from a crash) |
| sensors | IMU, barometer, GPS, magnetometer, ... | a sensor reports not working |
| compass | magnetometer calibration, heading lock | calibration required, heading lock lost |
| gimbal | calibration state, gimbal errors (the gimbal's own report) | calibration required, blocked or overloaded gimbal |
| gimbal movement (`--gimbal`) | a photo at -90° and one at 0°, compared | the two photos are the same: the camera didn't move |
| vibration | the drone's own vibration level (ok / warning / critical) | critical (only measured in flight) |
| alarms, takeoff checklist | motor cutout, freefall, gyro, ... | an alarm or a check other than GPS is on (newer firmware may be needed; "n/a" if not sent) |
| GPS, battery | fix and satellites; the battery check above | battery problems |

With `--watch` it then follows vibration, heading lock, motor errors and alerts
while you fly with the Skycontroller (take off, hover 1-2 minutes at 2 m, then
10 m, then fly gently). It stops when you land. The summary shows the worst
vibration seen in the air, the best sign of a bent propeller, a damaged motor or
a repaired arm that isn't straight. Exit code 1 on any FAIL or in-flight
problem. Checks go to `health_checks.json`.

Don't trust the gimbal's own report on its own. After the first field test's crash it
said "calibrated, no errors" and reported reaching every angle it was sent to,
while its tilt motor was dead and the camera never moved. Only the photos
showed it. `--gimbal` needs an SD card in the drone and leaves the two photos
on it.

It can't see everything. Inspect propellers, arms, hinges and motors (they
should spin freely by hand) by eye, and calibrate the compass and gimbal in
FreeFlight after a crash.

### Upload to the drone (laptop path)

With the drone on the ground, either join its Wi-Fi, or plug the Skycontroller
3 into the computer with a USB **data** cable and route the drone's address
through it (on a NetworkManager system this can be stored in the controller's
connection profile so it's added on every plug-in):

```bash
sudo ip route add 192.168.42.1/32 via 192.168.53.1
uv run anafi-drone ping
uv run anafi-drone upload out/flight_1.mavlink
```

`upload` validates the plan, uploads it, reads it back, and records its
**UID** (the MD5 of the file) in `uploads.json`. The drone forgets its plans
when switched off: upload in the same power cycle as the flight. Its web UI
at `http://192.168.42.1/` lists the plans too.

### Check and fly (Olympe, laptop path)

```bash
uv run anafi-drone --host 192.168.53.1 status
```

```
battery      73 %
state        landed
GPS          no fix, position unknown
home         unknown, type TAKEOFF, return home after 1 s link loss
flight plan  NOT ready (GPS MISSING, Calibration ok, TakeOff ok); stopped
limits       max altitude 150 m, max distance 100 m (geofence off)
```

```bash
uv run anafi-drone --host 192.168.53.1 fly out/tiny.mavlink
```

`fly` uploads the plan if needed and refuses to start unless the drone is
landed with a GPS fix, a home point, enough battery (`--min-battery`, default
30 %) and the mission fits its altitude and distance limits; then it asks you
to type `yes`. While flying it shows live telemetry and logs it to
`flight_logs/`: `r` return home, `p` pause/resume, `s` stop (hover), `q`
leave the view. `anafi-drone rth`, `pause` and `stop` also work on their own.

### Analyze past flights from their photos

```bash
uv run anafi-plan analyze path/to/photos/ --home "LAT,LON"
```

```
   133 photos (GPSLapse), 120 m AGL, 10.0 m/s, 9 lines
   photo spacing 26.1 m (front 81%), line spacing 33.6 m (side 82%)
   turn gaps (s): 16, 19, 18, 17, 18, 17, 16, 14
```

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
  it didn't create after a restart. It doesn't write the camera mode (GPS
  lapse) into plan files.
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

## How it works

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

## Roadmap

- **Field test, help wanted** ([docs/step3_field_test.md](docs/step3_field_test.md)):
  a tiny mission, then a small survey flown from FreeFlight with GPS lapse;
  check photos, timing and battery. Start with the vertical climb and descent.
- **More of the release checklist:** [docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md)
  (cross-platform phone transfer, easy install, configurable limits).
- **Android:** FreeFlight 6 on Android keeps its files elsewhere; a test plan
  with an old phone is in the release checklist.
- **Photos to WebODM:** a helper to copy a mission's photos (possibly straight
  from the drone's media API) into a folder for WebODM. Idea, not started.
- **Fly tab in the GUI** on top of the Olympe commands.
- **Pack check on any computer:** a small read-only client for Parrot's drone
  protocol, so the battery check works on macOS and Windows without Olympe.
- **A standalone program:** download, double-click, plan; no Python needed. A
  trial build works on Linux; the plan is in
  [docs/packaging.md](docs/packaging.md).
- **Apple Silicon:** Olympe has no ARM builds. Options considered, easiest
  first: the FreeFlight path (works today), a small x86 Linux box with the GUI
  opened from any browser, a Linux VM, our own pure-Python client for Parrot's
  protocol.

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
  fly.py            interactive mission run with live telemetry
ui/
  server.py         local web app (standard library): API, tile cache, imagery date
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

## Safety

This software sends autonomous flight plans to a real aircraft.

- Fly in an open area, within line of sight, following local regulations:
  many countries limit altitude to 120 m / 400 ft.
- Test every new feature with a tiny mission first.
- Keep the Skycontroller in hand so you can take over at any time.
- Check the battery estimate before each flight and leave margin for wind;
  measure your batteries (they age).
- Make sure the drone has a GPS fix and home point before starting a mission.
- Missions climb straight up over the planned takeoff point and come straight
  down over it (see the warning at the top; not flown yet). Take off exactly on that point,
  with open sky above it. Watch the climb and take over if the drone drifts
  sideways before reaching altitude.
- FreeFlight plans end hovering 5 m above the takeoff point. Land with the
  controller.
- After any impact, don't take off again until the drone has been inspected,
  even if it looks fine. On the first field test the crash left an arm cracked
  but intact; it broke apart on the next takeoff (6 s, motor stalled). Check propellers,
  arms and hinges by eye and by flexing them gently, spin each motor by hand,
  then run `anafi-drone health` on the ground and `health --watch` for a
  low hover first.

The drone's own failsafes (low-battery and lost-link return to home) remain
active; they are not a substitute for supervision.

## References

- [Olympe SDK](https://developer.parrot.com/docs/olympe/) ·
  [source](https://github.com/Parrot-Developers/olympe)
- [Parrot flight plan MAVLink commands](https://developer.parrot.com/docs/mavlink-flightplan/messages_v1.html) ·
  [enums](https://developer.parrot.com/docs/mavlink-flightplan/enums.html)
- [Parrot web server upload API](https://developer.parrot.com/docs/webserver-api/module_upload.html)
- [Parrot forum: flight plans with Olympe](https://forum.developer.parrot.com/t/flightplan-with-olympe/9541)
- [anafi-ai-survival-kit](https://github.com/mgr098/anafi-ai-survival-kit) (QGC → Anafi converter and upload example)
- [libimobiledevice](https://libimobiledevice.org/) (iPhone file transfer)

## Licence

BSD-3-Clause, see [LICENSE](LICENSE). Vendored: Leaflet (BSD-2-Clause) and the
Barlow fonts (SIL Open Font License), with their licences in `ui/static/vendor/`.

Not affiliated with Parrot or Pix4D. Parrot's repair and troubleshooting
manuals for the Anafi are on [Parrot's support site](https://www.parrot.com/en/support/anafi/how-to-repair-my-anafi-series-drone).
