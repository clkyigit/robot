"""Kamera: tek bir Picamera2 örneği hem fotoğraf hem canlı görüntü için kullanılır.

Picamera2 yoksa (ya da kamera açılamazsa) fotoğraflar rpicam-still ile çekilir
ve canlı görüntü kapalı olur.
"""

import asyncio
import io
import logging
import pathlib
import threading
import time
from typing import Optional

log = logging.getLogger("robot.camera")

WIDTH, HEIGHT = 1280, 720
STREAM_FPS = 10
IDLE_STOP_SECONDS = 30   # kimse izlemiyorsa kamera bu kadar sonra kapanır

try:
    from picamera2 import Picamera2
except ImportError:  # venv sistem paketlerini görmüyorsa
    Picamera2 = None


class Camera:
    def __init__(self) -> None:
        self._cam = None
        self._lock = threading.Lock()
        self._last_use = 0.0
        self._last_frame: Optional[bytes] = None
        self._last_frame_time = 0.0
        self.enabled = True   # gizlilik anahtarı

    # ---------------------------------------------------------------- iç işler

    def _ensure_started(self) -> bool:
        if self._cam is not None:
            return True
        if Picamera2 is None:
            return False
        try:
            cam = Picamera2()
            cam.configure(cam.create_video_configuration(main={"size": (WIDTH, HEIGHT), "format": "RGB888"}))
            cam.start()
            time.sleep(0.8)  # pozlama otursun
            self._cam = cam
            log.info("Kamera açıldı")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Kamera açılamadı: %s", e)
            self._cam = None
            return False

    def _stop(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            except Exception:  # noqa: BLE001
                pass
            self._cam = None
            log.info("Kamera kapandı")

    def _capture_jpeg(self) -> Optional[bytes]:
        with self._lock:
            self._last_use = time.time()
            # aynı anda birden fazla izleyici varsa aynı kareyi paylaş
            if self._last_frame and time.time() - self._last_frame_time < 1.0 / STREAM_FPS:
                return self._last_frame
            if not self._ensure_started():
                return None
            buf = io.BytesIO()
            self._cam.capture_file(buf, format="jpeg")
            self._last_frame = buf.getvalue()
            self._last_frame_time = time.time()
            return self._last_frame

    def _still_fallback(self) -> Optional[bytes]:
        import subprocess
        path = pathlib.Path("/tmp/robot_foto.jpg")
        with self._lock:
            self._stop()
            r = subprocess.run(["rpicam-still", "-n", "-t", "1500", "--width", "1536", "--height", "864",
                                "-o", str(path)], capture_output=True)
            if r.returncode != 0:
                return None
            return path.read_bytes()

    # ---------------------------------------------------------------- dışa açık

    async def photo(self) -> Optional[bytes]:
        if not self.enabled:
            return None
        img = await asyncio.to_thread(self._capture_jpeg)
        if img is None:
            img = await asyncio.to_thread(self._still_fallback)
        return img

    async def frame(self) -> Optional[bytes]:
        if not self.enabled:
            return None
        return await asyncio.to_thread(self._capture_jpeg)

    async def idle_watcher(self) -> None:
        """Kimse kamerayı kullanmıyorsa kapatır (ısı ve gizlilik için)."""
        while True:
            await asyncio.sleep(5)
            if self._cam is not None and time.time() - self._last_use > IDLE_STOP_SECONDS:
                await asyncio.to_thread(self._locked_stop)

    def _locked_stop(self) -> None:
        with self._lock:
            self._stop()

    async def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            await asyncio.to_thread(self._locked_stop)

    @property
    def active(self) -> bool:
        return self._cam is not None


camera = Camera()
