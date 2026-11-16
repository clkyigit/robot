"""Yaş hesabı testleri (ağ, kamera, mikrofon gerektirmez)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import brain  # noqa: E402


def test_normal():
    s = brain.yas_hesapla(1990, 2025)
    assert "35 yaşında" in s


def test_ayni_yil():
    assert "doğduğu yıl" in brain.yas_hesapla(2020, 2020)


def test_henuz_dogmamis():
    s = brain.yas_hesapla(2030, 2025)
    assert "doğmamış" in s and "5 yıl" in s


def test_istemde_yas_kurali_var():
    p = brain.system_prompt({"facts": [], "history": []})
    assert "Yaş hesabı" in p


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("tamam")
