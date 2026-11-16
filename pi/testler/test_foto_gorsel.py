"""Telegram'dan gelen fotoğrafın doğrudan modele verilmesi için yardımcıların testi (ağ gerektirmez)."""
import asyncio
import io
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import brain  # noqa: E402
from PIL import Image  # noqa: E402


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (10, 120, 200, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_kucuk_gorsel_jpeg_olur():
    data, mime = brain.prepare_image(_png(64, 48))
    assert mime == "image/jpeg"
    assert Image.open(io.BytesIO(data)).size == (64, 48)


def test_buyuk_gorsel_kucultulur():
    data, _ = brain.prepare_image(_png(4000, 2000))
    assert max(Image.open(io.BytesIO(data)).size) <= 1568


def test_bozuk_gorsel_hata_verir():
    try:
        brain.prepare_image(b"bu bir resim degil")
    except ValueError:
        return
    assert False, "ValueError bekleniyordu"


def test_model_yoksa_acikca_hata_verir():
    eski = (brain.claude, brain.gemini)
    brain.claude = brain.gemini = None
    try:
        asyncio.run(brain.chat_image("bak", _png(10, 10)))
    except RuntimeError as e:
        assert "model" in str(e).lower()
    else:
        assert False, "RuntimeError bekleniyordu"
    finally:
        brain.claude, brain.gemini = eski


if __name__ == "__main__":
    for ad, fn in list(globals().items()):
        if ad.startswith("test_"):
            fn()
    print("tamam")
