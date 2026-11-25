"""Robotun sesi: cevapları USB hoparlörden sesli okur.

Önce OpenAI'nin ses modeliyle (doğal ses) dener; anahtar yoksa ya da hata verirse internetsiz
espeak-ng ile (robotik ses) okur. Çalma `aplay` ile ~/.asoundrc'deki varsayılan cihaza yapılır.

Ayarlar (.env):
  TTS=1                       0 yaparsan sessiz başlar (Telegram: /ses ac | /ses kapa)
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
                data = None
                try:
                    data = await self._openai(t)
                except Exception as e:  # noqa: BLE001
                    self.error = f"OpenAI ses: {str(e)[:120]}"
                    log.warning("OpenAI ses üretemedi, espeak-ng'ye geçiyorum: %s", e)
                if data:
                    with open(path, "wb") as f:
                        f.write(data)
                    self.engine = f"openai/{MODEL}"
                elif shutil.which("espeak-ng"):
                    await self._run("espeak-ng", "-v", "tr", "-s", "155", "-w", path, t, timeout=30)
                    self.engine = "espeak-ng"
                else:
                    self.error = "Ses üretecek bir şey yok (OpenAI anahtarı yok, espeak-ng kurulu değil)"
                    return False
                self.speaking = True
                await self._run("aplay", "-q", "-D", DEVICE, path, timeout=180)
                if data:
                    self.error = ""
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
