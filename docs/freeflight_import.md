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

**Taking over:** pushing a stick during the plan pauses it at once and gives
you control (simulator, 2026-11-04). FreeFlight then offers Land, and a play
button to resume; return home also works.

- **Resume** (play) carries on to the waypoint the drone was heading for, and
  the camera tilts back down at the next line start.
- **Photos stay off after a resume.** The drone switches the photo capture off
  when it pauses and doesn't switch it back on. The rest of the plan is flown
  without photos. After a takeover, return home and fly the remaining lines
  as a new plan: in `anafi-gui`, open the mission, set **Finish an
  interrupted survey → Start at line** and press Send to FreeFlight (or
  `anafi-plan plan ... --from-line N`). The grid and line numbers stay the
  same; it's saved as `name_from_line_N`, so it doesn't replace the whole
  survey on the phone. Or fly with `anafi-drone fly` from a laptop instead.
- Repeating the photo start in the plan doesn't work around it: a photo start
  while photos are running stops them, and repeated stop/start pairs jammed the
  camera (skipped starts, 20 s stop timeouts, whole lines without photos, and
  the camera still shooting after landing).

### Camera: set by the plan

The plan's own actions set the camera, and they override FreeFlight's camera
control (photo/video icon, GPS lapse, Single...) as soon as the plan starts.
Found in the simulator (2026-11-04): a plan with video actions filmed video
whatever the camera control said. Our plans carry:

| Where | Action in `savedPlan.json` | FreeFlight sends |
|---|---|---|
| First waypoint (over home, survey altitude) | `Tilt`, angle −90, speed 180 | `2800` (panorama), vertical angle −90 at 180°/s |
| Start of line 1 | `Tilt` as above, then `ImageStartCapture`, period = photo spacing ÷ speed, rectilinear JPEG (`resolution` 12.58) | `2800`, then `2000` with the interval in s |
| Start of every later line | `Tilt` as above | `2800` |
| End of the last line | `ImageStopCapture` | `2001` |

- **Time lapse, not GPS lapse.** The only photo action seen in plan files
  takes a time interval, so the spacing is speed × period: in the simulator
  the drone flew 4.7 m/s instead of 5 and spaced photos 5.5 m instead of 5.8
  (front overlap 81 % instead of 80 %). Photos are also taken on the short
  hops between lines.
- **The tilt is relative** to the current tilt (−90 from level = straight
  down), but the gimbal stops at straight down, so repeating it is safe: it
  brings the camera back down after a manual tilt, at no extra hover time.
  It goes on waypoints, not takeoff: users report that a tilt in the takeoff
  actions makes firmware 1.8.x hover instead of flying the plan.
- In the app, these actions are in the plan's action menu: the button beside
  the plan list whose icon is a page with a "+".
- **At least 2 s between photos** (planned 2.2 s, for jitter). The planner slows the survey down when
  needed (see the [guide](guide.md)). Same rule as Jean-Charles Braun's
  `qgc-to-ff6` converter (QGroundControl survey → `savedPlan.json`,
  <https://gitlab.com/epysod12/qgc-to-ff6>, [tutorial](https://drone-est-captures.fr/wp-content/uploads/2026/10/qgc-to-ff6_tutoriel.pdf)), against
  camera buffer overload and slow SD writes. Its tutorial gives FreeFlight's
  plan limits as 2–150 m altitude and 1–11 m/s speed. It also says commands
  are "sent to the drone in real time" and the link is essential; in the
  simulator a plan kept flying after the link dropped (see
  [simulator](simulator.md)), so that looks wrong, at least for plans our
  program starts.
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
- **Photo plans hold one heading** (the first line's) for the whole flight,
  written as `follow: 0` and the same `yaw` on every waypoint. Tested
  2026-11-09: with `follow: 1` (the app's default) FreeFlight recomputes every
  yaw to face the next waypoint when it re-saves the plan, and the drone then
  turns gradually along each line toward the next one, so photos rotated up
  to 90° within a line (simulator). With `follow: 0` FreeFlight kept our yaws
  (iPhone re-save; it only sets the first waypoint to 360 and keeps view mode
  1), and the drone flying that re-saved file held 86–90° on all 52 photos,
  in the same time. The even lines are flown backwards, which doesn't matter
  with the camera straight down. Video plans keep `follow: 1`.
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

## Notes from drone-est-captures.fr

Jean-Charles Braun flies photogrammetry with an Anafi 4K and writes about it
(in French) at <https://drone-est-captures.fr/>. Useful for us, read 2026-11-09:

- **Two photo modes.** Rectilinear JPEG: 4608 × 3456 (16 MP), 75.5° wide,
  corrected by the firmware; this is what our camera model and plans use, and
  what Pix4Dcapture took. Wide JPEG: 5344 × 4016 (21 MP), 84° wide, not
  corrected. MAVLink plans (QGroundControl, our `.mavlink`) can only take
  rectilinear photos; FreeFlight plans can take wide ones. He prefers wide
  because the firmware's correction leaves some distortion that MicMac handles
  poorly. WebODM did fine with our rectilinear photos, so we keep them
  ([article](https://drone-est-captures.fr/2024/06/18/plan-de-vol-et-photogrammetrie-avec-anafi-qgc/)).
- **At least 2 s between photos**: the rule our planner follows (see Camera,
  above). He added it after a user's report of odd behaviour in flight.
- **Fixed heading.** With the camera straight down he keeps the drone's
  heading fixed for the whole plan, so it doesn't rotate at every waypoint:
  "several seconds per waypoint". In the simulator (2026-11-09) it saved no
  time with our plans, since the drone stops at every waypoint anyway, but it
  matters for FreeFlight plans: with the app's default heading rule the drone
  turned gradually along every line and photos rotated up to 90° within a
  line, so our photo plans now hold one heading (see Known limits and
  [simulator](simulator.md)).
- **Decimal altitude and speed.** FreeFlight's plan screen only offers whole
  numbers, but it keeps decimal values written in the file (`savedPlan.json`).
- **Buying Flight Plan.** He reports (September 2026, ParrotPilots forum) that
  FreeFlight 6's Follow Me and Flight Plan modes can no longer be bought. The
  FreeFlight path only works on a phone that already has Flight Plan. His
  alternative: QGroundControl's Android app flies the Anafi (rectilinear photos
  only), tested by him.
- **Ground control points** fix the "bowl" distortion of surveys that rely on
  the photos' GPS only: six markers (four corners and two inside) measured with
  differential GPS, for a 160 × 40 m site flown at 15 m ([article](https://drone-est-captures.fr/2023/04/16/reduire-les-ecarts-grace-aux-points-de-controle-gcp/)).
