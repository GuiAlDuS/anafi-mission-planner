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
2. Flight Plan mode: the plans are listed under the mission name ("name 1",
   "name 2" for multi-battery missions).
3. Open each plan and **set the camera to GPS lapse**: the camera mode isn't
   stored in the plan files (FreeFlight always writes video start/stop there).

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
