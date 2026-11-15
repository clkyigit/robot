"""Robotun kendiliğinden davranması ("iç ses").

Arada bir olaylar olur: sabah oldu, uzun süredir konuşulmadı, odaya biri geldi.
Her olayda robot sahibine yazmak isteyip istemediğine KENDİSİ karar verir (brain.initiative).
Varsayılan olarak sessiz saat ve günlük mesaj sınırı yoktur (istenirse QUIET_HOURS, MAX_INITIATIVES).

Ayarlar (.env):
  INITIATIVE=1            0 yaparsan kapalı başlar (Telegram: /oto ac, /oto kapat)
  QUIET_HOURS=            boş: gece de yazar; ör. 23-8 yazarsan bu saatlerde susar
  MORNING_TIME=08:30      sabah selamı için en erken saat
  SILENCE_HOURS=6         bu kadar saat konuşulmazsa halini sorabilir
  MAX_INITIATIVES=0       günde en fazla kaç kez kendiliğinden yazar (0: sınırsız)
  PRESENCE=1              kameradan hareket algılama (0: kapalı)
  PRESENCE_EVERY=60       kaç saniyede bir kameraya bakar
  ARRIVE_AFTER_MIN=30     bu kadar dakika hareketsizlikten sonra gelen hareket "biri geldi" sayılır
"""

import asyncio
import datetime
import io
import logging
import os
import time
from typing import Awaitable, Callable, Optional

import body
import brain

log = logging.getLogger("robot.initiative")


