"""Radyoloji kurallarının sistem istemine girdiğini denetler (ağ gerektirmez)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import brain  # noqa: E402


def test_kurallar_dolu():
    t = brain.radyoloji_kurallari()
    assert "Radyoloji" in t
    assert "gizliliği" in t
    assert "ayırıcı tanı" in t


def test_sistem_istemine_giriyor():
    p = brain.system_prompt({"facts": [], "history": []})
    assert brain.radyoloji_kurallari() in p


if __name__ == "__main__":
    test_kurallar_dolu()
    test_sistem_istemine_giriyor()
    print("tamam")
