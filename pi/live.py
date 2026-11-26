"""Canlı sesli sohbet (Gemini Live).

Akış: kulaklar "robot" kelimesini duyar -> canlı oturum açılır -> mikrofon sesi doğrudan Gemini'ye akar,
Gemini'nin sesli cevabı anında hoparlörden çalar. Sen sustuğunda cevap verir; cevaptan sonra birkaç
saniye içinde konuşursan kelimeyi tekrar demeden sohbet sürer. Sessiz kalırsan oturum kapanır ve robot
yeniden uyandırma kelimesini beklemeye döner.

Yankı: hoparlör mikrofona girmesin diye robot konuşurken mikrofon susturulur (yarım çift yönlü).
Araya girmek istersen LIVE_BARGE_IN=1 yap (yankı engelleyici mikrofon/hoparlör gerekir).

Ayarlar (.env):
  LIVE=1                         0 yaparsan eski yöntem: kaydet -> yazıya çevir -> düşün -> seslendir
  GEMINI_LIVE_MODEL=gemini-3.8-live
  LIVE_VOICE=Kore                Gemini ses tonu (AI Studio'da dinlenebilir)
  LIVE_IDLE_S=8                  son konuşmadan sonra bu kadar sessizlikte oturum kapanır
  LIVE_SPEECH_RMS=500            mikrofon bu seviyeyi geçerse "konuşuyor" sayılır
  LIVE_BARGE_IN=0                1: robot konuşurken de mikrofon açık (araya girebilirsin)
  LIVE_TELEGRAM=1                her soru-cevabı Telegram'a da yaz
  LIVE_CAMERA=1                  sohbet sırasında kamera görüntüsü de gider (saniyede 1 kare; gizlilik modunda gitmez)
  LIVE_CAMERA_FPS=1
  LIVE_SEARCH=1                  sohbette Google araması (güncel bilgi: haber, hava, kur, maç...)
  SPEAKER_DEVICE=default
"""

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional

log = logging.getLogger("robot.live")

MODELS = [m.strip() for m in (os.environ.get("GEMINI_LIVE_MODEL", "") +
                              ",gemini-3.8-live,gemini-3.1-flash-live-preview").split(",") if m.strip()]
MODELS = list(dict.fromkeys(MODELS))
VOICE_NAME = os.environ.get("LIVE_VOICE", "Kore").strip()
IDLE_S = float(os.environ.get("LIVE_IDLE_S", "8"))
SPEECH_RMS = float(os.environ.get("LIVE_SPEECH_RMS", "500"))
BARGE_IN = os.environ.get("LIVE_BARGE_IN", "0").strip() == "1"
DEVICE = os.environ.get("SPEAKER_DEVICE", "default").strip()
# Bağlam sıkıştırma açıkken oturum süresi sınırsız; bağlantı ~10 dk'da bir yenilenir (kaldığı yerden devam).
MAX_SESSION_S = float(os.environ.get("LIVE_MAX_MIN", "60")) * 60   # yine de bir güvenlik sınırı


class _Reconnect(Exception):
    """Sunucu bağlantıyı yeniliyor (GoAway): aynı oturuma tanıtıcıyla yeniden bağlan."""
OUT_RATE = 24000                 # Gemini'nin ses çıkışı
IN_RATE = 16000                  # kulakların verdiği ses
CHUNK = IN_RATE * 2 // 10        # 0,1 sn (16 bit)
LEAD_MS = int(os.environ.get("TTS_LEAD_MS", "300"))
ECHO_TAIL_S = 0.35
CAMERA_ON = os.environ.get("LIVE_CAMERA", "1").strip() != "0"
CAMERA_FPS = float(os.environ.get("LIVE_CAMERA_FPS", "1"))
CAMERA_SIDE = 768
SCREEN_SIDE = 1536               # ekran görüntüsü: yazılar okunabilsin
SCREEN_EVERY_S = 3.0
SEARCH_ON = os.environ.get("LIVE_SEARCH", "1").strip() != "0"                # Gemini Live için önerilen görüntü boyu               # hoparlör sustuktan sonra mikrofonu bu kadar daha kapalı tut

