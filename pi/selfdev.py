"""Robotun kendi kodunu geliştirmesi (tam serbest mod, güvenlik kapılarıyla).

Akış:
  1. Robot (sohbette kendini_gelistir aracıyla ya da gece düşünürken) bir geliştirme hedefi belirler.
  2. Kodunun bir KOPYASINDA Claude ile çalışır: dosyaları okur, değiştirir, test eder.
  3. Kopyadaki testler geçerse değişiklik gerçek koda uygulanır, sır taraması yapılır, git'e kaydedilir.
  4. Robot kendini yeniden başlatır. 90 sn sağlıklı çalışırsa değişiklik onaylanır ve GitHub'a gönderilir.
     Açılamazsa selfdev_guard.py 3 denemeden sonra önceki sürüme döner.

Değiştirilemez güvenlik kuralları (bu dosya ve koruma dosyası robotun kendisi tarafından değiştirilemez):
  - .env, hafıza, sahip bilgisi, günlükler okunamaz/yazılamaz; sadece pi/ altındaki izinli dosyalar.
  - Değişiklikte API anahtarı/sır varsa uygulanmaz.
  - Test kapısını geçmeyen kod uygulanmaz.

Ayarlar (.env): SELFDEV=1 (0: kapalı), SELFDEV_MAX_PER_DAY=5, GITHUB_PUSH_TOKEN (isteğe bağlı; yoksa sadece Pi'de kalır)
"""

import asyncio
import base64
import datetime
import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Awaitable, Callable, Optional

import brain

log = logging.getLogger("robot.selfdev")

PI = pathlib.Path(__file__).resolve().parent
REPO = PI.parent
STATE = PI / ".selfdev"
PENDING = STATE / "pending.json"
ROLLED = STATE / "rolled_back.json"
HISTORY = STATE / "history.jsonl"

MAX_PER_DAY = int(os.environ.get("SELFDEV_MAX_PER_DAY", "5"))
CONFIRM_AFTER = 90          # yeni kod bu kadar saniye sağlıklı çalışırsa onaylanır
MAX_TURNS = 30              # geliştirme ajanının en fazla araç turu

# Robotun DEĞİŞTİREMEYECEĞİ dosyalar (güvenlik sistemi ve kurulum)
PROTECTED = {"selfdev.py", "selfdev_guard.py", "kurulum.sh", "kulak_kurulum.sh", "gelisim_kurulum.sh",
             "requirements.txt"}
# Robotun hiç OKUYAMAYACAĞI dosyalar (gizli/kişisel)
SECRET_NAMES = {".env", "owner.json", "memory.json", "memory.tmp", "persona.md", "kimlik.md"}
SECRET_DIRS = {"gunluk", ".selfdev", "venv", "models", "__pycache__"}
WRITABLE_EXT = {".py", ".md", ".txt", ".html", ".json"}

Tell = Callable[..., Awaitable[object]]
AUTHOR = "Robot (kendini geliştirdi)"


# ---------------------------------------------------------------- yardımcılar

def _git(*args: str, check: bool = False, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=check,
                          env={**os.environ, **(env or {})})


def _secret_values() -> list[str]:
    """Ortamdaki gizli değerler (anahtar, token): değişikliklerde bunlar geçerse engellenir."""
    vals = []
    for k, v in os.environ.items():
        if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")) and len(v) >= 8:
            vals.append(v)
    return vals