def _env_bool(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip() != "0"


QUIET = os.environ.get("QUIET_HOURS", "").strip()   # boş: sessiz saat yok (ör. 23-8 yazılabilir)
MORNING_TIME = os.environ.get("MORNING_TIME", "08:30")
SILENCE_HOURS = float(os.environ.get("SILENCE_HOURS", "6"))
MAX_PER_DAY = int(os.environ.get("MAX_INITIATIVES", "0"))   # 0: sınırsız
PRESENCE_EVERY = float(os.environ.get("PRESENCE_EVERY", "60"))
ARRIVE_AFTER = float(os.environ.get("ARRIVE_AFTER_MIN", "30")) * 60
MOTION_PIXEL = 25.0   # bir pikselin "değişti" sayılması için gereken parlaklık farkı (0-255)
MOTION_FRACTION = float(os.environ.get("MOTION_FRACTION", "0.03"))   # görüntünün en az bu oranı değişmeli
PRESENCE_MAX_TEMP = 72.0     # bundan sıcaksa kamerayı yorma (fan bozuk)

Tell = Callable[..., Awaitable[object]]


class Initiative:
    def __init__(self) -> None:
        self.enabled = _env_bool("INITIATIVE")
        self.presence_on = _env_bool("PRESENCE")
        self.day = ""
        self.count = 0
        self.done_today: set[str] = set()
        self.last_motion = time.time()
        self.last_seen_someone = 0.0
        self.presence_error = ""
        self.last_event = ""
        self._prev = None
        self._tell: Optional[Tell] = None
        self._camera = None
        self._photo_sink: Optional[Callable[[bytes], Awaitable[None]]] = None

    def start(self, tell: Tell, camera, photo_sink) -> list[asyncio.Task]:
        self._tell, self._camera, self._photo_sink = tell, camera, photo_sink
        return [asyncio.create_task(self._clock_loop()), asyncio.create_task(self._presence_loop())]

    def status(self) -> dict:
        return {"enabled": self.enabled, "presence": self.presence_on, "today": self.count,
                "max_per_day": MAX_PER_DAY, "quiet_hours": QUIET, "last_event": self.last_event,
                "presence_error": self.presence_error}

    # ------------------------------------------------------------ kurallar

    def _new_day(self, now: datetime.datetime) -> None:
        d = now.strftime("%Y-%m-%d")
        if d != self.day:
            self.day, self.count, self.done_today = d, 0, set()

    @staticmethod
    def quiet(now: datetime.datetime) -> bool:
        if not QUIET:
            return False
        try:
            a, b = (int(x) for x in QUIET.split("-"))
        except ValueError:
            return False
        h = now.hour
        return (a <= h or h < b) if a > b else (a <= h < b)

    def _may_speak(self, now: datetime.datetime) -> bool:
        return self.enabled and not self.quiet(now) and (MAX_PER_DAY <= 0 or self.count < MAX_PER_DAY)

    async def fire(self, kind: str, event: str, photo: bytes | None = None) -> Optional[str]:
        """Olayı beyne sorar; robot yazmak isterse sahibine iletir."""
        now = datetime.datetime.now(brain.TZ)
        self._new_day(now)
        if not self._may_speak(now):
            return None
        self.last_event = f"{now:%H:%M} {kind}"
        msg = await brain.initiative(event)
        log.info("İç ses (%s): %s", kind, (msg or "sessiz kaldı")[:100])
        if not msg:
            return None
        self.count += 1
        if self._tell:
            await self._tell(msg, photo=photo, kind="oto")
        return msg

    # ------------------------------------------------------------ zamanla gelen olaylar

    async def _clock_loop(self) -> None:
        await asyncio.sleep(90)
        mh, mm = (int(x) for x in MORNING_TIME.split(":"))
        while True:
            try:
                now = datetime.datetime.now(brain.TZ)
                self._new_day(now)
                talked_today = any(e.get("role") == "user" for e in brain.day_events(self.day))

                if "sabah" not in self.done_today and (now.hour, now.minute) >= (mh, mm) and now.hour < 12:
                    self.done_today.add("sabah")
                    if not talked_today:
                        y = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                        diary = brain.diary_text(y)
                        await self.fire("sabah", "Sabah oldu, yeni bir gün başladı. Sahibin bugün henüz sana yazmadı."
                                        + (f" Dün akşam günlüğüne şunu yazmıştın: {diary}" if diary else ""))

                last = brain.last_talk_time()
                if (last and "sessizlik" not in self.done_today and 10 <= now.hour < 21
                        and time.time() - last > SILENCE_HOURS * 3600):
                    self.done_today.add("sessizlik")
                    hours = int((time.time() - last) // 3600)
                    await self.fire("sessizlik", f"Sahibinle {hours} saattir konuşmadınız.")
            except Exception as e:  # noqa: BLE001
                log.warning("İç ses hatası: %s", e)
            await asyncio.sleep(60)

    # ------------------------------------------------------------ kameradan hareket

    async def _presence_loop(self) -> None:
        try:
            from PIL import Image, ImageOps
            import numpy as np
        except ImportError:
            self.presence_error = "Pillow ya da numpy yok"
            log.warning("Hareket algılama kapalı: %s", self.presence_error)
            return
        await asyncio.sleep(120)
        while True:
            await asyncio.sleep(PRESENCE_EVERY)
            if not (self.enabled and self.presence_on and self._camera and self._camera.enabled):
                self._prev = None
                continue
            t = body.cpu_temp()
            if t is not None and t > PRESENCE_MAX_TEMP:
                continue
            try:
                await self._camera.frame()             # kamera açılırken pozlama otursun
                await asyncio.sleep(1.5)
                jpg = await self._camera.frame()
                if not jpg:
                    continue
                img = ImageOps.grayscale(Image.open(io.BytesIO(jpg))).resize((48, 27))
                a = np.asarray(img, dtype=np.float32)
                a -= a.mean()                          # ışığın genel değişimini değil, şekil değişimini say
                if self._prev is not None:
                    changed = float((np.abs(a - self._prev) > MOTION_PIXEL).mean())
                    if changed > MOTION_FRACTION:
                        still_for = time.time() - self.last_motion
                        self.last_motion = time.time()
                        if still_for > ARRIVE_AFTER and time.time() - self.last_seen_someone > 2 * 3600:
                            await self._arrival(int(still_for // 60))
                self._prev = a
                self.presence_error = ""
            except Exception as e:  # noqa: BLE001
                self.presence_error = str(e)[:120]
                log.warning("Hareket algılama hatası: %s", e)

    async def _arrival(self, still_min: int) -> None:
        now = datetime.datetime.now(brain.TZ)
        if not self._may_speak(now):
            return
        img = await self._camera.photo()
        if not img:
            return
        seen = await brain.describe_image(
            img, "Odada ya da masanın başında biri var mı? Varsa ne yapıyor? Kimse yoksa sadece 'kimse yok' de.")
        if "kimse yok" in seen.lower()[:40]:
            return
        self.last_seen_someone = time.time()
        await self.fire("biri geldi", f"{still_min} dakikadır önünde hiçbir şey kıpırdamıyordu, şimdi hareket oldu. "
                                      f"Kameranla baktın (fotoğraf çekildi) ve şunu gördün: {seen}", photo=img)


initiative = Initiative()
