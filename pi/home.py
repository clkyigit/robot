"""Ev kontrolü: Govee ışıklar (yerel ağ, UDP) ve Android/Google TV (ağ üzerinden ADB).

Govee (Işıklar uygulamasındaki yöntem): tarama 239.255.255.250:4001 -> cevap 4002, komut cihaz IP:4003.
  Her cihazda Govee Home > cihaz > Ayarlar > "LAN Control" açık olmalı.
TV: Ayarlar > Cihaz tercihleri > Hakkında > "Yapım numarası"na 7 kez bas (geliştirici modu),
  Geliştirici seçenekleri > "USB hata ayıklama" (+ varsa "Ağ üzerinden hata ayıklama") açık.
  İlk bağlantıda TV'de çıkan "Hata ayıklamaya izin ver" sorusuna "Her zaman izin ver" de.

Ayarlar (.env):
  TV_IP=192.168.1.50           isteğe bağlı: yazılmazsa robot TV'yi ağda kendisi bulur
  GOVEE_NAMES=H8005A:tavan,H6099:tv    isteğe bağlı: model -> isim (konuşurken "tavan ışığı" diyebilmek için)
  GOVEE_API_KEY=...            isteğe bağlı: LAN'da bulunamazsa Govee bulutu ile kontrol (Govee Home > Profil > ⚙️ > Apply for API Key)
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
TVPLUS_PKG = os.environ.get("TVPLUS_PKG", "").strip()
# Kanal adı -> TV+ kanal numarası. Numaralar pakete göre değişir; .env'de TVPLUS_KANALLAR ile düzeltilebilir.
# Sahibinin TV+ kanal listesinden (Kasım 2026 ekran görüntüleri). .env TVPLUS_KANALLAR ile değiştirilebilir/eklenebilir.
_KANAL_LISTESI = """
11 sinemaaksiyon, 12 sinemakomedi, 13 sinemaaile, 14 sinema1001, 15 sinemayerli2, 16 sinema2,
17 sinemaaksiyon2, 18 sinemaaile2, 19 sinema1002, 20 cosmo, 21 trt1, 22 kanald, 23 star, 24 atv, 25 show,
26 now|fox, 27 tv8, 30 tlc, 31 cnbce, 32 teve2|tv2, 34 kanal7, 36 beyaz, 37 tv100, 38 flash, 39 dmax, 40 bizimev,
41 tv85, 42 gzt, 45 tv4, 46 as, 47 ekol, 48 turkhaber, 49 cnnturk|cnn, 50 ntv, 51 haberturk, 52 ahaber,
53 trthaber, 54 24|yirmidort, 55 bloomberg|bloomberght, 56 apara, 57 halk, 58 tele1, 59 haberglobal,
60 ekoturk, 61 ulke, 62 tgrthaber|tgrt, 63 tvnet, 64 akit, 65 benguturk, 66 ulusal, 67 krt, 68 tv5, 69 szc|sozcu,
70 trtspor, 71 trtsporyildiz, 73 htspor, 74 fbtv, 75 tabiispor|tabii, 77 ssport, 78 ssport2, 79 eurosport|eurosport1,
80 eurosport2, 92 sportstv|sports, 94 ekstra|ekstratv, 95 trtbelgesel, 96 tarih, 97 explore|viasatexplore
"""


def _norm(name: str) -> str:
    t = name.lower().translate(str.maketrans("ıİşŞğĞüÜöÖçÇâ", "iissgguuoocca"))
    return re.sub(r"[^a-z0-9]", "", t)


TVPLUS_KANALLAR: dict[str, str] = {}
for _item in _KANAL_LISTESI.replace("\n", " ").split(","):
    _item = _item.strip()
    if _item:
        _num, _names = _item.split(" ", 1)
        for _n in _names.split("|"):
            TVPLUS_KANALLAR[_norm(_n)] = _num
TVPLUS_KANALLAR.update({_norm(k): v.strip() for k, v in
                        (p.split(":", 1) for p in os.environ.get("TVPLUS_KANALLAR", "").split(",") if ":" in p)})


def kanal_no(name: str) -> str:
    n = _norm(name)
    if n.isdigit():
        return n
    for cand in (n, re.sub(r"(kanali|kanal|tv|hd)$", "", n), re.sub(r"(kanali|kanal|hd)$", "", n),
                 re.sub(r"^(kanal)", "kanal", n)):
        if cand in TVPLUS_KANALLAR:
            return TVPLUS_KANALLAR[cand]
    return ""
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


GOVEE_API_KEY = os.environ.get("GOVEE_API_KEY", "").strip()
GOVEE_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "govee.json")
TV_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tv.json")
GOVEE_CLOUD = "https://openapi.api.govee.com/router/api/v1"


def _local_ip() -> str:
    """Robotun ev ağındaki adresi (paket göndermeden, yönlendirme tablosundan)."""
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    return ""


class Govee:
    """Işıkları kendisi bulur: çoklu yayın + genel yayın + tüm /24 ağa tek tek sorar; bulduklarını hatırlar.
    Yerel ağda hiç bulamazsa ve GOVEE_API_KEY varsa Govee bulutu üzerinden kontrol eder."""

    def __init__(self) -> None:
        self.devices: dict[str, dict] = {}     # id -> {sku, ip, name}
        self.cloud: dict[str, dict] = {}       # id -> {sku, name}
        self.scanned = 0.0
        self.error = ""
        with contextlib.suppress(Exception):
            with open(GOVEE_CACHE) as f:
                self.devices = json.load(f)

    def _save(self) -> None:
        with contextlib.suppress(Exception):
            with open(GOVEE_CACHE, "w") as f:
                json.dump(self.devices, f)

    def _scan_sync(self, timeout: float) -> dict:
        found: dict[str, dict] = {}
        me = _local_ip()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(("", LISTEN_PORT))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if me:
                with contextlib.suppress(OSError):   # çoklu yayını Wi-Fi arayüzünden çıkar
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(me))
            msg = _msg("scan", {"account_topic": "reserve"})

            def listen(until: float) -> None:
                while time.time() < until:
                    s.settimeout(max(0.05, until - time.time()))
                    try:
                        data, addr = s.recvfrom(4096)
                    except (socket.timeout, OSError):
                        return
                    with contextlib.suppress(Exception):
                        d = json.loads(data)["msg"]
                        if d.get("cmd") == "scan":
                            d = d["data"]
                            sku = d.get("sku", "")
                            found[d["device"].upper()] = {"sku": sku, "ip": d.get("ip") or addr[0],
                                                          "name": NAMES.get(sku, sku)}

            targets = [GOVEE_MCAST, "255.255.255.255"]
            if me:
                targets.append(me.rsplit(".", 1)[0] + ".255")
            for _ in range(2):
                for t in targets:
                    with contextlib.suppress(OSError):
                        s.sendto(msg, (t, SCAN_PORT))
            listen(time.time() + timeout)
            if not found and me:                    # yönlendirici çoklu yayını yutuyorsa: herkese tek tek sor
                base = me.rsplit(".", 1)[0]
                for i in range(1, 255):
                    with contextlib.suppress(OSError):
                        s.sendto(msg, (f"{base}.{i}", SCAN_PORT))
                listen(time.time() + timeout)
        finally:
            s.close()
        return found

    def _cloud_sync(self, path: str, body: dict | None = None) -> dict:
        import urllib.request
        req = urllib.request.Request(GOVEE_CLOUD + path, method="POST" if body else "GET",
                                     data=json.dumps(body).encode() if body else None,
                                     headers={"Govee-API-Key": GOVEE_API_KEY, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    async def _cloud_list(self) -> dict:
        if not GOVEE_API_KEY:
            return {}
        if not self.cloud:
            try:
                out = await asyncio.to_thread(self._cloud_sync, "/user/devices")
                for d in out.get("data") or []:
                    self.cloud[d["device"]] = {"sku": d.get("sku", ""),
                                               "name": d.get("deviceName") or NAMES.get(d.get("sku", ""), d.get("sku", ""))}
            except Exception as e:  # noqa: BLE001
                self.error = f"Govee bulut: {e}"
        return self.cloud

    async def scan(self, force: bool = False) -> dict:
        if force or not self.scanned or time.time() - self.scanned > 600:
            try:
                found = await asyncio.to_thread(self._scan_sync, 2.0)
                if found:
                    self.devices = found
                    self._save()
                self.scanned, self.error = time.time(), ""
            except OSError as e:
                self.error = str(e)
                self.scanned = time.time()
        if not self.devices:
            await self._cloud_list()
        return self.devices or {k: {**v, "ip": "bulut"} for k, v in self.cloud.items()}

    def _pick(self, which: str, pool: list[dict] | None = None) -> list[dict]:
        w = (which or "").lower().strip()
        devs = pool if pool is not None else list(self.devices.values())
        if not w or w in ("hepsi", "tümü", "tum", "tüm", "bütün", "ışıklar", "isiklar", "all"):
            return devs
        sel = [d for d in devs if w in d["name"].lower() or w in d["sku"].lower()]
        return sel or devs

    def _send_sync(self, ip: str, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(payload, (ip, CMD_PORT))

    async def _cloud_command(self, a: str, v: str, which: str, value: str) -> str:
        if a in ("ac", "aç", "on", "kapat", "kapa", "off"):
            cap, desc = ("devices.capabilities.on_off", "powerSwitch", 0 if a in ("kapat", "kapa", "off") else 1), \
                ("kapattım" if a in ("kapat", "kapa", "off") else "açtım")
        elif a in ("parlaklik", "parlaklık", "brightness"):
            n = max(1, min(100, int(re.sub(r"\D", "", v) or 50)))
            cap, desc = ("devices.capabilities.range", "brightness", n), f"parlaklığı %{n} yaptım"
        elif a in ("renk", "color"):
            rgb = RENKLER.get(v)
            if not rgb and re.fullmatch(r"#?[0-9a-f]{6}", v):
                h = v.lstrip("#")
                rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
            if not rgb:
                return f"'{value}' rengini bilmiyorum. Bildiklerim: {', '.join(sorted(set(RENKLER)))}"
            cap, desc = ("devices.capabilities.color_setting", "colorRgb",
                         (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]), f"rengi {value} yaptım"
        elif a in ("beyaz", "sicaklik", "sıcaklık", "kelvin"):
            k = max(2000, min(9000, SICAKLIK.get(v) or int(re.sub(r"\D", "", v) or 4000)))
            cap, desc = ("devices.capabilities.color_setting", "colorTemperatureK", k), f"beyaz ışığı {k} K yaptım"
        else:
            return f"Bu komut ({a}) bulut üzerinden desteklenmiyor; yerel ağ (LAN Control) gerekli."
        pool = [{"id": i, **d} for i, d in self.cloud.items()]
        targets = self._pick(which, pool)
        bad = []
        for d in targets:
            import uuid
            body = {"requestId": str(uuid.uuid4()), "payload": {"sku": d["sku"], "device": d["id"], "capability":
                    {"type": cap[0], "instance": cap[1], "value": cap[2]}}}
            try:
                await asyncio.to_thread(self._cloud_sync, "/device/control", body)
            except Exception:  # noqa: BLE001
                bad.append(d["name"])
        ok = [d["name"] for d in targets if d["name"] not in bad]
        return (f"💡 {', '.join(ok)}: {desc} (bulut)." if ok else "") + \
            (f" ⚠️ Ulaşamadım: {', '.join(bad)}." if bad else "")

    async def command(self, action: str, value: str = "", which: str = "") -> str:
        await self.scan()
        if not self.devices:
            await self.scan(force=True)
        if not self.devices and self.cloud:
            return await self._cloud_command((action or "").lower().strip(), (value or "").lower().strip(),
                                             which, value)
        if not self.devices:
            return ("Govee ışıkları ağda bulamadım (tüm ağı da taradım). Govee Home > cihaz > ⚙️ > 'LAN Control' "
                    "açık mı, ışıklar robotla aynı Wi-Fi'da mı? Alternatif: Govee API anahtarı ekle (GOVEE_API_KEY), "
                    "o zaman internet üzerinden ulaşırım." + (f" ({self.error})" if self.error else ""))
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

    async def _find(self) -> str:
        """TV_IP yoksa ya da değiştiyse: ağda adb portu (5555) açık cihazı kendisi bulur, hatırlar."""
        me = _local_ip()
        if not me:
            return ""
        base = me.rsplit(".", 1)[0]

        async def probe(ip: str) -> str:
            try:
                _, w = await asyncio.wait_for(asyncio.open_connection(ip, 5555), 0.6)
                w.close()
                return ip
            except Exception:  # noqa: BLE001
                return ""
        hits = [ip for ip in await asyncio.gather(*(probe(f"{base}.{i}") for i in range(1, 255))) if ip and ip != me]
        if hits:
            with contextlib.suppress(Exception):
                with open(TV_CACHE, "w") as f:
                    json.dump({"ip": hits[0]}, f)
        return hits[0] if hits else ""

    async def _connect(self) -> str | None:
        global TV_IP
        if not shutil.which("adb"):
            return "adb kurulu değil (sudo apt install adb)."
        if not TV_IP:
            with contextlib.suppress(Exception):
                with open(TV_CACHE) as f:
                    TV_IP = json.load(f)["ip"]
        if not TV_IP:
            TV_IP = await self._find()
        if not TV_IP:
            return ("TV'yi ağda bulamadım. TV açık mı, Geliştirici seçenekleri > 'USB hata ayıklama' açık mı? "
                    "(Gerekirse .env: TV_IP=...)")
        target = TV_IP if ":" in TV_IP else f"{TV_IP}:5555"
        state = await self._adb("-s", target, "get-state", timeout=5)
        if state.strip() == "device":
            return None
        out = await self._adb("connect", target, timeout=10)
        state = await self._adb("-s", target, "get-state", timeout=5)
        if state.strip() == "device":
            return None
        if "unauthorized" not in (out + state) and not os.environ.get("TV_IP"):
            ip = await self._find()                 # adres değişmiş olabilir: yeniden ara
            if ip and ip != TV_IP:
                TV_IP = ip
                return await self._connect()
        if "unauthorized" in (out + state):
            return "TV ekranındaki 'hata ayıklamaya izin ver' sorusuna 'Her zaman izin ver' de, sonra tekrar dene."
        return f"TV'ye bağlanamadım ({out[:120]}). TV açık mı, geliştirici modunda ağ/USB hata ayıklama açık mı?"

    async def _shell(self, cmd: str) -> str:
        target = TV_IP if ":" in TV_IP else f"{TV_IP}:5555"
        return await self._adb("-s", target, "shell", cmd)

    async def _key(self, code: str, n: int = 1) -> None:
        await self._shell(" ; ".join([f"input keyevent {code}"] * max(1, min(n, 30))))

    async def _tvplus_pkg(self) -> str:
        global TVPLUS_PKG
        if TVPLUS_PKG:
            return TVPLUS_PKG
        out = await self._shell("pm list packages")
        pk = [l.split(":", 1)[1] for l in out.splitlines() if l.startswith("package:")]
        for key in ("tvplus", "tv_plus", "tvartı", "turktelekom", "ttnet", "ttplus"):
            hit = [x for x in pk if key in x.lower()]
            if hit:
                TVPLUS_PKG = hit[0]
                return TVPLUS_PKG
        return ""

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
            if a in ("tvplus", "tv+", "kanal"):
                pkg = await self._tvplus_pkg()
                if not pkg:
                    return "📺 TV'de TV+ uygulamasını bulamadım (gerekirse .env: TVPLUS_PKG=paket.adı)."
                await self._key("KEYCODE_WAKEUP")
                await self._shell(f"monkey -p {pkg} -c android.intent.category.LEANBACK_LAUNCHER 1")
                if not v:
                    return "📺 TV+ açılıyor."
                num = kanal_no(v)
                if not num:
                    return (f"📺 TV+ açılıyor, ama '{v}' kanalını listemde bulamadım. Numarasıyla söyle "
                            "(örn. 'TV+ 25') ya da .env'e ekle: TVPLUS_KANALLAR=kanaladi:numara")
                await asyncio.sleep(8)          # uygulama ve canlı yayın açılsın
                await self._shell(" ; ".join(f"input keyevent KEYCODE_{d}" for d in num) + " ; sleep 1 ; "
                                  "input keyevent KEYCODE_DPAD_CENTER")
                return f"📺 TV+ açıldı, {v} ({num}. kanal) seçiliyor."
            if a in ("youtube_ara", "ara"):
                from urllib.parse import quote
                await self._key("KEYCODE_WAKEUP")
                await self._shell(f"am start -a android.intent.action.VIEW -d "
                                  f"'https://www.youtube.com/results?search_query={quote(v)}' {UYGULAMALAR['youtube']}")
                return f"📺 YouTube'da arıyorum: {v}"
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
                   "blutv, exxen...), youtube (deger: video adı; ilk sonucu TV'de oynatır), youtube_ara (deger: "
                   "arama; TV'de YouTube arama sonuçlarını gösterir), tvplus (deger: kanal adı ya da numarası, "
                   "örn. TRT 1, Show, Kanal D, NTV, Habertürk, S Sport, TRT Spor; "
                   "TV+ uygulamasını açıp o kanala geçer, deger boşsa sadece açar).",
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
