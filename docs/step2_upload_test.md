# Step 2 field test: flight plan upload (no flight)

Goal: confirm the drone's flight plan web API works on Anafi 4K firmware 1.8.2,
collect any plans already stored on the drone, and check the Skycontroller path.
The drone stays on the ground the whole time. Nothing here starts the motors.

All commands run from the project folder on the EndeavourOS desktop.

## Results (2026-10-24)

Drone: Anafi, firmware 1.8.2, hardware 3. Skycontroller 3: firmware 1.8.1.

- **Upload works through the Skycontroller over USB.** No drone Wi-Fi needed.
  The controller has no flight plan API of its own (its web server lists the
  modules `info, fdr, drones, report, system, update, upload-cert, web`, and
  `/api/v1/upload/...` is 404), but it routes packets to the drone. With one
  route added, the drone's own API answers at `192.168.42.1`:

  ```
  sudo ip route add 192.168.42.1/32 via 192.168.53.1
  ```

  The route goes away when the USB cable is unplugged.
- The controller needs a USB **data** cable. It appears as an RNDIS network
  interface (e.g. `enp0s20u1`) with a `192.168.53.x` address.
- The drone's Wi-Fi is WPA2 once paired with the Skycontroller. We didn't need
  its password.
- The drone held no flight plans before the test, so there was nothing to pull.
- `out/tiny.mavlink` uploaded with the first (`raw`) header variant. Read-back
  was identical and the plan is listed, also in the drone's web UI
  ("Flightplans" tab, which has its own upload button).
- **The UID is the MD5 of the file content** (`md5sum out/tiny.mavlink`), so the
  same file always gets the same UID.
- **The drone forgets its flight plans when switched off.** After a power cycle
  the list was empty except for a new upload. Upload after switching the drone
  on, in the same power cycle as the flight (so again after a battery swap).
- Right after boot the drone's clock is at 1970, so plan dates aren't reliable.
  Identify plans by UID.
- The SDK port (TCP 44444) is open on `192.168.53.1`, so Olympe through the
  controller should work (step 3).
- **FreeFlight 6 doesn't list plans uploaded through the API.** Its Flight Plan
  mode works (unlocked) but only shows plans saved in the app (one from 2021).
  It keeps its own library on the phone. Using it to fly our plans would need
  an import into the app (untested).

## FreeFlight 6 plan files

FreeFlight 6 stores each plan on the iPhone as a folder named by a UUID
(visible in the Files app), holding `savedPlan.json` (its own format),
`flightPlan.mavlink` (written on every save) and `screenshot.png`. Three test
plans saved this way all used the same layout, which our writer now copies:

- frame 3 and current = 0 on every line, tab separated, 6 decimals;
- no takeoff, land or return-to-home items;
- speed `178` with p1 = 0, p2 = speed, p3 = -1;
- waypoints `16` with p2 = 5 (acceptance radius), p4 = yaw toward the next
  waypoint (the last repeats the previous), altitude above takeoff;
- `50000` (view mode) p1 = 1 right after the first waypoint. We keep p1 = 0
  instead: Parrot documents 1 as turning steadily between waypoints, while 0
  turns to the waypoint's yaw on arrival and holds it, which suits photo lines;
- video start/stop (`2500`/`2501`) even when the plan is set to GPS lapse in the
  app. FreeFlight doesn't write the photo settings into the file, so these
  plans don't show how firmware 1.8.2 expects distance-triggered photos.

One copy, moved to made-up coordinates, is `tests/data/ff6_prueba.mavlink`.

## Checks for the first field day

Moved to [step3_field_test.md](step3_field_test.md).

## 0. Before connecting (with internet)

Make a tiny test mission. Use the coordinates where the drone will be tested
later; the waypoint here is ~15 m north of home at 10 m altitude.

```
uv run anafi-plan tiny "LAT,LON" "LAT2,LON" --alt 10 -o out/tiny.mavlink
```

## 1. Join the drone's Wi-Fi

Power on the drone (Skycontroller off for this part). Its network is named
`ANAFI-xxxxxxx`. Connect `wlan0` to it; the wired connection keeps internet:

```
nmcli device wifi list ifname wlan0
nmcli device wifi connect "ANAFI-xxxxxxx" ifname wlan0
```

Unknown yet: whether the network has a password, and whether the drone accepts
a direct connection while it is paired with the Skycontroller. Note what happens.

## 2. Check the API and save what's on the drone

```
uv run anafi-drone ping
uv run anafi-drone list
uv run anafi-drone pull-all -o drone_plans/
```

Do `pull-all` **before uploading anything**: the drone drops its oldest plans
when it has too many. Old Pix4Dcapture or FreeFlight plans are real files that
firmware 1.8.2 accepted, and answer the open format questions.

## 3. Upload the tiny mission

```
uv run anafi-drone upload out/tiny.mavlink
```

Record the output: the UID, which upload variant worked (`raw` or
`survival-kit`), and whether the read-back was `identical` or `equivalent`.
The UID is also saved in `uploads.json`.

## 4. Look at it from the other side

- Browser: `http://192.168.42.1/`, "Flightplans" tab. Is the new plan there?
- FreeFlight 6 on the iPhone: does the uploaded plan appear anywhere?

## 5. Through the Skycontroller

Disconnect `wlan0` from the drone, turn the Skycontroller on, let it connect
to the drone, and plug it into the desktop over USB.

```
ip addr          # expect a new USB network interface with a 192.168.53.x address
sudo ip route add 192.168.42.1/32 via 192.168.53.1
uv run anafi-drone ping
uv run anafi-drone list
uv run anafi-drone upload out/tiny.mavlink
```

`http://192.168.53.1/` is the controller's own web UI (no flight plans);
`http://192.168.42.1/` through the route is the drone's.

## What to bring back

- Output of every command above (errors included).
- The `drone_plans/` folder.
- Answers: Wi-Fi password? Direct Wi-Fi while paired? Plan visible in the web UI?
  In FreeFlight 6? Skycontroller USB interface and upload result?
