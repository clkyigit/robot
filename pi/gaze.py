"""Bakış ve varlık: robot kamerasıyla önündekini izler.

- Gözler kişiye bakar: yüz bulunursa (OpenCV varsa) yüze, yoksa hareketin olduğu yere.
- Biri gelince sevinir (kısa "mutlu" yüz).
- Uzun süre kimse görünmezse uyuklar (ekran servisi "uykulu" yüz gösterir).

Sonuç /dev/shm/robot-gaze.json dosyasına yazılır; ekran servisi (face.py) okur.
Kareler kaydedilmez, internete gitmez; gizlilik anahtarı kapalıyken kamera hiç açılmaz.

Ayarlar (.env):
  GAZE=1             0: kapalı (gözler rastgele bakınır)
  GAZE_FPS=4         biri varken saniyede kaç kare bakılır
  GAZE_IDLE_EVERY=2  kimse yokken kaç saniyede bir bakılır
  GAZE_AWAY_MIN=5    bu kadar dakika kimse görünmezse uyuklar
  GAZE_MIRROR=0      gözler ters yöne bakıyorsa 1 yap
  GAZE_MAX_TEMP=72   işlemci bundan sıcaksa izlemeye ara verir
"""

import asyncio
import json
import logging
import os
import pathlib
import time

import body
import brain

log = logging.getLogger("robot.gaze")

GAZE_FILE = pathlib.Path("/dev/shm/robot-gaze.json")
ENABLED = os.environ.get("GAZE", "1").strip() != "0"
FPS = max(0.5, float(os.environ.get("GAZE_FPS", "4")))
IDLE_EVERY = max(0.5, float(os.environ.get("GAZE_IDLE_EVERY", "2")))
AWAY_AFTER = float(os.environ.get("GAZE_AWAY_MIN", "5")) * 60
MIRROR = os.environ.get("GAZE_MIRROR", "0").strip() == "1"
MAX_TEMP = float(os.environ.get("GAZE_MAX_TEMP", "72"))
MOTION_PIXEL = 25.0        # bir pikselin "değişti" sayılması için gereken parlaklık farkı
MOTION_MIN = 0.01          # karenin en az bu oranı değişmeli
RECENT = 30.0              # son bu kadar saniyede biri görüldüyse hızlı bak


def look_from(cx: float, cy: float) -> list[float]:
    """Görüntüdeki konum (0..1) -> göz yönü (-1..1).

    Kamera ile ekran aynı yöne bakar: kişi görüntünün solundaysa robotun solunda, yani
    karşıdan bakana göre ekranın sağ tarafındadır; göz bebekleri o yana kayar.
    """
    x = (1.0 - 2.0 * cx) * 0.9
    if MIRROR:
        x = -x
    y = (2.0 * cy - 1.0) * 0.7
    return [round(max(-1.0, min(1.0, x)), 3), round(max(-1.0, min(1.0, y)), 3)]


