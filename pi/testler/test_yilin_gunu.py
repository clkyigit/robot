"""Yılın günü hesabı ve sistem istemine eklenmesi testleri (ağ, kamera, mikrofon gerektirmez)."""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import brain  # noqa: E402


def test_normal_yil():
    assert brain.yilin_gunu(datetime.date(2025, 1, 1)) == (1, 365)
    assert brain.yilin_gunu(datetime.date(2025, 3, 12)) == (71, 365)
    assert brain.yilin_gunu(datetime.date(2025, 12, 31)) == (365, 365)


def test_artik_yil():
    assert brain.yilin_gunu(datetime.date(2024, 3, 1)) == (61, 366)
    assert brain.yilin_gunu(datetime.date(2024, 12, 31)) == (366, 366)
    assert brain.yilin_gunu(datetime.date(1900, 12, 31)) == (365, 365)
    assert brain.yilin_gunu(datetime.date(2000, 12, 31)) == (366, 366)


def test_datetime_kabul_eder():
    assert brain.yilin_gunu(datetime.datetime(2025, 2, 1, 10, 30)) == (32, 365)


def test_istemde_yilin_gunu_var():
    p = brain.system_prompt({"facts": [], "history": []})
    gun_no, toplam = brain.yilin_gunu(datetime.datetime.now(brain.TZ))
    assert f"yılın {gun_no}. günü" in p
    assert f"{toplam} gün" in p


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("tamam")
