"""Kamera fotoğraf güvenilirliği testi: netlik ölçümü, döndürme, en netini seçme, diske kayıt.
Kamera, ağ ya da mikrofon gerektirmez."""
import io
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import camera  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402


def _jpeg(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def _net(w=320, h=180):
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            v = 255 if ((x // 8) + (y // 8)) % 2 else 0
            px[x, y] = (v, v, v)
    return img


def test_net_goruntu_bulaniktan_yuksek_skorlu():
    net = _net()
    assert camera.sharpness(_jpeg(net)) > camera.sharpness(_jpeg(net.filter(ImageFilter.GaussianBlur(6)))) * 5


def test_dondurme_boyutlari():
    data = _jpeg(_net(320, 180))
    assert Image.open(io.BytesIO(camera.rotate_jpeg(data, 0))).size == (320, 180)
    assert Image.open(io.BytesIO(camera.rotate_jpeg(data, 180))).size == (320, 180)
    assert Image.open(io.BytesIO(camera.rotate_jpeg(data, 90))).size == (180, 320)
    assert Image.open(io.BytesIO(camera.rotate_jpeg(data, 270))).size == (180, 320)


def test_dondurme_yonu_ve_gecersiz_deger():
    img = Image.new("RGB", (80, 40), (0, 0, 0))
    img.paste((255, 255, 255), (0, 0, 24, 16))   # sol üst köşe beyaz
    out = Image.open(io.BytesIO(camera.rotate_jpeg(_jpeg(img), 180)))
    assert out.getpixel((70, 35))[0] > 200   # 180 derece: sağ alta gider
    assert out.getpixel((5, 5))[0] < 50
    assert camera._parse_rotation("45") == 0
    assert camera._parse_rotation("abc") == 0
    assert camera._parse_rotation(" 270 ") == 270
    assert camera._parse_rotation(-90) == 270


def test_bulaniksa_tekrar_cekip_en_netini_secer():
    c = camera.Camera()
    net = _jpeg(_net())
    bulanik = _jpeg(_net().filter(ImageFilter.GaussianBlur(6)))
    kareler = iter([bulanik, net])
    odak = []
    eski = camera.MIN_SHARPNESS
    camera.MIN_SHARPNESS = camera.sharpness(net) / 2   # net kare eşiği geçer, bulanık geçmez
    try:
        assert camera.sharpness(bulanik) < camera.MIN_SHARPNESS
        best, score, scores, _ = c._best_of(lambda: next(kareler), lambda: odak.append(1) or True)
    finally:
        camera.MIN_SHARPNESS = eski
    assert best == net and len(scores) == 2 and len(odak) == 2
    assert scores[1] > scores[0] and abs(score - scores[1]) < 0.1


def test_hep_bulaniksa_en_fazla_deneme_kadar_ceker():
    c = camera.Camera()
    sayac = []
    kareler = [_jpeg(_net().filter(ImageFilter.GaussianBlur(r))) for r in (8, 4, 6, 9, 10)]

    def grab():
        sayac.append(1)
        return kareler[len(sayac) - 1]

    eski = camera.MIN_SHARPNESS
    camera.MIN_SHARPNESS = 1e12   # hiçbir kare yeterince net sayılmasın
    try:
        best, _, scores, _ = c._best_of(grab)
    finally:
        camera.MIN_SHARPNESS = eski
    assert len(sayac) == camera.MAX_ATTEMPTS and len(scores) == camera.MAX_ATTEMPTS
    assert best == kareler[1]   # ilk üçün en az bulanığı (yarıçap 4)


def test_kare_diske_zaman_damgasiyla_kaydedilir():
    with tempfile.TemporaryDirectory() as d:
        c = camera.Camera()
        c.photo_dir = pathlib.Path(d) / "fotolar"
        c.rotation = 90
        out = c._finalize(_jpeg(_net(320, 180)), 123.4, [10.0, 123.4], True, "deneme")
        assert Image.open(io.BytesIO(out)).size == (180, 320)
        dosyalar = list(c.photo_dir.glob("*.jpg"))
        assert len(dosyalar) == 1
        kayit = json.loads((c.photo_dir / "foto_log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert kayit["dosya"] == dosyalar[0].name and kayit["dondurme"] == 90 and kayit["netlik"] == 123.4
        assert c.last_photo_info["denemeler"] == [10.0, 123.4]


if __name__ == "__main__":
    for ad, fn in list(globals().items()):
        if ad.startswith("test_"):
            fn()
    print("tamam")
