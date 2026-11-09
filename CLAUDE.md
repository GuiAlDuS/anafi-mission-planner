# Anafi Mission Planner — project brief

Plan photogrammetry missions for the original **Parrot Anafi** on a computer and
fly them, replacing Pix4Dcapture (gone from the app stores since 2023). Two ways
to fly: **FreeFlight 6** on the iPhone (plans copied over USB) or **a Linux
laptop** through the Skycontroller 3 with Parrot's Olympe SDK.

This file is the brief: rules, where things are documented, what is still open.
Facts and test results live in the docs (one home per topic, see below); link
to them instead of repeating them here.

## Hardware and environment

| Item | Value |
|---|---|
| Drone | Parrot Anafi 4K, firmware 1.8.2 (final) |
| Controller | Skycontroller 3, fw 1.8.1 |
| Phone | iPhone with FreeFlight 6 (Flight Plan mode) |
| Dev machine | x86_64 Linux (Olympe only has x86_64 Linux builds) |
| Simulator | Parrot Sphinx in Docker (`sim/`), Anafi PC firmware **1.10.4**, not 1.8.2 |

## Rules learned the hard way (don't regress)

- **Never change altitude while moving sideways near the ground.** Missions climb
  and descend vertically over home (`home_route` in `planner/ff6.py`, the home
  waypoint in `planner/mavlink.py`); a test checks every mission kind. The first
  field flight (2026-11-01) climbed on a slant into branches.
- **Never Start a plan that is paused on the drone from the ground:** it resumes
  mid-mission on a slant. `FlightSession.start` stops it first.
- **Takeoff more than 10 m from the mission's takeoff point is refused**
  (`preflight_problems`): home would move and the climb would slant.
- **FreeFlight plans start photos once** (line 1) and stop them once: repeated
  photo starts toggle and jam the camera.
- **Photos at least 2.2 s apart.** `SurveySettings.speed_ms` is the speed flown
  (lowered when needed); `max_speed_ms` is what the user asked for. Always use
  `speed_ms` for anything that flies or estimates.
- **FreeFlight photo plans hold one heading** (`follow: 0`, same yaw): with the
  app's `follow: 1` FreeFlight recomputes yaws and photos rotate along lines.
- **A lost link doesn't end a flight plan** (simulator): plans must bring the
  drone back over home on their own. They do; keep it that way.
- **Launch the simulator only through `sim/anafi.sh`** (it passes
  `wifi_iface=`); a bare `sphinx` takes the PC's Wi-Fi card.
- **After editing `ui/static/*.js`, run `scripts/check_gui.py`** (headless
  browser: plan, route, notes, save and reopen, tabs, page errors): unit tests
  don't run the JavaScript. The planner's scripts share one scope and load in
  order (see the top of `ui/static/app.js`): load-time code may only use names
  from its own file or earlier ones.
- Test every new mission feature on a tiny mission (and in the simulator) first.

## Where things are documented

| Topic | Home |
|---|---|
| What the tool does, install, the two ways to fly, safety | `README.md` |
| Using the GUI and every command | `docs/guide.md` |
| Hardware findings, mission file layouts, time model, code layout | `docs/how_it_works.md` |
| Simulator: how to run it, every test result | `docs/simulator.md` |
| FreeFlight plan format, camera actions, known limits, drone-est-captures.fr notes | `docs/freeflight_import.md` |
| Upload API and field checks | `docs/step2_upload_test.md`, `docs/step3_field_test.md` |
| Sharing with the community | `docs/RELEASE_CHECKLIST.md`, `docs/packaging.md` |
| MAVLink commands the Anafi accepts | table in `planner/mavlink.py` |
| Camera model (EXIF of real flights) | `planner/camera.py` |

## Development

- `uv sync --all-extras`, then `uv run pytest`; GUI changes also
  `uv run --with playwright==1.64.1 python scripts/check_gui.py`. Python is pinned to 3.12
  (Olympe breaks on 3.13). Extras: `fly` (Olympe, x86_64 Linux), `ortho`
  (rio-tiler).
- CLIs: `anafi-plan`, `anafi-drone`, `anafi-gui`.

## Still open

- Everything above was last verified in the simulator (fw 1.10.4). On the real
  drone (fw 1.8.2), still to check: lost-link behaviour during a plan, the held
  heading in FreeFlight plans, distance-triggered photos (`50001`, `2000`/`2001`)
  and takeoff/land/RTL in our `.mavlink`, a real stick takeover under `fly`.
- The camera's real minimum photo interval (2 s is a forum rule, not measured).
- Whether FreeFlight 6's Flight Plan mode can still be bought (reported not, Sept 2026).

## References

- Olympe: https://developer.parrot.com/docs/olympe/ · https://github.com/Parrot-Developers/olympe
- Parrot flight plan MAVLink: https://developer.parrot.com/docs/mavlink-flightplan/messages_v1.html
- Parrot forum, flight plans with Olympe: https://forum.developer.parrot.com/t/flightplan-with-olympe/9541
- Parrot forum, mission rejected (missing altitudes or waypoints): https://forum.developer.parrot.com/t/anafi-olympe-mavlink-start-get-startingerrorevent/14394
- QGC → Anafi `.mavlink`: https://github.com/mgr098/anafi-ai-survival-kit
- QGC → FreeFlight 6 (J.-C. Braun): https://gitlab.com/epysod12/qgc-to-ff6, tutorial https://drone-est-captures.fr/wp-content/uploads/2026/10/qgc-to-ff6_tutoriel.pdf
- Drone Est Captures (Anafi know-how, French): https://drone-est-captures.fr/
- Pix4Dcapture legacy: https://support.pix4d.com/hc/en-us/articles/360000240783
