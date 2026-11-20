"""Yüz takibi: yumuşatma ve yüz çiziminin bakışla kayması (ağ/donanım gerektirmez)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import face
import face_draw


def test_settle_kucuk_oynama_yok_sayilir():
    assert face.settle([0.5, 0.2], [0.51, 0.21]) == [0.5, 0.2]


def test_settle_buyuk_hareket_izlenir():
    assert face.settle([0.5, 0.2], [0.1, 0.2]) == [0.1, 0.2]
    assert face.settle(None, [0.3, 0.3]) == [0.3, 0.3]


def test_yuz_cizimi_bakisla_kayar():
    a = face_draw.face("normal", (0.0, 0.0), s=1)
    b = face_draw.face("normal", (1.0, 0.0), s=1)
    assert a.size == b.size == (240, 240)
    assert a.tobytes() != b.tobytes()


def test_animator_yuz_kaybolunca_ortaya_doner():
    an = face.Animator()
    an.following = True
    an.look_target = [0.8, 0.3]
    an.look = [0.8, 0.3]
    orig = (face.read_gaze, face.wanted_expression)
    face.read_gaze = lambda now: {}
    face.wanted_expression = lambda now, gaze=None: "normal"
    try:
        an.frame(100.0)
    finally:
        face.read_gaze, face.wanted_expression = orig
    assert an.look_target == [0.0, 0.0] and an.following is False
    assert 0.0 < an.look[0] < 0.8      # yavaşça dönüyor, bir anda değil


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("ok")
