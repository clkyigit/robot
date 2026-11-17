"""Robotun kendi bedenini algılaması: sıcaklık, fan, bellek, disk, Wi-Fi, çalışma süresi.

Beyin her düşünmeden önce buradan kısa bir "beden durumu" alır, böylece robot kendi
durumunu bilerek konuşur. Takılı ve gelecek parçaların listesi govde.md dosyasındadır.
"""

import datetime
import glob
import os
import pathlib
import re
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo

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


def fan_status() -> dict | None:
    """GPIO'ya bağlı fanın servisi (fan.py) durumunu /dev/shm/robot-fan.json'a yazar."""
    import json
    try:
        d = json.loads(pathlib.Path("/dev/shm/robot-fan.json").read_text())
    except (OSError, ValueError):
        return None
    return d if time.time() - d.get("t", 0) < 30 else None


def fan_rpm() -> int | None:
    st = fan_status()
    if st and st.get("rpm") is not None:
        return int(st["rpm"])
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


def parse_wireless(text: str) -> tuple[int | None, int | None]:
    """/proc/net/wireless metninden (sinyal kalitesi %, sinyal gücü dBm) çıkarır."""
    for line in text.splitlines()[2:]:
        parts = line.split()
        if len(parts) > 3:
            try:
                quality = round(float(parts[2].rstrip(".")) / 70 * 100)
                level = int(float(parts[3].rstrip(".")))
                if level > 0:        # bazı sürücüler 0-255 ölçeğinde verir; dBm'e çevir
                    level -= 256
                return min(quality, 100), level
            except ValueError:
                continue
    return None, None


def parse_iwconfig(text: str) -> tuple[int | None, int | None]:
    """iwconfig çıktısından (sinyal kalitesi %, sinyal gücü dBm) çıkarır."""
    quality = level = None
    m = re.search(r"Link Quality[=:]\s*(\d+)\s*/\s*(\d+)", text)
    if m and int(m.group(2)) > 0:
        quality = min(round(int(m.group(1)) / int(m.group(2)) * 100), 100)
    m = re.search(r"Signal level[=:]\s*(-?\d+)", text)
    if m:
        level = int(m.group(1))
    return quality, level


