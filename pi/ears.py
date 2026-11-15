"""Robotun kulakları.

Mikrofonu (INMP441, I2S) sürekli dinler. Uyandırma kelimesini internetsiz olarak (Vosk, Türkçe küçük model)
yakalar, ardından söylediğin cümleyi sessizlik gelene kadar kaydeder ve WAV olarak geri çağırma
fonksiyonuna verir. Yazıya çevirme ve cevap beyinde (brain.transcribe / brain.chat) yapılır.

Ayarlar (.env):
  EARS=1                     0 yaparsan kulaklar kapalı başlar
  WAKE_WORDS=robot           virgülle birden fazla kelime yazılabilir (Vosk sözlüğünde olmalı)
  MIC_DEVICE=auto            ya da plughw:2,0 gibi
  MIC_GAIN=4                 ses yükseltme
  MIC_CHANNEL=0              L/R pini GND'de ise 0 (sol)
  VOSK_MODEL=models/vosk-model-small-tr-0.3
"""

import asyncio
import collections
import io
import json
import logging
import os
import pathlib
import re
import subprocess
import threading
import time
import wave
from typing import Awaitable, Callable, Optional

log = logging.getLogger("robot.ears")

BASE = pathlib.Path(__file__).resolve().parent
RATE_IN = 48000            # mikrofonun çalıştığı hız
RATE = 16000               # Vosk ve yazıya çevirme için
DECIM = RATE_IN // RATE
CHUNK_S = 0.1
CHUNK_FRAMES = int(RATE_IN * CHUNK_S)

WAKE_WORDS = [w.strip().lower() for w in os.environ.get("WAKE_WORDS", "robot").split(",") if w.strip()]
MIC_DEVICE = os.environ.get("MIC_DEVICE", "auto").strip()
MIC_GAIN = float(os.environ.get("MIC_GAIN", "4"))
MIC_CHANNEL = int(os.environ.get("MIC_CHANNEL", "0"))
VOSK_MODEL = pathlib.Path(os.environ.get("VOSK_MODEL", "models/vosk-model-small-tr-0.3"))
if not VOSK_MODEL.is_absolute():
    VOSK_MODEL = BASE / VOSK_MODEL

PREROLL_S = 1.5            # uyandırma kelimesinden hemen sonra konuşursan baş kısmı kaybolmasın
WAIT_FOR_SPEECH_S = 5.0    # kelimeden sonra bu kadar konuşmazsan vazgeçer
END_SILENCE_S = 1.2        # konuşma bittikten sonra bu kadar sessizlik olunca kaydı bitirir
MAX_RECORD_S = 20.0
MIN_SPEECH_RMS = 400.0

Utterance = Callable[[bytes], Awaitable[None]]
Wake = Callable[[], Awaitable[None]]


def find_device() -> str:
    """'auto' ise arecord -l çıktısında I2S mikrofon kartını bulur."""
    if MIC_DEVICE != "auto":
        return MIC_DEVICE
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    for line in out.splitlines():
        m = re.match(r"card (\d+):", line)
        if m and any(k in line.lower() for k in ("adau7002", "voicehat", "i2s", "inmp")):
            return f"plughw:{m.group(1)},0"
    return "plughw:2,0"


def strip_wake(text: str) -> str:
    """Yazıya çevrilmiş metnin başındaki uyandırma kelimesini atar: 'Robot, saat kaç?' -> 'saat kaç?'"""
    words = "|".join(re.escape(w) for w in WAKE_WORDS)
    return re.sub(rf"^\W*(hey\s+|hay\s+)?({words})\b\W*", "", text.strip(), flags=re.IGNORECASE).strip()


