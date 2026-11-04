# User guide

Details for the GUI and the command line. The [README](../README.md) has the
short version.

- [The map](#the-map)
- [Map layers](#map-layers)
- [The Batteries tab](#the-batteries-tab)
- [Command line](#command-line)

## The map

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

## Map layers

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

## The Batteries tab

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

`--from-line N` flies only lines N to the end, to finish a survey after a
takeover: the grid, its direction and the line numbers are the ones of the
whole survey, and the remaining lines are split into batteries again. In
`anafi-gui` it's **Finish an interrupted survey → Start at line**: the lines
left out turn grey on the map, and the mission is saved as
`name_from_line_N`. It goes back to 1 whenever you draw, load or clear an
area, so a new survey never skips lines.

A tiny test mission (takeoff, vertical climb over home, one waypoint, land) for first flights:

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
