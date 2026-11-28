"""Çakışma kontrolü: bileşen kutuları (Pi, servolar, ekran, kamera...) parçalara giriyor mu,
kafa eğilince boyna/kapağa çarpıyor mu. Değerler mm^3 (≈0 olmalı)."""
import math
import numpy as np
from robot_v4 import *

R = 0.5
def grid(x0, x1, y0, y1, z0, z1):
    return (np.arange(x0, x1, R, dtype=f32)[:, None, None], np.arange(y0, y1, R, dtype=f32)[None, :, None],
            np.arange(z0, z1, R, dtype=f32)[None, None, :])


def kafa_m(x, y, z, egim=0.0):   # montaj koordinatında, eğim (x ekseni etrafında)
    a = math.radians(-egim); c, s = math.cos(a), math.sin(a)
    zz = z - EKSEN_Z
    return kafa(x, c * y - s * zz, s * y + c * zz)


def overlap(part, dummy, g, shrink=0.2):
    x, y, z = g
    m = (np.broadcast_to(part(x, y, z), np.broadcast_shapes(x.shape, y.shape, z.shape)) < 0) & (dummy(x, y, z) < -shrink)
    return m.sum() * R ** 3


x0, x1, y0, y1, z0, z1 = pan_servo_kutu()
tx0, tx1, ty0, ty1, tz0, tz1 = tilt_servo_kutu()
lz = EKSEN_Z + KAM_Z
ca, sa = math.cos(math.radians(DC_ACI)), math.sin(math.radians(DC_ACI))
def dc(x, y, z):
    rad = x * ca + y * sa; lat = np.sqrt((x * sa - y * ca) ** 2 + (z - DC_Z) ** 2)
    return np.maximum(lat - 6, np.maximum((G_IC - 18) - rad, rad - (G_IC - 4.6)))
dummies = {
    "Pi kartı":      lambda x, y, z: box(x, y, z, -42.5, 42.5, PI_YB, PI_YB + 1.6, PI_Z0, PI_Z1),
    "Pi bileşenleri": lambda x, y, z: box(x, y, z, -42.5, 42.5, PI_YB, PI_YB + 18, PI_Z0, PI_Z1),
    "HAT yığını":    lambda x, y, z: box(x, y, z, -42.5, 22.5, PI_YB, PI_YB + PI_YIGIN, PI_Z0, PI_Z1),
    "USB/Eth çıkıntı": lambda x, y, z: box(x, y, z, 42.5, 45.5, PI_YB + 1.6, PI_YB + 17.6, PI_Z0 + 2, PI_Z1 - 2),
    "açılı USB-C":   lambda x, y, z: box(x, y, z, PI_USBC_X - 6, PI_USBC_X + 6, PI_YB + 1.6, PI_YB + 9, PLAKA_T + 1.5, PI_Z0),
    "pan servo":     lambda x, y, z: box(x, y, z, x0, x1, y0, y1, z0, z1),
    "pan korna":     lambda x, y, z: cyl(x, y, z, "z", (0, 0), KORNA_D / 2, z1, KORNA_UST),
    "servo kartı":   lambda x, y, z: box(x, y, z, BUS_KART[0], BUS_KART[1], BUS_Y[1], BUS_Y[1] + 10, BUS_KART[2], BUS_KART[3]),
    "DC jak":        dc,
    "tilt servo":    lambda x, y, z: box(x, y, z - EKSEN_Z, tx0, tx1, ty0, ty1, tz0, tz1),
    "tilt korna":    lambda x, y, z: cyl(x, y, z - EKSEN_Z, "x", (0, 0), KORNA_D / 2, tx1, KOL_IC),
    "ekran":         lambda x, y, z: cyl(x, y, z - EKSEN_Z, "y", (0, EKRAN_Z), EKRAN_KART / 2, YUZ_Y + DUVAR, YUZ_Y + DUVAR + EKRAN_KALINLIK),
    "kamera kartı":  lambda x, y, z: box(x, y, z, -12.5, 12.5, KAM_KART_Y, KAM_KART_Y + 1.2, lz - (24 - KAM_LENS_UST), lz + KAM_LENS_UST),
    "kamera lensi":  lambda x, y, z: box(x, y, z, -4.25, 4.25, YUZ_Y - 3, KAM_KART_Y, lz - 4.25, lz + 4.25),
    "2x 608ZZ":      lambda x, y, z: cyl(x, y, z - EKSEN_Z, "x", (0, 0), R608[1] / 2, -KOL_IC - GOBEK_L, -KOL_IC - 2),
    "M8 cıvata":     lambda x, y, z: cyl(x, y, z - EKSEN_Z, "x", (0, 0), 4.0, -KOL_IC - GOBEK_L - 6, -DUZ_X + DUVAR + 12),
    "M8 somun":      lambda x, y, z: cyl(x, y, z - EKSEN_Z, "x", (0, 0), 6.4, -DUZ_X + DUVAR + 3, -DUZ_X + DUVAR + 9.5),
}
g_govde = grid(-56, 56, -56, 56, -1, K_UST + 3)
g_kafa = grid(-62, 56, -56, 56, EKSEN_Z - 56, EKSEN_Z + 56)
parts_lo = {"govde": govde, "alt_plaka": alt_plaka, "kapak": kapak, "boyun": boyun}
print("Bileşen -> parça çakışmaları (mm³):")
bad = 0
for dn, dfn in dummies.items():
    g = g_kafa if dn.startswith(("tilt", "ekran", "kamera", "2x", "M8")) else g_govde
    parts = {"kafa": lambda x, y, z: kafa_m(x, y, z), "boyun": boyun} if g is g_kafa else parts_lo
    for pn, pfn in parts.items():
        v = overlap(pfn, dfn, g)
        if v > 0.5:
            bad += 1
            print(f"  ! {dn} ∩ {pn}: {v:.1f}")
print("  temiz" if not bad else f"  {bad} çakışma")

print("Kafa eğimi -> boyun/kapak çarpması (mm³):")
g = grid(-62, 56, -60, 60, G_UST - 2, EKSEN_Z + 60)
for e in (-40, -25, 0, 25, 40):
    v1 = overlap(lambda x, y, z: kafa_m(x, y, z, e), boyun, g, shrink=0)
    v2 = overlap(lambda x, y, z: kafa_m(x, y, z, e), kapak, g, shrink=0)
    print(f"  eğim {e:+3d}°: boyun {v1:.1f}  kapak {v2:.1f}")
print("Parça-parça (montaj):")
g = grid(-62, 56, -56, 56, -1, K_UST + 30)
for a, b in (("govde", "alt_plaka"), ("govde", "kapak"), ("kapak", "boyun"), ("alt_plaka", "kapak")):
    print(f"  {a} ∩ {b}: {overlap(parts_lo[a], parts_lo[b], g, shrink=0):.1f}")
