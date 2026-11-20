"""Robotun yüzü: 1.28" yuvarlak GC9A01 ekranı (240x240, SPI) süren ayrı servis (robot-face.service).

Bağlantı: VCC->pin 17, GND->pin 20, SCL->pin 23 (SCLK), SDA->pin 19 (MOSI), CS->pin 24 (CE0),
          DC->pin 22 (GPIO25), RST->pin 13 (GPIO27), BLK->pin 18 (GPIO24)

Robot ifadeyi /dev/shm/robot-face.json dosyasına yazar ({"expr": "...", "t": zaman, "hold": saniye}).
Bu servis yüzü canlandırır: göz kırpma, etrafa bakınma, ifadeler arası geçiş. Robot bir şey demiyorsa
kendi dinlenme yüzünü gösterir (gece uykulu, sıcakken terli).

Deneme: python3 face.py --demo   (ifadeleri sırayla gösterir)
Kamera bakışı: /dev/shm/robot-gaze.json (gaze.py) taze ise gözler kişiye bakar, kimse yoksa uyuklar.
Ayarlar: FACE_SPI_HZ=40000000, FACE_FPS=15, FACE_ROTATE=0, FACE_BGR=1, FACE_INVERT=1
"""
import json
import math
import os
import pathlib
import random
import sys
import time

import numpy as np

import face_draw

STATE = pathlib.Path("/dev/shm/robot-face.json")
FAN_STATE = pathlib.Path("/dev/shm/robot-fan.json")
GAZE = pathlib.Path("/dev/shm/robot-gaze.json")    # robotun kamerası: kişinin yeri ve var mı (gaze.py)
DC, RST, BL = 25, 27, 24
SPI_HZ = int(os.environ.get("FACE_SPI_HZ", "40000000"))
FPS = float(os.environ.get("FACE_FPS", "15"))
ROTATE = int(os.environ.get("FACE_ROTATE", "0"))
BGR = os.environ.get("FACE_BGR", "1") != "0"
INVERT = os.environ.get("FACE_INVERT", "1") != "0"
IDLE = os.environ.get("FACE_IDLE", "merakli")      # robotun kendi seçtiği dinlenme yüzü

# GC9A01 başlatma dizisi (yaygın kullanılan sürücülerden)
INIT = [
    (0xEF, []), (0xEB, [0x14]), (0xFE, []), (0xEF, []), (0xEB, [0x14]),
    (0x84, [0x40]), (0x85, [0xFF]), (0x86, [0xFF]), (0x87, [0xFF]), (0x88, [0x0A]), (0x89, [0x21]),
    (0x8A, [0x00]), (0x8B, [0x80]), (0x8C, [0x01]), (0x8D, [0x01]), (0x8E, [0xFF]), (0x8F, [0xFF]),
    (0xB6, [0x00, 0x20]), (0x3A, [0x05]), (0x90, [0x08, 0x08, 0x08, 0x08]), (0xBD, [0x06]),
    (0xBC, [0x00]), (0xFF, [0x60, 0x01, 0x04]), (0xC3, [0x13]), (0xC4, [0x13]), (0xC9, [0x22]),
    (0xBE, [0x11]), (0xE1, [0x10, 0x0E]), (0xDF, [0x21, 0x0C, 0x02]),
    (0xF0, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A]), (0xF1, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F]),
    (0xF2, [0x45, 0x09, 0x08, 0x08, 0x26, 0x2A]), (0xF3, [0x43, 0x70, 0x72, 0x36, 0x37, 0x6F]),
    (0xED, [0x1B, 0x0B]), (0xAE, [0x77]), (0xCD, [0x63]),
    (0x70, [0x07, 0x07, 0x04, 0x0E, 0x0F, 0x09, 0x07, 0x08, 0x03]), (0xE8, [0x34]),
    (0x62, [0x18, 0x0D, 0x71, 0xED, 0x70, 0x70, 0x18, 0x0F, 0x71, 0xEF, 0x70, 0x70]),
    (0x63, [0x18, 0x11, 0x71, 0xF1, 0x70, 0x70, 0x18, 0x13, 0x71, 0xF3, 0x70, 0x70]),
    (0x64, [0x28, 0x29, 0xF1, 0x01, 0xF1, 0x00, 0x07]),
    (0x66, [0x3C, 0x00, 0xCD, 0x67, 0x45, 0x45, 0x10, 0x00, 0x00, 0x00]),
    (0x67, [0x00, 0x3C, 0x00, 0x00, 0x00, 0x01, 0x54, 0x10, 0x32, 0x98]),
    (0x74, [0x10, 0x85, 0x80, 0x00, 0x00, 0x4E, 0x00]), (0x98, [0x3E, 0x07]), (0x35, []),
]


