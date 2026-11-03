#!/usr/bin/env bash
# Build Parrot's ulogger kernel module (needed by the simulated drone firmware)
# for the running kernel. Nothing is installed: up.sh loads it with insmod and
# down.sh unloads it. Source: ulogger-dkms from https://debian.parrot.com/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="$here/cache/ulogger"
kver="$(uname -r)"
ko="$out/ulogger-$kver.ko"
[[ -f "$ko" ]] && { echo "$ko"; exit 0; }
mkdir -p "$out/src"
cd "$out/src"
if [[ ! -f ulogger-dkms.deb ]]; then
  f=$(for c in main generic; do
        curl -sfL "https://debian.parrot.com/dists/noble/$c/binary-amd64/Packages.gz" | zcat
      done | awk '/^Package: ulogger-dkms$/{p=1} p&&/^Filename:/{print $2; exit}')
  [[ -n "$f" ]] || { echo "ulogger-dkms not found in Parrot's repo" >&2; exit 1; }
  curl -sfL "https://debian.parrot.com/$f" -o ulogger-dkms.deb
fi
rm -rf deb && mkdir deb && (cd deb && ar x ../ulogger-dkms.deb && tar xf data.tar.*)
chmod -R u+rwX deb
src=$(echo deb/usr/src/ulogger-*)
make -s -C "/lib/modules/$kver/build" M="$PWD/$src" modules >/dev/null
cp "$src/ulogger.ko" "$ko"
echo "$ko"
