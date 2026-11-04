"""The drone's live camera for the Fly tab, as MJPEG a browser shows in a plain <img>.

The drone streams H.264 over RTSP (rtsp://<drone>/live, RTP over UDP: it doesn't
answer RTSP over TCP). ffmpeg decodes it on this computer and keeps 5 small JPEG
frames a second, so the drone's link carries nothing extra: it streams its live
video to the controller anyway. ffmpeg only runs while a page is watching
(started on the first viewer, stopped 10 s after the last one leaves).
Measured: 7-8 % of one core (Ryzen 5900X), ~27 KB a frame.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

FPS = 5
WIDTH = 640
IDLE_STOP_S = 10.0
RESTART_S = 1.0  # after the stream ends while a page watches
SOI, EOI = b"\xff\xd8", b"\xff\xd9"


class VideoRelay:
    def __init__(self, ffmpeg: str | None = None, popen=subprocess.Popen, clock=time.monotonic):
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.popen, self.clock = popen, clock
        self.url: str | None = None
        self.frame: bytes | None = None
        self.frame_at = 0.0
        self.frames = 0
        self.error: str | None = None
        self.restarts = 0
        self._proc = None
        self._viewers = 0
        self._last_viewer = 0.0
        self._lock = threading.Lock()
        self._new = threading.Condition(self._lock)

    @property
    def available(self) -> bool:
        return self.ffmpeg is not None

    # ---- viewers

    def watch(self, url: str) -> None:
        """A page starts watching `url` (rtsp://...). Starts ffmpeg if needed."""
        if not self.available:
            raise RuntimeError("the camera view needs ffmpeg: install it (e.g. pacman -S ffmpeg)")
        with self._lock:
            self._viewers += 1
            if self._proc is None or self._proc.poll() is not None or self.url != url:
                self._start(url)

    def leave(self) -> None:
        with self._lock:
            self._viewers = max(0, self._viewers - 1)
            self._last_viewer = self.clock()
        threading.Timer(IDLE_STOP_S + 0.5, self._stop_if_idle).start()

    def _stop_if_idle(self) -> None:
        with self._lock:
            if self._viewers == 0 and self.clock() - self._last_viewer >= IDLE_STOP_S:
                self._stop()

    def stop(self) -> None:
        with self._lock:
            self._stop()

    # ---- ffmpeg

    def _start(self, url: str) -> None:
        self._stop()
        self.url, self.error, self.frame, self.frames = url, None, None, 0
        cmd = [self.ffmpeg, "-nostdin", "-loglevel", "error", "-rtsp_transport", "udp", "-i", url, "-an",
               "-vf", f"fps={FPS},scale={WIDTH}:-2", "-q:v", "7", "-f", "image2pipe", "-c:v", "mjpeg", "-"]
        self._proc = self.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read, args=(self._proc,), name="video", daemon=True).start()

    def _stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        self._new.notify_all()

    def _read(self, proc) -> None:
        buf = b""
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:  # JPEGs back to back: cut at each end-of-image marker
                start = buf.find(SOI)
                end = buf.find(EOI, start + 2) if start >= 0 else -1
                if end < 0:
                    buf = buf[start:] if start > 0 else buf
                    break
                with self._lock:
                    self.frame, self.frame_at = buf[start:end + 2], self.clock()
                    self.frames += 1
                    self._new.notify_all()
                buf = buf[end + 2:]
        err = proc.stderr.read().decode(errors="ignore").strip() if proc.stderr else ""
        with self._lock:
            if proc is self._proc or self._proc is None:
                self.error = err.splitlines()[-1] if err else "the video stream ended"
            self._new.notify_all()
            # The drone ends the stream when it reconfigures its camera (photo/video switches
            # during a survey): start again while someone watches.
            restart = proc is self._proc and self._viewers > 0 and self.url
        if restart:
            threading.Timer(RESTART_S, self._restart_if_wanted, args=(proc,)).start()

    def _restart_if_wanted(self, ended) -> None:
        with self._lock:
            if self._proc is ended and self._viewers > 0:
                self.restarts += 1
                self._start(self.url)

    # ---- for the server

    def next_frame(self, after: float, timeout: float = 5.0) -> tuple[bytes | None, float]:
        """The newest frame newer than `after` (a frame_at), waiting up to `timeout` seconds."""
        with self._lock:
            self._new.wait_for(lambda: self.frame_at > after or self._proc is None, timeout)
            return (self.frame if self.frame_at > after else None), self.frame_at

    def state(self) -> dict:
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            return {"available": self.available, "running": running, "url": self.url, "frames": self.frames,
                    "restarts": self.restarts,
                    "age_s": round(self.clock() - self.frame_at, 1) if self.frame else None, "error": self.error}
