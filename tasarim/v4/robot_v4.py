"""Masaüstü robot v0.4 — küre kafa + silindir gövde (STL üreteci).

Çalıştır:  python3 robot_v4.py          -> stl/*.stl + onizleme.png + çakışma raporu
Gerekenler: numpy, scikit-image, matplotlib  (OpenSCAD gerekmez)

İçerik
  Kafa : Raspberry Pi Camera Module 3 + 1.28" yuvarlak GC9A01 ekran, tilt servo (ST3215) içinde
  Boyun: pan servo kornasına oturan çatal; bir kolda tilt kornası, diğerinde M3 mil
  Gövde: Pi 5 + M.2 HAT+ (tabanda yatay), Bus Servo Adapter (A) kapağa asılı,
         GPIO karşısında pencere, Pi USB-C ile besleme, 12 V DC jak (bus servo), SD yuvası

"ÖLÇ" yazan değerler üretici çizimi/tahmin: parça elindeyken kumpasla doğrula.
Birim mm. Koordinat: +x sağ, -y ön (yüz), +z yukarı. Gövde tabanı z=0.
"""
import math, os, struct, sys
import numpy as np

RES = float(os.environ.get("RES", 0.35))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stl")
B = 0.3            # tolerans
DUVAR = 2.4

# ---------------- Ölçüler -------------------------------------------------
# ST3215 (üretici: 45.2 x 24.7 x 35). Mil, uzun kenarın bir ucundan 11 mm'de (ÖLÇ).
SV_L, SV_W, SV_H = 45.2, 24.7, 35.0
SV_MIL_UC = 11.0            # ÖLÇ: mil merkezi -> yakın uç
SV_CIKINTI = 6.0            # ÖLÇ: servo üst yüzü -> korna dış yüzü
KORNA_D = 20.0              # ÖLÇ: korna çapı
KORNA_DELIK_C = 14.0        # ÖLÇ: korna vida çemberi
KORNA_VIDA = 2.5            # ÖLÇ: korna vida geçiş deliği (M2 ise 2.4, M3 ise 3.3)

# Pi 5 (85 x 56, delikler 58 x 49, kenardan 3.5). Uzun eksen y, USB/Ethernet +y (arka).
PI_X, PI_Y = 56.0, 85.0
PI_DELIK = [(-24.5, -39.0), (24.5, -39.0), (-24.5, 19.0), (24.5, 19.0)]
PLAKA_T, PI_AYAK = 3.0, 5.0
PI_Z = PLAKA_T + PI_AYAK            # kartın alt yüzü
PI_YIGIN = 38.0                     # ÖLÇ: Pi + Active Cooler + M.2 HAT+ (+SSD)

# Gövde
G_R = 55.0
G_IC = G_R - DUVAR
G_UST = 85.5                        # tüp üst kenarı = kapak alt yüzü
K_T = 4.0                           # kapak kalınlığı
K_UST = G_UST + K_T                 # 89.5
KORNA_UST = K_UST + 1.5             # pan kornasının üst yüzü
SV_PAN_UST = KORNA_UST - SV_CIKINTI # pan servo üst yüzü (85.0)
PLAKA_KULE_ACI = [20, 160, 200, 340]
KAPAK_KULE_ACI = [90, 220, 330]
KULE_R = 48.0

# Boyun
BOYUN_T = 5.0
BOYUN_Z0 = KORNA_UST                # 91
KOL_IC = 39.5                       # kol iç yüzü (|x|)
KOL_T = 5.0
KOL_W = 24.0

