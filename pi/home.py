"""Ev kontrolü: Govee ışıklar (yerel ağ, UDP) ve Android/Google TV (ağ üzerinden ADB).

Govee (Işıklar uygulamasındaki yöntem): tarama 239.255.255.250:4001 -> cevap 4002, komut cihaz IP:4003.
  Her cihazda Govee Home > cihaz > Ayarlar > "LAN Control" açık olmalı.
TV: Ayarlar > Cihaz tercihleri > Hakkında > "Yapım numarası"na 7 kez bas (geliştirici modu),
  Geliştirici seçenekleri > "USB hata ayıklama" (+ varsa "Ağ üzerinden hata ayıklama") açık.
  İlk bağlantıda TV'de çıkan "Hata ayıklamaya izin ver" sorusuna "Her zaman izin ver" de.

Ayarlar (.env):
  TV_IP=192.168.1.50           TV'nin ev ağındaki adresi (sabit IP verilmesi önerilir)
  GOVEE_NAMES=H8005A:tavan,H6099:tv    isteğe bağlı: model -> isim (konuşurken "tavan ışığı" diyebilmek için)
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import socket
import time

log = logging.getLogger("robot.home")

GOVEE_MCAST, SCAN_PORT, LISTEN_PORT, CMD_PORT = "239.255.255.250", 4001, 4002, 4003
TV_IP = os.environ.get("TV_IP", "").strip()
NAMES = dict(p.split(":", 1) for p in os.environ.get("GOVEE_NAMES", "").split(",") if ":" in p)

RENKLER = {
    "kırmızı": (255, 0, 0), "kirmizi": (255, 0, 0), "yeşil": (0, 255, 0), "yesil": (0, 255, 0),
    "mavi": (0, 80, 255), "sarı": (255, 200, 0), "sari": (255, 200, 0), "turuncu": (255, 110, 0),
    "mor": (150, 0, 255), "pembe": (255, 60, 150), "turkuaz": (0, 220, 200), "lacivert": (0, 0, 140),
    "beyaz": (255, 255, 255), "eflatun": (200, 0, 200), "altın": (255, 170, 30),
}
SICAKLIK = {"sıcak": 2700, "sicak": 2700, "gün ışığı": 5000, "gun isigi": 5000, "soğuk": 6500,
            "soguk": 6500, "doğal": 4000, "dogal": 4000, "okuma": 4500}
UYGULAMALAR = {
    "youtube": "com.google.android.youtube.tv", "netflix": "com.netflix.ninja",
    "prime": "com.amazon.amazonvideo.livingroom", "disney": "com.disney.disneyplus",
    "spotify": "com.spotify.tv.android", "blutv": "com.blu.blutv", "exxen": "com.exxen.androidtv",
    "trt": "com.trt.tabii.android.tv", "tabii": "com.trt.tabii.android.tv", "plex": "com.plexapp.android",
    "kodi": "org.xbmc.kodi", "tod": "com.digiturk.iq.mobil", "chrome": "com.android.chrome",
}


def _msg(cmd: str, data: dict) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()


class Govee:
    def __init__(self) -> None:
        self.devices: dict[str, dict] = {}     # id -> {sku, ip, name}
        self.scanned = 0.0
        self.error = ""

    def _scan_sync(self, timeout: float) -> dict:
        found = {}
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("", LISTEN_PORT))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            for _ in range(2):
                s.sendto(_msg("scan", {"account_topic": "reserve"}), (GOVEE_MCAST, SCAN_PORT))
            end = time.time() + timeout
            while time.time() < end:
                s.settimeout(max(0.05, end - time.time()))
                try:
                    data, addr = s.recvfrom(4096)
                except socket.timeout:
                    break
                with contextlib.suppress(Exception):
                    d = json.loads(data)["msg"]
                    if d.get("cmd") == "scan":
                        d = d["data"]
                        sku = d.get("sku", "")
                        found[d["device"].upper()] = {"sku": sku, "ip": d.get("ip") or addr[0],
                                                      "name": NAMES.get(sku, sku)}
        finally:
            s.close()
        return found

    async def scan(self, force: bool = False) -> dict:
        if force or not self.devices or time.time() - self.scanned > 600:
            try:
                found = await asyncio.to_thread(self._scan_sync, 2.5)
                if found or force:
                    self.devices = found
                self.scanned, self.error = time.time(), ""
            except OSError as e:
                self.error = str(e)
        return self.devices

    def _pick(self, which: str) -> list[dict]:
        w = (which or "").lower().strip()
        devs = list(self.devices.values())
        if not w or w in ("hepsi", "tümü", "tum", "tüm", "bütün", "ışıklar", "isiklar", "all"):
            return devs
        sel = [d for d in devs if w in d["name"].lower() or w in d["sku"].lower()]
        return sel or devs

    def _send_sync(self, ip: str, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(payload, (ip, CMD_PORT))

    async def command(self, action: str, value: str = "", which: str = "") -> str:
        await self.scan()
        if not self.devices:
            await self.scan(force=True)
        if not self.devices:
            return ("Govee ışık bulamadım. Govee Home'da her cihaz için 'LAN Control' açık mı, robotla aynı "
                    "Wi-Fi'da mı?" + (f" ({self.error})" if self.error else ""))
        a, v = (action or "").lower().strip(), (value or "").lower().strip()
        if a in ("ac", "aç", "on"):
            payload, desc = _msg("turn", {"value": 1}), "açtım"
        elif a in ("kapat", "kapa", "off"):
            payload, desc = _msg("turn", {"value": 0}), "kapattım"
        elif a in ("parlaklik", "parlaklık", "brightness"):
            n = int(re.sub(r"\D", "", v) or 50)
            payload, desc = _msg("brightness", {"value": max(1, min(100, n))}), f"parlaklığı %{n} yaptım"
        elif a in ("renk", "color"):
            rgb = RENKLER.get(v)
            if not rgb and re.fullmatch(r"#?[0-9a-f]{6}", v):
                h = v.lstrip("#")
                rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            if not rgb:
                return f"'{value}' rengini bilmiyorum. Bildiklerim: {', '.join(sorted(set(RENKLER)))}"
            payload = _msg("colorwc", {"color": dict(zip("rgb", rgb)), "colorTemInKelvin": 0})
            desc = f"rengi {value} yaptım"
        elif a in ("beyaz", "sicaklik", "sıcaklık", "kelvin"):
            k = SICAKLIK.get(v) or int(re.sub(r"\D", "", v) or 4000)
            payload = _msg("colorwc", {"color": {"r": 255, "g": 255, "b": 255},
                                       "colorTemInKelvin": max(2000, min(9000, k))})
            desc = f"beyaz ışığı {k} K yaptım"
        elif a in ("tv_senkron", "senkron", "video"):
            import base64
            frame = bytearray(20)
            frame[:6] = bytes([0x33, 0x05, 0x00, 0x01, 0x00, 0x64])
            c = 0
            for b in frame[:19]:
                c ^= b
            frame[19] = c
            payload = _msg("ptReal", {"command": [base64.b64encode(bytes(frame)).decode()]})
            desc = "TV ile senkron (video) modunu açtım"
        else:
            return f"Bilinmeyen ışık komutu: {action}"
        targets = self._pick(which)
        for d in targets:
            await asyncio.to_thread(self._send_sync, d["ip"], payload)
        return f"💡 {', '.join(d['name'] for d in targets)}: {desc}."

    def status(self) -> dict:
        return {"devices": [{"name": d["name"], "sku": d["sku"], "ip": d["ip"]} for d in self.devices.values()],
                "error": self.error}


class TV:
    def __init__(self) -> None:
        self.error = ""

    async def _adb(self, *args: str, timeout: float = 15) -> str:
        p = await asyncio.create_subprocess_exec("adb", *args, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
        return out.decode(errors="ignore").strip()

    async def _connect(self) -> str | None:
        if not TV_IP:
            return "TV adresi ayarlı değil (.env: TV_IP=TV'nin IP adresi)."
        if not shutil.which("adb"):
            return "adb kurulu değil (sudo apt install adb)."
        target = TV_IP if ":" in TV_IP else f"{TV_IP}:5555"
        state = await self._adb("-s", target, "get-state", timeout=5)
        if state.strip() == "device":
            return None
        out = await self._adb("connect", target, timeout=10)
        state = await self._adb("-s", target, "get-state", timeout=5)
        if state.strip() == "device":
            return None
        if "unauthorized" in (out + state):
            return "TV ekranındaki 'hata ayıklamaya izin ver' sorusuna 'Her zaman izin ver' de, sonra tekrar dene."
        return f"TV'ye bağlanamadım ({out[:120]}). TV açık mı, geliştirici modunda ağ/USB hata ayıklama açık mı?"

    async def _shell(self, cmd: str) -> str:
        target = TV_IP if ":" in TV_IP else f"{TV_IP}:5555"
        return await self._adb("-s", target, "shell", cmd)

    async def _key(self, code: str, n: int = 1) -> None:
        await self._shell(" ; ".join([f"input keyevent {code}"] * max(1, min(n, 30))))

    async def command(self, action: str, value: str = "") -> str:
        err = await self._connect()
        if err:
            self.error = err
            return "📺 " + err
        a, v = (action or "").lower().strip(), (value or "").strip()
        n = int(re.sub(r"\D", "", v) or 3) if v and re.search(r"\d", v) else 3
        try:
            if a in ("ac", "aç", "on"):
                await self._key("KEYCODE_WAKEUP"); return "📺 TV'yi açtım."
            if a in ("kapat", "kapa", "off"):
                await self._key("KEYCODE_SLEEP"); return "📺 TV'yi kapattım."
            if a in ("ses_ac", "ses_aç", "sesi_ac", "ses+"):
                await self._key("KEYCODE_VOLUME_UP", n); return f"📺 Sesi {n} kademe açtım."
            if a in ("ses_kis", "ses_kıs", "sesi_kis", "ses-"):
                await self._key("KEYCODE_VOLUME_DOWN", n); return f"📺 Sesi {n} kademe kıstım."
            if a in ("sessiz", "mute"):
                await self._key("KEYCODE_VOLUME_MUTE"); return "📺 Sesi kapattım/açtım (sessiz)."
            if a in ("duraklat", "oynat", "pause", "play"):
                await self._key("KEYCODE_MEDIA_PLAY_PAUSE"); return "📺 Oynat/duraklat."
            if a in ("ev", "home", "ana_ekran"):
                await self._key("KEYCODE_HOME"); return "📺 Ana ekrana döndüm."
            if a in ("geri", "back"):
                await self._key("KEYCODE_BACK"); return "📺 Geri."
            if a in ("uygulama", "ac_uygulama", "app"):
                pkg = UYGULAMALAR.get(v.lower(), v if "." in v else "")
                if not pkg:
                    return f"📺 '{v}' uygulamasını tanımıyorum. Bildiklerim: {', '.join(UYGULAMALAR)}"
                await self._shell(f"monkey -p {pkg} -c android.intent.category.LEANBACK_LAUNCHER 1")
                return f"📺 {v} açılıyor."
            if a in ("youtube", "video"):
                from music import _ytdlp
                exe = _ytdlp()
                if not exe:
                    return "📺 Videoyu bulmak için yt-dlp gerekli."
                p = await asyncio.create_subprocess_exec(
                    exe, "--no-warnings", "--get-id", "--get-title", f"ytsearch1:{v}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                out, _ = await asyncio.wait_for(p.communicate(), 40)
                lines = out.decode(errors="ignore").strip().splitlines()
                if len(lines) < 2:
                    return f"📺 '{v}' için video bulamadım."
                title, vid = lines[0], lines[1]
                await self._key("KEYCODE_WAKEUP")
                await self._shell(f"am start -a android.intent.action.VIEW -d https://www.youtube.com/watch?v={vid} "
                                  f"{UYGULAMALAR['youtube']}")
                return f"📺 TV'de açıyorum: {title}"
            return f"📺 Bilinmeyen TV komutu: {action}"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:150]
            return f"📺 TV komutu çalışmadı: {self.error}"

    def status(self) -> dict:
        return {"ip": TV_IP, "error": self.error}


govee = Govee()
tv = TV()

TOOL_ISIK = {
    "name": "isik",
    "description": "Evdeki Govee ışıkları kontrol eder (yerel ağ). eylem: ac, kapat, parlaklik (deger 1-100), "
                   "renk (deger: kırmızı, mavi, yeşil, mor, turuncu, pembe, sarı, turkuaz, beyaz... ya da #ff8800), "
                   "beyaz (deger: sıcak, doğal, gün ışığı, soğuk ya da 2000-9000 K), tv_senkron (TV ekranıyla "
                   "senkron modu). hangi: belirli bir ışık adı; boşsa hepsi.",
    "params": {"eylem": {"type": "string"}, "deger": {"type": "string"}, "hangi": {"type": "string"}},
    "required": ["eylem"],
}
TOOL_TV = {
    "name": "tv",
    "description": "Evdeki Android/Google TV'yi kontrol eder. eylem: ac, kapat, ses_ac, ses_kis (deger: kaç kademe), "
                   "sessiz, duraklat, ev, geri, uygulama (deger: youtube, netflix, prime, disney, spotify, tabii, "
                   "blutv, exxen...), youtube (deger: aranacak video; TV'de YouTube'da açar).",
    "params": {"eylem": {"type": "string"}, "deger": {"type": "string"}},
    "required": ["eylem"],
}


async def run(name: str, args: dict) -> str:
    args = args or {}
    if name == "isik":
        return await govee.command(str(args.get("eylem", "")), str(args.get("deger", "")), str(args.get("hangi", "")))
    if name == "tv":
        return await tv.command(str(args.get("eylem", "")), str(args.get("deger", "")))
    return f"Bilinmeyen ev aracı: {name}"
