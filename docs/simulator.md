# Testing in Parrot's simulator (Sphinx)

[Parrot Sphinx](https://developer.parrot.com/docs/sphinx/) runs the Anafi's real
firmware on a PC, in a 3D world with simulated physics, GPS and camera. Our
missions, uploads and Olympe commands work on it exactly as on the drone, at
`10.202.0.1`. It's the safe place to try anything new before a field test.

`sim/` runs it in Docker, so it works on any x86_64 Linux, not only the Ubuntu
releases Parrot supports.

> [!NOTE]
> The simulated drone runs firmware **1.10.4**, the only PC build Parrot offers
> for the Anafi 4K. Real drones are on 1.8.2. Results here are strong evidence,
> not proof: still test on the real drone, with a tiny mission first.

## Results so far (Sphinx 2.25.2, 2026-11-03)

| Test | Result |
|---|---|
| Tiny mission: takeoff (`22`), waypoint (`16`), land (`21`) | ✅ flown end to end in 25 s, started by UID with Olympe |
| Pause, resume, stop, return home (`fly`'s `p`, `s`, `r` keys) | ✅ pause and stop hover within 1 s; resume continues the line; return home flies back and hovers 2 m above home |
| FreeFlight 6 plan from our planner (Send to FreeFlight, flown from the phone), 8 lines at 25 m, 5 m/s (2026-11-04) | ✅ vertical climb over home (0.1 m drift), lines at 25.0 m, back over home, vertical descent to 5 m (within 1.1 m of home) and hover; about 2:30 from takeoff to hover (3:31 estimated with landing). The plan's video actions overrode the app's camera control: one 2.5 min video |
| Same plan with photo actions: tilt −90° at the first waypoint, time lapse every 1.16 s (5.8 m at 5 m/s) (2026-11-04) | ✅ 104 photos, all at −90°, spacing 5.5 m (drone flew 4.7 m/s), front/side overlap 81/82 %. Now the default for Send to FreeFlight |
| 7-line survey at 25 m, 5 m/s, distance-triggered photos (`50001`, `2000`/`2001`) | ✅ 63 photos in GPS-lapse mode, spacing 5.9 m (5.8 m planned), front/side overlap 80/83 % as planned, camera at −90°, 3:00 flown vs 3:20 estimated |

What the simulator taught us:

- **`anafi-plan tiny` climbed on a slant.** The Anafi's takeoff only lifts it to
  about 1 m; the tiny mission then flew straight to its waypoint at 10 m,
  climbing diagonally, the hazard that crashed the first field test. The
  surveys already climbed vertically; the tiny mission now does too, and a test
  checks every kind of mission for legs that change altitude while moving
  sideways.
- **`fly` uploaded to the wrong address** for any host other than the drone's
  Wi-Fi or the Skycontroller. It now uploads to `--host` (the Skycontroller still
  goes through the route to `192.168.42.1`).
- **The Anafi stops at every waypoint.** Each line starts from standstill: a
  photo at the line start, about 3 s to the next, then even spacing. The real
  drone does the same (Pix4Dcapture's flight logs show 3.5 s hovers at every
  line end and start). Parrot's docs mark the waypoint's "pass radius" as
  ignored, and a larger acceptance radius (10 m) changed nothing. It costs time,
  not photo quality: photos are triggered by distance, and the time model
  already includes these stops.
- **The first photo of a line is taken before the drone turns onto the line**
  (still facing the hop from the previous line). The camera points down, so it
  doesn't matter for mapping. `anafi-plan analyze` now splits lines by the
  direction of travel, not the heading, so it isn't fooled by it.
- **Return home doesn't land.** It ends hovering about 2 m above home; `fly` now
  says so and asks you to land with the controller.
- **The drone records video during a flight plan** until the mission switches
  the camera to photos.
- **A branch right above the takeoff point ends the flight** even with a
  vertical climb: in the mountains world the default spawn point is under a
  tree, and the drone hit it at 4.5 m. Open sky above the takeoff point matters.

## Requirements

- x86_64 Linux with Docker
- an NVIDIA graphics card and the NVIDIA Container Toolkit (`--gpus all`)
- the kernel headers for your running kernel, to build Parrot's `ulogger` module
- for QDroneCtrl's camera view: a firewall rule for the simulator's subnet (below)
- about 12 GB of disk: Sphinx and the "landscape-mountains" 3D world, plus a 40 MB
  firmware download

## Run it

```bash
docker build -t anafi-sphinx:2.25.2 sim/   # once; downloads Sphinx and the 3D world
sim/up.sh                                  # first run builds ulogger and tells you to:
sudo insmod sim/cache/ulogger/ulogger-$(uname -r).ko
sim/up.sh                                  # starts the container (downloads the firmware once)
sim/anafi.sh ::pose=lake                   # starts the 3D world and the drone
sim/anafi.sh keyboard                      # optional: fly it with the keyboard (QDroneCtrl)
```

**Firewall:** the drone sends video and telemetry to the PC unprompted, so a
firewall that blocks incoming traffic (firewalld's `block` or `public` zones)
leaves QDroneCtrl without camera or instruments, although its commands work.
Trust only the simulator's private, PC-internal subnet:

```bash
sudo firewall-cmd --permanent --zone=trusted --add-source=10.202.0.0/24 && sudo firewall-cmd --reload
```

In QDroneCtrl press **Connect**; **Tools > Options** lists the keys.

Then use the normal commands with `--host 10.202.0.1`:

```bash
uv run anafi-drone --host 10.202.0.1 status
uv run anafi-plan tiny "48.879455,2.370077" "48.879590,2.370077" --alt 10 -o out/sim_tiny.mavlink
uv run anafi-drone --host 10.202.0.1 fly out/sim_tiny.mavlink
```

At the `lake` spawn point home is 48.879455, 2.370077 (the world sits near
Paris). The drone's distance limit in the simulator is 100 m, so keep test
missions small. Stop with `sim/anafi.sh stop` (drone) and `sim/down.sh`
(container). `sim/anafi.sh status` says what's running. The 3D window shows the
drone, and the web dashboard is at http://localhost:9002.

## Fly it with your Skycontroller 3 and FreeFlight 6

Tested 2026-11-03: Skycontroller 3 (fw 1.8.1) and FreeFlight 6.7.5 on iOS both
work with the simulated drone (fw 1.10.4). The PC's Wi-Fi card becomes the
simulated drone's access point, so the PC needs another way online (Ethernet):

```bash
sim/anafi.sh check-wifi wlan0               # only checks: AP mode, another connection online
sim/anafi.sh start --wifi wlan0 ::pose=lake # prints the drone's network, e.g. Anafi_PC_b8d232
```

The PC's Wi-Fi is off until `sim/anafi.sh stop`. The network is on 2.4 GHz
channel 6 (the Skycontroller 3 has no 5 GHz; `SIM_WIFI_CHANNEL` picks another
one) and has no password.

1. **Phone first.** With the Skycontroller off, join the drone's network
   (`Anafi_PC_…`) in the iPhone's Wi-Fi settings and open FreeFlight 6. It
   finds the drone and shows its camera; you can already fly from the phone.
2. **Then the Skycontroller.** Switch it on and plug it into the phone.
   FreeFlight now talks through the controller, which is still searching for
   the real drone it was paired with, so the drone disappears from the app.
   Tell the controller which network to join: tap the **controller box** on
   FreeFlight's homepage, then **Connect**. FreeFlight shows the Wi-Fi
   networks the controller can see. Choose the PC's `Anafi_PC_…` network.
   Once the controller joins it, the sticks, buttons and camera view all work
   with the simulated drone.

Afterwards the controller is paired with the simulated drone. Pair it with a
real Anafi again with the USB cable (controller USB-A to drone USB-C).

The simulated drone behaves like the real one when the link drops: with no
controller left it starts return home after 1 s.

## ⚠️ Your Wi-Fi

By default Sphinx takes the PC's Wi-Fi card and turns it into the drone's
access point, which cuts the PC's internet if Wi-Fi is how it gets online.
`sim/anafi.sh` always passes `wifi_iface=` (empty) so this can't happen, and
stops the simulation if the PC's Wi-Fi cards change. The only exception is
`--wifi CARD` above, which refuses unless another connection is online. Launch
the drone through `sim/anafi.sh`, never with a bare `sphinx` command.

If it happens anyway: `sim/down.sh`, then
`sudo modprobe -r iwlmvm iwlwifi && sudo modprobe iwlwifi` (Intel cards).

## When something doesn't work

The firmware's own log says why the drone refuses something; Olympe usually
doesn't:

```bash
docker exec anafi-sim ulogcat -d | grep -E "forbidden|defective|KO reason"
```

The log lives in the host's `ulogger` module and survives container restarts:
clear it with `docker exec anafi-sim ulogcat -c` before a new try. What we hit
while setting this up, all fixed in `sim/`:

| Symptom | Cause |
|---|---|
| PC loses internet when the drone starts | Sphinx took the Wi-Fi card (see above) |
| Drone never answers; `invoke_net_helper config returned -125` | firmwared can't load the `ifb` module: `up.sh` mounts the host's `/lib/modules` |
| Plans never start: `takeoff is not ready`, `VCam is defective` | the firmware borrows the container's graphics libraries and needs glibc 2.35: the image is Ubuntu 22.04 with `libgles2` |
| `Smartbattery is defective` | normal for about a minute after boot |
| 3D world quits: `Failed to load Vulkan Driver` | `libvulkan1` missing |
| Sphinx and the 3D world wait for each other forever | the world package doesn't match the Sphinx version (Parrot's "meadow" is still a 2.15.1 build); the Dockerfile pins the world to 2.25.2 |
| QDroneCtrl flies the drone but shows no camera and 0 % battery | the firewall drops the drone's video and telemetry: trust `10.202.0.0/24` (see above) |
| The drone sinks forever when landing | the "empty" world only has ground at the spawn point |
