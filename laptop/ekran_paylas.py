"""Robot ekran paylaşımı (Windows).

Robot laptop ekranına bakmak istediğinde bir onay penceresi açar. İzin verirsen ekranın bir görüntüsünü
robota gönderir; reddedersen hiçbir şey gitmez. Robot laptopa bağlanmaz; bu program robota sorar.

Adres ya da anahtar yazmak gerekmez: program robotu aynı ağda kendisi bulur (pi.local ya da ağ taraması),
ekranda 6 haneli bir kod gösterir; Telegram'dan robota "/esle KOD" yazınca bağlanır. Ayarlar
%APPDATA%\\RobotEkran'a kaydedilir. Robotun adresi değişirse kendisi yeniden bulur.
"""
import base64
import io
import json
import os
import queue
import random
import socket
import threading
import urllib.error
from concurrent.futures import ThreadPoolExecutor
import time
import tkinter as tk
import urllib.request
from tkinter import simpledialog

from PIL import ImageGrab

CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RobotEkran")
CFG = os.path.join(CFG_DIR, "ayar.json")
PORT = 8080
ASK_SECONDS = 30
WINDOW_MINUTES = 5


def load_cfg() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_json(url: str, body: dict | None = None, token: str = "", timeout: float = 20) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def is_robot(base: str, timeout: float = 0.6) -> bool:
    try:
        return bool(get_json(base + "/api/hello", timeout=timeout).get("robot"))
    except Exception:  # noqa: BLE001
        return False


def local_prefixes() -> list[str]:
    """Laptopun bağlı olduğu ağların ilk üç hanesi (ör. 192.168.1.)."""
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))          # paket gitmez, sadece hangi ağ kartının kullanılacağını öğrenir
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return sorted({ip.rsplit(".", 1)[0] + "." for ip in ips
                   if ip.count(".") == 3 and not ip.startswith(("127.", "169.254."))})


