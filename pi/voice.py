"""Robotun sesi: cevapları USB hoparlörden sesli okur.

Sıra TTS_ORDER ile belirlenir (varsayılan gemini,openai,espeak): önce Gemini Live'ın sesiyle
(ücretsiz katmanda çalışır, canlı sohbetle aynı ses), olmazsa OpenAI, o da olmazsa internetsiz
espeak-ng (robotik ses). Çalma `aplay` ile yapılır.

Ayarlar (.env):
  TTS=1                       0 yaparsan sessiz başlar (Telegram: /ses ac | /ses kapa)
  TTS_ORDER=gemini,openai,espeak
  LIVE_VOICE=Kore             Gemini ses tonu (canlı sohbetle aynı)
  TTS_MODEL=gpt-4o-mini-tts   OpenAI ses modeli
  TTS_VOICE=alloy             OpenAI ses tonu
  SPEAKER_DEVICE=default      ya da plughw:CARD=Device,DEV=0 gibi
  TTS_MAX_CHARS=600           daha uzun cevapların sadece başı okunur
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile

log = logging.getLogger("robot.voice")

MODEL = os.environ.get("TTS_MODEL", "gpt-4o-mini-tts").strip()
VOICE = os.environ.get("TTS_VOICE", "alloy").strip()
DEVICE = os.environ.get("SPEAKER_DEVICE", "default").strip()
MAX_CHARS = int(os.environ.get("TTS_MAX_CHARS", "600"))
LEAD_MS = int(os.environ.get("TTS_LEAD_MS", "300"))   # hoparlör uyansın diye baştaki sessizlik
ORDER = [e.strip() for e in os.environ.get("TTS_ORDER", "gemini,openai,espeak").split(",") if e.strip()]

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def speakable(text: str) -> str:
    """Sesli okunacak hale getirir: bağlantı, markdown işareti ve emoji atılır, çok uzunsa kısaltılır."""
    t = re.sub(r"https?://\S+", "", text or "")
    t = re.sub(r"```.*?```", " ", t, flags=re.S)          # kod blokları okunmaz
    t = _EMOJI.sub("", t)
    t = re.sub(r"[*_#`>|~\[\]]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > MAX_CHARS:
        cut = t[:MAX_CHARS]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        t = cut[: end + 1] if end > MAX_CHARS // 3 else cut
    return t


class Voice:
    def __init__(self) -> None:
        self.enabled = os.environ.get("TTS", "1").strip() != "0"
        self.error = ""
        self.engine = ""
        self.speaking = False
        self._lock = asyncio.Lock()

    async def _openai(self, text: str) -> bytes | None:
        import brain
        if not getattr(brain, "openai_client", None):
            return None
        kw = {"model": MODEL, "voice": VOICE, "input": text, "response_format": "wav"}
        if "gpt-4o" in MODEL:
            kw["instructions"] = "Türkçe, sıcak, doğal ve sakin konuş."
        resp = await asyncio.wait_for(brain.openai_client.audio.speech.create(**kw), 40)
        data = resp.content
        return data if isinstance(data, (bytes, bytearray)) and len(data) > 1000 else None

    async def _gemini(self, text: str) -> bool:
        """Gemini Live'a metni okutur ve sesi GELDİKÇE hoparlöre akıtır (beklemeden). Çaldıysa True."""
        import brain
        import live
        if not getattr(brain, "gemini", None):
            return False
        cfg = {"response_modalities": ["AUDIO"],
               "system_instruction": "Sen bir seslendiricisin. Sana verilen Türkçe metni hiçbir şey ekleme"
                                     "den, değiştirmeden, doğal ve sıcak bir sesle aynen oku.",
               "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": live.VOICE_NAME}}}}
        last = None
        for model in live.MODELS:
            player = None
            got = 0
            try:
                async def run():
                    nonlocal player, got
                    async with brain.gemini.aio.live.connect(model=model, config=cfg) as session:
                        await session.send_client_content(
                            turns={"role": "user", "parts": [{"text": "Aynen oku: " + text}]}, turn_complete=True)
                        async for resp in session.receive():
                            if resp.data:
                                if player is None:
                                    player = await asyncio.create_subprocess_exec(
                                        "aplay", "-q", "-D", DEVICE, "-t", "raw", "-f", "S16_LE", "-r", "24000",
                                        "-c", "1", stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                                    self.speaking = True
                                    # USB hoparlör uyanırken ilk heceler kesilmesin: önce kısa sessizlik
                                    player.stdin.write(bytes(24000 * 2 * LEAD_MS // 1000))
                                player.stdin.write(resp.data)
                                await player.stdin.drain()
                                got += len(resp.data)
                            if resp.server_content and resp.server_content.turn_complete:
                                break
                await asyncio.wait_for(run(), 60)
                if player:
                    player.stdin.close()
                    _, err = await asyncio.wait_for(player.communicate(), 120)
                    if player.returncode:
                        raise RuntimeError("aplay: " + err.decode(errors="ignore").strip()[:150])
                if got > 2000:
                    self.engine = f"gemini/{model}"
                    return True
            except Exception as e:  # noqa: BLE001
                if player and player.returncode is None:
                    player.kill()
                if got:            # ses başladıysa başka modelle baştan okuma
                    raise
                last = e
                log.warning("Gemini ses (%s) olmadı: %s", model, e)
        if last:
            raise last
        return False

    @staticmethod
    async def _run(*cmd: str, timeout: float) -> None:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"{cmd[0]} zaman aşımı")
        if proc.returncode:
            raise RuntimeError(f"{cmd[0]}: {err.decode(errors='ignore').strip()[:150]}")

    async def say(self, text: str) -> bool:
        """Metni sesli okur. Okuduysa True. Aynı anda tek konuşma (sıraya girer)."""
        if not self.enabled:
            return False
        t = speakable(text)
        if not t:
            return False
        async with self._lock:
            fd, path = tempfile.mkstemp(suffix=".wav", prefix="robot-ses-")
            os.close(fd)
            try:
                made, errors = False, []
                for eng in ORDER:
                    try:
                        if eng == "gemini":
                            if await self._gemini(t):     # kendisi çaldı
                                self.error = "; ".join(errors)
                                return True
                        elif eng == "openai":
                            data = await self._openai(t)
                            if data:
                                with open(path, "wb") as f:
                                    f.write(data)
                                self.engine, made = f"openai/{MODEL}", True
                        elif eng == "espeak" and shutil.which("espeak-ng"):
                            await self._run("espeak-ng", "-v", "tr", "-s", "155", "-w", path, t, timeout=30)
                            self.engine, made = "espeak-ng", True
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{eng}: {str(e)[:100]}")
                    if made:
                        break
                if not made:
                    self.error = "; ".join(errors) or "Ses üretecek bir şey yok (Gemini/OpenAI anahtarı ya da espeak-ng)"
                    return False
                self.error = "; ".join(errors)   # yedeğe düştüyse neden düştüğü görünsün
                self.speaking = True
                await self._run("aplay", "-q", "-D", DEVICE, path, timeout=180)
                return True
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:150]
                log.error("Konuşamadım: %s", e)
                return False
            finally:
                self.speaking = False
                try:
                    os.unlink(path)
                except OSError:
                    pass

    def set_enabled(self, on: bool) -> None:
        self.enabled = on

    def status(self) -> dict:
        return {"enabled": self.enabled, "device": DEVICE, "engine": self.engine,
                "speaking": self.speaking, "error": self.error}


voice = Voice()
