"""Anlık sistem ölçümü testleri (ağ, kamera, mikrofon gerektirmez)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import body  # noqa: E402

WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
wlan0: 0000   56.  -54.  -256        0      0      0      0      0        0
"""

IWCONFIG = """wlan0     IEEE 802.11  ESSID:"ev"
          Link Quality=42/70  Signal level=-68 dBm
"""


def test_wireless_ayristir():
    q, lvl = body.parse_wireless(WIRELESS)
    assert q == 80 and lvl == -54
    assert body.parse_wireless("") == (None, None)


def test_iwconfig_ayristir():
    q, lvl = body.parse_iwconfig(IWCONFIG)
    assert q == 60 and lvl == -68
    assert body.parse_iwconfig("no wireless extensions") == (None, None)


def test_canli_durum_metni():
    s = body.live_status()
    txt = body.format_live(s)
    assert "Ölçüm zamanı:" in txt
    assert "CPU sıcaklığı" in txt and "Bellek" in txt and "Disk" in txt
    assert "Wi-Fi" in txt and "Yük" in txt


def test_birim_ve_tarih():
    import datetime
    s = {"at": datetime.datetime(2025, 3, 12, 14, 5, 3), "temp_c": 41.2,
         "mem": {"pct": 37, "used_gb": 1.4, "total_gb": 3.9},
         "disk": {"free_gb": 20.5, "total_gb": 30.0, "pct": 32}, "load": (0.5, 0.4, 0.3),
         "wifi_pct": 80, "wifi_dbm": -54, "uptime_s": 3700}
    txt = body.format_live(s)
    assert "12.03.2025 14:05:03" in txt
    assert "41.2 °C" in txt and "%37" in txt and "20.5 GB" in txt and "-54 dBm" in txt
    assert "1 saat" in txt


def test_describe_olcum_icerir():
    assert "Anlık sistem ölçümü" in body.describe()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("tamam")