# Kafa (orijin = tilt ekseni)
K_R = 53.0
K_IC = K_R - DUVAR
DUZ_X = 38.0                        # yan düzlükler
YUZ_Y = -38.0                       # yüz düzlüğü (Ø74)
BOLME_Y = -16.0                     # ön/arka kabuk ayrımı
EKSEN_Z = BOYUN_Z0 + BOYUN_T + K_R + 5.0      # 154
EKRAN_Z, EKRAN_PENCERE, EKRAN_KART, EKRAN_KALINLIK = -10.0, 33.0, 40.0, 4.0   # ÖLÇ
KAM_Z = 25.0                        # lens merkezi
KAM_KART = (25.0, 24.0)             # en x boy
KAM_LENS_UST = 9.5                  # ÖLÇ: lens merkezi -> kartın üst kenarı
KAM_DELIK = [(-10.5, 7.5), (10.5, 7.5), (-10.5, -5.0), (10.5, -5.0)]   # ÖLÇ: lense göre (x, z)
KAM_KART_Y = -31.0                  # kartın ön yüzü
KAM_LENS_D = 12.0                   # ÖLÇ
# Tilt servo kafada: mil +x, korna dış yüzü x = KOL_IC, gövde yukarı uzanır
SV_T_YUZ = KOL_IC - SV_CIKINTI      # 33.5
KAFA_VIDA = [(-28.0, 28.0), (-28.0, -28.0), (28.0, -28.0), (0.0, -42.0)]  # (x, z)

# ---------------- SDF temel parçalar ----------------------------------------
f32 = np.float32


def box(x, y, z, x0, x1, y0, y1, z0, z1):
    cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
    hx, hy, hz = (x1 - x0) / 2, (y1 - y0) / 2, (z1 - z0) / 2
    qx, qy, qz = np.abs(x - cx) - hx, np.abs(y - cy) - hy, np.abs(z - cz) - hz
    out = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2 + np.maximum(qz, 0) ** 2)
    return out + np.minimum(np.maximum(np.maximum(qx, qy), qz), 0)


def cyl(x, y, z, axis, c, r, a0, a1, r2=None):
    """Silindir/koni. axis 'x'|'y'|'z'; c = diğer iki eksendeki merkez; a0..a1 eksen aralığı."""
    if axis == "z":
        u, v, a = x - c[0], y - c[1], z
    elif axis == "y":
        u, v, a = x - c[0], z - c[1], y
    else:
        u, v, a = y - c[0], z - c[1], x
    rr = np.sqrt(u * u + v * v)
    if r2 is not None:
        t = np.clip((a - a0) / (a1 - a0), 0, 1)
        rad = r + (r2 - r) * t
    else:
        rad = r
    return np.maximum(rr - rad, np.maximum(a0 - a, a - a1))


def sphere(x, y, z, c, r):
    return np.sqrt((x - c[0]) ** 2 + (y - c[1]) ** 2 + (z - c[2]) ** 2) - r


U = np.minimum
def D(a, b): return np.maximum(a, -b)
I = np.maximum


def Un(*a):
    r = a[0]
    for b in a[1:]:
        r = np.minimum(r, b)
    return r


