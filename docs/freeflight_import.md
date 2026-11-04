# Flying our plans from FreeFlight 6

Plan on the computer, copy the plans to the iPhone over its USB cable (no
iCloud), and fly them with FreeFlight 6 and the Skycontroller. No laptop in
the field.

**Status (2026-10-24):** FreeFlight 6 lists and opens plans copied this way
(tested with a 4-waypoint plan at 40 m).

**The first flight failed.** The plans then started at line 1, so the drone
climbed on a slant from takeoff toward it and hit tree branches. Plans now
climb straight up over home and come straight down over it (see Known limits;
not flown yet).

## How it works

FreeFlight 6 (`com.parrot.freeflight6`) shares its Documents folder, so a
computer can read and write it over USB with libimobiledevice's
`afcclient --documents`, the same file-sharing service Finder uses. Its plans
live in:

```
/Documents/.Contents/FlightPlan/<UUID>/
    savedPlan.json      FreeFlight's own description of the plan
    flightPlan.mavlink  generated from it
    screenshot.png      map thumbnail (we write a plain grey one)
```

`planner/ff6.py` writes these files, copying plans the app saved itself.
`drone/iphone.py` copies them over. A plan's UUID is derived from the mission
name and flight number, so sending an edited mission again replaces it in
FreeFlight instead of adding a copy.

Other folders in `/Documents/.Contents`: `flightPlanCache` (the plan last
opened) and `runs` (FreeFlight's flight logs since 2021, JSON).

## Setup (once)

- `libimobiledevice` and `usbmuxd` installed (Arch: `pacman -S libimobiledevice usbmuxd`).
- Plug the iPhone in, unlock it and tap "Trust this computer".

## Use

In `anafi-gui`: name the mission and press **Send to FreeFlight** (enabled
while an iPhone is connected). Then on the phone:

1. Close FreeFlight 6 (swipe it away) and reopen it; it reads new plans on start.
2. Flight Plan mode: open the plan. Plans are listed under the mission name
   ("name 1", "name 2" for multi-battery missions). No camera setting is
   needed: the plan sets it.
3. Start the plan. It climbs straight up over the takeoff point, tilts the
   camera straight down, takes photos from the start of the first line to the
   end of the last one, comes back over home and descends to 5 m: land with
   the controller.

### Camera: set by the plan

The plan's own actions set the camera, and they override FreeFlight's camera
control (photo/video icon, GPS lapse, Single...) as soon as the plan starts.
Found in the simulator (2026-11-04): a plan with video actions filmed video
whatever the camera control said. Our plans carry:

| Where | Action in `savedPlan.json` | FreeFlight sends |
|---|---|---|
| First waypoint (over home, survey altitude) | `Tilt`, angle −90, speed 180 | `2800` (panorama), vertical angle −90 at 180°/s |
| Start of line 1 | `ImageStartCapture`, period = photo spacing ÷ speed, rectilinear JPEG (`resolution` 12.58) | `2000`, interval in s |
| End of the last line | `ImageStopCapture` | `2001` |

- **Time lapse, not GPS lapse.** The only photo action seen in plan files
  takes a time interval, so the spacing is speed × period: in the simulator
  the drone flew 4.7 m/s instead of 5 and spaced photos 5.5 m instead of 5.8
  (front overlap 81 % instead of 80 %). Photos are also taken on the short
  hops between lines.
- **The tilt is relative** to the current tilt (−90 from level = straight
  down), and it goes on the first waypoint: users report that a tilt in the
  takeoff actions makes firmware 1.8.x hover instead of flying the plan.
- In the app, these actions are in the plan's action menu: the button beside
  the plan list whose icon is a page with a "+".
- Reference: `tests/data/ff6_sim_photos.mavlink`, our plan as FreeFlight re-saved it.

From the command line:

```
uv run anafi-drone freeflight list                          # plans on the phone
uv run anafi-drone freeflight send out/<name>/freeflight/*.zip
uv run anafi-drone freeflight pull -o freeflight_plans/     # copy all plans back
```

## Known limits

- FreeFlight's first speed item says 6 m/s in a plan whose waypoints say 5; we
  write the plan speed for both. Meaning unknown.
- Each waypoint gets `follow: 1` like the app's own plans, which makes the
  heading turn gradually between waypoints. Fine for nadir photos, untested.
- Climb and descent are vertical over home: the plan's first waypoint is above
  home at survey altitude, and it ends above home at survey altitude, then at
  5 m (`END_ALTITUDE_M` in `planner/ff6.py`). Before 2026-11-02 plans started
  at line 1, and the drone climbed on a slant toward it after takeoff. "Home"
  is the planner's home point, so take off on that spot: any offset turns into
  a slanted climb.
- What happens after the last waypoint is FreeFlight's choice (its own
  return-home setting). Expect it to hover at 5 m above home and land it with
  the controller. The "At the end" setting in the planner only applies to our
  own `.mavlink` files.
