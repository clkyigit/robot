"""Robotun yüzü: 240x240 yuvarlak ekran (GC9A01) için iki göz + ağız, PIL ile çizilir.

face(ifade, bakış, kırpma) bir PIL resmi döner. Ekran sürücüsü (face.py) bunu saniyede ~20 kez çizer.
Renk duyguya göre değişir (robot kendisi böyle istedi).
"""
import math

from PIL import Image, ImageDraw, ImageFont

W = H = 240
BG = (12, 14, 20)

# Her duygunun rengi
COLORS = {
    "normal": (93, 202, 165),       # yeşil-turkuaz
    "mutlu": (120, 225, 150),       # parlak yeşil
    "dinliyor": (90, 200, 240),     # açık mavi
    "dusunuyor": (170, 150, 240),   # lavanta
    "uykulu": (110, 140, 220),      # yumuşak mavi
    "sicak": (255, 160, 90),        # sıcak turuncu
    "saskin": (250, 210, 90),       # sarı
    "sevgi": (255, 120, 160),       # pembe
    "kafasi_karisik": (190, 140, 230),  # mor
    "merakli": (93, 220, 200),      # parlak turkuaz
}
EXPRESSIONS = list(COLORS)

try:
    _FONT = lambda size: ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    _FONT(10)
except OSError:
    _FONT = lambda size: ImageFont.load_default(size)


def _heart(d, cx, cy, r, fill):
    d.ellipse([cx - r, cy - r, cx, cy], fill=fill)
    d.ellipse([cx, cy - r, cx + r, cy], fill=fill)
    d.polygon([(cx - r, cy - r / 2.4), (cx + r, cy - r / 2.4), (cx, cy + r * 1.05)], fill=fill)


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(8):
        a = math.pi / 4 * i
        rr = r if i % 2 == 0 else r * 0.3
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def _wave(d, x0, x1, y, amp, waves, fill, width):
    n = 40
    pts = [(x0 + (x1 - x0) * i / n, y + amp * math.sin(i / n * waves * 2 * math.pi)) for i in range(n + 1)]
    d.line(pts, fill=fill, width=width, joint="curve")


def face(expr: str = "normal", look=(0.0, 0.0), blink: float = 0.0, t: float = 0.0, s: int = 3) -> Image.Image:
    """expr: EXPRESSIONS'tan biri. look: (-1..1, -1..1) bakış yönü. blink: 0 açık, 1 kapalı.
    t: saniye (parıltı ve baloncuk animasyonları için). s: kenar yumuşatma için büyütme katsayısı."""
    col = COLORS.get(expr, COLORS["normal"])
    soft = tuple(int(c * 0.45 + b * 0.55) for c, b in zip(col, BG))
    S = lambda v: int(round(v * s))
    im = Image.new("RGB", (W * s, H * s), BG)
    d = ImageDraw.Draw(im)
    lx, ly = look[0] * 14, look[1] * 10
    cy = 98 + ly
    eyes = (80 + lx, 160 + lx)

    for i, cx in enumerate(eyes):
        ew, eh = 46, 62
        if expr in ("dinliyor", "merakli"):
            ew, eh = 52, 72
        if expr == "uykulu":
            eh = 14
        if expr == "kafasi_karisik":
            ew, eh = (52, 70) if i == 0 else (40, 34)
        eh = max(5, eh * (1 - blink))
        if expr == "mutlu":
            d.arc([S(cx - 22), S(cy - 6), S(cx + 22), S(cy + 34)], 200, 340, fill=col, width=S(9))
        elif expr == "sevgi":
            pulse = 1 + 0.06 * math.sin(t * 6)
            _heart(d, S(cx), S(cy + 6), S(26 * pulse), col)
        elif expr == "saskin":
            d.ellipse([S(cx - 26), S(cy - 26), S(cx + 26), S(cy + 26)], fill=col)
            d.ellipse([S(cx - 9), S(cy - 9), S(cx + 9), S(cy + 9)], fill=BG)
        else:
            d.rounded_rectangle([S(cx - ew / 2), S(cy - eh / 2), S(cx + ew / 2), S(cy + eh / 2)],
                                radius=S(min(ew, eh) / 2.2), fill=col)
            if eh > 20:
                d.ellipse([S(cx + 6), S(cy - eh / 2 + 8), S(cx + 16), S(cy - eh / 2 + 18)], fill=(235, 255, 248))
            if expr == "merakli" and eh > 30:     # parıldayan gözler
                tw = 0.7 + 0.3 * math.sin(t * 5 + i)
                _star(d, S(cx - 10), S(cy + 12), S(9 * tw), (255, 255, 255))
                d.ellipse([S(cx + 8), S(cy + 18), S(cx + 13), S(cy + 23)], fill=(235, 255, 248))

    # yanaklar
    if expr in ("mutlu", "sevgi", "sicak"):
        cheek = (240, 120, 140)
    elif expr in ("normal", "dinliyor", "merakli"):
        cheek = (90, 60, 70)
    else:
        cheek = None
    if cheek:
        for cx in (52, 188):
            d.ellipse([S(cx - 14), S(140), S(cx + 14), S(152)], fill=cheek)

    # ağız ve süsler
    mx, my = 120, 168
    if expr in ("normal", "dinliyor"):
        d.arc([S(mx - 16), S(my - 12), S(mx + 16), S(my + 10)], 20, 160, fill=col, width=S(6))
    elif expr in ("mutlu", "sevgi"):
        d.chord([S(mx - 22), S(my - 14), S(mx + 22), S(my + 20)], 0, 180, fill=col)
    elif expr == "merakli":
        d.ellipse([S(mx - 7), S(my - 4), S(mx + 7), S(my + 10)], fill=col)
    elif expr == "dusunuyor":
        d.line([S(mx - 12), S(my + 2), S(mx + 12), S(my - 2)], fill=col, width=S(6))
        for k, r in enumerate((4, 6, 8)):          # sırayla beliren düşünce baloncukları
            if (t * 2) % 4 >= k:
                x, y = 126 + k * 15, 60 - k * 12
                d.ellipse([S(x - r), S(y - r), S(x + r), S(y + r)], fill=soft)
    elif expr == "uykulu":
        d.ellipse([S(mx - 6), S(my - 4), S(mx + 6), S(my + 8)], fill=col)
        off = (t * 6) % 12
        d.text((S(168), S(44 - off)), "z", fill=soft, font=_FONT(S(20)))
        d.text((S(184), S(28 - off)), "z", fill=soft, font=_FONT(S(14)))
    elif expr == "sicak":                         # telaşlı ama neşeli
        d.chord([S(mx - 14), S(my - 8), S(mx + 14), S(my + 14)], 0, 180, fill=col)
        drop = 4 * math.sin(t * 4)
        d.ellipse([S(194), S(66 + drop), S(208), S(88 + drop)], fill=(120, 190, 255))
        d.polygon([(S(201), S(58 + drop)), (S(195), S(72 + drop)), (S(207), S(72 + drop))], fill=(120, 190, 255))
    elif expr == "saskin":
        d.ellipse([S(mx - 10), S(my - 10), S(mx + 10), S(my + 12)], outline=col, width=S(6))
    elif expr == "kafasi_karisik":
        _wave(d, S(mx - 18), S(mx + 18), S(my), S(4), 1.5, col, S(5))
        d.text((S(166), S(28)), "?", fill=soft, font=_FONT(S(34)))

    im = im.resize((W, H), Image.LANCZOS)
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, W - 1, H - 1], fill=255)
    out = Image.new("RGB", (W, H), (0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out