# ---------------- PARÇALAR (tasarım koordinatında) -------------------------
def govde(x, y, z):
    tup = D(cyl(x, y, z, "z", (0, 0), G_R, 0, G_UST), cyl(x, y, z, "z", (0, 0), G_IC, -1, G_UST + 1))
    sol = tup
    # alt plaka kuleleri (M3 heat-set insert, alttan vida) + üstte 45° destek konisi
    for a in PLAKA_KULE_ACI:
        cx, cy = KULE_R * math.cos(math.radians(a)), KULE_R * math.sin(math.radians(a))
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        k = cyl(x, y, z, "z", (cx, cy), 4.0, PLAKA_T, PLAKA_T + 7)
        # duvara bağlı 45° takoz (gövde baş aşağı basılınca desteksiz)
        along, lat = x * ca + y * sa, np.abs(x * sa - y * ca)
        web = I(I(cyl(x, y, z, "z", (0, 0), G_R - 1, PLAKA_T, 40), lat - 4), (KULE_R - 4) - along)
        web = I(web, (z - (PLAKA_T + 7)) - (along - (KULE_R - 4)))
        k = U(k, web)
        k = D(k, cyl(x, y, z, "z", (cx, cy), 2.0, PLAKA_T - 1, PLAKA_T + 6))
        sol = U(sol, k)
    # kapak kuleleri (üstte, M3 insert yukarıdan) + altta 45° koni
    for a in KAPAK_KULE_ACI:
        ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
        cx, cy = KULE_R * ca, KULE_R * sa
        k = U(cyl(x, y, z, "z", (cx, cy), 4.0, G_UST - 8, G_UST), cyl(x, y, z, "z", (cx, cy), 0.3, G_UST - 14, G_UST - 8, 4.0))
        web = I(cyl(x, y, z, "z", (0, 0), G_R - 1, G_UST - 8, G_UST), I(np.abs(x * sa - y * ca) - 3, -(x * ca + y * sa - KULE_R)))
        k = U(k, web)
        k = D(k, cyl(x, y, z, "z", (cx, cy), 2.0, G_UST - 6.5, G_UST + 1))
        sol = U(sol, k)
    # DC jak için düz yatak (kavisli duvarda somun otursun): içte y=45, dışta y=48 -> 3 mm panel
    sol = U(sol, I(cyl(x, y, z, "y", (-20, 68), 9.0, 45.0, 60), cyl(x, y, z, "z", (0, 0), G_R, 0, G_UST)))
    sol = D(sol, cyl(x, y, z, "y", (-20, 68), 9.5, 48.0, 70))
    # --- açıklıklar ---
    kes = Un(
        # USB + Ethernet (arka, +y)
        box(x, y, z, -27, 27, 35, 70, PI_Z + 1, PI_Z + 19),
        # USB-C güç (sol, -x)  — Pi 5'te USB-C, SD tarafındaki uçtan 11.2 mm
        box(x, y, z, -70, -35, -31.3 - 7, -31.3 + 7, PI_Z - 1.3, PI_Z + 7.7),
        # microSD (ön, -y, kartın altında)               ÖLÇ: yuvanın x konumu
        box(x, y, z, -9, 9, -70, -38, PLAKA_T + 1, PI_Z + 2),
        # GPIO penceresi (sağ, +x): başlık boyunca, HAT üstündeki pinlere kadar
        box(x, y, z, 30, 70, -38, 18, PI_Z + 7, PI_Z + 39),
        # 12 V DC jak (arka)                                ÖLÇ: panel jak deliği
        cyl(x, y, z, "y", (-20, 68), 5.5, 30, 70),
        # yan havalandırma (±x)
        *[I(cyl(x, y, z, "x", (yy, zz), 1.3, -70, 70), np.abs(x) - 40) for zz in range(58, 81, 5) for yy in (-9, 9)],
        *[I(box(x, y, z, -70, 70, -9, 9, zz - 1.3, zz + 1.3), np.abs(x) - 40) for zz in range(58, 81, 5)],
    )
    return D(sol, kes)


def alt_plaka(x, y, z):
    p = cyl(x, y, z, "z", (0, 0), G_IC - B, 0, PLAKA_T)
    for (px, py) in PI_DELIK:
        p = U(p, cyl(x, y, z, "z", (px, py), 3.25, PLAKA_T - 0.01, PLAKA_T + PI_AYAK))
    kes = []
    for (px, py) in PI_DELIK:   # M2.5 alttan, baş gömülü
        kes += [cyl(x, y, z, "z", (px, py), 1.45, -1, PI_Z + 1), cyl(x, y, z, "z", (px, py), 2.7, -1, 1.6)]
    for a in PLAKA_KULE_ACI:    # M3 havşa
        cx, cy = KULE_R * math.cos(math.radians(a)), KULE_R * math.sin(math.radians(a))
        kes += [cyl(x, y, z, "z", (cx, cy), 1.7, -1, PLAKA_T + 1), cyl(x, y, z, "z", (cx, cy), 3.3, -0.01, 1.6, 1.7)]
    for i in range(-3, 4):      # havalandırma
        kes.append(box(x, y, z, -18, 18, i * 8 - 12, i * 8 - 8, -1, PLAKA_T + 1))
    for a in (90, 210, 330):    # lastik ayak yuvaları (Ø10)
        kes.append(cyl(x, y, z, "z", (40 * math.cos(math.radians(a)), 40 * math.sin(math.radians(a))), 5.25, -1, 1.0))
    return D(p, Un(*kes))


