"""Command line: plan a survey, analyze past flights and battery use, write a tiny test mission."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from .analyze import analyze_directory
from .estimate import FlightModel
from .geo import LatLon
from .grid import SurveySettings
from .mavlink import format_mission, survey_mission, tiny_mission, validate_mission
from .plan import plan_geojson, plan_survey
from .runs import endurance_estimate, load_labels, read_runs, save_labels


def parse_latlon(s: str) -> LatLon:
    lat, lon = (float(v) for v in s.replace(" ", ",").split(",") if v)
    return LatLon(lat, lon)


def read_polygon(path: str | Path) -> list[LatLon]:
    """First polygon in a GeoJSON file (Polygon, Feature or FeatureCollection)."""
    obj = json.loads(Path(path).read_text())
    if obj.get("type") == "FeatureCollection":
        geoms = [f["geometry"] for f in obj["features"]]
    elif obj.get("type") == "Feature":
        geoms = [obj["geometry"]]
    else:
        geoms = [obj]
    for g in geoms:
        if g["type"] == "Polygon":
            ring = g["coordinates"][0]
        elif g["type"] == "MultiPolygon":
            ring = g["coordinates"][0][0]
        else:
            continue
        if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat, *_ in ring):
            raise ValueError(f"{path}: coordinates are not WGS84 lon/lat degrees; "
                             "export the layer as EPSG:4326")
        pts = [LatLon(lat, lon) for lon, lat, *_ in ring]
        return pts[:-1] if pts[0] == pts[-1] else pts
    raise ValueError(f"no polygon found in {path}")


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def cmd_plan(args) -> int:
    settings = SurveySettings(altitude_m=args.alt, front_overlap=args.front / 100, side_overlap=args.side / 100,
                              speed_ms=args.speed, angle_deg=args.angle, overshoot_m=args.overshoot)
    model = replace(FlightModel(), endurance_min=args.endurance, reserve_frac=args.reserve / 100)
    plan = plan_survey(read_polygon(args.area), parse_latlon(args.home), settings, model)
    g = plan.grid
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"GSD {g.gsd_m * 100:.2f} cm/px | footprint {settings.footprint_across_m:.0f} x {settings.footprint_along_m:.0f} m")
    print(f"Lines: {len(g.lines)} at {g.angle_deg:.1f} deg, spacing {g.line_spacing_m:.1f} m "
          f"(side overlap {g.side_overlap_actual:.0%}), photo every {g.photo_spacing_m:.1f} m, ~{g.photo_count} photos")
    print(f"Flights: {len(plan.flights)}, total {_mmss(plan.total_s)}")
    rc = 0
    for n, f in enumerate(plan.flights, 1):
        e = f.estimate
        phases = ", ".join(f"{k} {v:.0f}s" for k, v in e.phases_s.items())
        print(f"  Flight {n}: lines {f.first_line_index + 1}-{f.first_line_index + len(f.lines)}, "
              f"{_mmss(e.total_s)}, battery ~{e.battery_used_frac:.0%} [{e.status.value}], "
              f"max {e.max_distance_from_home_m:.0f} m from home")
        print(f"            {phases}")
        items = survey_mission(f.lines, plan.home, settings.altitude_m, settings.speed_ms,
                               g.photo_spacing_m, end_action=args.end)
        problems = validate_mission(items)
        for p in problems:
            print(f"            PROBLEM: {p}", file=sys.stderr)
            rc = 1
        path = out / f"flight_{n}.mavlink"
        path.write_text(format_mission(items))
        print(f"            -> {path}")
    (out / "plan.geojson").write_text(json.dumps(plan_geojson(plan)))
    print(f"Preview: {out / 'plan.geojson'}")
    return rc


def cmd_analyze(args) -> int:
    home = parse_latlon(args.home) if args.home else None
    for d in args.dirs:
        for s in analyze_directory(d, home):
            print(f"== {d}  flight {s.flight_uid}")
            print(f"   {s.photos} photos ({s.photo_mode}), {s.altitude_agl_m:.0f} m AGL, {s.speed_ms:.1f} m/s, {s.lines} lines")
            ls = f"{s.line_spacing_m:.1f} m" if s.line_spacing_m else "n/a"
            so = f"{s.side_overlap:.0%}" if s.side_overlap is not None else "n/a"
            print(f"   photo spacing {s.photo_spacing_m:.1f} m (front {s.front_overlap:.0%}), line spacing {ls} (side {so})")
            print(f"   turn gaps (s): {', '.join(f'{t:.0f}' for t in s.turn_gaps_s)}")
            if s.run_to_first_photo_s is not None:
                print(f"   run start -> first photo: {s.run_to_first_photo_s:.0f} s")
            if home:
                print(f"   from home: first photo {s.first_photo_from_home_m:.0f} m, "
                      f"last {s.last_photo_from_home_m:.0f} m, max {s.max_from_home_m:.0f} m")
    return 0


def cmd_tiny(args) -> int:
    items = tiny_mission(parse_latlon(args.home), parse_latlon(args.target), args.alt)
    problems = validate_mission(items)
    for p in problems:
        print(f"PROBLEM: {p}", file=sys.stderr)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_mission(items))
    print(f"-> {args.out}")
    return 1 if problems else 0


def cmd_runs(args) -> int:
    if args.label:
        labels = load_labels(args.dir)
        for item in args.label:
            flight, _, battery = item.partition("=")
            if not battery:
                print(f"ERROR: --label wants FLIGHT=BATTERY, e.g. 0042=B (got {item!r})", file=sys.stderr)
                return 1
            labels[flight.strip()] = battery.strip()
        save_labels(args.dir, labels)
    runs = read_runs(args.dir)
    if not runs:
        print(f"No flights in {args.dir}. Copy them from the iPhone: anafi-drone freeflight pull-runs")
        return 1
    shown = runs if args.all else [r for r in runs if r.endurance_min is not None]
    print(f"{'flight':6} {'date':16} {'battery':7} {'air':>5} {'charge':>9} {'%/min':>5} {'full lasts':>10} "
          f"{'alt':>4} {'far':>4}  notes")
    for r in shown:
        notes = (["survey"] if r.survey else []) + ([f"low-battery alert at {r.low_battery_at}%"]
                                                     if r.low_battery_at is not None else [])
        lasts = f"{r.endurance_min:.0f} min" if r.endurance_min else "-"
        print(f"{r.id:6} {r.date:16} {r.battery or '?':7} {r.airborne_s / 60:4.1f}m "
              f"{r.battery_start:3d}->{r.battery_end:3d}% {r.drain_pct_per_min:5.1f} {lasts:>10} "
              f"{r.max_altitude_m:4.0f} {r.max_distance_m:4.0f}  {', '.join(notes)}")
    print()
    for name in sorted({r.battery for r in runs if r.battery}) + [None]:
        group = [r for r in runs if r.battery == name] if name else runs
        est = endurance_estimate(group)
        if est:
            minutes, used = est
            who = f"Battery {name}" if name else "All flights"
            span = ", ".join(f"{r.id} {r.endurance_min:.0f} min" for r in used)
            print(f"{who}: plan with an endurance of {math.floor(minutes)} min or less "
                  f"(worst of the last {len(used)} measurable flights: {span}).")
    if not any(r.battery for r in runs):
        print("Tag flights with the battery that flew to compare them: anafi-plan runs --label 0042=B")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="anafi-plan", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="plan a survey over a GeoJSON polygon")
    p.add_argument("area", help="GeoJSON file with the survey polygon")
    p.add_argument("--home", required=True, help="takeoff point 'lat,lon'")
    p.add_argument("--alt", type=float, default=120, help="altitude above takeoff (m)")
    p.add_argument("--front", type=float, default=80, help="front overlap (%%)")
    p.add_argument("--side", type=float, default=80, help="side overlap (%%)")
    p.add_argument("--speed", type=float, default=10, help="survey speed (m/s, 1-10)")
    p.add_argument("--angle", type=float, help="line heading (deg from north); default: fastest")
    p.add_argument("--overshoot", type=float, help="extend lines past the area (m); default: one photo spacing")
    p.add_argument("--end", choices=["rth", "land"], default="rth", help="end of mission action")
    p.add_argument("--endurance", type=float, default=FlightModel.endurance_min, help="usable battery minutes")
    p.add_argument("--reserve", type=float, default=FlightModel.reserve_frac * 100, help="battery reserve (%%)")
    p.add_argument("-o", "--out", default="out", help="output directory")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("analyze", help="summarize flown missions from photo EXIF")
    p.add_argument("dirs", nargs="+")
    p.add_argument("--home", help="takeoff point 'lat,lon'")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("runs", help="battery use per flight from FreeFlight 6's flight logs")
    p.add_argument("dir", nargs="?", default="freeflight_runs", help="folder with the logs (JSON)")
    p.add_argument("--label", action="append", metavar="FLIGHT=BATTERY",
                   help="record which battery flew a flight, e.g. 0042=B (repeatable)")
    p.add_argument("--all", action="store_true", help="also list flights too short to measure")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("tiny", help="write a takeoff -> one waypoint -> land test mission")
    p.add_argument("home", help="takeoff point 'lat,lon'")
    p.add_argument("target", help="waypoint 'lat,lon'")
    p.add_argument("--alt", type=float, default=10)
    p.add_argument("-o", "--out", default="tiny.mavlink")
    p.set_defaults(func=cmd_tiny)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
