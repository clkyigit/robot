"""Utangaç yüz ifadesi testi (ekran, kamera, ağ gerektirmez)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import face_draw


def test_utangac_listede():
    assert "utangac" in face_draw.EXPRESSIONS
    assert "utangac" in face_draw.COLORS


def test_utangac_cizilir():
    for blink in (0.0, 0.5, 1.0):
        im = face_draw.face("utangac", (0.3, 0.5), blink=blink, t=1.0, s=1)
        assert im.size == (240, 240)
    # yanaklar pembe olmalı: sol yanak bölgesinde pembe bir piksel bulunur
    im = face_draw.face("utangac", (0.0, 0.0), s=2)
    assert im.getpixel((52, 146))[0] > 150


def test_utangac_normalden_farkli():
    a = face_draw.face("utangac", s=1).tobytes()
    b = face_draw.face("normal", s=1).tobytes()
    assert a != b


if __name__ == "__main__":
    test_utangac_listede()
    test_utangac_cizilir()
    test_utangac_normalden_farkli()
    print("tamam")
