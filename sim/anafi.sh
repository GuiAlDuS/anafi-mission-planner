#!/usr/bin/env bash
# Spawn the simulated Anafi 4K in the container started by sim/up.sh.
#   sim/anafi.sh [start] [::param=value ...]   3D world + drone, waits until the drone answers
#   sim/anafi.sh stop                          stop drone and 3D world (container keeps running)
#   sim/anafi.sh status                        what is running, is the drone reachable
# Extra ::param=value pairs go to the drone, e.g.  sim/anafi.sh ::pose="0 0 0.2 0 0 0"
# Olympe connects to the simulated drone at 10.202.0.1 (virtual link fd_veth0 on the host).
# Logs: sim/cache/runs/sphinx.log and ue4.log. Web dashboard: http://localhost:9002
# 3D world: SIM_WORLD=landscape-mountains (default) or empty. "empty" has ground only at the spawn
# point, so a drone landing anywhere else sinks forever.
#
# wifi_iface is forced empty: Sphinx's default ("auto") takes the PC's Wi-Fi card and
# cut this PC's internet on 2026-11-03. Overriding it is refused.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
name="anafi-sim"
drone_ip="10.202.0.1"
drone_file="/opt/parrot-sphinx/usr/share/sphinx/drones/anafi4k.drone"
firmware="/firmware/anafi-pc.ext2.zip"   # = sim/cache/firmware/anafi-pc.ext2.zip
runs="$here/cache/runs"
world="${SIM_WORLD:-landscape-mountains}"
[[ $world =~ ^[a-z0-9-]+$ ]] || { echo "bad SIM_WORLD: $world" >&2; exit 2; }

in_container() { docker exec -u sim "$name" "$@"; }
running() { in_container pgrep -f "$1" >/dev/null 2>&1; }
reachable() { ping -c1 -W1 "$drone_ip" >/dev/null 2>&1; }
wifi_cards() { for d in /sys/class/net/*; do [[ -d $d/wireless ]] && basename "$d"; done; true; }

need_container() {
  docker ps --format '{{.Names}}' | grep -qx "$name" \
    || { echo "container $name is not running: run sim/up.sh first" >&2; exit 1; }
}

status() {
  need_container
  running /usr/bin/sphinx && echo "drone (sphinx): running" || echo "drone (sphinx): stopped"
  running UnrealApp && echo "3D world (UE4): running" || echo "3D world (UE4): stopped"
  reachable && echo "drone $drone_ip: reachable" || echo "drone $drone_ip: not reachable"
  echo "Wi-Fi cards on this PC: $(wifi_cards | tr '\n' ' ')"
}

stop() {
  need_container
  in_container pkill -INT -f /usr/bin/sphinx 2>/dev/null || true
  # Sphinx can take well over 10 s to drop a crashed drone; force it after 30 s.
  local deadline=$((SECONDS + 30))
  while running /usr/bin/sphinx && (( SECONDS < deadline )); do sleep 1; done
  if running /usr/bin/sphinx; then
    in_container pkill -KILL -f /usr/bin/sphinx 2>/dev/null || true
    docker exec "$name" fdc dropall >/dev/null 2>&1 || true
  fi
  in_container pkill -f UnrealApp 2>/dev/null || true
  for _ in $(seq 10); do running UnrealApp || break; sleep 1; done
  echo "drone and 3D world stopped"
}

start() {
  for a in "$@"; do
    [[ $a == ::* ]] || { echo "extra arguments must look like ::param=value, got: $a" >&2; exit 2; }
    [[ $a == *wifi_iface* ]] && { echo "refusing to change wifi_iface (it takes the PC's Wi-Fi)" >&2; exit 2; }
  done
  need_container
  [[ -f "$here/cache/firmware/anafi-pc.ext2.zip" ]] \
    || { echo "firmware missing: sim/cache/firmware/anafi-pc.ext2.zip" >&2; exit 1; }
  if running /usr/bin/sphinx; then echo "drone already running"; status; exit 0; fi
  in_container fdc ping >/dev/null 2>&1 || { echo "firmwared not answering: run sim/up.sh" >&2; exit 1; }

  local wifi_before; wifi_before="$(wifi_cards)"
  local args="$drone_file::firmware=$firmware::wifi_iface="
  for a in "$@"; do args+="$a"; done

  running UnrealApp || docker exec -u sim -d "$name" bash -c "parrot-ue4-$world > /runs/ue4.log 2>&1"
  # printf %q keeps quoted values (e.g. a pose with spaces) intact through bash -c.
  docker exec -u sim -d "$name" bash -c "sphinx $(printf '%q' "$args") > /runs/sphinx.log 2>&1"
  echo "starting drone (first start can take a minute)..."

  local deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    if [[ "$(wifi_cards)" != "$wifi_before" ]]; then
      echo "WARNING: this PC's Wi-Fi cards changed ($wifi_before -> $(wifi_cards)). Stopping." >&2
      stop
      echo "To get Wi-Fi back: sudo modprobe -r iwlmvm iwlwifi && sudo modprobe iwlwifi" >&2
      exit 1
    fi
    grep -q "All drones instantiated" "$runs/sphinx.log" 2>/dev/null && reachable && break
    running /usr/bin/sphinx || { echo "sphinx exited, last log lines:" >&2; tail -20 "$runs/sphinx.log" >&2; exit 1; }
    sleep 1
  done

  reachable || { echo "drone not reachable at $drone_ip after 300 s, see $runs/sphinx.log" >&2; exit 1; }
  echo "drone ready at $drone_ip  (dashboard http://localhost:9002)"
}

case "${1:-start}" in
  start) shift || true; start "$@" ;;
  ::*) start "$@" ;;
  stop) stop ;;
  status) status ;;
  -h|--help) sed -n '2,13p' "$0" ;;
  *) echo "usage: sim/anafi.sh [start|stop|status] [::param=value ...]" >&2; exit 2 ;;
esac
