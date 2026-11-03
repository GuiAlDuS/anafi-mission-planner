"""Command line for the drone: flight plan upload (web API) and flight control (Olympe)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .iphone import PhoneError
from .upload import (DRONE_HOST, SKYCONTROLLER_HOST, DroneError, FlightPlanClient, NotReachable,
                     Rejected, append_log, pull_all, upload_and_verify)

# The Skycontroller 3 (fw 1.8.1) has no upload module of its own, but it routes
# packets to the drone, so the drone's API is reachable through it at DRONE_HOST.
ROUTE_CMD = f"sudo ip route add {DRONE_HOST}/32 via {SKYCONTROLLER_HOST}"
HINT = (f"Either join the drone's Wi-Fi, or plug the Skycontroller in over USB and add a route "
        f"to the drone through it (lost when the cable is unplugged):\n  {ROUTE_CMD}")
SKYCONTROLLER_404 = (f"The Skycontroller doesn't serve flight plans itself. Use the default host "
                     f"{DRONE_HOST} with this route:\n  {ROUTE_CMD}")


def cmd_ping(c: FlightPlanClient, args) -> int:
    plans = c.list()
    print(f"OK: flight plan API answering at {c.host}, {len(plans)} plan(s) stored")
    return 0


def cmd_list(c: FlightPlanClient, args) -> int:
    plans = c.list()
    if args.json:
        print(json.dumps(plans, indent=2))
        return 0
    for p in plans:
        print(f"{p.get('uid')}  {p.get('date', '')}")
    print(f"{len(plans)} plan(s)")
    cur = c.current()
    if cur:
        print(f"current: {cur.get('uid')}")
    return 0


def cmd_upload(c: FlightPlanClient, args) -> int:
    rec = upload_and_verify(c, args.file, force=args.force)
    append_log(rec, args.log)
    print(f"UID: {rec.uid}")
    print(f"  upload variant: {rec.variant}")
    print(f"  read-back: {rec.readback}, listed on drone: {'yes' if rec.listed else 'NO'}")
    print(f"  logged to {args.log}")
    if rec.readback == "mismatch" or not rec.listed:
        print("WARNING: the drone does not hold the same plan that was sent", file=sys.stderr)
        return 1
    return 0


def cmd_download(c: FlightPlanClient, args) -> int:
    data = c.download(args.uid)
    if args.out:
        Path(args.out).write_bytes(data)
        print(f"-> {args.out}")
    else:
        sys.stdout.write(data.decode(errors="replace"))
    return 0


def cmd_status(c: FlightPlanClient, args) -> int:
    from . import control
    with control.connect(args.host) as dev:
        print(control.format_status(control.read_status(dev)))
    return 0


def cmd_battery(c: FlightPlanClient, args) -> int:
    from . import battery
    log = battery.load_log(args.log)
    if args.history:
        print(battery.format_history(log))
        return 0
    from . import control
    with control.connect(args.host) as dev:
        r = battery.read_battery(dev, args.seconds)
    previous = battery.readings(log)
    known = log["names"].get(r.serial)
    if args.name and known and known != args.name:
        print(f"note: this pack (serial {r.serial}) is already named {known}; kept that name. "
              f"Was the pack swapped and the drone restarted?")
    battery.record(log, r, args.name)
    battery.save_log(args.log, log)
    problems, notes = battery.assess(r, previous)
    print(battery.format_reading(r, problems, notes))
    print(f"logged to {args.log}")
    return 1 if problems else 0


def cmd_health(c: FlightPlanClient, args) -> int:
    from . import control, health
    with control.connect(args.host) as dev:
        r = health.read_health(dev, with_battery=not args.no_battery)
        checks = health.assess(r)
        if args.gimbal:
            print("gimbal test: the camera moves to -90 and 0 deg and takes a photo at each "
                  "(about 30 s)...")
            checks.append(health.gimbal_photo_test(dev))
        print(health.format_report(r, checks))
        summary = None
        if args.watch:
            print()
            summary = health.watch(dev, args.minutes)
            print()
            print(health.format_summary(summary))
    health.append_log(args.log, r, checks, summary)
    print(f"logged to {args.log}")
    bad = any(ch.status == health.FAIL for ch in checks)
    if summary is not None:
        bad = bad or bool(summary.motor_errors) or summary.worst_vibration in ("warning", "critical")
    return 1 if bad else 0


def cmd_fly(c: FlightPlanClient, args) -> int:
    from .fly import fly
    # The Skycontroller has no upload API of its own: plans go to the drone through
    # it (route 192.168.42.1 via 192.168.53.1). Any other host (the drone's Wi-Fi, the
    # simulator at 10.202.0.1) takes the upload itself.
    http_host = DRONE_HOST if args.host == SKYCONTROLLER_HOST else args.host
    return fly(args.host, args.file, min_battery_pct=args.min_battery, http_host=http_host,
               log_dir=args.log_dir)


def _plan_command(name: str):
    def cmd(c: FlightPlanClient, args) -> int:
        from . import control
        with control.connect(args.host) as dev:
            {"rth": control.return_home, "pause": control.pause_plan, "stop": control.stop_plan}[name](dev)
            print(control.format_status(control.read_status(dev)))
        return 0
    return cmd


def cmd_freeflight(c: FlightPlanClient, args) -> int:
    import io
    import zipfile

    from . import iphone
    if args.action == "list":
        for p in iphone.list_plans():
            print(f"{p['uuid']}  {p['title']}")
    elif args.action == "pull":
        for d in iphone.pull_plans(args.out or "freeflight_plans"):
            print(f"-> {d}")
    elif args.action == "pull-runs":
        new = iphone.pull_runs(args.out or "freeflight_runs")
        for f in new:
            print(f"-> {f}")
        print(f"{len(new)} new flight log(s). Battery use: uv run anafi-plan runs")
    else:
        for z in args.zips:
            path = Path(z)
            with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as f:
                files = {n: f.read(n) for n in f.namelist()}
            iphone.send_plan(path.stem, files)
            print(f"sent {path.name}")
        print("Close and reopen FreeFlight 6 to see new plans.")
    return 0


def cmd_pull_all(c: FlightPlanClient, args) -> int:
    written = pull_all(c, args.out)
    for p in written:
        print(f"-> {p}")
    print(f"{len(written)} new file(s) in {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anafi-drone", description=__doc__)
    ap.add_argument("--host", default=DRONE_HOST,
                    help=f"drone web server (default {DRONE_HOST}, over Wi-Fi or through the Skycontroller)")
    ap.add_argument("--timeout", type=float, default=5.0, help="seconds per request")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check the flight plan API answers").set_defaults(func=cmd_ping)

    p = sub.add_parser("list", help="list flight plans stored on the drone")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("upload", help="validate, upload, read back and log a .mavlink file")
    p.add_argument("file")
    p.add_argument("--force", action="store_true", help="upload even if validation finds problems")
    p.add_argument("--log", default="uploads.json", help="upload log (JSON)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("download", help="download one flight plan by UID")
    p.add_argument("uid")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_download)

    sub.add_parser("status", help="battery, GPS, flight plan readiness and limits (Olympe, read-only)"
                   ).set_defaults(func=cmd_status)

    p = sub.add_parser("battery", help="battery health check on the ground: serial, health, cycles, "
                       "voltage, alerts (Olympe, read-only, motors never start)")
    p.add_argument("--name", help="short name for this pack (A, B, ...), remembered by serial")
    p.add_argument("--seconds", type=float, default=5.0, help="how long to sample the voltage")
    p.add_argument("--log", default="battery_checks.json", help="check history (JSON)")
    p.add_argument("--history", action="store_true", help="show past checks, don't connect")
    p.set_defaults(func=cmd_battery)

    p = sub.add_parser("health", help="drone health check (after a crash or repair): motors, sensors, "
                       "compass, gimbal, alarms, GPS, battery (Olympe, read-only, motors never start)")
    p.add_argument("--watch", action="store_true",
                   help="then follow vibration, heading lock and motor errors while you fly by hand")
    p.add_argument("--gimbal", action="store_true",
                   help="also move the camera and compare a photo at -90 and 0 deg (needs an SD card; "
                   "saves 2 photos)")
    p.add_argument("--minutes", type=float, default=10.0, help="longest watch")
    p.add_argument("--no-battery", action="store_true", help="skip the battery part")
    p.add_argument("--log", default="health_checks.json", help="check history (JSON)")
    p.set_defaults(func=cmd_health)

    p = sub.add_parser("fly", help="check, upload if needed, confirm, start and watch a mission (Olympe)")
    p.add_argument("file", help=".mavlink mission")
    p.add_argument("--min-battery", type=int, default=30, help="refuse to start below this battery %%")
    p.add_argument("--log-dir", default="flight_logs", help="telemetry CSV per flight")
    p.set_defaults(func=cmd_fly)

    for name, help_ in [("rth", "return home now (pauses a running flight plan first)"),
                        ("pause", "pause the running flight plan (the drone hovers)"),
                        ("stop", "stop the flight plan (the drone hovers)")]:
        sub.add_parser(name, help=help_).set_defaults(func=_plan_command(name))

    p = sub.add_parser("freeflight", help="FreeFlight 6 on a USB-connected iPhone: plans and flight logs")
    p.add_argument("action", choices=["list", "pull", "send", "pull-runs"],
                   help="list/pull/send plans, or pull-runs to copy new flight logs")
    p.add_argument("zips", nargs="*", help="for send: plan zips from out/<name>/freeflight/")
    p.add_argument("-o", "--out", help="destination folder (pull: freeflight_plans, pull-runs: freeflight_runs)")
    p.set_defaults(func=cmd_freeflight)

    p = sub.add_parser("pull-all", help="download every flight plan on the drone")
    p.add_argument("-o", "--out", default="drone_plans")
    p.set_defaults(func=cmd_pull_all)

    args = ap.parse_args(argv)
    client = FlightPlanClient(args.host, args.timeout)
    try:
        return args.func(client, args)
    except NotReachable as e:
        print(f"ERROR: {e}\n{HINT}", file=sys.stderr)
        return 2
    except Rejected as e:
        hint = f"\n{SKYCONTROLLER_404}" if e.status == 404 and args.host == SKYCONTROLLER_HOST else ""
        print(f"ERROR: {e}{hint}", file=sys.stderr)
        return 1
    except (DroneError, ValueError, PhoneError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted. A running flight plan continues on the drone; use the Skycontroller "
              "or `anafi-drone rth`.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
