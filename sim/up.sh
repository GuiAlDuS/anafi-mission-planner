#!/usr/bin/env bash
# Start the Sphinx container and firmwared. Run sim/anafi.sh next to spawn the drone.
#   sim/up.sh            start (needs ulogger loaded: see below)
#   sim/down.sh          stop
# Host side: ulogger.ko must be loaded (the container shares the host kernel):
#   sudo insmod sim/cache/ulogger/ulogger-$(uname -r).ko
# Launch the drone with sim/anafi.sh, never a bare `sphinx` command: it forces
# ::wifi_iface= (empty). The default "auto" makes Sphinx take the host's Wi-Fi card
# (renamed eth0, used as the drone's access point), which cut this PC's internet on
# 2026-11-03. Olympe reaches the drone over the virtual link fd_veth0 at 10.202.0.1.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
image="anafi-sphinx:2.25.2"
name="anafi-sim"
mkdir -p "$here/cache/firmware" "$here/cache/runs" "$here/missions" \
  "$here/cache/firmwared/cache/mount_points" "$here/cache/firmwared/firmwares"

# Simulator firmware for the Anafi 4K (PC build, 1.10.4, ~40 MB), downloaded once.
fw="$here/cache/firmware/anafi-pc.ext2.zip"
if [[ ! -f $fw ]]; then
  echo "downloading the simulated Anafi firmware..."
  curl -fL --progress-bar -o "$fw.part" \
    "https://firmware.parrot.com/Versions/anafi/pc/%23latest/images/anafi-pc.ext2.zip"
  mv "$fw.part" "$fw"
fi

if [[ ! -e /dev/ulog_main ]]; then
  ko="$("$here/build-ulogger.sh" | tail -1)"
  echo "ulogger is not loaded. Run:  sudo insmod $ko" >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -qx "$name"; then
  echo "container $name already running"
else
  docker rm -f "$name" >/dev/null 2>&1 || true
  # privileged + host network: firmwared builds network namespaces, veth pairs and
  # mounts for each simulated drone. FIRMWARED_DISABLE_APPARMOR: the Arch kernel
  # runs without AppArmor (the drone firmware then runs unconfined in the container).
  # firmwared's folders are host bind mounts: overlayfs can't stack on Docker's overlay2.
  # /lib/modules: firmwared modprobes host modules (ifb, iptable_nat); without them the
  # drone's network setup fails ("invoke_net_helper config returned -125") and it never boots.
  docker run -d --name "$name" \
    --privileged --network host --gpus all \
    -e DISPLAY="${DISPLAY:-:0}" -e XAUTHORITY=/home/sim/.Xauthority -e FIRMWARED_DISABLE_APPARMOR=y \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v "${XAUTHORITY:-$HOME/.Xauthority}:/home/sim/.Xauthority:ro" \
    -v /dev:/dev \
    -v /lib/modules:/lib/modules:ro \
    -v "$here/cache/firmware:/firmware" \
    -v "$here/cache/firmwared/cache:/var/cache/firmwared" \
    -v "$here/cache/firmwared/firmwares:/usr/share/firmwared/firmwares" \
    -v "$here/cache/runs:/runs" \
    -v "$here/missions:/missions:ro" \
    "$image" sleep infinity >/dev/null
  echo "container $name started"
fi

if ! docker exec "$name" fdc ping >/dev/null 2>&1; then
  docker exec -d "$name" bash -c 'firmwared > /runs/firmwared.log 2>&1'
  for _ in $(seq 20); do
    docker exec "$name" fdc ping >/dev/null 2>&1 && break
    sleep 0.5
  done
fi
docker exec "$name" fdc ping