class GC9A01:
    def __init__(self) -> None:
        import lgpio
        import spidev
        self.lg = lgpio
        self.h = self._open_chip()
        for pin, val in ((DC, 0), (RST, 1), (BL, 0)):
            lgpio.gpio_claim_output(self.h, pin, val)
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = SPI_HZ
        self.spi.mode = 0
        self.reset()
        for cmd, data in INIT:
            self.command(cmd, data)
        self.command(0x36, [0x08 if BGR else 0x00])     # bellek düzeni / renk sırası
        self.command(0x21 if INVERT else 0x20)           # renk ters çevirme (IPS ekranlarda açık)
        self.command(0x11)
        time.sleep(0.12)
        self.command(0x29)
        time.sleep(0.02)
        self.backlight(True)

    def _open_chip(self):
        lg = self.lg
        for n in range(16):
            try:
                h = lg.gpiochip_open(n)
            except Exception:  # noqa: BLE001
                continue
            try:
                label = str(lg.gpio_get_chip_info(h))
            except Exception:  # noqa: BLE001
                label = ""
            if "rp1" in label.lower() or "bcm2835" in label.lower():
                return h
            lg.gpiochip_close(h)
        raise RuntimeError("GPIO yongası bulunamadı")

    def reset(self) -> None:
        for v, d in ((1, 0.01), (0, 0.01), (1, 0.12)):
            self.lg.gpio_write(self.h, RST, v)
            time.sleep(d)

    def command(self, cmd: int, data: list | bytes | None = None) -> None:
        self.lg.gpio_write(self.h, DC, 0)
        self.spi.writebytes([cmd])
        if data:
            self.lg.gpio_write(self.h, DC, 1)
            self.spi.writebytes2(bytes(data))

    def backlight(self, on: bool) -> None:
        self.lg.gpio_write(self.h, BL, 1 if on else 0)

    def show(self, img) -> None:
        if ROTATE:
            img = img.rotate(ROTATE)
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        buf = rgb565.astype(">u2").tobytes()
        self.command(0x2A, [0, 0, 0, 239])
        self.command(0x2B, [0, 0, 0, 239])
        self.command(0x2C, buf)

    def close(self) -> None:
        try:
            self.backlight(False)
            self.spi.close()
            self.lg.gpiochip_close(self.h)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- canlı yüz

def read_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def cpu_temp() -> float | None:
    try:
        return int(pathlib.Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def read_gaze(now: float) -> dict:
    """Kameradan gelen bakış bilgisi; eskiyse (robot kapalı, kamera kapalı) boş döner."""
    g = read_json(GAZE)
    if not g.get("active") or now - g.get("t", 0) > 10:
        return {}
    return g


def wanted_expression(now: float, gaze: dict | None = None) -> str:
    st = read_json(STATE)
    expr = st.get("expr")
    if expr in face_draw.COLORS and now - st.get("t", 0) < st.get("hold", 5):
        return expr
    t = cpu_temp()
    if t is not None and t >= 75:
        return "sicak"
    if gaze and gaze.get("present") is False:
        return "uykulu"                     # uzun süredir kimse yok
    hour = time.localtime(now).tm_hour
    if hour >= 23 or hour < 7:
        return "uykulu"
    return IDLE if IDLE in face_draw.COLORS else "normal"


class Animator:
    """Göz kırpma, bakış kayması ve ifade geçişi."""

    def __init__(self) -> None:
        self.look = [0.0, 0.0]
        self.look_target = [0.0, 0.0]
        self.next_look = time.time() + 2
        self.next_blink = time.time() + 3
        self.blink_start = 0.0
        self.expr = None
        self.changed = 0.0

    def frame(self, now: float):
        gaze = read_gaze(now)
        expr = wanted_expression(now, gaze)
        if expr != self.expr:
            self.expr, self.changed = expr, now
            self.blink_start = now            # ifade değişirken göz kırpar: yumuşak geçiş
        follow = (gaze.get("look") and now - gaze.get("look_t", 0) < 1.5
                  and expr not in ("dusunuyor", "uykulu"))
        if follow:
            self.look_target = list(gaze["look"])     # kişiye bak
            self.next_look = now + random.uniform(1.0, 2.5)
        elif now >= self.next_look:
            if expr in ("dusunuyor",):
                self.look_target = [0.7, -0.9]
            elif expr in ("uykulu",):
                self.look_target = [0.0, 0.3]
            else:
                self.look_target = [random.uniform(-0.8, 0.8), random.uniform(-0.6, 0.6)]
                if random.random() < 0.4:
                    self.look_target = [0.0, 0.0]          # sık sık karşıya bakar
            self.next_look = now + random.uniform(1.5, 4.5)
        for i in (0, 1):
            self.look[i] += (self.look_target[i] - self.look[i]) * 0.35
        if now >= self.next_blink and expr not in ("uykulu", "mutlu"):
            self.blink_start = now
            self.next_blink = now + random.uniform(2.5, 6.0) * (0.5 if random.random() < 0.15 else 1)
        blink = 0.0
        dt = now - self.blink_start
        if dt < 0.24:
            blink = math.sin(dt / 0.24 * math.pi)
        return face_draw.face(expr, tuple(self.look), blink=blink, t=now, s=2)


def run(demo: bool = False) -> None:
    lcd = GC9A01()
    anim = Animator()
    period = 1.0 / FPS
    exprs = face_draw.EXPRESSIONS
    start = time.time()
    print(f"Yüz servisi başladı ({SPI_HZ // 1_000_000} MHz, {FPS:g} fps)", flush=True)
    try:
        while True:
            t0 = time.time()
            if demo:
                e = exprs[int((t0 - start) // 3) % len(exprs)]
                STATE.write_text(json.dumps({"expr": e, "t": t0, "hold": 5}))
            lcd.show(anim.frame(t0))
            time.sleep(max(0.0, period - (time.time() - t0)))
    finally:
        lcd.close()


if __name__ == "__main__":
    run(demo="--demo" in sys.argv)