def pan_servo_kutu():   # servo gövdesi (tasarım koordinatı)
    return (-SV_MIL_UC, SV_L - SV_MIL_UC, -SV_W / 2, SV_W / 2, SV_PAN_UST - SV_H, SV_PAN_UST)


KELEPCE_X = 11.6
def kapak(x, y, z):
    k = cyl(x, y, z, "z", (0, 0), G_R, G_UST, K_UST)
    x0, x1, y0, y1, z0, z1 = pan_servo_kutu()
    kafes = D(box(x, y, z, x0 - B - DUVAR, x1 + B + DUVAR, y0 - B - DUVAR, y1 + B + DUVAR, z0 - 0.3, G_UST + 0.1),
              box(x, y, z, x0 - B, x1 + B, y0 - B, y1 + B, z0 - 5, G_UST - 0.01))
    # kelepçe kuleleri (servo altındaki şerit, 2x M3 insert)
    for s in (-1, 1):
        kafes = U(kafes, D(cyl(x, y, z, "z", (KELEPCE_X, s * 18.5), 3.8, z0 - 0.3, G_UST),
                           cyl(x, y, z, "z", (KELEPCE_X, s * 18.5), 2.0, z0 - 1, z0 + 6)))
    # Bus Servo Adapter (A) askısı: -x tarafta dikey plaka, kelepçe (cırt/zip) yuvaları
    aski = box(x, y, z, -37.5, -34.5, -22, 22, z0 + 3, G_UST + 0.1)
    for zz in (z0 + 10, z0 + 26):
        for yy in (-15, 15):
            aski = D(aski, box(x, y, z, -40, -30, yy - 2, yy + 2, zz - 1.5, zz + 1.5))
    k = Un(k, kafes, aski)
    kes = [cyl(x, y, z, "z", (0, 0), 11.5, G_UST - 1, K_UST + 1),                       # korna + mil
           box(x, y, z, -28.5, -21.5, -13, 13, G_UST - 1, K_UST + 1)]                    # kablo yarığı (kamera FFC + kablolar)
    for a in KAPAK_KULE_ACI:
        cx, cy = KULE_R * math.cos(math.radians(a)), KULE_R * math.sin(math.radians(a))
        kes += [cyl(x, y, z, "z", (cx, cy), 1.7, G_UST - 1, K_UST + 1), cyl(x, y, z, "z", (cx, cy), 1.7, K_UST - 1.7, K_UST + 0.01, 3.4)]
    return D(k, Un(*kes))


def kelepce(x, y, z):   # pan servoyu alttan tutan şerit (kendi koordinatı: tabla z=0)
    p = box(x, y, z, -5, 5, -23, 23, 0, 3.5)
    for s in (-1, 1):
        p = D(p, cyl(x, y, z, "z", (0, s * 18.5), 1.7, -1, 5))
    return p


