"""Robotun müzik çalarlığı: şarkıyı internette (YouTube) arar ve USB hoparlörden çalar.

Dosya indirmez, sadece sesi akıtır (yt-dlp ile bulur, mpv ile çalar). Robot konuşurken müzik
durur (USB hoparlör aynı anda tek sesi çalabildiği için kapatılır), konuşma bitince kaldığı yerden devam eder.

Gerekenler: sudo apt install mpv  ·  venv içinde: pip install -U yt-dlp
Ayarlar (.env): MUSIC_VOLUME=60  ·  SPEAKER_DEVICE=default
"""

import asyncio
import contextlib
import json
import logging
import os
import pathlib
import shlex
import shutil
import signal
import sys
import time

log = logging.getLogger("robot.music")

SOCK = "/tmp/robot-mpv.sock"
VOLUME = int(os.environ.get("MUSIC_VOLUME", "60"))
DEVICE = os.environ.get("SPEAKER_DEVICE", "default").strip()


def _ytdlp() -> str | None:
    venv = pathlib.Path(sys.executable).with_name("yt-dlp")
    return str(venv) if venv.exists() else shutil.which("yt-dlp")


class Music:
    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.title = ""
        self.started = 0.0
        self.error = ""
        self._ducks = 0
        self.url = ""
        self._resume_at: float | None = None

    def playing(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _find(self, query: str) -> tuple[str, str]:
        """(başlık, doğrudan ses adresi). İlk 5 sonuçtan şarkı uzunluğundakini (1-12 dk) seçer. İndirmez."""
        exe = _ytdlp()
        if not exe:
            raise RuntimeError("yt-dlp kurulu değil (venv içinde: pip install -U yt-dlp)")
        p = await asyncio.create_subprocess_exec(
            exe, "--no-playlist", "--no-warnings", "-f", "bestaudio[ext=m4a]/bestaudio/best", "-J",
            f"ytsearch5:{query}", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(p.communicate(), 60)
        if p.returncode and not out:
            raise RuntimeError(err.decode(errors="ignore").strip()[-300:] or "arama başarısız")
        data = json.loads(out or b"{}")
        entries = [e for e in (data.get("entries") or [data]) if e and e.get("url")]
        if not entries:
            raise RuntimeError("Şarkı bulunamadı" + (": " + err.decode(errors="ignore").strip()[-200:] if err else ""))
        songs = [e for e in entries if 60 <= (e.get("duration") or 0) <= 720 and not e.get("is_live")]
        best = (songs or entries)[0]
        return best.get("title") or query, best["url"]

    async def _start(self, url: str, start: float = 0.0) -> bool:
        """mpv sesi çözer, ham PCM olarak aplay'e verir. aplay robotun konuşmasıyla aynı yoldan çaldığı için
        (çalıştığı biliniyor) mpv'nin kendi ses sürücüsüne gerek kalmaz."""
        with contextlib.suppress(FileNotFoundError):
            os.unlink(SOCK)
        with contextlib.suppress(OSError):
            open("/tmp/robot-mpv.log", "w").close()
        mpv = ["mpv", "--no-video", "--no-ytdl", "--no-terminal", "--msg-level=all=error",
               "--log-file=/tmp/robot-mpv.log", f"--volume={VOLUME}", f"--input-ipc-server={SOCK}",
               "--ao=pcm", "--ao-pcm-file=/dev/stdout", "--ao-pcm-waveheader=no",
               "--audio-format=s16", "--audio-samplerate=48000", "--audio-channels=stereo"]
        if start > 1:
            mpv.append(f"--start={start:.1f}")
        mpv.append(url)
        cmd = (" ".join(shlex.quote(a) for a in mpv) + " | aplay -q -D " + shlex.quote(DEVICE)
               + " -t raw -f S16_LE -r 48000 -c 2")
        self.proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE, start_new_session=True)
        await asyncio.sleep(4)
        if not self.playing():
            err = (await self.proc.stderr.read()).decode(errors="ignore").strip()
            with contextlib.suppress(OSError):
                lines = [l for l in open("/tmp/robot-mpv.log", errors="ignore")
                         if "error" in l.lower() or "fail" in l.lower()]
                err = (err + " | " + " | ".join(l.strip() for l in lines[-3:])).strip(" |")
            self.error = (err or "çalma hemen bitti")[-250:]
            return False
        return True

    def _kill(self) -> None:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)

    async def play(self, query: str) -> str:
        """Şarkıyı bulup çalar. Kullanıcıya gösterilecek kısa sonuç döner."""
        query = (query or "").strip()
        if not query:
            return "Hangi şarkıyı çalayım?"
        if not shutil.which("mpv"):
            return "Müzik çalamıyorum: mpv kurulu değil (sudo apt install mpv)."
        try:
            title, url = await self._find(query)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:200]
            return f"Şarkıyı bulamadım: {self.error}"
        await self.stop()
        self.title, self.url, self.started, self.error = title, url, time.time(), ""
        if self._ducks:                      # robot şu an konuşuyor: konuşma bitince başlar
            self._resume_at = 0.0
            return f"Çalacağım: {title}"
        if not await self._start(url):
            return f"Çalmaya başlayamadım: {self.error}"
        log.info("Çalıyor: %s", title)
        return f"Çalıyorum: {title}"

    async def stop(self) -> str:
        self._resume_at = None
        if not self.playing():
            self.proc = None
            return "Zaten bir şey çalmıyordu."
        self._kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.proc.wait(), 3)
        t, self.proc, self.title = self.title, None, ""
        return f"Durdurdum: {t}"

    async def _get(self, prop: str):
        with contextlib.suppress(Exception):
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(SOCK), 1)
            w.write((json.dumps({"command": ["get_property", prop], "request_id": 7}) + "\n").encode())
            await w.drain()
            for _ in range(20):
                line = await asyncio.wait_for(r.readline(), 1)
                msg = json.loads(line or b"{}")
                if msg.get("request_id") == 7:
                    w.close()
                    return msg.get("data")
        return None

    async def _cmd(self, *cmd) -> None:
        with contextlib.suppress(Exception):
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(SOCK), 1)
            w.write((json.dumps({"command": list(cmd)}) + "\n").encode())
            await w.drain()
            w.close()

    async def set_volume(self, v: int) -> str:
        global VOLUME
        VOLUME = max(0, min(100, int(v)))
        if self.playing():
            await self._cmd("set_property", "volume", VOLUME)
        return f"Ses seviyesi %{VOLUME}"

    @contextlib.asynccontextmanager
    async def ducked(self):
        """Robot konuşurken müziği kapatır (hoparlör serbest kalsın), bitince kaldığı yerden açar."""
        self._ducks += 1
        if self._ducks == 1 and self.playing():
            pos = await self._get("time-pos")
            title = self.title
            self._kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 2)
            self.proc, self.title = None, title
            self._resume_at = float(pos or 0)
            await asyncio.sleep(0.2)
        try:
            yield
        finally:
            self._ducks -= 1
            if self._ducks == 0 and self._resume_at is not None and self.url:
                at, self._resume_at = self._resume_at, None
                asyncio.create_task(self._start(self.url, at))

    def status(self) -> dict:
        busy = self.playing() or self._resume_at is not None
        return {"playing": busy, "title": self.title if busy else "",
                "volume": VOLUME, "error": self.error}


music = Music()