Turn = Callable[[str, str], Awaitable[None]]

VOICE_RULES = (
    "ŞU AN SESLİ KONUŞUYORSUN: Söylediğin her şey hoparlörden sesli okunuyor. Kısa ve doğal konuş, "
    "genelde bir-üç cümle yeter; liste, madde işareti, emoji ve bağlantı kullanma. Türkçe konuş. "
    "Güncel bir bilgi gerekirse (haber, hava durumu, döviz, maç, bir yerin çalışma saati, yeni bir gelişme) "
    "Google araması yap; hafızandan tahmin etme, bulduğunu kısaca ve kaynağını belirterek söyle. "
    "Sahibin laptop ekranına bakmanı isterse ekrana_bak aracını kullan. "
    "Bu sesli sohbette diğer araçların (fotoğraf çekme, geçmişe bakma, kod geliştirme) yok; bunlar gerekiyorsa "
    "sahibine Telegram'dan yazmasını söyle. Sahibin konuşmayı bitirmek isterse kısaca vedalaş."
)
CAMERA_RULES = (
    "KAMERAN AÇIK: Sohbet boyunca kameranın görüntüsünü saniyede bir kare olarak görüyorsun (önündeki kişi ve "
    "masa). Sorulursa ya da gerçekten önemliyse gördüğünü söyle; gördüğün her şeyi sürekli anlatma. Emin "
    "olmadığın şeyi görmüş gibi söyleme."
)
# Not: ekran görüntüsü de kamera gibi görüntü akışından gelir; ekran akarken kamera karesi gönderilmez.


def small_jpeg(jpeg: bytes, side: int = 0) -> bytes:
    """Kareyi Gemini Live için küçültür (kamera 768, ekran yazıları okunsun diye daha büyük)."""
    side = side or CAMERA_SIDE
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(jpeg))
    im.thumbnail((side, side))
    out = io.BytesIO()
    im.convert("RGB").save(out, "JPEG", quality=70)
    return out.getvalue()


