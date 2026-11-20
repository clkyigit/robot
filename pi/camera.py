"""Kamera: tek bir Picamera2 örneği hem fotoğraf hem canlı görüntü için kullanılır.

Picamera2 yoksa (ya da kamera açılamazsa) fotoğraflar rpicam-still ile çekilir
ve canlı görüntü kapalı olur.
"""

import asyncio
import io
import json
import logging
import os
import pathlib
import threading
import time
from typing import Callable, Optional

log = logging.getLogger("robot.camera")

WIDTH, HEIGHT = 1280, 720
STREAM_FPS = 10
IDLE_STOP_SECONDS = 30   # kimse izlemiyorsa kamera bu kadar sonra kapanır


def _parse_rotation(value) -> int:
    """Döndürme ayarını 0/90/180/270'e çevirir; geçersizse 0 (dönüş yok) olur."""
    try:
        v = int(str(value).strip()) % 360
    except (TypeError, ValueError):
        log.warning("ROBOT_CAMERA_ROTATION geçersiz (%r), 0 kullanılıyor", value)
        return 0
    if v not in (0, 90, 180, 270):
        log.warning("ROBOT_CAMERA_ROTATION 0/90/180/270 olmalı (%r), 0 kullanılıyor", value)
        return 0
    return v


# Görüntü yönü: tek ayar. Değer, görüntünün SAAT YÖNÜNÜN TERSİNE kaç derece döndürüleceğidir.
# Yatay (1280x720) görüntü için 0 ya da 180 kullan; 90/270 görüntüyü dikey yapar.
ROTATION = _parse_rotation(os.environ.get("ROBOT_CAMERA_ROTATION", "0"))

MAX_ATTEMPTS = 3                  # çok bulanıksa en fazla bu kadar çekilir, en net olan seçilir
MIN_SHARPNESS = float(os.environ.get("ROBOT_CAMERA_MIN_SHARPNESS", "30"))   # Laplacian varyansı eşiği
AF_TIMEOUT = 3.0                  # otomatik odak için en fazla bekleme (sn)
PHOTO_DIR = pathlib.Path(os.environ.get("ROBOT_PHOTO_DIR", str(pathlib.Path.home() / "robot_fotolar")))
MAX_SAVED_PHOTOS = 300            # diskte tutulan en fazla kare (eskiler silinir)

try:
    from picamera2 import Picamera2
except ImportError:  # venv sistem paketlerini görmüyorsa
    Picamera2 = None


def sharpness(jpeg: bytes) -> float:
    """Görüntünün netliğini ölçer: gri tonlamanın Laplacian varyansı (yüksek = net, düşük = bulanık)."""
    import numpy as np
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg)).convert("L")
    if im.width > 640:   # hız için küçült; gürültü etkisi de azalır
        im = im.resize((640, max(1, round(im.height * 640 / im.width))))
    a = np.asarray(im, dtype=np.float32)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return 0.0
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    return float(lap.var())


def rotate_jpeg(jpeg: bytes, degrees: int) -> bytes:
    """JPEG'i saat yönünün tersine 0/90/180/270 derece döndürür (0 ise aynen döner)."""
    degrees = _parse_rotation(degrees)
    if degrees == 0:
        return jpeg
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg))
    im.load()
    T = getattr(Image, "Transpose", Image)   # eski Pillow sürümleriyle de çalışsın
    method = {90: T.ROTATE_90, 180: T.ROTATE_180, 270: T.ROTATE_270}[degrees]
    out = io.BytesIO()
    im.convert("RGB").transpose(method).save(out, "JPEG", quality=90)
    return out.getvalue()


