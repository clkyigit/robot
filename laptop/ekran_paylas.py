"""Robot ekran paylaşımı (Windows).

Robot laptop ekranına bakmak istediğinde bir onay penceresi açar. İzin verirsen ekranın bir görüntüsünü
robota gönderir; reddedersen hiçbir şey gitmez. Robot laptopa bağlanmaz; bu program robota sorar.

İlk açılışta robot adresi (ör. http://192.168.68.106:8080) ve API_TOKEN sorulur, %APPDATA%\\RobotEkran'a kaydedilir.
"""
import base64
import io
import json
import os
import queue
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import simpledialog

from PIL import ImageGrab

CFG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RobotEkran")
CFG = os.path.join(CFG_DIR, "ayar.json")
ASK_SECONDS = 30
WINDOW_MINUTES = 5


def load_cfg() -> dict:
    try:
        with open(CFG, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cfg(cfg: dict) -> None:
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Robot ekran paylaşımı")
        self.root.geometry("380x170")
        self.status = "Başlıyor…"
        self.lbl = tk.Label(self.root, text=self.status, wraplength=350, justify="left", font=("Segoe UI", 10))
        self.lbl.pack(padx=12, pady=12, anchor="w")
        self.allow_lbl = tk.Label(self.root, text="", fg="#b35c00", font=("Segoe UI", 9))
        self.allow_lbl.pack(padx=12, anchor="w")
        row = tk.Frame(self.root)
        row.pack(pady=10)
        tk.Button(row, text="İzni şimdi kapat", command=self.revoke).pack(side="left", padx=4)
        tk.Button(row, text="Ayarlar", command=self.settings).pack(side="left", padx=4)
        tk.Button(row, text="Çık", command=self.root.destroy).pack(side="left", padx=4)
        self.cfg = load_cfg()
        if not self.cfg.get("url") or not self.cfg.get("token"):
            self.root.after(100, self.settings)
        self.allow_until = 0.0
        self.last_id = None
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()
        self.root.after(200, self.tick)

    # -------------------------------------------------- ayarlar
    def settings(self) -> None:
        url = simpledialog.askstring("Robot adresi", "Robotun adresi:",
                                     initialvalue=self.cfg.get("url", "http://192.168.68.106:8080"), parent=self.root)
        if url:
            token = simpledialog.askstring("Erişim anahtarı", "API_TOKEN (uygulamaya girdiğin anahtar):",
                                           show="•", parent=self.root)
            if token:
                self.cfg = {"url": url.strip().rstrip("/"), "token": token.strip()}
                save_cfg(self.cfg)

    def revoke(self) -> None:
        self.allow_until = 0.0

    # -------------------------------------------------- robotla konuşma (arka plan)
    def api(self, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.cfg["url"] + path, data=data, method="POST" if data else "GET",
                                     headers={"Authorization": "Bearer " + self.cfg["token"],
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")

    def grab(self) -> str:
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        img.thumbnail((1920, 1920))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return base64.b64encode(buf.getvalue()).decode()

    def loop(self) -> None:
        while True:
            if not self.cfg.get("url") or not self.cfg.get("token"):
                self.status = "Ayar bekleniyor (Ayarlar düğmesi)."
                time.sleep(2)
                continue
            try:
                r = self.api("/api/screen/poll")
                self.status = "✅ Robota bağlı. Robot ekranına bakmak isterse sana soracak."
            except Exception as e:  # noqa: BLE001
                self.status = f"❌ Robota ulaşılamıyor: {str(e)[:120]}"
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