def boyun(x, y, z):
    z0, z1 = BOYUN_Z0, BOYUN_Z0 + BOYUN_T
    tabla = U(cyl(x, y, z, "z", (0, 0), 32, z0, z1), box(x, y, z, -KOL_IC - KOL_T, KOL_IC + KOL_T, -KOL_W / 2, KOL_W / 2, z0, z1))
    kol = Un(*[U(box(x, y, z, a, a + KOL_T, -KOL_W / 2, KOL_W / 2, z0, EKSEN_Z),
                 cyl(x, y, z, "x", (0, EKSEN_Z), KOL_W / 2, a, a + KOL_T)) for a in (KOL_IC, -KOL_IC - KOL_T)])
    # iç köşe takviyesi (45°)
    ax = np.abs(x)
    gus = I(I(box(ax, y, z, KOL_IC - 10, KOL_IC + 0.01, -KOL_W / 2, KOL_W / 2, z1 - 0.01, z1 + 10)
              , (z - z1) + (KOL_IC - ax) - 10), -ax + 0)
    p = Un(tabla, kol, gus)
    kes = [cyl(x, y, z, "z", (0, 0), 4.0, z0 - 1, z1 + 1)]                                # korna merkez vidası
    for i in range(4):
        a = math.radians(45 + 90 * i)
        cx, cy = KORNA_DELIK_C / 2 * math.cos(a), KORNA_DELIK_C / 2 * math.sin(a)
        kes += [cyl(x, y, z, "z", (cx, cy), KORNA_VIDA / 2, z0 - 1, z1 + 1), cyl(x, y, z, "z", (cx, cy), 2.6, z1 - 2, z1 + 1)]
    # sağ kol: tilt kornası
    kes.append(cyl(x, y, z, "x", (0, EKSEN_Z), 4.0, KOL_IC - 1, KOL_IC + KOL_T + 1))
    for i in range(4):
        a = math.radians(45 + 90 * i)
        cy, cz = KORNA_DELIK_C / 2 * math.cos(a), EKSEN_Z + KORNA_DELIK_C / 2 * math.sin(a)
        kes += [cyl(x, y, z, "x", (cy, cz), KORNA_VIDA / 2, KOL_IC - 1, KOL_IC + KOL_T + 1),
                cyl(x, y, z, "x", (cy, cz), 2.6, KOL_IC + KOL_T - 2, KOL_IC + KOL_T + 1)]
    # sol kol: M3 mil vidası
    kes += [cyl(x, y, z, "x", (0, EKSEN_Z), 1.7, -KOL_IC - KOL_T - 1, -KOL_IC + 1),
            cyl(x, y, z, "x", (0, EKSEN_Z), 3.0, -KOL_IC - KOL_T - 1, -KOL_IC - KOL_T + 2)]
    return D(p, Un(*kes))


def tilt_servo_kutu():  # kafa koordinatı (orijin eksen): mil +x, gövde yukarı uzanır
    return (SV_T_YUZ - SV_H, SV_T_YUZ, -SV_W / 2, SV_W / 2, -SV_MIL_UC, SV_L - SV_MIL_UC)


