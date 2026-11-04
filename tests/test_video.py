"""The live camera relay (ui/video.py) with a fake ffmpeg."""

import io
import threading

from ui.video import VideoRelay

JPEG_A = b"\xff\xd8" + b"A" * 50 + b"\xff\xd9"
JPEG_B = b"\xff\xd8" + b"B" * 70 + b"\xff\xd9"


class FakeProc:
    def __init__(self, data: bytes):
        self.stdout = io.BytesIO(data)
        self.stderr = io.BytesIO(b"")
        self.done = threading.Event()
        self.cmd = None

    def poll(self):
        return 0 if self.done.is_set() else None

    def terminate(self):
        self.done.set()

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.done.set()


def _relay(data):
    procs = []

    def popen(cmd, **kw):
        p = FakeProc(data)
        p.cmd = cmd
        procs.append(p)
        return p

    return VideoRelay(ffmpeg="/usr/bin/ffmpeg", popen=popen), procs


def test_frames_are_cut_from_the_stream_newest_kept():
    relay, procs = _relay(b"junk" + JPEG_A + JPEG_B[:30])
    relay.watch("rtsp://10.202.0.1/live")
    frame, at = relay.next_frame(0.0, timeout=2)
    assert frame == JPEG_A and relay.frames >= 1
    cmd = procs[0].cmd
    assert "udp" in cmd and "rtsp://10.202.0.1/live" in cmd and "fps=5,scale=640:-2" in cmd
    relay.stop()


def test_one_ffmpeg_for_many_viewers_and_a_new_one_for_another_address():
    relay, procs = _relay(JPEG_A)
    relay.watch("rtsp://192.168.42.1/live")
    relay.watch("rtsp://192.168.42.1/live")
    procs[0].done.clear()  # still running
    relay.watch("rtsp://192.168.42.1/live")
    assert len(procs) == 1
    relay.watch("rtsp://10.202.0.1/live")
    assert len(procs) == 2
    relay.stop()


def test_without_ffmpeg_it_says_so():
    relay = VideoRelay(ffmpeg=None)
    relay.ffmpeg = None
    assert not relay.state()["available"]
    try:
        relay.watch("rtsp://x/live")
    except RuntimeError as e:
        assert "ffmpeg" in str(e)
    else:
        raise AssertionError("expected an error")


def test_restarts_when_the_drone_ends_the_stream_while_watched():
    import time
    relay, procs = _relay(JPEG_A)  # each fake ffmpeg sends one frame, then the stream ends
    relay.watch("rtsp://192.168.42.1/live")
    end = time.monotonic() + 3
    while len(procs) < 2 and time.monotonic() < end:
        time.sleep(0.05)
    assert len(procs) >= 2 and relay.state()["restarts"] >= 1
    relay.leave()
    relay._viewers = 0
    relay.stop()
