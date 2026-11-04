"""Start the mission planner web app and open it in the browser."""

from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from .server import serve


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anafi-gui", description=__doc__)
    ap.add_argument("--host", default="127.0.0.1",
                    help="address to listen on (0.0.0.0 to use it from another device on the network)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", default="out", help="folder for saved missions")
    ap.add_argument("--runs", default="freeflight_runs", help="folder with FreeFlight flight logs")
    ap.add_argument("--battery-log", default="battery_checks.json", help="battery health check history")
    ap.add_argument("--orthophotos", default="orthophotos",
                    help="folder for your own orthophotos (GeoTIFF), loaded from the map or dropped here")
    ap.add_argument("--wms-layers", default="wms_layers.json", help="the WMS layers you added (JSON)")
    ap.add_argument("--tiles", action="append", default=[], metavar="NAME=URL",
                    help="add a base map of your own, e.g. 'Orthophoto=https://server/{z}/{x}/{y}.png' "
                         "(repeatable; use only sources whose terms allow it)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)

    try:
        server = serve(args.host, args.port, args.out, args.runs, args.battery_log, extra_tiles=args.tiles,
                       orthophotos=args.orthophotos, wms_layers=args.wms_layers)
    except ValueError as e:
        ap.error(str(e))
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    print(f"Mission planner at {url}  (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server.fly.status is not None and server.fly.status.flying_state not in (None, "landed"):
            print("The drone is flying: it now has no controller here. It carries on with the plan or "
                  "returns home; take over with the Skycontroller.")
        server.fly.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