def wifi_live() -> tuple[int | None, int | None]:
    """Wi-Fi sinyalini anlık okur: önce /proc/net/wireless, olmazsa iwconfig."""
    q, lvl = parse_wireless(_read("/proc/net/wireless"))
    if q is not None:
        return q, lvl
    try:
        out = subprocess.run(["iwconfig"], capture_output=True, text=True, timeout=3)
        return parse_iwconfig(out.stdout + out.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return None, None


def _memory_live() -> dict | None:
    """Bellek: psutil varsa onunla, yoksa /proc/meminfo ile (anlık)."""
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return {"pct": round(vm.percent), "used_gb": (vm.total - vm.available) / 1e9, "total_gb": vm.total / 1e9}
    except Exception:  # noqa: BLE001  (psutil yok ya da okunamadı)
        pass
    info = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            info[k] = int(v.split()[0]) * 1024
    if info.get("MemTotal") and "MemAvailable" in info:
        used = info["MemTotal"] - info["MemAvailable"]
        return {"pct": round(100 * used / info["MemTotal"]), "used_gb": used / 1e9, "total_gb": info["MemTotal"] / 1e9}
    return None


def _disk_live() -> dict | None:
    """Disk: psutil varsa onunla, yoksa shutil ile (anlık)."""
    try:
        import psutil  # type: ignore
        d = psutil.disk_usage("/")
        return {"free_gb": d.free / 1e9, "total_gb": d.total / 1e9, "pct": round(d.percent)}
    except Exception:  # noqa: BLE001
        pass
    try:
        d = shutil.disk_usage("/")
        return {"free_gb": d.free / 1e9, "total_gb": d.total / 1e9, "pct": round(100 * d.used / d.total)}
    except OSError:
        return None


def live_status() -> dict:
    """Sistem durumunu O AN Pi'den ölçer (hafızadan/tahminden değil). Her çağrıda yeniden okur."""
    try:
        tz = ZoneInfo(os.environ.get("ROBOT_TZ", "Europe/Istanbul"))
    except Exception:  # noqa: BLE001
        tz = None
    now = datetime.datetime.now(tz)
    up = _read("/proc/uptime").split()
    try:
        load = os.getloadavg()
    except OSError:
        load = None
    wq, wl = wifi_live()
    return {"at": now, "temp_c": cpu_temp(), "mem": _memory_live(), "disk": _disk_live(), "load": load,
            "wifi_pct": wq, "wifi_dbm": wl, "uptime_s": float(up[0]) if up else None}


def format_live(s: dict) -> str:
    """live_status() sonucunu birim ve tarihle birlikte okunur metne çevirir."""
    lines = [f"Ölçüm zamanı: {s['at'].strftime('%d.%m.%Y %H:%M:%S')} (az önce Pi'den okundu)"]
    t = s.get("temp_c")
    lines.append(f"- CPU sıcaklığı: {t:.1f} °C" if t is not None else "- CPU sıcaklığı: okunamadı")
    m = s.get("mem")
    lines.append(f"- Bellek: %{m['pct']} dolu ({m['used_gb']:.1f} GB / {m['total_gb']:.1f} GB)" if m
                 else "- Bellek: okunamadı")
    d = s.get("disk")
    lines.append(f"- Disk: {d['free_gb']:.1f} GB boş / {d['total_gb']:.1f} GB (%{d['pct']} dolu)" if d
                 else "- Disk: okunamadı")
    ld = s.get("load")
    lines.append(f"- Yük ortalaması (1/5/15 dk): {ld[0]:.2f} / {ld[1]:.2f} / {ld[2]:.2f}" if ld
                 else "- Yük: okunamadı")
    if s.get("wifi_pct") is not None:
        dbm = f", {s['wifi_dbm']} dBm" if s.get("wifi_dbm") is not None else ""
        lines.append(f"- Wi-Fi sinyali: %{s['wifi_pct']}{dbm}")
    else:
        lines.append("- Wi-Fi sinyali: okunamadı (kablolu ya da kart yok olabilir)")
    if s.get("uptime_s") is not None:
        lines.append(f"- Bilgisayar açık kalma süresi: {uptime_text(s['uptime_s'])}")
    return "\n".join(lines)


def live_report() -> str:
    """Her çağrıda yeniden ölçüp metin döner; sistem durumu sorulduğunda kullanılır."""
    return format_live(live_status())


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
    live = live_status()          # sayılar bu çağrıda bir kez ölçülür, her yerde aynı değer kullanılır
    t = live["temp_c"]
    if t is not None:
        feel = "serin" if t < 55 else "ılık" if t < 70 else "sıcak" if t < 80 else "çok sıcak, tehlikeli"
        lines.append(f"- İşlemci sıcaklığım {feel} (sayı aşağıdaki ölçümde).")
    if s["fan_rpm"] is not None:
        st = fan_status()
        if st and st.get("duty") == 0:
            lines.append("- Fanım şu an duruyor (serin olduğum için gerek yok).")
        else:
            duty = f", %{st['duty']} hızda" if st and st.get("duty") is not None else ""
            lines.append(f"- Fanım {'dönüyor' + duty + ', ' + str(s['fan_rpm']) + ' devir' if s['fan_rpm'] > 0 else 'dönmüyor (hız komutu verildiği halde)'}.")
    if s["throttled"]:
        lines.append(f"- Güç/ısı uyarısı: {', '.join(s['throttled'])}.")
    # Sayılar hafızadan değil, bu çağrıda Pi'den ölçülür (birim ve zamanla)
    lines.append("Anlık sistem ölçümü (sistem durumu sorulursa sayıları SADECE buradan, birimiyle söyle):\n"
                 + format_live(live))
    lines += [f"- {e}" for e in (extra or [])]
    body = _read(str(BODY_FILE))
    return "\n".join(lines) + (f"\n\nBedenim (parçalarım):\n{body}" if body else "")