def kafa(x, y, z):
    """Tam kafa (kafa koordinatı)."""
    dis = I(I(sphere(x, y, z, (0, 0, 0), K_R), np.abs(x) - DUZ_X), YUZ_Y - y)
    ic = I(I(sphere(x, y, z, (0, 0, 0), K_IC), np.abs(x) - (DUZ_X - DUVAR)), (YUZ_Y + DUVAR) - y)
    k = D(dis, ic)
    ic_bolge = ic
    x0, x1, y0, y1, z0, z1 = tilt_servo_kutu()
    cep = D(box(x, y, z, x0 - B - DUVAR, DUZ_X - 0.5, y0 - B - DUVAR, y1 + B + DUVAR, z0 - B - DUVAR, z1 + B + DUVAR),
            box(x, y, z, x0 - B, x1 + B, y0 - B, y1 + B, z0 - B, z1 + B))
    cep = D(cep, box(x, y, z, -60, 60, -60, y0 - B, -60, 60))          # önden açık: servo önden girer
    cep = I(cep, sphere(x, y, z, (0, 0, 0), K_IC + 0.5))
    # sol mil yuvası (M3 insert) + dış pul
    mil = U(cyl(x, y, z, "x", (0, 0), 5.5, -DUZ_X - 1.2, -DUZ_X + DUVAR + 6), cyl(x, y, z, "x", (0, 0), 7, -DUZ_X, -DUZ_X + DUVAR))
    # ekran yatağı (ön duvarın arkası)
    ekran = D(cyl(x, y, z, "y", (0, EKRAN_Z), EKRAN_KART / 2 + 2.4, YUZ_Y + DUVAR - 0.01, YUZ_Y + DUVAR + EKRAN_KALINLIK),
              cyl(x, y, z, "y", (0, EKRAN_Z), EKRAN_KART / 2 + B, YUZ_Y, YUZ_Y + DUVAR + EKRAN_KALINLIK + 1))
    ekran = I(ekran, ic_bolge + 0.01)
    # kamera ayakları (M2, kendinden kılavuzlu)
    kam = Un(*[cyl(x, y, z, "y", (dx, KAM_Z + dz), 2.2, YUZ_Y + 1, KAM_KART_Y) for dx, dz in KAM_DELIK])
    # birleştirme kuleleri (ayrım düzleminin iki yanında)
    kule = Un(*[cyl(x, y, z, "y", (px, pz), 4.0, BOLME_Y - 8, BOLME_Y + 10) for px, pz in KAFA_VIDA])
    k = Un(k, cep, mil, I(ekran, sphere(x, y, z, (0, 0, 0), K_R)), kam, kule)
    kes = [cyl(x, y, z, "y", (0, EKRAN_Z), EKRAN_PENCERE / 2, YUZ_Y - 5, YUZ_Y + DUVAR + 0.01),   # ekran penceresi
           cyl(x, y, z, "y", (0, KAM_Z), KAM_LENS_D / 2, YUZ_Y - 5, KAM_KART_Y),                 # kamera lensi
           cyl(x, y, z, "x", (0, 0), 11.5, SV_T_YUZ - 0.5, DUZ_X + 1),                          # sağ: korna deliği
           cyl(x, y, z, "x", (0, 0), 2.0, -DUZ_X - 2, -DUZ_X + DUVAR + 6.5)]                      # sol: M3 insert
    kes += [cyl(x, y, z, "y", (dx, KAM_Z + dz), 0.8, YUZ_Y + 1.5, KAM_KART_Y + 1) for dx, dz in KAM_DELIK]
    # kablo çıkışı (arka-alt): kamera FFC + ekran + servo kabloları
    dy, dz = math.cos(math.radians(55)), -math.sin(math.radians(55))
    kes.append(I(I(np.abs(x) - 10, np.abs(y * dz - z * dy) - 4.5), (K_IC - 6) - (y * dy + z * dz)))
    # birleştirme vidaları: M3x16, arkadan; önde insert
    for px, pz in KAFA_VIDA:
        kes += [cyl(x, y, z, "y", (px, pz), 2.0, BOLME_Y - 7, BOLME_Y),            # ön: insert Ø4
                cyl(x, y, z, "y", (px, pz), 1.7, BOLME_Y - 0.1, BOLME_Y + 10.1),  # arka: geçiş
                cyl(x, y, z, "y", (px, pz), 3.3, BOLME_Y + 10, 80)]               # vida başı kanalı
    return D(k, Un(*kes))


def kafa_on(x, y, z):   return I(kafa(x, y, z), y - BOLME_Y)
def kafa_arka(x, y, z): return I(kafa(x, y, z), BOLME_Y - y)


