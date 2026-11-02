# Step 3 field test: first flights with `anafi-drone fly`

Goal: confirm that the drone runs our flight plans, and that start, pause,
stop and return home work from the laptop. Start with the tiny mission
(takeoff, one waypoint 15 m away at 10 m, land), then a tiny survey.

## Before leaving

- Field laptop: x86_64 Linux with `uv sync --extra fly` done and tests passing.
- Make the missions for the field's coordinates (at home, with internet):

  ```
  uv run anafi-plan tiny "HOME_LAT,HOME_LON" "WP_LAT,WP_LON" --alt 10 -o out/tiny.mavlink
  ```

- Charged drone batteries, Skycontroller, USB data cable, laptop battery.

## Setup at the field

1. Open area, nobody under the flight path, drone on level ground.
2. Switch on the drone and the Skycontroller; let them connect.
3. Plug the Skycontroller into the laptop. On this desktop the
   "Skycontroller 3" network profile adds the route to the drone by itself;
   elsewhere run `sudo ip route add 192.168.42.1/32 via 192.168.53.1`.
4. Wait for GPS. Repeat until "flight plan ready":

   ```
   uv run anafi-drone --host 192.168.53.1 status
   ```

## Fly

```
uv run anafi-drone --host 192.168.53.1 fly out/tiny.mavlink
```

It uploads the plan if the drone doesn't have it (the drone forgets plans when
switched off), checks battery, GPS, home point, the drone's own readiness and
its altitude/distance limits, then asks you to type `yes`.

While flying it shows one live line (time, battery, state, altitude, speed,
distance from home, mission item) and logs it once per second to
`flight_logs/`. Keys:

| Key | Action |
|---|---|
| `r` | Return home (pauses the plan first) |
| `p` | Pause (hover) / resume |
| `s` | Stop the plan (hover) |
| `q` | Leave the view (press twice while flying; the plan keeps running) |

The Skycontroller stays the primary way to take over. Without the live view:
`anafi-drone --host 192.168.53.1 rth` (or `pause`, `stop`).

## What to check and write down

1. **Tiny mission:** does it take off (`22`), reach the waypoint at 10 m, and
   land (`21`)? If `fly` says the drone didn't start the plan, note the error.
2. **Pause / resume and stop** during a second tiny flight.
3. **Return home:** press `r` during a flight.
4. **Tiny survey with photos:** a small area at 30-40 m. Are photos taken every
   N meters with the camera pointing down? Check the photo count and EXIF
   afterwards (`anafi-plan analyze`).
5. **FreeFlight 6 photo plan:** fly a short FreeFlight plan set to GPS lapse,
   then `uv run anafi-drone pull-all -o drone_plans/` to see what FreeFlight
   sends for photos.
6. Keep the `flight_logs/` CSVs, and after the session copy FreeFlight's logs
   (`anafi-drone freeflight pull-runs`), label each flight with the battery
   that flew it (`anafi-plan runs --label 0042=B`) and check each battery's
   endurance with `anafi-plan runs`.
