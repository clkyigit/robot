"""Açılış koruması: robot servisi her başlarken (systemd ExecStartPre) çalışır.

Robot kendi kodunu değiştirdiyse ve yeni kodla art arda açılamıyorsa bir önceki sürüme döner.
Sadece standart kütüphane kullanır ve robotun diğer dosyalarına bağlı değildir; robotun kodu tamamen
bozulsa bile çalışır. Robot bu dosyayı değiştiremez (selfdev.py'de koruma altında).
"""
import json
import pathlib
import subprocess
import time

PI = pathlib.Path(__file__).resolve().parent
REPO = PI.parent
STATE = PI / ".selfdev"
PENDING = STATE / "pending.json"
ROLLED = STATE / "rolled_back.json"
MAX_TRIES = 3


def main() -> None:
    if not PENDING.exists():
        return
    try:
        p = json.loads(PENDING.read_text())
    except (OSError, json.JSONDecodeError):
        PENDING.unlink(missing_ok=True)
        return
    p["tries"] = p.get("tries", 0) + 1
    if p["tries"] <= MAX_TRIES:
        PENDING.write_text(json.dumps(p))
        print(f"selfdev_guard: yeni kod deneniyor ({p['tries']}/{MAX_TRIES})")
        return
    # Yeni kodla açılamadı: önceki sürüme dön
    prev = p.get("prev", "")
    ok = False
    if prev:
        r = subprocess.run(["git", "-C", str(REPO), "reset", "--hard", prev], capture_output=True, text=True)
        ok = r.returncode == 0
    ROLLED.write_text(json.dumps({"at": time.time(), "prev": prev, "bad": p.get("new", ""),
                                  "summary": p.get("summary", ""), "ok": ok}, ensure_ascii=False))
    PENDING.unlink(missing_ok=True)
    print(f"selfdev_guard: yeni kod {MAX_TRIES} kez açılamadı, {prev[:8]} sürümüne dönüldü (başarılı={ok})")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001  — koruma asla servisin başlamasını engellememeli
        print(f"selfdev_guard hatası: {e}")