class Camera:
    def __init__(self) -> None:
        self._cam = None
        self._lock = threading.Lock()
        self._last_use = 0.0
        self._last_frame: Optional[bytes] = None
        self._last_frame_time = 0.0
        self.enabled = True   # gizlilik anahtarı
        self.rotation = ROTATION
        self.photo_dir = PHOTO_DIR
        self.last_photo_info: Optional[dict] = None   # son fotoğrafın kaydı (dosya, netlik, ...)

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
            self._set_continuous_af(cam)
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
            raw = self._grab_raw()
            try:
                self._last_frame = rotate_jpeg(raw, self.rotation)
            except Exception as e:  # noqa: BLE001
                log.warning("Kare döndürülemedi: %s", e)
                self._last_frame = raw
            self._last_frame_time = time.time()
            return self._last_frame

    def _grab_raw(self) -> bytes:
        """Çalışan kameradan o anki kareyi (döndürmeden, önbelleksiz) JPEG olarak alır. Kilit tutulmalı."""
        buf = io.BytesIO()
        self._cam.capture_file(buf, format="jpeg")
        return buf.getvalue()

    @staticmethod
    def _set_continuous_af(cam) -> None:
        """Canlı görüntü için sürekli otomatik odak (Camera Module 3). Odaksız kamerada sessizce geçer."""
        try:
            from libcamera import controls
            if "AfMode" in cam.camera_controls:
                cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
        except Exception as e:  # noqa: BLE001
            log.debug("Sürekli odak ayarlanamadı: %s", e)

    def _autofocus(self) -> bool:
        """Tek seferlik otomatik odak başlatır, oturmasını bekler. Odak oturduysa True döner."""
        cam = self._cam
        try:
            from libcamera import controls
            if "AfMode" not in cam.camera_controls:
                return False   # odak motoru yok (sabit odaklı kamera)
            cam.set_controls({"AfMode": controls.AfModeEnum.Auto})
            time.sleep(0.1)
            cam.set_controls({"AfTrigger": controls.AfTriggerEnum.Start})
            time.sleep(0.25)   # durum bilgisi birkaç kare gecikir
            deadline = time.time() + AF_TIMEOUT
            focused = False
            while time.time() < deadline:
                state = cam.capture_metadata().get("AfState")
                if state == 2:      # Focused
                    focused = True
                    break
                if state == 3:      # Failed
                    break
                time.sleep(0.05)
            time.sleep(0.2)         # mercek otursun, görüntü durulsun
            if not focused:
                log.info("Otomatik odak oturmadı (zaman aşımı ya da başarısız)")
            return focused
        except Exception as e:  # noqa: BLE001
            log.warning("Otomatik odak çalışmadı: %s", e)
            return False

    def _best_of(self, grab: Callable[[], Optional[bytes]],
                 prepare: Optional[Callable[[], object]] = None) -> tuple[Optional[bytes], float, list, bool]:
        """En fazla MAX_ATTEMPTS kare çeker; yeterince netse durur, değilse en netini seçer.

        (en_iyi_kare, netlik, tüm_netlikler, son_odak_durumu) döner.
        """
        best: Optional[bytes] = None
        best_score = -1.0
        scores: list = []
        focused = False
        for _ in range(MAX_ATTEMPTS):
            if prepare:
                focused = bool(prepare())
            try:
                data = grab()
            except Exception as e:  # noqa: BLE001
                log.warning("Kare alınamadı: %s", e)
                continue
            if not data:
                continue
            try:
                score = sharpness(data)
            except Exception as e:  # noqa: BLE001
                log.warning("Netlik ölçülemedi: %s", e)
                score = 0.0
            scores.append(round(score, 1))
            if score > best_score:
                best, best_score = data, score
            if score >= MIN_SHARPNESS:
                break
            log.info("Kare bulanık (netlik %.1f < %.1f), yeniden çekiliyor", score, MIN_SHARPNESS)
        return best, max(best_score, 0.0), scores, focused

    def _finalize(self, best: bytes, score: float, scores: list, focused: bool, source: str) -> bytes:
        """Seçilen kareyi döndürür, zaman damgasıyla diske kaydeder ve log'a yazar."""
        try:
            out = rotate_jpeg(best, self.rotation)
        except Exception as e:  # noqa: BLE001
            log.warning("Fotoğraf döndürülemedi, ham kare kullanılıyor: %s", e)
            out = best
        now = time.time()
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now)) + f"_{int((now % 1) * 1000):03d}"
        info = {"zaman": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "dosya": f"{stamp}.jpg",
                "kaynak": source, "netlik": round(score, 1), "denemeler": scores, "odak_oturdu": focused,
                "dondurme": self.rotation, "bayt": len(out)}
        try:
            self.photo_dir.mkdir(parents=True, exist_ok=True)
            (self.photo_dir / info["dosya"]).write_bytes(out)
            with (self.photo_dir / "foto_log.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(info, ensure_ascii=False) + "\n")
            self._prune_saved()
        except OSError as e:
            info["dosya"] = ""
            log.warning("Fotoğraf diske kaydedilemedi: %s", e)
        self.last_photo_info = info
        log.info("Fotoğraf çekildi: %s", json.dumps(info, ensure_ascii=False))
        return out

    def _prune_saved(self) -> None:
        files = sorted(self.photo_dir.glob("*.jpg"))
        for old in files[:-MAX_SAVED_PHOTOS]:
            try:
                old.unlink()
            except OSError:
                pass

    def _photo_sync(self) -> Optional[bytes]:
        """Picamera2 ile fotoğraf: önce odakla, bulanıksa tekrarla, en netini döndür."""
        with self._lock:
            self._last_use = time.time()
            if not self._ensure_started():
                return None
            best, score, scores, focused = self._best_of(self._grab_raw, self._autofocus)
            self._set_continuous_af(self._cam)
            if best is None:
                return None
            return self._finalize(best, score, scores, focused, "picamera2")

    def _still_fallback(self) -> Optional[bytes]:
        """Picamera2 yoksa rpicam-still ile (otomatik odaklı, aynı netlik/döndürme/kayıt kurallarıyla)."""
        import subprocess
        path = pathlib.Path("/tmp/robot_foto.jpg")

        def grab() -> Optional[bytes]:
            r = subprocess.run(["rpicam-still", "-n", "-t", "2000", "--autofocus-mode", "auto",
                                "--autofocus-on-capture", "--width", "1536", "--height", "864",
                                "-o", str(path)], capture_output=True)
            return path.read_bytes() if r.returncode == 0 else None

        with self._lock:
            self._stop()
            best, score, scores, _ = self._best_of(grab)
            if best is None:
                return None
            return self._finalize(best, score, scores, False, "rpicam-still")

    # ---------------------------------------------------------------- dışa açık

    async def photo(self) -> Optional[bytes]:
        if not self.enabled:
            return None
        img = await asyncio.to_thread(self._photo_sync)
        if img is None:
            img = await asyncio.to_thread(self._still_fallback)
        return img

    async def frame(self) -> Optional[bytes]:
        if not self.enabled:
            return None
        return await asyncio.to_thread(self._capture_jpeg)

    def _glance_sync(self, step: int) -> Optional["object"]:
        """Bakış için küçük gri kare (numpy, JPEG'siz, kaydedilmez). Döndürme ayarı uygulanır."""
        import numpy as np
        with self._lock:
            self._last_use = time.time()
            if not self._ensure_started():
                return None
            arr = self._cam.capture_array("main")
        g = arr[::step, ::step, :3].mean(axis=2).astype(np.uint8)
        if self.rotation:
            g = np.ascontiguousarray(np.rot90(g, k=self.rotation // 90))   # saat yönünün tersine, foto ile aynı
        return g

    async def glance(self, step: int = 4):
        """Hızlı küçük gri kare (1280x720 için step=4 -> 320x180). Gizlilik kapalıysa None."""
        if not self.enabled:
            return None
        try:
            return await asyncio.to_thread(self._glance_sync, step)
        except Exception as e:  # noqa: BLE001
            log.warning("Bakış karesi alınamadı: %s", e)
            return None

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
