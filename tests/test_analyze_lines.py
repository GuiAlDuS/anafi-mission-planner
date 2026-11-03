"""split_lines on synthetic photos: no exiftool or real photos needed."""

import datetime as dt

from planner.analyze import Photo, split_lines
from planner.geo import LatLon, LocalProjection

ORIGIN = LatLon(48.8794, 2.3700)
T0 = dt.datetime(2026, 11, 3, 12, 0, 0)


def _photo(x_m, y_m, t_s, yaw):
    pos = LocalProjection(ORIGIN).to_latlon(x_m, y_m)
    return Photo(path=f"{t_s}.jpg", time=T0 + dt.timedelta(seconds=t_s), pos=pos, agl_m=25,
                 yaw_deg=yaw, photo_mode="GPSLapse", flight_uid="f", run_date=None)


def _survey(lines=3, photos=6, spacing=6.0, line_gap=7.0, first_yaw_off=True):
    """Lines north/south, stop-and-go like the Anafi: the first photo of a line is
    taken at the line start, still facing the sideways hop (yaw 270), then 3 s to
    the second photo while accelerating, then 1.2 s per photo."""
    out, t = [], 0.0
    for i in range(lines):
        x = i * line_gap
        ys = [k * spacing for k in range(photos)]
        north = i % 2 == 0
        if not north:
            ys = ys[::-1]
        for k, y in enumerate(ys):
            yaw = 0 if north else 180
            if k == 0 and first_yaw_off and i > 0:
                yaw = 270
            out.append(_photo(x, y, t, yaw))
            t += 3.0 if k == 0 else 1.2
        t += 8.0  # stop, hop to the next line, stop
    return out


def test_first_photo_facing_the_hop_stays_in_its_line():
    lines = split_lines(_survey())
    assert [len(ln) for ln in lines] == [6, 6, 6]


def test_pix4d_like_flight_with_long_turns():
    lines = split_lines(_survey(lines=4, first_yaw_off=False))
    assert [len(ln) for ln in lines] == [6, 6, 6, 6]


def test_hovering_photos_stay_in_the_line():
    photos = _survey(lines=1)
    p = photos[2]
    photos.insert(3, Photo(**{**p.__dict__, "time": p.time + dt.timedelta(seconds=0.5)}))
    photos.sort(key=lambda q: q.time)
    assert [len(ln) for ln in split_lines(photos)] == [7]