def _load_face_finder():
    """OpenCV yüz bulucu (varsa). Yoksa None: sadece hareketle bakar."""
    try:
        import cv2
    except ImportError:
        return None
    paths = []
    if getattr(cv2, "data", None):
        paths.append(pathlib.Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    paths += [pathlib.Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
              pathlib.Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml")]
    for p in paths:
        if p.exists():
            c = cv2.CascadeClassifier(str(p))
            if not c.empty():
                return c
    return None


def find_motion(prev, cur):
    """İki gri kare arasındaki hareketin merkezi (0..1, 0..1); hareket yoksa None."""
    import numpy as np
    a = cur.astype(np.float32)
    b = prev.astype(np.float32)
    a -= a.mean()                  # genel ışık değişimini sayma
    b -= b.mean()
    mask = np.abs(a - b) > MOTION_PIXEL
    if mask.mean() < MOTION_MIN:
        return None
    ys, xs = np.nonzero(mask)
    h, w = cur.shape
    return float(xs.mean()) / w, float(ys.mean()) / h


class Gaze:
    def __init__(self) -> None:
        self.enabled = ENABLED
        self.present = True            # açılışta uyuklamasın
        self.last_seen = time.time()
        self.away_since = 0.0
        self.look = None
        self.look_t = 0.0
        self.method = ""
        self.error = ""
        self.frame = None              # son bakış karesi (sadece /bakis için bellekte; kaydedilmez)
        self.spot = None
        self.kind = ""                 # son karede ne bulundu: yüz / hareket / ""
        self.counts = {"kare": 0, "yüz": 0, "hareket": 0}

    def status(self) -> dict:
        return {"enabled": self.enabled, "present": self.present, "method": self.method,
                "last_seen_s": int(time.time() - self.last_seen), "error": self.error}

    def snapshot_png(self) -> bytes | None:
        """Son bakış karesi: bulunan yer işaretli, küçük PNG (hata ayıklama için)."""
        if self.frame is None:
            return None
        import io
        from PIL import Image, ImageDraw
        im = Image.fromarray(self.frame).convert("RGB").resize((640, 360))
        d = ImageDraw.Draw(im)
        if self.spot:
            x, y = self.spot[0] * 640, self.spot[1] * 360
            d.ellipse((x - 18, y - 18, x + 18, y + 18), outline=(255, 60, 60), width=4)
        d.text((8, 8), f"{self.kind or 'bir sey yok'} | {self.counts}", fill=(255, 255, 0))
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()

    def _write(self, active: bool) -> None:
        st = {"t": time.time(), "active": active, "present": self.present,
              "look": self.look, "look_t": self.look_t, "away_since": self.away_since}
        try:
            GAZE_FILE.write_text(json.dumps(st))
        except OSError:
            pass

    def start(self, camera) -> list[asyncio.Task]:
        return [asyncio.create_task(self._loop(camera))]

    async def _loop(self, camera) -> None:
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.error = "numpy yok"
            return
        finder = await asyncio.to_thread(_load_face_finder)
        self.method = "yüz+hareket" if finder is not None else "hareket"
        log.info("Bakış başladı (%s)", self.method)
        await asyncio.sleep(10)
        prev = None
        while True:
            try:
                t = body.cpu_temp()
                if not (self.enabled and camera.enabled) or (t is not None and t > MAX_TEMP):
                    prev = None
                    self._write(False)
                    await asyncio.sleep(5)
                    continue
                g = await camera.glance()
                if g is None:
                    prev = None
                    self._write(False)
                    await asyncio.sleep(5)
                    continue
                spot, kind = None, ""
                if finder is not None:
                    faces = await asyncio.to_thread(finder.detectMultiScale, g, 1.15, 3, 0, (18, 18))
                    if len(faces):
                        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
                        spot, kind = ((x + w / 2) / g.shape[1], (y + h / 2) / g.shape[0]), "yüz"
                if spot is None and prev is not None and prev.shape == g.shape:
                    spot = find_motion(prev, g)
                    kind = "hareket" if spot else ""
                prev = g
                self.frame, self.spot, self.kind = g, spot, kind
                self.counts["kare"] += 1
                if kind:
                    self.counts[kind] += 1
                self._update(spot)
                self.error = ""
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:120]
                log.warning("Bakış hatası: %s", e)
                prev = None
                await asyncio.sleep(5)
            recent = time.time() - self.last_seen < RECENT
            await asyncio.sleep(1.0 / FPS if recent else IDLE_EVERY)

    def _update(self, spot) -> None:
        now = time.time()
        if spot is not None:
            self.look = look_from(*spot)
            self.look_t = now
            self.last_seen = now
            if not self.present:               # uyukluyordu, biri geldi
                self.present = True
                self.away_since = 0.0
                brain.set_face("mutlu", 4)
                log.info("Biri geldi, uyandım")
        elif self.present and now - self.last_seen > AWAY_AFTER:
            self.present = False               # kimse yok: ekran servisi uyuklatır
            self.away_since = now
            log.info("Kimse görünmüyor, uyukluyorum")
        self._write(True)


gaze = Gaze()