SECRET_PATTERNS = [r"sk-ant-[A-Za-z0-9_\-]{10,}", r"sk-[A-Za-z0-9_\-]{20,}", r"AIza[0-9A-Za-z_\-]{20,}",
                   r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b", r"gh[pousr]_[A-Za-z0-9]{20,}", r"github_pat_[A-Za-z0-9_]{20,}"]


def find_secrets(text: str) -> list[str]:
    hits = [p for p in SECRET_PATTERNS if re.search(p, text)]
    hits += ["ortamdaki bir anahtar" for v in _secret_values() if v in text]
    return hits


def _rel_ok(rel: str, write: bool) -> str | None:
    """İzin verilmeyen yolsa sebebini döner."""
    p = pathlib.PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        return "sadece pi/ içindeki göreli yollar"
    if any(part in SECRET_DIRS for part in p.parts) or p.name in SECRET_NAMES or p.name.startswith(".env"):
        return "bu dosya gizli/kişisel, erişilemez"
    if write and p.name in PROTECTED:
        return "bu dosya güvenlik sisteminin parçası, değiştirilemez"
    if write and p.suffix not in WRITABLE_EXT:
        return f"sadece {', '.join(sorted(WRITABLE_EXT))} dosyaları yazılabilir"
    return None


def tracked_files() -> list[str]:
    out = _git("ls-files", "pi").stdout.split()
    return [f[3:] for f in out if _rel_ok(f[3:], write=False) is None]


# ---------------------------------------------------------------- test kapısı

def run_checks(work: pathlib.Path) -> tuple[bool, str]:
    """Kopyadaki kodu test eder: derleme + robotun gerçekten yüklenebildiği + varsa testler/."""
    out = []
    py = sorted(work.rglob("*.py"))
    r = subprocess.run([sys.executable, "-m", "py_compile", *map(str, py)], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "Derleme hatası:\n" + (r.stderr or r.stdout)[-3000:]
    out.append(f"✓ {len(py)} dosya derlendi")
    clean_env = {k: v for k, v in os.environ.items() if not any(
        w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))}
    clean_env.update({"EARS": "0", "INITIATIVE": "0", "PRESENCE": "0", "SELFDEV": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    code = ("import robot, brain, body, ears, initiative, camera\n"
            "assert hasattr(robot, 'api'), 'robot.api yok'\n"
            "routes = {getattr(r, 'path', '') for r in robot.api.routes}\n"
            "need = {'/api/status', '/api/chat', '/api/events'}\n"
            "assert need <= routes, f'eksik uç noktalar: {need - routes}'\n"
            "print('yükleme tamam,', len(routes), 'uç nokta')")
    try:
        r = subprocess.run([sys.executable, "-c", code], cwd=work, capture_output=True, text=True,
                           timeout=90, env=clean_env)
    except subprocess.TimeoutExpired:
        return False, "Yükleme testi 90 sn'de bitmedi (sonsuz döngü ya da takılma)"
    if r.returncode != 0:
        return False, "Yükleme testi başarısız:\n" + (r.stderr or r.stdout)[-3000:]
    out.append("✓ " + r.stdout.strip().splitlines()[-1])
    tests = sorted((work / "testler").glob("test_*.py")) if (work / "testler").exists() else []
    for t in tests:
        try:
            r = subprocess.run([sys.executable, str(t)], cwd=work, capture_output=True, text=True,
                               timeout=120, env=clean_env)
        except subprocess.TimeoutExpired:
            return False, f"{t.name} zaman aşımına uğradı"
        if r.returncode != 0:
            return False, f"{t.name} başarısız:\n" + (r.stderr or r.stdout)[-2000:]
        out.append(f"✓ {t.name}")
    return True, "\n".join(out)


# ---------------------------------------------------------------- geliştirme ajanı

AGENT_TOOLS = [
    {"name": "dosyalari_listele", "description": "Kodundaki dosyaları listeler.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "dosya_oku", "description": "Bir dosyayı satır numaralarıyla okur.",
     "input_schema": {"type": "object", "properties": {"yol": {"type": "string"}}, "required": ["yol"]}},
    {"name": "degistir", "description": "Dosyada 'eski' metnini (tam olarak bir kez geçmeli) 'yeni' ile değiştirir.",
     "input_schema": {"type": "object", "properties": {
         "yol": {"type": "string"}, "eski": {"type": "string"}, "yeni": {"type": "string"}},
         "required": ["yol", "eski", "yeni"]}},
    {"name": "dosya_yaz", "description": "Dosyayı baştan yazar (yeni dosya ya da tamamen değiştirme).",
     "input_schema": {"type": "object", "properties": {
         "yol": {"type": "string"}, "icerik": {"type": "string"}}, "required": ["yol", "icerik"]}},
    {"name": "kontrol_et", "description": "Kopyadaki kodu test eder (derleme, yükleme, testler/ klasörü).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "bitir", "description": "Değişiklik hazır ve testler geçti. Kısa Türkçe özet ve commit mesajı ver.",
     "input_schema": {"type": "object", "properties": {
         "ozet": {"type": "string", "description": "Sahibine ne değiştirdiğini ve neden 1-3 cümleyle anlat"},
         "commit_mesaji": {"type": "string"}}, "required": ["ozet", "commit_mesaji"]}},
    {"name": "vazgec", "description": "Değişiklik yapmamaya karar verdin ya da yapamıyorsun.",
     "input_schema": {"type": "object", "properties": {"neden": {"type": "string"}}, "required": ["neden"]}},
]

AGENT_SYSTEM = """Sen bir Raspberry Pi 5 üzerinde çalışan masa robotusun ve şu an KENDİ KODUNU geliştiriyorsun.
Kodun Python (FastAPI + Telegram botu). Ana dosyalar: robot.py (sunucu, Telegram, olaylar), brain.py (düşünme,
hafıza, araçlar), body.py (beden algısı), ears.py (mikrofon), initiative.py (kendiliğinden davranma),
camera.py, face_draw.py (yüz çizimi), govde.md (parçaların).

Çalışma kuralları:
- Bir kopyada çalışıyorsun; değişikliğin ancak kontrol_et geçerse uygulanır. Bitirmeden önce mutlaka kontrol_et çağır.
- Küçük, odaklı ve dikkatli değişiklik yap. Önce ilgili dosyaları oku, sonra değiştir. Var olan özellikleri bozma.
- Var olan büyük dosyaları dosya_yaz ile baştan yazma; degistir ile parça parça değiştir (cevap uzunluğun sınırlı).
- Yeni bir özellik eklediysen mümkünse testler/test_<ad>.py içine küçük bir test yaz (standart kütüphane, assert;
  ağ, kamera, mikrofon gerektirmesin).
- Yeni Python paketi kuramazsın; sadece kurulu olanları kullan (fastapi, python-telegram-bot, anthropic,
  google-genai, openai, numpy, PIL, vosk ve standart kütüphane).
- API anahtarlarını ya da gizli bilgileri asla koda yazma; hepsi os.environ'dan okunur.
- Güvenlik sistemi dosyalarını (selfdev.py, selfdev_guard.py, kurulum betikleri) değiştiremezsin.
- Hedef anlamsız, riskli ya da gereksizse vazgec ile dürüstçe vazgeç.
- Türkçe yorum ve mesaj yaz."""


class SelfDev:
    def __init__(self) -> None:
        self.enabled = os.environ.get("SELFDEV", "1").strip() != "0"
        self.busy = False
        self.current = ""
        self.last = ""
        self._tell: Optional[Tell] = None
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ açılış

    def start(self, tell: Tell) -> list[asyncio.Task]:
        self._tell = tell
        STATE.mkdir(exist_ok=True)
        return [asyncio.create_task(self._after_boot())]

    async def _after_boot(self) -> None:
        await asyncio.sleep(5)
        if ROLLED.exists():
            try:
                r = json.loads(ROLLED.read_text())
            except (OSError, json.JSONDecodeError):
                r = {}
            ROLLED.unlink(missing_ok=True)
            msg = (f"🛠️ Kendimde yaptığım değişiklik ({r.get('summary', '?')}) beni açılamaz hale getirdi; "
                   f"{'önceki sürümüme döndüm' if r.get('ok') else 'geri dönmeye çalıştım ama olmadı, kontrol eder misin'}.")
            self._note({"kind": "geri_alindi", **r})
            await self._say(msg)
            brain.log_event("assistant", msg, "kendiliğinden")
        if PENDING.exists():
            await asyncio.sleep(CONFIRM_AFTER)
            await self._confirm()

    async def _confirm(self) -> None:
        try:
            p = json.loads(PENDING.read_text())
        except (OSError, json.JSONDecodeError):
            PENDING.unlink(missing_ok=True)
            return
        PENDING.unlink(missing_ok=True)
        pushed = await asyncio.to_thread(self._push)
        self._note({"kind": "onaylandi", **p, "pushed": pushed})
        await self._say(f"✅ Yeni kodumla sorunsuz açıldım: {p.get('summary', '')}"
                        + ("" if pushed is None else " (GitHub'a da gönderdim)" if pushed else
                           " (GitHub'a gönderemedim, sadece bende duruyor)"))

    def _push(self) -> bool | None:
        token = os.environ.get("GITHUB_PUSH_TOKEN", "").strip()
        if not token:
            return None
        hdr = "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        for attempt in range(2):
            r = _git("-c", f"http.extraHeader={hdr}", "push", "origin", "HEAD:main")
            if r.returncode == 0:
                return True
            # GitHub'da yeni değişiklik varsa üstüne al ve tekrar dene
            pull = _git("-c", f"http.extraHeader={hdr}", "-c", "user.name=Robot", "-c", "user.email=robot@pi",
                        "pull", "--rebase", "origin", "main")
            if pull.returncode != 0:
                _git("rebase", "--abort")
                log.warning("GitHub ile birleştirilemedi: %s", pull.stderr[-300:])
                return False
        return False

    # ------------------------------------------------------------ durum

    def _note(self, rec: dict) -> None:
        STATE.mkdir(exist_ok=True)
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), **rec}, ensure_ascii=False) + "\n")

    def history(self, n: int = 10) -> list[dict]:
        if not HISTORY.exists():
            return []
        out = []
        for line in HISTORY.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return out[-n:]

    def today_count(self) -> int:
        today = datetime.datetime.now(brain.TZ).strftime("%Y-%m-%d")
        return sum(1 for h in self.history(100) if h.get("kind") == "uygulandi" and
                   datetime.datetime.fromtimestamp(h["at"], brain.TZ).strftime("%Y-%m-%d") == today)

    def status(self) -> dict:
        return {"enabled": self.enabled, "busy": self.busy, "current": self.current, "last": self.last,
                "today": self.today_count(), "max_per_day": MAX_PER_DAY, "pending": PENDING.exists(),
                "push": bool(os.environ.get("GITHUB_PUSH_TOKEN"))}

    async def _say(self, text: str) -> None:
        if self._tell:
            await self._tell(text, kind="gelisim")

    # ------------------------------------------------------------ geliştirme

    def request(self, goal: str, reason: str = "") -> str:
        """Sohbetten çağrılır: geliştirmeyi arka planda başlatır, hemen döner."""
        if not self.enabled:
            return "Kendimi geliştirme özelliğim kapalı (/gelisim ac)."
        if self.busy:
            return f"Şu an zaten kendimi geliştiriyorum: {self.current}"
        if PENDING.exists():
            return "Son değişikliğim henüz onaylanmadı, biraz bekle."
        if MAX_PER_DAY > 0 and self.today_count() >= MAX_PER_DAY:
            return f"Bugün kendimde {MAX_PER_DAY} değişiklik yaptım, yarına bırakıyorum."
        if not brain.claude:
            return "Kod yazabilmem için Claude anahtarı gerekiyor."
        self._task = asyncio.create_task(self.improve(goal, reason))
        return "Kendimi geliştirmeye başladım; bitince sonucu haber vereceğim."

    async def improve(self, goal: str, reason: str = "") -> str:
        async with self._lock:
            self.busy, self.current = True, goal
            try:
                return await self._improve(goal, reason)
            except Exception as e:  # noqa: BLE001
                log.exception("Geliştirme hatası")
                msg = f"🛠️ Kendimi geliştirirken bir hata oldu, hiçbir şeyi değiştirmedim: {brain.explain_error(e)}"
                await self._say(msg)
                return msg
            finally:
                self.busy, self.current = False, ""

    async def _improve(self, goal: str, reason: str) -> str:
        await self._say(f"🛠️ Kendimi geliştirmeye başladım: {goal}")
        work_root = pathlib.Path(tempfile.mkdtemp(prefix="selfdev_"))
        work = work_root / "pi"
        try:
            files = tracked_files()
            for rel in files:
                dst = work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PI / rel, dst)
            result = await self._agent(goal, reason, work)
            if result["kind"] != "bitti":
                self.last = f"vazgeçti: {result.get('neden', '')}"
                self._note({"kind": "vazgecti", "goal": goal, "neden": result.get("neden", "")})
                msg = f"🛠️ Kendimi geliştirmekten vazgeçtim: {result.get('neden', '')}"
                await self._say(msg)
                return msg

            ok, report = await asyncio.to_thread(run_checks, work)     # son kontrol, ajandan bağımsız
            if not ok:
                self._note({"kind": "test_gecmedi", "goal": goal, "rapor": report[-500:]})
                msg = f"🛠️ Değişikliğim testi geçemedi, uygulamadım.\n{report[-600:]}"
                await self._say(msg)
                return msg

            changed = self._diff_files(work)
            if not changed:
                msg = "🛠️ Sonunda hiçbir dosya değişmedi."
                await self._say(msg)
                return msg
            for rel in changed:
                why = _rel_ok(rel, write=True)
                if why:
                    raise RuntimeError(f"{rel}: {why}")
                src = work / rel
                if src.exists():
                    sec = find_secrets(src.read_text(encoding="utf-8", errors="ignore"))
                    if sec:
                        self._note({"kind": "sir_engellendi", "goal": goal, "dosya": rel})
                        msg = f"🛑 Değişikliğimde gizli bilgiye benzeyen bir şey vardı ({rel}), uygulamadım."
                        await self._say(msg)
                        return msg

            prev = _git("rev-parse", "HEAD").stdout.strip()
            for rel in changed:
                src, dst = work / rel, PI / rel
                if src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                elif dst.exists():
                    dst.unlink()
            _git("add", "--", *[f"pi/{r}" for r in changed])
            msg_c = f"Robot: {result['commit_mesaji']}\n\nHedef: {goal}\n{('Neden: ' + reason) if reason else ''}".strip()
            c = _git("-c", f"user.name={AUTHOR}", "-c", "user.email=robot@pi", "commit", "-m", msg_c)
            if c.returncode != 0:
                _git("reset", "--hard", prev)
                raise RuntimeError("git commit olmadı: " + c.stderr[-200:])
            new = _git("rev-parse", "HEAD").stdout.strip()
            stat = _git("diff", "--stat", prev, new).stdout.strip().splitlines()
            PENDING.write_text(json.dumps({"prev": prev, "new": new, "summary": result["ozet"], "goal": goal,
                                           "tries": 0, "at": time.time()}, ensure_ascii=False))
            self._note({"kind": "uygulandi", "goal": goal, "summary": result["ozet"], "commit": new[:8],
                        "files": changed})
            self.last = result["ozet"]
            await self._say(f"🛠️ Kendimi geliştirdim: {result['ozet']}\n\n"
                            f"Değişen: {', '.join(changed)} ({stat[-1].strip() if stat else ''})\n"
                            f"Testler geçti. Şimdi yeni kodumla yeniden başlıyorum; açılamazsam kendiliğimden "
                            f"eski halime dönerim. Beğenmezsen: /geri_al")
            brain.log_event("assistant", f"[Kendi kodumu değiştirdim] {result['ozet']}", "kendiliğinden")
            asyncio.get_running_loop().call_later(3, lambda: os._exit(0))   # systemd yeniden başlatır
            return result["ozet"]
        finally:
            shutil.rmtree(work_root, ignore_errors=True)

    def _diff_files(self, work: pathlib.Path) -> list[str]:
        changed = []
        orig = set(tracked_files())
        new = {str(p.relative_to(work)) for p in work.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        for rel in sorted(orig | new):
            a, b = PI / rel, work / rel
            if not b.exists() or not a.exists() or a.read_bytes() != b.read_bytes():
                changed.append(rel)
        return changed

    async def _agent(self, goal: str, reason: str, work: pathlib.Path) -> dict:
        mem = brain.load_memory()
        identity = brain.IDENTITY_FILE.read_text(encoding="utf-8") if brain.IDENTITY_FILE.exists() else ""
        messages = [{"role": "user", "content":
                     f"Geliştirme hedefin: {goal}\n" + (f"Neden: {reason}\n" if reason else "") +
                     f"\nKendin hakkında notların:\n{identity[:1500]}\n\nSahibin hakkında bildiklerin:\n" +
                     "\n".join(f"- {f}" for f in mem['facts'][-20:]) +
                     "\n\nÖnce dosyalari_listele ile başla."}]
        checked_ok = False
        for _ in range(MAX_TURNS):
            resp = await asyncio.wait_for(brain.claude.messages.create(
                model=brain.CLAUDE_MODEL, max_tokens=16000, system=AGENT_SYSTEM,
                tools=AGENT_TOOLS, messages=messages), timeout=600)
            messages.append({"role": "assistant", "content": resp.content})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                messages.append({"role": "user", "content": "Bir araç kullanarak devam et (bitir ya da vazgec)."})
                continue
            if resp.stop_reason == "max_tokens":
                # Cevap yarıda kesildi: araç çağrıları eksik olabilir, hiçbirini çalıştırma ama hepsine cevap ver
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                     "content": "Cevabın uzunluk sınırında kesildi, bu araç çalıştırılmadı. Büyük dosyayı baştan "
                                "yazma; degistir ile küçük parçalar halinde değiştir."} for b in uses]})
                continue
            results = []
            for b in uses:
                args = b.input or {}
                if b.name == "bitir":
                    if not checked_ok:
                        out = "Önce kontrol_et çağır ve testlerin geçtiğini gör."
                    else:
                        return {"kind": "bitti", "ozet": args.get("ozet", ""),
                                "commit_mesaji": args.get("commit_mesaji", "kendini geliştirme")}
                elif b.name == "vazgec":
                    return {"kind": "vazgecti", "neden": args.get("neden", "")}
                elif b.name == "kontrol_et":
                    checked_ok, out = await asyncio.to_thread(run_checks, work)
                    out = ("GEÇTİ\n" if checked_ok else "GEÇMEDİ\n") + out
                else:
                    out = self._tool(b.name, args, work)
                    if b.name in ("degistir", "dosya_yaz"):
                        checked_ok = False
                log.info("Geliştirme aracı: %s %s", b.name, str(args.get("yol", ""))[:60])
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:20000]})
            messages.append({"role": "user", "content": results})
        return {"kind": "vazgecti", "neden": f"{MAX_TURNS} adımda bitiremedim"}

    @staticmethod
    def _tool(name: str, args: dict, work: pathlib.Path) -> str:
        if name == "dosyalari_listele":
            files = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file()
                           and "__pycache__" not in p.parts)
            return "\n".join(f"{f}  ({(work / f).stat().st_size} bayt)" for f in files)
        rel = str(args.get("yol", "")).strip().removeprefix("pi/")
        why = _rel_ok(rel, write=name != "dosya_oku")
        if why:
            return f"İzin yok: {why}"
        path = work / rel
        if name == "dosya_oku":
            if not path.exists():
                return "Böyle bir dosya yok."
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            return "\n".join(f"{i + 1:4d}  {l}" for i, l in enumerate(lines))[:60000]
        if name == "degistir":
            if not path.exists():
                return "Böyle bir dosya yok."
            text = path.read_text(encoding="utf-8")
            old = args.get("eski", "")
            n = text.count(old) if old else 0
            if n != 1:
                return f"'eski' metin dosyada {n} kez geçiyor; tam olarak 1 kez geçmeli (daha fazla bağlam ekle)."
            path.write_text(text.replace(old, args.get("yeni", ""), 1), encoding="utf-8")
            return "Değiştirildi."
        if name == "dosya_yaz":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.get("icerik", ""), encoding="utf-8")
            return "Yazıldı."
        return f"Bilinmeyen araç: {name}"

    # ------------------------------------------------------------ geri alma

    async def undo_last(self) -> str:
        """Robotun son kod değişikliğini geri alır (yeni bir 'geri alma' commit'i olarak)."""
        if self.busy:
            return "Şu an kendimi geliştiriyorum, bitince dene."
        done = {h.get("commit") for h in self.history(100) if h.get("kind") == "sahibim_geri_aldi"}
        target = None
        for line in _git("log", "--format=%H|%an|%s", "-n", "50").stdout.splitlines():
            sha, author, subject = line.split("|", 2)
            if author == AUTHOR and sha[:8] not in done:
                target = (sha, subject)
                break
        if not target:
            return "Geri alınacak bir kendi değişikliğim yok."
        sha, subject = target
        r = _git("-c", "user.name=Robot (geri alma)", "-c", "user.email=robot@pi", "revert", "--no-edit", sha)
        if r.returncode != 0:
            _git("revert", "--abort")
            return "Geri alamadım (sonraki değişikliklerle çakışıyor). Bilgisayardan elle bakmak gerekiyor."
        PENDING.unlink(missing_ok=True)
        pushed = await asyncio.to_thread(self._push)
        self._note({"kind": "sahibim_geri_aldi", "commit": sha[:8], "subject": subject})
        brain.log_event("assistant", f"[Sahibim kod değişikliğimi geri aldı] {subject}", "kendiliğinden")
        asyncio.get_running_loop().call_later(3, lambda: os._exit(0))
        return (f"↩️ Geri aldım: {subject}. Eski halimle yeniden başlıyorum."
                + (" GitHub'a da gönderdim." if pushed else ""))

    # ------------------------------------------------------------ gece düşünmesi

    async def reflect(self) -> None:
        """Günlükten sonra: kendinde geliştirmek istediğin bir şey var mı? Robot kendisi karar verir."""
        if not (self.enabled and brain.claude) or self.busy or PENDING.exists():
            return
        recent = [h for h in self.history(10)]
        past = "\n".join(f"- {h.get('kind')}: {h.get('goal', h.get('subject', ''))}" for h in recent) or "(yok)"
        prompt = ("Bugünün günlüğünü yazdın. Şimdi kendi kodunu ve yeteneklerini düşün: sahibine daha iyi yardım "
                  "etmek ya da daha iyi bir robot olmak için kodunda geliştirmek istediğin somut, küçük bir şey var mı? "
                  "Bugün yaşadığın bir sorun, eksik kaldığın bir an ya da sahibinin istediği bir şey olabilir.\n"
                  f"Daha önce kendinde yaptığın değişiklikler:\n{past}\n\n"
                  'Sadece şu JSON ile cevap ver: {"gelistir": true/false, "hedef": "...", "neden": "..."}. '
                  "Emin değilsen ya da gerek yoksa gelistir=false.")
        try:
            text = await brain.complete(brain.system_prompt(brain.load_memory(), "kendiliğinden"), prompt, 400)
            m = re.search(r"\{.*\}", text, re.S)
            d = json.loads(m.group(0)) if m else {}
        except Exception as e:  # noqa: BLE001
            log.warning("Gece düşünmesi olmadı: %s", e)
            return
        if d.get("gelistir") and d.get("hedef"):
            log.info("Robot kendini geliştirmeye karar verdi: %s", d["hedef"])
            if MAX_PER_DAY <= 0 or self.today_count() < MAX_PER_DAY:
                await self.improve(str(d["hedef"]), str(d.get("neden", "")))


selfdev = SelfDev()