class Player:
    """Gemini'nin 24 kHz ham sesini aplay'e akıtır. Ne zaman susacağını tahmin eder (yankı için)."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.until = 0.0          # sesin hoparlörde biteceği tahmini zaman

    async def write(self, data: bytes) -> None:
        if self.proc is None or self.proc.returncode is not None:
            self.proc = await asyncio.create_subprocess_exec(
                "aplay", "-q", "-D", DEVICE, "-t", "raw", "-f", "S16_LE", "-r", str(OUT_RATE), "-c", "1",
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            data = bytes(OUT_RATE * 2 * LEAD_MS // 1000) + data   # USB hoparlör uyanırken ilk hece kesilmesin
        now = time.time()
        self.until = max(self.until, now) + len(data) / (OUT_RATE * 2)
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self.proc = None

    def playing(self) -> bool:
        return time.time() < self.until + ECHO_TAIL_S

    async def flush(self) -> None:
        """Araya girildi: kuyruktaki sesi at."""
        self.until = 0.0
        await self.close()

    async def close(self) -> None:
        p, self.proc = self.proc, None
        if p and p.returncode is None:
            try:
                p.stdin.close()
                await asyncio.wait_for(p.wait(), 0.5)
            except Exception:  # noqa: BLE001
                p.kill()


class Live:
    def __init__(self) -> None:
        self.enabled = os.environ.get("LIVE", "1").strip() != "0"
        self.active = False
        self.error = ""
        self.model = ""
        self.started_at = 0.0
        self.turns = 0
        self.on_turn: Optional[Turn] = None
        self.camera = None            # robot.py verir
        self.screen_grab = None       # robot.py verir: laptop ekranı (onaylı)
        self.camera_frames = 0
        self.search = False
        self._handle = None           # oturumu yeni bağlantıda sürdürmek için

    def available(self) -> bool:
        import brain
        return self.enabled and getattr(brain, "gemini", None) is not None

    def _config(self, search: bool = True) -> dict:
        import brain
        try:
            system = brain.system_prompt(brain.load_memory(), "ses")
        except Exception as e:  # noqa: BLE001
            log.warning("Sistem metni hazırlanamadı: %s", e)
            system = "Sen bir masaüstü robotusun."
        return {
            "response_modalities": ["AUDIO"],
            "system_instruction": system + "\n\n" + VOICE_RULES + ("\n\n" + CAMERA_RULES if self._camera_ok() else ""),
            # uzun sohbet: eski kısmı özetleyerek sürdür (görüntülü oturumlar aksi halde ~2 dk ile sınırlı)
            "context_window_compression": {"sliding_window": {}},
            "session_resumption": {"handle": self._handle},
            "tools": self._tools(search),
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": VOICE_NAME}}},
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            "realtime_input_config": {"automatic_activity_detection": {"silence_duration_ms": 700}},
        }

    def _tools(self, search: bool) -> list:
        tools = []
        if SEARCH_ON and search:
            tools.append({"google_search": {}})
        if self.screen_grab:
            tools.append({"function_declarations": [{
                "name": "ekrana_bak",
                "description": "Sahibinin laptop ekranını görmek için. Sahibin 'ekranıma bak', 'ekranda ne var', "
                               "'şu tabloyu oku' gibi bir şey dediğinde kullan. Laptopta onay penceresi çıkar; "
                               "izin verirse ekran görüntüsü görüntü akışına eklenir ve onu görürsün. Sahibin "
                               "'5 dk izin ver' derse ekran birkaç saniyede bir güncellenir.",
                "parameters": {"type": "object", "properties": {
                    "neden": {"type": "string", "description": "Ekranda neye bakacağın (onay penceresinde görünür)"}}},
            }]})
        return tools

    def _camera_ok(self) -> bool:
        return CAMERA_ON and self.camera is not None and getattr(self.camera, "enabled", False)

    async def run(self, preroll: bytes, ears, pending: list | None = None) -> bool:
        """Bir sohbet oturumu yürütür. Oturum açılamadıysa False (çağıran eski yönteme düşebilir)."""
        import brain
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        def put(item) -> None:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

        ears.begin_stream(lambda pcm, rms: loop.call_soon_threadsafe(put, (pcm, rms)))
        preroll += b"".join(pending or [])   # oturum açılırken söylenenler
        player = Player()
        self.active, self.started_at, self.error = True, time.time(), ""
        opened = False
        self._handle = None
        state = {"last_user": time.time(), "last_model": 0.0, "heard": "", "said": "", "busy": False,
                 "tool": 0, "screen_until": 0.0}
        try:
            last_err = None
            chosen = None
            for model in MODELS:                 # ilk bağlantı: çalışan model/arama birleşimini bul
                for search in ([True, False] if SEARCH_ON else [False]):
                    try:
                        session_cm = brain.gemini.aio.live.connect(model=model, config=self._config(search))
                        session = await session_cm.__aenter__()
                        chosen = (model, search, session_cm, session)
                        break
                    except Exception as e:  # noqa: BLE001
                        last_err = e
                        log.warning("Canlı model açılamadı (%s, arama %s): %s", model, search, e)
                if chosen:
                    break
            if not chosen:
                raise RuntimeError(f"Canlı sohbet açılamadı: {last_err}")
            model, search, session_cm, session = chosen
            opened, self.model, self.search = True, model, search
            log.info("Canlı sohbet açıldı (%s, arama %s)", model, "açık" if search else "kapalı")
            reconnects = 0
            first = True
            while True:
                try:
                    try:
                        await self._converse(session, queue, preroll if first else b"", player, state)
                        return True                  # sessizlikle bitti
                    finally:
                        first = False
                        try:
                            await session_cm.__aexit__(None, None, None)
                        except Exception:  # noqa: BLE001
                            pass
                except Exception as e:  # noqa: BLE001 — GoAway ya da kopma: aynı oturumu sürdür
                    if not self._handle or reconnects >= 5:
                        raise
                    reconnects += 1
                    log.info("Canlı sohbet bağlantısı yenileniyor (%d): %s", reconnects, type(e).__name__)
                    session_cm = brain.gemini.aio.live.connect(model=model, config=self._config(search))
                    session = await session_cm.__aenter__()
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:200]
            log.error("Canlı sohbet hatası: %s", e)
            return opened
        finally:
            await player.close()
            ears.end_stream()
            self.active = False
            log.info("Canlı sohbet kapandı (%d tur, %.0f sn)", self.turns, time.time() - self.started_at)

    async def _converse(self, session, queue: asyncio.Queue, preroll: bytes, player: Player, state: dict) -> None:
        from google.genai import types
        state["busy"] = False
        bg: set = set()

        async def send_screen(img: bytes) -> None:
            data = await asyncio.to_thread(small_jpeg, img, SCREEN_SIDE)
            await session.send_realtime_input(video=types.Blob(data=data, mime_type="image/jpeg"))

        async def screen_stream() -> None:
            """İzin süresi boyunca ekranı birkaç saniyede bir yeniler (laptop bu sürede sormadan verir)."""
            while time.time() < state["screen_until"]:
                await asyncio.sleep(SCREEN_EVERY_S)
                img, _err, left = await self.screen_grab("Sesli sohbette ekranı izliyorum", wait=10)
                if img is None:
                    break
                await send_screen(img)
                state["screen_until"] = time.time() + left
            state["screen_until"] = 0.0

        async def on_tool(fc) -> None:
            state["tool"] += 1
            try:
                if fc.name != "ekrana_bak":
                    result = f"Bilinmeyen araç: {fc.name}"
                else:
                    reason = (fc.args or {}).get("neden") or "Sesli sohbette ekranına bakmak istiyorum"
                    img, err, left = await self.screen_grab(reason, wait=40)
                    if img is None:
                        result = err
                    else:
                        await send_screen(img)
                        result = ("Ekran görüntüsü görüntü akışına eklendi, şimdi ekranı görüyorsun; sahibinin "
                                  "sorusunu ona göre cevapla. Hasta adı/TC gibi kişisel bilgileri yüksek sesle okuma.")
                        if left > 0:
                            state["screen_until"] = time.time() + left
                            result += f" Ekran {int(left // 60)} dakika boyunca birkaç saniyede bir güncellenecek."
                            t = asyncio.create_task(screen_stream())
                            bg.add(t)
                            t.add_done_callback(bg.discard)
                await session.send_tool_response(function_responses=[
                    types.FunctionResponse(id=fc.id, name=fc.name, response={"sonuc": result})])
            except Exception as e:  # noqa: BLE001
                log.warning("Ekran aracı hata verdi: %s", e)
            finally:
                state["tool"] -= 1
                state["last_model"] = time.time()

        async def send() -> None:
            # Uyandırma kelimesiyle aynı nefeste söylenen cümle kaybolmasın: önce kelimenin öncesi/sonrası
            for i in range(0, len(preroll), CHUNK):
                await session.send_realtime_input(
                    audio=types.Blob(data=preroll[i:i + CHUNK], mime_type=f"audio/pcm;rate={IN_RATE}"))
            while True:
                try:
                    pcm, rms = await asyncio.wait_for(queue.get(), 1.0)
                except asyncio.TimeoutError:
                    continue
                now = time.time()
                muted = player.playing() and not BARGE_IN
                if muted:
                    pcm = bytes(len(pcm))          # robot konuşurken sessizlik gönder (yankı olmasın)
                elif rms > SPEECH_RMS:
                    state["last_user"] = now
                await session.send_realtime_input(
                    audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={IN_RATE}"))

        async def receive() -> None:
            while True:
                async for resp in session.receive():
                    upd = getattr(resp, "session_resumption_update", None)
                    if upd and getattr(upd, "resumable", False) and getattr(upd, "new_handle", None):
                        self._handle = upd.new_handle
                    if getattr(resp, "go_away", None):
                        raise _Reconnect()
                    if getattr(resp, "tool_call", None) and resp.tool_call.function_calls:
                        for fc in resp.tool_call.function_calls:
                            t = asyncio.create_task(on_tool(fc))
                            bg.add(t)
                            t.add_done_callback(bg.discard)
                    if resp.data:
                        state["busy"] = True
                        state["last_model"] = time.time()
                        await player.write(resp.data)
                    sc = resp.server_content
                    if not sc:
                        continue
                    if sc.interrupted:
                        await player.flush()
                    if sc.input_transcription and sc.input_transcription.text:
                        state["heard"] += sc.input_transcription.text
                        state["last_user"] = time.time()
                    if sc.output_transcription and sc.output_transcription.text:
                        state["said"] += sc.output_transcription.text
                    if sc.turn_complete:
                        state["busy"] = False
                        state["last_model"] = time.time()
                        heard, said = state["heard"].strip(), state["said"].strip()
                        state["heard"] = state["said"] = ""
                        if heard or said:
                            self.turns += 1
                            if self.on_turn:
                                asyncio.create_task(self._safe_turn(heard, said))

        async def watch() -> None:
            """Kamerayı saniyede bir kare olarak gönderir. Kamera hata verirse sadece görüntü durur."""
            self.camera_frames = 0
            if not self._camera_ok():
                await asyncio.Event().wait()
            while True:
                try:
                    if self._camera_ok() and time.time() > state["screen_until"]:   # ekran akarken kamera bekler
                        jpg = await self.camera.frame()
                        if jpg:
                            await session.send_realtime_input(
                                video=types.Blob(data=await asyncio.to_thread(small_jpeg, jpg), mime_type="image/jpeg"))
                            self.camera_frames += 1
                except Exception as e:  # noqa: BLE001
                    log.warning("Kamera görüntüsü gönderilemedi, görüntüsüz devam: %s", e)
                    await asyncio.Event().wait()
                await asyncio.sleep(1.0 / max(CAMERA_FPS, 0.1))

        async def watchdog() -> None:
            while True:
                await asyncio.sleep(0.5)
                now = time.time()
                if now - self.started_at > MAX_SESSION_S:
                    log.info("Canlı sohbet süre sınırına geldi")
                    return
                quiet_since = max(state["last_user"], player.until, state["last_model"])
                if (not state["busy"] and not state["tool"] and not player.playing()
                        and now - quiet_since > IDLE_S and now > state["screen_until"]):
                    return

        tasks = [asyncio.create_task(t()) for t in (send, receive, watchdog, watch)]
        if state["screen_until"] > time.time():          # yeniden bağlandık: ekran izlemeye devam
            t = asyncio.create_task(screen_stream())
            bg.add(t)
            t.add_done_callback(bg.discard)
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if t.exception():
                    raise t.exception()
        finally:
            for t in tasks + list(bg):
                t.cancel()
            await asyncio.gather(*tasks, *bg, return_exceptions=True)

    async def _safe_turn(self, heard: str, said: str) -> None:
        try:
            await self.on_turn(heard, said)
        except Exception as e:  # noqa: BLE001
            log.warning("Sohbet turu kaydedilemedi: %s", e)

    def status(self) -> dict:
        return {"enabled": self.enabled, "active": self.active, "model": self.model or MODELS[0],
                "camera": self._camera_ok(), "camera_frames": self.camera_frames, "search": self.search,
                "voice": VOICE_NAME, "turns": self.turns, "error": self.error}


live = Live()
