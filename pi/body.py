"""Robotun kendi bedenini algılaması: sıcaklık, fan, bellek, disk, Wi-Fi, çalışma süresi.

Beyin her düşünmeden önce buradan kısa bir "beden durumu" alır, böylece robot kendi
durumunu bilerek konuşur. Takılı ve gelecek parçaların listesi govde.md dosyasındadır.
"""

import glob
import os
import pathlib
import shutil
import subprocess
import time

BASE = pathlib.Path(__file__).resolve().parent
BODY_FILE = BASE / "govde.md"
BOOT = time.time()

THROTTLE_BITS = {
    0: "şu an voltaj düşük", 1: "şu an frekans sınırlı", 2: "şu an yavaşlatılıyor", 3: "şu an ısı sınırında",
    16: "açıldığından beri voltaj düşüklüğü yaşandı", 18: "açıldığından beri ısıdan yavaşlatıldı",
    19: "açıldığından beri ısı sınırına geldi",
}


def _read(path: str) -> str:
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return ""


def cpu_temp() -> float | None:
    v = _read("/sys/class/thermal/thermal_zone0/temp")
    return int(v) / 1000 if v.isdigit() else None


def fan_rpm() -> int | None:
    for p in glob.glob("/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input"):
        v = _read(p)
        if v.isdigit():
            return int(v)
    return None


def throttled() -> list[str]:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        val = int(out.strip().split("=")[1], 16)
    except (OSError, IndexError, ValueError, subprocess.TimeoutExpired):
        return []
    return [msg for bit, msg in THROTTLE_BITS.items() if val & (1 << bit)]


def memory_pct() -> int | None:
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        info[k] = int(v.split()[0]) if v.split() else 0
    if "MemTotal" in info and "MemAvailable" in info:
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]))
    return None


def wifi_quality() -> int | None:
    for line in _read("/proc/net/wireless").splitlines()[2:]:
        parts = line.split()
        if len(parts) > 2:
            try:
                return round(float(parts[2].rstrip(".")) / 70 * 100)
            except ValueError:
                pass
    return None


def uptime_text(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    parts = [f"{d} gün" if d else "", f"{h} saat" if h else "", f"{m} dakika" if m or not (d or h) else ""]
    return " ".join(p for p in parts if p)


def snapshot() -> dict:
    disk = shutil.disk_usage("/")
    return {
        "temp": cpu_temp(), "fan_rpm": fan_rpm(), "throttled": throttled(), "mem_pct": memory_pct(),
        "disk_free_gb": round(disk.free / 1e9, 1), "load": round(os.getloadavg()[0], 2),
        "wifi": wifi_quality(), "awake": time.time() - BOOT,
        "system_up": float(_read("/proc/uptime").split()[0] or 0) if _read("/proc/uptime") else 0,
    }


def describe(extra: list[str] | None = None) -> str:
    """Beyne verilen kısa beden durumu (Türkçe)."""
    s = snapshot()
    lines = [f"- Bu sefer uyanalı {uptime_text(s['awake'])} oldu (bilgisayarım {uptime_text(s['system_up'])} açık)."]
    if s["temp"] is not None:
        t = s["temp"]
        feel = "serin" if t < 55 else "ılık" if t < 70 else "sıcak" if t < 80 else "çok sıcak, tehlikeli"
        lines.append(f"- İşlemci sıcaklığım {t:.0f} °C ({feel}).")
    if s["fan_rpm"] is not None:
        lines.append(f"- Fanım {'dönüyor, ' + str(s['fan_rpm']) + ' devir' if s['fan_rpm'] > 0 else 'dönmüyor'}.")
    if s["throttled"]:
        lines.append(f"- Güç/ısı uyarısı: {', '.join(s['throttled'])}.")
    if s["mem_pct"] is not None:
        lines.append(f"- Belleğimin %{s['mem_pct']}'i dolu, diskimde {s['disk_free_gb']} GB boş yer var, yük {s['load']}.")
    if s["wifi"] is not None:
        lines.append(f"- Wi-Fi sinyalim %{s['wifi']}.")
    lines += [f"- {e}" for e in (extra or [])]
    body = _read(str(BODY_FILE))
    return "\n".join(lines) + (f"\n\nBedenim (parçalarım):\n{body}" if body else "")