class Ears:
    def __init__(self) -> None:
        self.enabled = os.environ.get("EARS", "1").strip() != "0"
        self.running = False
        self.error = ""
        self.device = ""
        self.last_text = ""
        self.last_at = 0.0
        self.missing_words: list[str] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_utterance: Optional[Utterance] = None
        self._on_wake: Optional[Wake] = None
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._busy = threading.Event()   # cevap hazırlanırken yeni kelime dinlenmez

    # ------------------------------------------------------------ dışarıdan

    def start(self, loop: asyncio.AbstractEventLoop, on_utterance: Utterance, on_wake: Optional[Wake] = None) -> None:
        self._loop, self._on_utterance, self._on_wake = loop, on_utterance, on_wake
        self._thread = threading.Thread(target=self._run, name="ears", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        if not on:
            self._kill()
        log.info("Kulaklar %s", "açıldı" if on else "kapatıldı")

    def status(self) -> dict:
        return {"enabled": self.enabled, "running": self.running, "error": self.error,
                "device": self.device, "wake_words": WAKE_WORDS, "missing_words": self.missing_words,
                "last_text": self.last_text, "last_at": self.last_at}

    def heard(self, text: str) -> None:
        self.last_text, self.last_at = text, time.time()

    # ------------------------------------------------------------ iç işler

    def _kill(self) -> None:
        p = self._proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()

    def _call(self, coro) -> None:
        if self._loop:
            fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
            fut.add_done_callback(lambda f: f.exception() and log.error("Kulak geri çağırma hatası: %s", f.exception()))

    def _run(self) -> None:
        try:
            import numpy as np  # noqa: F401
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError:
            self.error = "vosk kurulu değil (bash pi/kulak_kurulum.sh)"
            log.warning("Kulaklar kapalı: %s", self.error)
            return
        if not VOSK_MODEL.exists():
            self.error = f"Ses modeli yok: {VOSK_MODEL.name} (bash pi/kulak_kurulum.sh)"
            log.warning("Kulaklar kapalı: %s", self.error)
            return
        SetLogLevel(-1)
        model = Model(str(VOSK_MODEL))
        self.missing_words = [w for w in WAKE_WORDS if model.find_word(w) < 0]
        known = [w for w in WAKE_WORDS if w not in self.missing_words]
        if self.missing_words:
            log.warning("Bu kelimeler ses modelinin sözlüğünde yok, algılanamaz: %s", self.missing_words)
        if not known:
            self.error = f"Uyandırma kelimesi sözlükte yok: {', '.join(WAKE_WORDS)}"
            log.warning("Kulaklar kapalı: %s", self.error)
            return
        grammar = json.dumps(known + ["[unk]"], ensure_ascii=False)
        log.info("Kulaklar hazır. Uyandırma kelimesi: %s", ", ".join(known))

        while not self._stop.is_set():
            if not self.enabled:
                self.running = False
                time.sleep(0.5)
                continue
            self.device = find_device()
            try:
                self._listen(KaldiRecognizer(model, RATE, grammar), known)
            except Exception as e:  # noqa: BLE001
                self.error = str(e)[:150]
                log.error("Mikrofon hatası: %s", e)
            self.running = False
            self._kill()
            if not self._stop.is_set() and self.enabled:
                time.sleep(5)   # cihaz meşgulse ya da koptuysa biraz bekleyip yeniden dene

    def _listen(self, rec, known: list[str]) -> None:
        import numpy as np

        cmd = ["arecord", "-q", "-D", self.device, "-c", "2", "-r", str(RATE_IN), "-f", "S32_LE", "-t", "raw"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        nbytes = CHUNK_FRAMES * 2 * 4
        dc = 0.0
        noise = 200.0
        preroll: collections.deque = collections.deque(maxlen=int(PREROLL_S / CHUNK_S))
        recording: Optional[list] = None
        rec_start = last_loud = 0.0
        spoke = False
        skip = int(0.5 / CHUNK_S)   # açılıştaki "pıt" sesini atla
        self.running, self.error = True, ""
        log.info("Mikrofon dinleniyor (%s)", self.device)

        while self.enabled and not self._stop.is_set():
            buf = self._proc.stdout.read(nbytes)
            if len(buf) < nbytes:
                err = self._proc.stderr.read().decode(errors="ignore").strip() if self._proc.poll() is not None else ""
                raise RuntimeError(err or "Mikrofon akışı kesildi")
            if skip:
                skip -= 1
                continue

            x = np.frombuffer(buf, dtype="<i4").reshape(-1, 2)[:, MIC_CHANNEL].astype(np.float32) / 65536.0
            dc = 0.9 * dc + 0.1 * float(x.mean())           # sabit kaymayı (DC) temizle
            x = (x - dc).reshape(-1, DECIM).mean(axis=1) * MIC_GAIN   # 48 kHz -> 16 kHz
            pcm = np.clip(x, -32767, 32767).astype("<i2").tobytes()
            rms = float(np.sqrt(np.mean(x * x)))
            now = time.time()

            if self._busy.is_set():          # cevap hazırlanıyor
                continue

            if recording is None:
                if rms < noise * 2:                       # ortam gürültüsünü öğren
                    noise = 0.97 * noise + 0.03 * rms
                preroll.append(pcm)
                if rec.AcceptWaveform(pcm):
                    text = json.loads(rec.Result()).get("text", "")
                else:
                    text = json.loads(rec.PartialResult()).get("partial", "")
                if any(w in text.split() for w in known):
                    log.info("Uyandırma kelimesi duyuldu (%s)", text)
                    rec.Reset()
                    recording, rec_start, last_loud, spoke = list(preroll), now, now, False
                    if self._on_wake:
                        self._call(self._on_wake())
                continue

            recording.append(pcm)
            threshold = max(noise * 3, MIN_SPEECH_RMS)
            if rms > threshold:
                last_loud = now
                if now - rec_start > 0.4:   # kelimenin kendisinden sonra gelen konuşma
                    spoke = True
            elapsed = now - rec_start
            done = (spoke and now - last_loud > END_SILENCE_S) or elapsed > MAX_RECORD_S
            gave_up = not spoke and elapsed > WAIT_FOR_SPEECH_S
            if done or gave_up:
                audio = b"".join(recording)
                recording = None
                preroll.clear()
                rec.Reset()
                if gave_up and elapsed < MAX_RECORD_S:
                    # sadece kelimeyi söyleyip sustun: yine de kaydı gönder, belki tek cümleydi
                    log.info("Kelimeden sonra konuşma gelmedi")
                self._busy.set()
                self._call(self._deliver(self._wav(audio)))

    async def _deliver(self, wav: bytes) -> None:
        try:
            if self._on_utterance:
                await self._on_utterance(wav)
        finally:
            self._busy.clear()

    @staticmethod
    def _wav(pcm: bytes) -> bytes:
        out = io.BytesIO()
        with wave.open(out, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm)
        return out.getvalue()


ears = Ears()