# ---------------- Üretim -------------------------------------------------------
def evaluate(fn, bounds, res=RES):
    (x0, x1), (y0, y1), (z0, z1) = bounds
    xs = np.arange(x0, x1 + res, res, dtype=f32)
    ys = np.arange(y0, y1 + res, res, dtype=f32)
    zs = np.arange(z0, z1 + res, res, dtype=f32)
    vol = np.empty((len(xs), len(ys), len(zs)), dtype=np.float16)
    step = max(1, int(4e6 // (len(ys) * len(zs))))
    for i in range(0, len(xs), step):
        X = xs[i:i + step, None, None]
        v = fn(X, ys[None, :, None], zs[None, None, :])
        vol[i:i + step] = np.clip(np.broadcast_to(v, (len(X), len(ys), len(zs))), -50, 50).astype(np.float16)
    return vol, (xs, ys, zs)


def mesh(fn, bounds, res=RES, step=1):
    from skimage.measure import marching_cubes
    vol, (xs, ys, zs) = evaluate(fn, bounds, res)
    v, f, _, _ = marching_cubes(vol.astype(np.float32), 0.0, spacing=(res, res, res), step_size=step)
    v += np.array([xs[0], ys[0], zs[0]], dtype=np.float32)
    return v, f, vol


def write_stl(path, v, f):
    tri = v[f]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    data = np.zeros(len(f), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    data["n"], data["v"] = n, tri
    with open(path, "wb") as fh:
        fh.write(b"robot v0.4".ljust(80, b" "))
        fh.write(struct.pack("<I", len(f)))
        fh.write(data.tobytes())


def rot_x(v, deg):
    a = math.radians(deg); c, s = math.cos(a), math.sin(a)
    return np.stack([v[:, 0], c * v[:, 1] - s * v[:, 2], s * v[:, 1] + c * v[:, 2]], 1)


def flip_z(v, top):   # baş aşağı (x ekseni etrafında 180°)
    return np.stack([v[:, 0], -v[:, 1], top - v[:, 2]], 1)


def to_bed(v):
    return v - np.array([0, 0, v[:, 2].min()])


PARTS = {
    # ad: (fonksiyon, sınırlar, baskı yönü dönüşümü)
    "govde":     (govde, ((-G_R - 1, G_R + 1), (-G_R - 1, G_R + 1), (-0.5, G_UST + 0.5)), lambda v: flip_z(v, G_UST)),
    "alt_plaka": (alt_plaka, ((-G_IC, G_IC), (-G_IC, G_IC), (-0.5, PI_Z + 0.5)), lambda v: v),
    "kapak":     (kapak, ((-G_R - 1, G_R + 1), (-G_R - 1, G_R + 1), (40, K_UST + 0.5)), lambda v: flip_z(v, K_UST)),
    "kelepce":   (kelepce, ((-6, 6), (-24, 24), (-0.5, 4)), lambda v: v),
    "boyun":     (boyun, ((-KOL_IC - KOL_T - 1, KOL_IC + KOL_T + 1), (-33, 33), (BOYUN_Z0 - 0.5, EKSEN_Z + KOL_W / 2 + 0.5)),
                  lambda v: v - np.array([0, 0, BOYUN_Z0])),
    "kafa_on":   (kafa_on, ((-K_R - 1, K_R + 1), (YUZ_Y - 0.5, BOLME_Y + 0.5), (-K_R - 1, K_R + 1)),
                  lambda v: to_bed(rot_x(v, 90))),   # yüz tablada
    "kafa_arka": (kafa_arka, ((-K_R - 1, K_R + 1), (BOLME_Y - 0.5, K_R + 1), (-K_R - 1, K_R + 1)),
                  lambda v: to_bed(rot_x(v, 90))),
}


def assembly_transform(name, v):   # tasarım koordinatından montaj koordinatına (eğim 0)
    if name.startswith("kafa"):
        return v + np.array([0, 0, EKSEN_Z])
    if name == "kelepce":
        x0, x1, y0, y1, z0, z1 = pan_servo_kutu()
        return np.stack([v[:, 0] + KELEPCE_X, v[:, 1], (z0 - 0.3) - v[:, 2]], 1)
    return v


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    only = sys.argv[1:] or list(PARTS)
    for name in only:
        fn, bounds, tf = PARTS[name]
        v, f, _ = mesh(fn, bounds)
        pv = tf(v)
        write_stl(os.path.join(OUT, f"{name}.stl"), pv.astype(np.float32), f)
        ext = pv.max(0) - pv.min(0)
        print(f"{name:10s} {len(f):8d} üçgen  baskı ölçüsü {ext[0]:.0f} x {ext[1]:.0f} x {ext[2]:.0f} mm", flush=True)
