#!/usr/bin/env bash
# Stop the simulated drone, firmwared and the container. ulogger stays loaded
# until you run:  sudo rmmod ulogger
set -uo pipefail
name="anafi-sim"
docker exec "$name" bash -c 'pkill -INT sphinx; sleep 2; fdc dropall; fdc quit' >/dev/null 2>&1
docker rm -f "$name" >/dev/null 2>&1 && echo "container $name removed"
lsmod | grep -q '^ulogger' && echo "ulogger still loaded: sudo rmmod ulogger"
exit 0