def find_robot(hint: str = "") -> str:
    """Robotu ağda bulur; bulamazsa boş döner."""
    for base in [hint, f"http://pi.local:{PORT}"]:
        if base and is_robot(base, 1.5):
            return base
    cands = [f"http://{p}{i}:{PORT}" for p in local_prefixes() for i in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as ex:
        for base, ok in zip(cands, ex.map(is_robot, cands)):
            if ok:
                return base
    return ""


def save_cfg(cfg: dict) -> None:
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Robot ekran paylaşımı")
        self.root.geometry("420x210")
        self.status = "Başlıyor…"
        self.lbl = tk.Label(self.root, text=self.status, wraplength=390, justify="left", font=("Segoe UI", 11))
        self.lbl.pack(padx=12, pady=12, anchor="w")
        self.allow_lbl = tk.Label(self.root, text="", fg="#b35c00", font=("Segoe UI", 9))
        self.allow_lbl.pack(padx=12, anchor="w")
        row = tk.Frame(self.root)
        row.pack(pady=10)
        tk.Button(row, text="İzni şimdi kapat", command=self.revoke).pack(side="left", padx=4)
        tk.Button(row, text="Yeniden eşle", command=self.repair).pack(side="left", padx=4)
        tk.Button(row, text="Elle adres", command=self.settings).pack(side="left", padx=4)
        tk.Button(row, text="Çık", command=self.root.destroy).pack(side="left", padx=4)
        self.cfg = load_cfg()
        self.allow_until = 0.0
        self.last_id = None
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()
        self.root.after(200, self.tick)

    # -------------------------------------------------- ayarlar
    def settings(self) -> None:
        """Otomatik bulma olmazsa (ör. Tailscale adresi) adresi elle yazmak için. Anahtar eşleşmeyle gelir."""
        url = simpledialog.askstring("Robot adresi", "Robotun adresi (ör. http://192.168.1.190:8080):",
                                     initialvalue=self.cfg.get("url", ""), parent=self.root)
        if url:
            url = url.strip().rstrip("/")
            if not url.startswith("http"):
                url = "http://" + url
            if url.count(":") < 2:
                url += f":{PORT}"
            self.cfg["url"] = url
            save_cfg(self.cfg)

    def repair(self) -> None:
        self.cfg.pop("token", None)
        save_cfg(self.cfg)

    def pair(self) -> bool:
        """Kod gösterir, sahibi Telegram'dan /esle KOD yazana kadar bekler (5 dk)."""
        code = f"{random.SystemRandom().randint(0, 999999):06d}"
        try:
            get_json(self.cfg["url"] + "/api/pair/request", {"code": code}, timeout=8)
        except Exception as e:  # noqa: BLE001
            self.status = f"Eşleşme başlatılamadı: {str(e)[:100]}"
            return False
        self.status = f"🔑 Telegram'dan robota şunu yaz:\n\n/esle {code}"
        end = time.time() + 300
        while time.time() < end and "token" not in self.cfg:
            time.sleep(2)
            try:
                r = get_json(self.cfg["url"] + "/api/pair/status", {"code": code}, timeout=8)
            except Exception:  # noqa: BLE001
                continue
            if r.get("ok") and r.get("token"):
                self.cfg["token"] = r["token"]
                save_cfg(self.cfg)
                return True
            if not r.get("waiting"):
                break
        return "token" in self.cfg

    def revoke(self) -> None:
        self.allow_until = 0.0

    # -------------------------------------------------- robotla konuşma (arka plan)
    def api(self, path: str, body: dict | None = None) -> dict:
        return get_json(self.cfg["url"] + path, body, self.cfg.get("token", ""))

    def grab(self) -> str:
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        img.thumbnail((1920, 1920))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def loop(self) -> None:
        fails = 0
        while True:
            if not self.cfg.get("url") or fails >= 3:
                self.status = "🔎 Robotu ağda arıyorum…"
                url = find_robot(self.cfg.get("url", ""))
                if not url:
                    self.status = ("❌ Robotu bulamadım. Laptop robotla aynı Wi-Fi'da mı? Robot açık mı?\n"
                                   "(Farklı ağdaysan 'Elle adres' ile yaz.) 15 sn sonra tekrar deneyeceğim.")
                    time.sleep(15)
                    continue
                self.cfg["url"], fails = url, 0
                save_cfg(self.cfg)
            if not self.cfg.get("token"):
                if not is_robot(self.cfg["url"], 3):
                    fails = 3          # kayıtlı adres eski/yanlış: robotu yeniden ara
                    continue
                if not self.pair():
                    time.sleep(3)
                continue
            try:
                r = self.api("/api/screen/poll")
                fails = 0
                self.status = f"✅ Robota bağlı ({self.cfg['url']}).\nRobot ekranına bakmak isterse sana soracak."
            except urllib.error.HTTPError as e:
                if e.code == 401:          # anahtar değişmiş: yeniden eşle
                    self.cfg.pop("token", None)
                    save_cfg(self.cfg)
                else:
                    self.status = f"❌ Robot hata verdi: {e.code}"
                    time.sleep(5)
                continue
            except Exception as e:  # noqa: BLE001
                fails += 1
                self.status = f"❌ Robota ulaşılamıyor ({fails}/3): {str(e)[:100]}"
                time.sleep(5)
                continue
            req = r.get("request")
            if req and req.get("id") != self.last_id:
                self.last_id = req["id"]
                if time.time() < self.allow_until:
                    answer = {"ok": True}
                else:
                    ev = threading.Event()
                    answer: dict = {}
                    self.q.put((req, ev, answer))
                    ev.wait(ASK_SECONDS + 5)
                body = {"id": req["id"], "approved": bool(answer.get("ok"))}
                if answer.get("minutes"):
                    self.allow_until = time.time() + WINDOW_MINUTES * 60
                body["window"] = max(0.0, self.allow_until - time.time())   # robot ekranı bu süre izleyebilir
                if body["approved"]:
                    try:
                        body["image"] = self.grab()
                    except Exception as e:  # noqa: BLE001
                        body = {"id": req["id"], "approved": False, "error": f"Ekran görüntüsü alınamadı: {e}"}
                try:
                    self.api("/api/screen/answer", body)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(1.5)

    # -------------------------------------------------- arayüz (ana iş parçacığı)
    def tick(self) -> None:
        self.lbl.config(text=self.status)
        left = int(self.allow_until - time.time())
        self.allow_lbl.config(text=f"Robot {left // 60}:{left % 60:02d} boyunca sormadan bakabilir." if left > 0 else "")
        while not self.q.empty():
            self.ask(*self.q.get())
        self.root.after(300, self.tick)

    def ask(self, req: dict, ev: threading.Event, answer: dict) -> None:
        top = tk.Toplevel(self.root)
        top.title("Robot ekranına bakmak istiyor")
        top.attributes("-topmost", True)
        top.geometry("420x210")
        reason = req.get("reason") or "Ekranına bakmak istiyor."
        tk.Label(top, text="🤖 Robot ekranına bakmak istiyor", font=("Segoe UI", 11, "bold")).pack(pady=(12, 4))
        tk.Label(top, text=reason, wraplength=390, font=("Segoe UI", 10)).pack(padx=12)
        tk.Label(top, text="Ekranda hasta bilgisi ya da gizli bir şey varsa Reddet.\n"
                           "(Görüntü yapay zekâ servislerine gönderilir.)",
                 fg="#a00000", font=("Segoe UI", 9)).pack(pady=6)
        row = tk.Frame(top)
        row.pack(pady=6)

        def done(ok: bool, minutes: bool = False) -> None:
            if not ev.is_set():
                answer.update(ok=ok, minutes=minutes)
                ev.set()
            top.destroy()

        tk.Button(row, text="İzin ver", width=10, command=lambda: done(True)).pack(side="left", padx=4)
        tk.Button(row, text=f"{WINDOW_MINUTES} dk izin ver", width=12,
                  command=lambda: done(True, True)).pack(side="left", padx=4)
        tk.Button(row, text="Reddet", width=10, command=lambda: done(False)).pack(side="left", padx=4)
        top.protocol("WM_DELETE_WINDOW", lambda: done(False))
        top.after(ASK_SECONDS * 1000, lambda: done(False) if top.winfo_exists() else None)
        top.bell()
        top.lift()
        top.focus_force()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
