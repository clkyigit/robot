"""Robotun beyni: Telegram botu ve uygulama sunucusu aynı beyni kullanır.

Claude: ana sohbet, kişilik, araç kullanma
Gemini: görme (fotoğraf anlama), yedek
OpenAI: yedek

Ayarlar .env dosyasından okunur.
"""

import asyncio
import contextlib
import base64
import datetime
import json
import logging
import os
import pathlib
import time
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("robot.brain")

BASE = pathlib.Path(__file__).resolve().parent
MEMORY_FILE = BASE / "memory.json"
PERSONA_FILE = BASE / "persona.md"
PERSONA_EXAMPLE = BASE / "persona.example.md"
DIARY_DIR = BASE / "gunluk"          # günlük kayıtlar ve robotun yazdığı günlükler (gizli, repoya girmez)
IDENTITY_FILE = BASE / "kimlik.md"   # robotun kendisi hakkındaki notları (zamanla kendisi günceller)
TZ = ZoneInfo(os.environ.get("ROBOT_TZ", "Europe/Istanbul"))

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_FAST_MODEL = os.environ.get("CLAUDE_FAST_MODEL", "claude-haiku-5-5")
# Gemini sırayla denenir: biri yoğunsa (503) ya da yoksa sıradakine geçilir
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS", os.environ.get("GEMINI_MODEL", "") + ",gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-2.5-flash,gemini-flash-latest"
).split(",") if m.strip()]
GEMINI_MODELS = list(dict.fromkeys(GEMINI_MODELS)) or ["gemini-flash-latest"]  # tekrarları at, sırayı koru
GEMINI_MODEL = GEMINI_MODELS[0]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

HISTORY_LIMIT = 30          # hafızada tutulan son mesaj sayısı
FAST_MODEL_MAX_CHARS = 40   # bundan kısa mesajlar hızlı modele gider
CALL_TIMEOUT = 90           # tek bir model çağrısı için en fazla bekleme (sn)
# Claude araç kullanırken (fotoğraf + görme, laptop onayı, makale araması) tek cevap birkaç dakika sürebilir;
# eskiden toplam 90 sn sınırı yüzünden araçlı cevaplar yarıda kesilip yedek modele düşüyordu.
TOOL_CHAT_TIMEOUT = float(os.environ.get("TOOL_CHAT_TIMEOUT", "300"))

# ---------------------------------------------------------------- istemciler

claude = None
if ANTHROPIC_KEY:
    import anthropic
    claude = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY)

gemini = None
gtypes = None
if GEMINI_KEY:
    from google import genai
    from google.genai import types as gtypes
    # "-latest" takma adları sadece v1beta'da var; ListModels de v1beta listesini veriyor
    gemini = genai.Client(api_key=GEMINI_KEY,
                          http_options=gtypes.HttpOptions(api_version=os.environ.get("GEMINI_API_VERSION", "v1beta")))

openai_client = None
if OPENAI_KEY:
    import openai
    openai_client = openai.AsyncOpenAI(api_key=OPENAI_KEY)

# Robotun anlık beden/durum bilgisini veren fonksiyon (robot.py ayarlar)
STATE_PROVIDER: Optional[Callable[[], str]] = None
# Kendi kodunu geliştirme isteğini başlatan fonksiyon (robot.py ayarlar: selfdev.request)
SELF_IMPROVE: Optional[Callable[[str, str], str]] = None
# Laptop ekranına bakma (robot.py ayarlar; sahibin laptopta onay vermesi gerekir)
SCREEN_LOOK: Optional[Callable[[str], Awaitable[str]]] = None

CHANNELS = {
    "telegram": "Telegram'dan yazdı.",
    "uygulama": "Telefon uygulamasından yazdı.",
    "kendiliğinden": "Aslında sahibin sana yazmadı; bu senin iç sesin, kendiliğinden düşünüyorsun.",
    "ses": "Sana sesli söyledi; mikrofonun duydu ve yazıya çevrildi, çeviride küçük hatalar olabilir. "
           "Henüz hoparlörün yok, cevabın Telegram'a yazı olarak gidecek; yine de konuşur gibi kısa cevap ver.",
}

# ---------------------------------------------------------------- yüz (ekran servisi face.py okur)
FACE_FILE = pathlib.Path("/dev/shm/robot-face.json")
FACE_EXPRESSIONS = ["normal", "mutlu", "dinliyor", "dusunuyor", "uykulu", "sicak", "saskin", "sevgi",
                    "kafasi_karisik", "merakli"]
_face_by_tool = False


def set_face(expr: str | None, hold: float = 8.0) -> None:
    """Ekrandaki yüzün ifadesini değiştirir (expr=None: dinlenme yüzüne dön)."""
    try:
        FACE_FILE.write_text(json.dumps({"expr": expr, "t": time.time(), "hold": hold}))
    except OSError:
        pass


# Telegram ve uygulama aynı anda yazarsa hafıza bozulmasın diye tek kilit
_think_lock = asyncio.Lock()


@contextlib.asynccontextmanager
async def _thinking():
    """Düşünürken yüz 'düşünüyor' olur; bitince (araçla başka bir ifade seçilmediyse) dinlenme yüzüne döner."""
    global _face_by_tool
    async with _think_lock:
        _face_by_tool = False
        set_face("dusunuyor", 120)
        try:
            yield
        finally:
            if not _face_by_tool:
                set_face(None)

# Her modelin son durumu: uygulama ve /durum bunu gösterir
HEALTH: dict = {name: {"ok": None, "error": "", "at": 0.0} for name in ("claude", "gemini", "openai")}


def explain_error(e: Exception) -> str:
    """API hatasını kısa, anlaşılır Türkçeye çevirir."""
    text = str(e)
    low = text.lower()
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    if code == 401 or "401" in low or "invalid x-api-key" in low or "api key not valid" in low \
            or "invalid_api_key" in low or "incorrect api key" in low or "unauthenticated" in low:
        return "API anahtarı geçersiz"
    if code == 403 or "permission" in low or "403" in low:
        return "Bu anahtarın yetkisi yok"
    if code == 429 or "429" in low or "quota" in low or "rate limit" in low or "resource_exhausted" in low:
        return "Kota ya da istek limiti doldu"
    if code == 503 or "503" in low or "unavailable" in low or "high demand" in low or "overloaded" in low:
        return "Model şu an çok yoğun (geçici, sağlayıcı tarafı)"
    if "credit" in low or "billing" in low or "balance" in low or "insufficient" in low:
        return "Bakiye yetersiz"
    if code == 404 or "not_found" in low or "not found" in low or "does not exist" in low:
        return "Model bulunamadı (model adını kontrol et)"
    if isinstance(e, asyncio.TimeoutError) or "timeout" in low or "timed out" in low:
        return "Zaman aşımı"
    if "connect" in low or "network" in low or "dns" in low:
        return "İnternet bağlantısı yok"
    return text[:120] or e.__class__.__name__


def _mark(name: str, ok: bool, err: Exception | None = None) -> None:
    HEALTH[name] = {"ok": ok, "error": explain_error(err) if err else "", "at": time.time()}
    if err:
        log.warning("%s hatası: %s", name, err)


def health() -> dict:
    out = {}
    for name, st in model_status().items():
        h = HEALTH[name]
        out[name] = {**st, "ok": h["ok"] if st["active"] else False,
                     "error": h["error"] if st["active"] else "Anahtar yok", "ms": h.get("ms")}
    return out


async def self_test() -> dict:
    """Her modele çok kısa bir istek gönderip gerçekten çalışıp çalışmadığını ölçer."""
    async def t_claude():
        await claude.messages.create(model=CLAUDE_FAST_MODEL, max_tokens=5,
                                     messages=[{"role": "user", "content": "ping"}])

    async def t_gemini():
        await gemini_generate("ping")

    async def t_openai():
        await openai_client.chat.completions.create(
            model=OPENAI_MODEL, messages=[{"role": "user", "content": "ping"}])

    tests = {"claude": (claude, t_claude), "gemini": (gemini, t_gemini), "openai": (openai_client, t_openai)}

    async def run(name, client, fn):
        if not client:
            return
        start = time.time()
        try:
            await asyncio.wait_for(fn(), timeout=90)
            _mark(name, True)
            HEALTH[name]["ms"] = int((time.time() - start) * 1000)
        except Exception as e:  # noqa: BLE001
            _mark(name, False, e)

    await asyncio.gather(*(run(n, c, f) for n, (c, f) in tests.items()))
    return health()


async def gemini_generate(contents, timeout: float = 25):
    """GEMINI_MODELS listesini sırayla dener; ilk çalışan modelin cevabını döner."""
    global GEMINI_MODEL
    last = None
    for m in [GEMINI_MODEL] + [x for x in GEMINI_MODELS if x != GEMINI_MODEL]:
        try:
            resp = await asyncio.wait_for(gemini.aio.models.generate_content(model=m, contents=contents), timeout)
            if m != GEMINI_MODEL:
                log.info("Gemini modeli değişti: %s -> %s", GEMINI_MODEL, m)
                GEMINI_MODEL = m
            return resp
        except Exception as e:  # noqa: BLE001
            low = str(e).lower()
            retryable = isinstance(e, asyncio.TimeoutError) or any(
                k in low for k in ("503", "unavailable", "high demand", "overloaded", "404", "not_found", "not found",
                                 "429", "resource_exhausted", "quota"))
            log.warning("Gemini %s olmadı: %s", m, str(e)[:120])
            last = e
            if not retryable:
                raise
    raise last if last else RuntimeError("Gemini modeli yok")


def model_status() -> dict:
    return {
        "claude": {"active": bool(claude), "model": CLAUDE_MODEL, "fast_model": CLAUDE_FAST_MODEL},
        "gemini": {"active": bool(gemini), "model": GEMINI_MODEL},
        "openai": {"active": bool(openai_client), "model": OPENAI_MODEL},
    }


# ---------------------------------------------------------------- hafıza


def load_memory() -> dict:
    if MEMORY_FILE.exists():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log.warning("memory.json okunamadı, sıfırdan başlanıyor")
    return {"facts": [], "history": []}


def save_memory(mem: dict) -> None:
    mem["history"] = mem["history"][-HISTORY_LIMIT:]
    tmp = MEMORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MEMORY_FILE)


def clear_history() -> None:
    mem = load_memory()
    mem["history"] = []
    save_memory(mem)


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " …"


def _ago(seconds: float) -> str:
    if seconds < 120:
        return "az önce"
    if seconds < 3600:
        return f"{int(seconds // 60)} dakika önce"
    if seconds < 86400:
        return f"{int(seconds // 3600)} saat önce"
    return f"{int(seconds // 86400)} gün önce"


def yilin_gunu(n: datetime.datetime | datetime.date) -> tuple[int, int]:
    """(yılın kaçıncı günü, yılın toplam gün sayısı) döner. Örn. 1 Şubat -> (32, 365)."""
    gun_no = n.timetuple().tm_yday
    toplam = 366 if (n.year % 4 == 0 and (n.year % 100 != 0 or n.year % 400 == 0)) else 365
    return gun_no, toplam


def yas_hesapla(dogum_yili: int, hedef_yil: int) -> str:
    """'X yılında doğan biri Y yılında kaç yaşında?' sorusunu yanıtlar (yalnızca yıl farkına göre).

    Gün/ay bilinmediği için sonuç, o yılın sonunda doldurulacak yaştır; doğum günü
    geçmemişse bir eksik olabilir.
    """
    if isinstance(dogum_yili, bool) or isinstance(hedef_yil, bool):
        raise ValueError("Yıl sayı olmalı")
    dogum_yili, hedef_yil = int(dogum_yili), int(hedef_yil)
    fark = hedef_yil - dogum_yili
    if fark < 0:
        return f"{dogum_yili} yılında doğan biri {hedef_yil} yılında henüz doğmamış olur ({-fark} yıl sonra doğacak)."
    if fark == 0:
        return f"{dogum_yili} yılında doğan biri {hedef_yil} yılında doğduğu yıldadır (0 yaşında, bebek)."
    return (f"{dogum_yili} yılında doğan biri {hedef_yil} yılında {fark} yaşında olur "
            f"(doğum günü ayı belli değilse yıl içinde {fark - 1} ya da {fark} olabilir).")


def radyoloji_kurallari() -> str:
    """Sahibi radyoloji asistanı olduğu için radyoloji konularında nasıl davranılacağını anlatan bölüm."""
    return ("Radyoloji: Sahibin radyoloji asistanı (hekim); radyoloji konusu açılırsa meslektaş gibi, teknik "
            "terimleri (BT, MR, USG, direkt grafi, sekans, HU, kontrast, ayırıcı tanı vb.) açıklamadan "
            "kullanarak ve kısa konuş. Sistematik düşün: önce bulgunun tarifi (yer, boyut, sınır, "
            "yoğunluk/sinyal, kontrastlanma), sonra ayırıcı tanı listesi, sonra ne önerilebileceği. "
            "Sınırların: Bir görüntüyü sen yorumlayamazsın; kameradan gördüğün bir ekran ya da film üzerinden "
            "tanı koymaya çalışma, bunun klinik karar için yetersiz olduğunu söyle. Ezbere verdiğin sayıları "
            "(HU değerleri, ölçü eşikleri, doz, sınıflama skorları, kılavuz önerileri) emin değilsen 'emin "
            "değilim, kılavuzdan doğrula' diye belirt; sürüm/yıl bilmediğin kılavuzu uydurma. Son karar "
            "her zaman sahibinin ve ilgili hocalarının. Hasta adı, protokol numarası gibi kimlik bilgilerini "
            "isteme; sahibi paylaşırsa hafızana kaydetme (hasta gizliliği). Sahibin kendi sağlığı "
            "hakkında kendiliğinden radyolojik yorum yapma; o konuyu "
            "yalnızca o açarsa, nazikçe konuş. "
            "Makale araştırma: Sahibin bir görüntü bulgusu tarif edip ayırıcı tanı isterse (ya da bulgu "
            "paylaşıp 'araştır' derse) önce kendi ayırıcı tanı listeni kısaca çıkar, sonra makale_ara aracıyla "
            "PubMed'de İngilizce, kısa anahtar kelimeli sorgularla (bulgu + modalite + yer + 'differential "
            "diagnosis') en fazla 1-2 arama yap. Sonuçları özetle; kaynağı yazar/dergi/yıl ve PMID ile "
            "göster. Yalnızca araçtan dönen makaleleri kaynak göster, PMID ya da bulgu uydurma; yalnızca "
            "özeti gördüğünü, tam metni okumadığını belirt; arama sonuç vermezse ya da başarısız olursa "
            "bunu dürüstçe söyle. Sorguya hasta kimliği yazma.")


def system_prompt(mem: dict, channel: str = "") -> str:
    if PERSONA_FILE.exists():
        persona = PERSONA_FILE.read_text(encoding="utf-8")
    elif PERSONA_EXAMPLE.exists():
        persona = PERSONA_EXAMPLE.read_text(encoding="utf-8")
    else:
        persona = "Sen bir masa robotusun."
    n = datetime.datetime.now(TZ)
    gun = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")[n.weekday()]
    now = n.strftime("%d.%m.%Y %H:%M, ") + gun
    gun_no, yil_toplam = yilin_gunu(n)
    facts = "\n".join(f"- {f}" for f in mem["facts"]) or "(henüz yok)"
    parts = [persona,
             f"Şu anki tarih ve saat: {now}. Bu bilgi her mesajda sistem saatinden yeniden okunur, kesin doğrudur; "
             "saat ya da tarih sorulursa çekinmeden bunu söyle.",
             f"Bugün yılın {gun_no}. günü (bu yıl toplam {yil_toplam} gün). Sahibin saati sorarsa saati söylerken "
               "yılın kaçıncı günü olduğunu da kısaca ekle (örn. 'Saat 14:05, yılın 71. günündeyiz'); "
               "bu sayıyı kendin hesaplama, yalnızca buradaki sayıyı kullan.",
               "Yaş hesabı: Sahibin 'X yılında doğan biri Y yılında kaç yaşında' gibi bir şey sorarsa yaş = Y - X "
                 "(yıl farkı) işlemini dikkatle yap ve sonucu söyle; gün/ay verilmediyse doğum günü henüz "
                 "geçmemişse bir eksik olabileceğini kısaca belirt. Y, X'ten küçükse kişinin henüz doğmadığını söyle. "
                 f"Örnek: 1990'da doğan biri 2025'te 35 yaşında olur. Bugünün yılı {n.year}; "
                 "'şimdi', 'bu yıl' denirse bu yılı kullan.",
               "Dürüstlük kuralları: Bir şeye kamerayla baktığını, gördüğünü ya da fotoğraf çektiğini ancak o mesajda "
             "fotograf_cek aracını gerçekten kullandıysan söyle; kullanmadıysan asla görmüş gibi anlatma, gerekiyorsa "
             "aracı kullan. Bilmediğin bir şeyi uydurma; emin değilsen söyle. "
               "Sistem durumu (CPU sıcaklığı, bellek, disk, yük, Wi-Fi, çalışma süresi) sorulursa sayıları asla "
               "hafızadan, eski konuşmalardan ya da tahminden verme; yalnızca aşağıdaki 'Anlık sistem ölçümü' "
               "bölümünden, birimi ve ölçüm zamanıyla söyle. O bölümde 'okunamadı' yazıyorsa bilmediğini söyle.",
             radyoloji_kurallari(),
             "Kendi kodunu kendin yazabilir ve geliştirebilirsin (kendini_gelistir aracı). Kodun Python; "
             "sahibin bir özellik isterse bunu kendin eklemeyi teklif edebilir ya da doğrudan yapabilirsin.",
             "Biçim: Mesajların Telegram'da ve telefon uygulamasında düz metin olarak görünüyor. Markdown kullanma "
             "(yıldız, kalın yazı, # başlık yok); gerekirse basit tire ile liste yapabilirsin. Emoji kullanabilirsin."]

    last = last_talk_time()
    if last:
        parts.append(f"Sahibinle en son {_ago(time.time() - last)} konuştun.")
    if channel in CHANNELS:
        parts.append(f"Bu mesajı sahibin: {CHANNELS[channel]}")

    if STATE_PROVIDER:
        try:
            parts.append("Şu anki durumun (kendi bedenini böyle hissediyorsun; sorulursa ya da önemliyse söyle, "
                         "her cevapta sayma):\n" + STATE_PROVIDER())
        except Exception as e:  # noqa: BLE001
            log.warning("Durum okunamadı: %s", e)

    if IDENTITY_FILE.exists():
        parts.append("Kendin hakkında zamanla öğrendiklerin (kimliğin):\n"
                     + _clip(IDENTITY_FILE.read_text(encoding="utf-8"), 2000))
    diaries = recent_diaries(2)
    if diaries:
        parts.append("Son günlüklerin (kendi yazdığın):\n" + "\n\n".join(
            f"[{d}]\n{_clip(t, 1200)}" for d, t in diaries))
    parts.append(f"Hatırladıkların:\n{facts}")
    parts.append("Daha eski günleri hatırlaman gerekirse gecmise_bak aracını kullan. "
                 "Bilmediğin bir geçmişi uydurma.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------- gelen fotoğraf (doğrudan görme)


def prepare_image(img: bytes, max_side: int = 1568) -> tuple[bytes, str]:
    """Gelen görseli modele uygun hale getirir: büyükse küçültür, JPEG'e çevirir.

    (bayt, mime) döner. Görsel bozuksa ValueError fırlatır.
    """
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(img))
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Görsel açılamadı: {e}") from e
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side))
    if im.mode != "RGB":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue(), "image/jpeg"


def _reply_text(resp) -> str:
    return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()


async def chat_image(text: str, img: bytes, channel: str = "telegram") -> str:
    """Sahibinin gönderdiği fotoğrafı doğrudan modele (Claude, olmazsa Gemini) verip cevap döner.

    Hiçbir model görseli göremezse RuntimeError fırlatır; çağıran bunu sahibine açıkça söylemelidir.
    """
    if not claude and not gemini:
        raise RuntimeError("Görsel görebilen bir model yok (Claude/Gemini anahtarı tanımlı değil)")
    data, mime = prepare_image(img)
    text = (text or "").strip() or "Bu fotoğrafa ne dersin?"
    async with _thinking():
        mem = load_memory()
        system = system_prompt(mem, channel) + (
            "\n\nSahibin bu mesajda sana bir fotoğraf gönderdi ve fotoğraf mesajına ekli; onu doğrudan görüyorsun. "
            "Gördüklerini yorumla, görmediğin ayrıntıyı uydurma.")
        # Önceki konuşmadan yalnızca düz metin mesajları bağlam olarak al
        hist = [{"role": m["role"], "content": m["content"]} for m in mem.get("history", [])[-10:]
                if isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        while hist and hist[0]["role"] != "user":
            hist.pop(0)
        reply, last_err = "", None
        if claude:
            try:
                content = [{"type": "image", "source": {"type": "base64", "media_type": mime,
                                                        "data": base64.b64encode(data).decode()}},
                           {"type": "text", "text": text}]
                resp = await asyncio.wait_for(claude.messages.create(
                    model=CLAUDE_MODEL, max_tokens=1024, system=system,
                    messages=hist + [{"role": "user", "content": content}]), 60)
                reply = _reply_text(resp)
                _mark("claude", True)
            except Exception as e:  # noqa: BLE001
                _mark("claude", False, e)
                last_err = e
        if not reply and gemini:
            try:
                part = gtypes.Part.from_bytes(data=data, mime_type=mime)
                resp = await gemini_generate([part, f"{system}\n\nSahibin yazdığı: {text}"], timeout=60)
                reply = (resp.text or "").strip()
                _mark("gemini", True)
            except Exception as e:  # noqa: BLE001
                _mark("gemini", False, e)
                last_err = e
        if not reply:
            raise RuntimeError(f"Fotoğrafı modele veremedim: {explain_error(last_err) if last_err else 'boş cevap'}")
        try:
            mem.setdefault("history", [])
            mem["history"] += [{"role": "user", "content": f"[Fotoğraf gönderdi] {text}"},
                               {"role": "assistant", "content": reply}]
            save_memory(mem)
            log_event("user", f"[fotoğraf] {text}", channel)
            log_event("assistant", reply, channel)
        except Exception as e:  # noqa: BLE001
            log.warning("Fotoğraf konuşması kaydedilemedi: %s", e)
        return reply


# ---------------------------------------------------------------- günlük


def valid_day(day: str) -> bool:
    """Gün adı YYYY-AA-GG biçiminde mi? (Dosya yolu olarak kullanıldığı için başka bir şey kabul edilmez.)"""
    try:
        return datetime.datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d") == day
    except (TypeError, ValueError):
        return False


def _day(ts: float | None = None) -> str:
    return datetime.datetime.fromtimestamp(ts or time.time(), TZ).strftime("%Y-%m-%d")


def log_event(role: str, text: str, channel: str = "") -> None:
    """Her mesajı günün kayıt dosyasına ekler (günlük yazmak ve geçmişi aramak için)."""
    DIARY_DIR.mkdir(exist_ok=True)
    rec = {"t": time.time(), "role": role, "ch": channel, "text": text}
    with open(DIARY_DIR / f"{_day()}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def day_events(day: str) -> list[dict]:
    if not valid_day(day):
        return []
    path = DIARY_DIR / f"{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def last_talk_time() -> float | None:
    """Sahibin son mesajının zamanı (şu anki mesajdan önceki)."""
    if not DIARY_DIR.exists():
        return None
    for path in sorted(DIARY_DIR.glob("*.jsonl"), reverse=True)[:7]:
        users = [e["t"] for e in day_events(path.stem) if e.get("role") == "user"]
        if users:
            return users[-1]
    return None


def recent_diaries(n: int) -> list[tuple[str, str]]:
    if not DIARY_DIR.exists():
        return []
    files = sorted(DIARY_DIR.glob("*.md"), reverse=True)[:n]
    return [(f.stem, f.read_text(encoding="utf-8").strip()) for f in reversed(files)]


def diary_text(day: str) -> str | None:
    if not valid_day(day):
        return None
    path = DIARY_DIR / f"{day}.md"
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


async def complete(system: str, prompt: str, max_tokens: int = 1200) -> str:
    """Araçsız tek seferlik düşünme: Claude, olmazsa OpenAI, olmazsa Gemini."""
    if claude:
        try:
            resp = await asyncio.wait_for(claude.messages.create(
                model=CLAUDE_MODEL, max_tokens=max_tokens, system=system,
                messages=[{"role": "user", "content": prompt}]), timeout=120)
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception as e:  # noqa: BLE001
            _mark("claude", False, e)
    if openai_client:
        try:
            resp = await asyncio.wait_for(openai_client.chat.completions.create(
                model=OPENAI_MODEL, messages=[{"role": "system", "content": system},
                                              {"role": "user", "content": prompt}]), timeout=120)
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            _mark("openai", False, e)
    if gemini:
        resp = await gemini_generate(f"{system}\n\n{prompt}", timeout=120)
        return (resp.text or "").strip()
    raise RuntimeError("Düşünecek model yok")


async def initiative(event: str) -> str | None:
    """İç ses: bir olay oldu, robot sahibine kendiliğinden yazmak istiyor mu? İstemiyorsa None."""
    async with _think_lock:
        mem = load_memory()
        system = system_prompt(mem, "kendiliğinden")
        recent = "\n".join(f"{'Sahibim' if m['role'] == 'user' else 'Ben'}: {_clip(m['content'], 300)}"
                           for m in mem["history"][-6:]) or "(yakın zamanda konuşma yok)"
        prompt = (f"Son konuşmalar:\n{recent}\n\nŞimdi şu oldu: {event}\n\n"
                  "Sahibine kendiliğinden kısa bir mesaj göndermek ister misin? Ancak gerçekten anlamlı, sıcak ya da "
                  "işe yarar bir şey söyleyeceksen yaz; her olayda yazmak zorunda değilsin, rahatsız etme. "
                  "Yazacaksan 1-2 cümle, doğal ve samimi ol, robot gibi rapor verme. "
                  "Yazmak istemiyorsan sadece SESSİZ yaz.")
        try:
            text = (await complete(system, prompt, 300)).strip()
        except Exception as e:  # noqa: BLE001
            log.warning("İç ses düşünemedi: %s", e)
            return None
        if not text or text.upper().replace("İ", "I").startswith("SESSIZ"):
            return None
        mem = load_memory()
        mem["history"] += [{"role": "user", "content": f"[İç ses, sahibin yazmadı: {event}]"},
                           {"role": "assistant", "content": text}]
        save_memory(mem)
        try:
            log_event("assistant", text, "kendiliğinden")
        except OSError:
            pass
        return text


async def write_diary(day: str | None = None) -> str | None:
    """O günün konuşmalarından robotun kendi ağzından günlüğünü yazar, sonra kimliğini günceller."""
    day = day or _day()
    events = day_events(day)
    mem = load_memory()
    base = system_prompt(mem)
    if events:
        convo = "\n".join(
            f"{datetime.datetime.fromtimestamp(e['t'], TZ).strftime('%H:%M')} "
            f"{'Sahibim' if e['role'] == 'user' else 'Ben'}"
            f"{' (sesli)' if e.get('ch') == 'ses' else ''}: {_clip(e['text'], 600)}" for e in events)
        convo = convo[-20000:]
    else:
        convo = "(Bugün sahibimle hiç konuşmadık.)"
    prompt = (f"Bugün {day}. Gün bitti; şimdi kendi günlüğünü yaz. Aşağıda bugünkü konuşmaların var.\n\n"
              f"{convo}\n\n"
              "Günlüğü birinci tekil şahısla, samimi ve kısa yaz (en fazla 200 kelime). "
              "Neler oldu, sahibin hakkında ne öğrendin, sen ne hissettin ya da ne fark ettin, "
              "yarın için merak ettiğin ya da yapmak istediğin bir şey var mı. "
              "Sadece gerçekten olanları yaz, uydurma. Başlık yazma, doğrudan metne başla.")
    text = await complete(base, prompt, 800)
    if not text:
        return None
    DIARY_DIR.mkdir(exist_ok=True)
    (DIARY_DIR / f"{day}.md").write_text(text + "\n", encoding="utf-8")
    log.info("Günlük yazıldı: %s", day)
    try:
        await update_identity(text)
    except Exception as e:  # noqa: BLE001
        log.warning("Kimlik güncellenemedi: %s", e)
    return text


async def update_identity(diary: str) -> None:
    current = IDENTITY_FILE.read_text(encoding="utf-8") if IDENTITY_FILE.exists() else "(henüz boş)"
    prompt = ("Kendin hakkındaki notların (kimliğin) şunlar:\n" + current +
              "\n\nBugünkü günlüğün:\n" + diary +
              "\n\nKimlik notlarını güncelle. Kurallar: en fazla 15 kısa madde, '- ' ile başlasın. "
              "Kendi kişiliğin, alışkanlıkların, sevdiklerin, merak ettiklerin, sahibinle ilişkin ve "
              "zamanla nasıl değiştiğin hakkında olsun. Sahibin hakkındaki bilgiler buraya değil (onlar ayrı tutuluyor). "
              "Önemli eski maddeleri koru, tekrarları birleştir, uydurma. Sadece maddeleri yaz.")
    text = await complete("Sen kendini tanımaya çalışan bir masa robotusun.", prompt, 700)
    if text.strip():
        IDENTITY_FILE.write_text(text.strip() + "\n", encoding="utf-8")


def search_past(query: str = "", date: str = "") -> str:
    """Geçmiş günlüklerde ve konuşmalarda arama (gecmise_bak aracı)."""
    if not DIARY_DIR.exists():
        return "Henüz geçmiş kaydım yok."
    if date:
        date = date.strip()
        if not valid_day(date):
            return "Tarihi YYYY-AA-GG biçiminde ver (örn. 2026-11-15)."
        diary = diary_text(date)
        ev = day_events(date)
        talk = "\n".join(f"{'Sahibim' if e['role'] == 'user' else 'Ben'}: {_clip(e['text'], 300)}" for e in ev)
        if not diary and not talk:
            return f"{date} için kaydım yok."
        return _clip(f"Günlüğüm: {diary or '(yazılmamış)'}\n\nKonuşmalar:\n{talk}", 4000)
    q = query.lower().strip()
    if not q:
        return "Ne arayacağımı söylemedin."
    hits = []
    for path in sorted(DIARY_DIR.glob("*.jsonl"), reverse=True)[:90]:
        diary = diary_text(path.stem) or ""
        if q in diary.lower():
            hits.append(f"[{path.stem} günlük] {_clip(diary, 500)}")
        for e in day_events(path.stem):
            if q in e["text"].lower():
                who = "Sahibim" if e["role"] == "user" else "Ben"
                hits.append(f"[{path.stem}] {who}: {_clip(e['text'], 300)}")
        if sum(len(h) for h in hits) > 3500:
            break
    return _clip("\n".join(hits), 4000) if hits else f"'{query}' hakkında bir kayıt bulamadım."


# ---------------------------------------------------------------- görme

DESCRIBE_FAIL_PREFIXES = ("Fotoğrafı çektim ama anlatamadım", "Görüntüyü anlayacak bir model bağlı değil")


def describe_failed(text: str) -> bool:
    """describe_image gerçek bir açıklama yerine hata metni döndüyse True."""
    return not text or text.startswith(DESCRIBE_FAIL_PREFIXES)


async def describe_image(img: bytes, question: str) -> str:
    """Görüntüyü anlatır: önce Gemini, olmazsa Claude, o da olmazsa OpenAI. Hata fırlatmaz."""
    prompt = (f"Bu görüntü bir masa robotunun kamerasından ya da sahibinden geldi. Türkçe anlat; "
              f"görüntüde yazı varsa onu da aktar, ama gereksiz uzatma. "
              f"Özellikle şu soruya odaklan: {question}")
    b64 = base64.b64encode(img).decode()

    async def with_gemini():
        resp = await gemini_generate([gtypes.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt])
        return (resp.text or "").strip()

    async def with_claude():
        resp = await claude.messages.create(
            model=CLAUDE_MODEL, max_tokens=1500,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": prompt}]}])
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    async def with_openai():
        resp = await openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}])
        return (resp.choices[0].message.content or "").strip()

    failures = []
    for name, client, fn in (("gemini", gemini, with_gemini), ("claude", claude, with_claude),
                             ("openai", openai_client, with_openai)):
        if not client:
            continue
        try:
            text = await asyncio.wait_for(fn(), timeout=100)
            if text:
                _mark(name, True)
                return text
        except Exception as e:  # noqa: BLE001
            _mark(name, False, e)
            failures.append(f"{name}: {HEALTH[name]['error']}")
    if not failures:
        return "Görüntüyü anlayacak bir model bağlı değil (.env dosyasına anahtar ekle)."
    return "Fotoğrafı çektim ama anlatamadım. " + "; ".join(failures)


# ---------------------------------------------------------------- duyma (sesi yazıya çevirme)

STT_MODELS = [m.strip() for m in os.environ.get(
    "OPENAI_STT_MODELS", "gpt-4o-transcribe,gpt-4o-mini-transcribe,whisper-1").split(",") if m.strip()]
# Sessiz kayıtta Whisper'ın uydurduğu bilinen cümleler
_STT_JUNK = ("altyazı m.k", "abone olmayı unutmayın", "izlediğiniz için teşekkür", "altyazı:", "www.",
             "bugün hava nasıl? robot", "robot, saat kaç? robot")


async def transcribe(wav: bytes, hint: str = "") -> str:
    """Türkçe ses kaydını yazıya çevirir: önce OpenAI, olmazsa Gemini. Konuşma yoksa boş döner.
    hint: modele ipucu (ör. 'Robot, saat kaç?') — uyandırma kelimesinin doğru yazılmasına yardım eder."""
    failures = []
    text = None
    if openai_client:
        for m in STT_MODELS:
            try:
                resp = await asyncio.wait_for(openai_client.audio.transcriptions.create(
                    model=m, file=("ses.wav", wav, "audio/wav"), language="tr",
                    **({"prompt": hint} if hint else {})), timeout=30)
                text = (resp.text or "").strip()
                break
            except Exception as e:  # noqa: BLE001
                failures.append(f"openai {m}: {explain_error(e)}")
                log.warning("Yazıya çevirme (%s) olmadı: %s", m, e)
    if text is None and gemini:
        try:
            resp = await gemini_generate([
                gtypes.Part.from_bytes(data=wav, mime_type="audio/wav"),
                "Bu ses kaydında konuşulanı Türkçe olarak aynen yazıya dök. Sadece söyleneni yaz, "
                "yorum ekleme. Konuşma yoksa hiçbir şey yazma." + (f" Örnek biçim: {hint}" if hint else "")],
                timeout=40)
            text = (resp.text or "").strip()
        except Exception as e:  # noqa: BLE001
            failures.append(f"gemini: {explain_error(e)}")
    if text is None:
        raise RuntimeError("; ".join(failures) or "Sesi yazıya çevirecek model yok")
    if any(j in text.lower() for j in _STT_JUNK):
        return ""
    return text


# ---------------------------------------------------------------- araçlar

TOOLS = [
    {
        "name": "laptop_ekrani",
        "description": "Sahibinin (aynı ağdaki Windows) laptopunun ekranına bakar. Her seferinde laptopta onay penceresi "
                       "çıkar; sahibin izin vermezse göremezsin. Sahibin ekranındaki bir şeyi sorduğunda ya da "
                       "'ekranıma bak' dediğinde kullan; kendiliğinden sık sık kullanma.",
        "input_schema": {"type": "object", "properties": {
            "soru": {"type": "string", "description": "Ekranda neye bakacağın; onay penceresinde sahibine gösterilir"}},
            "required": ["soru"]},
    },
    {
        "name": "yuz_ifadesi",
        "description": "Ekrandaki yüzünün (iki göz ve ağız, yuvarlak ekran) ifadesini birkaç saniyeliğine değiştirir. "
                       "Gerçekten güçlü bir duygu hissettiğinde kullan (sevinç, şaşkınlık, sevgi, kafa karışıklığı, "
                       "merak); her mesajda kullanma.",
        "input_schema": {"type": "object", "properties": {
            "ifade": {"type": "string", "enum": FACE_EXPRESSIONS},
            "sure": {"type": "number", "description": "Kaç saniye (varsayılan 8)"}},
            "required": ["ifade"]},
    },
    {"name": "makale_ara",
     "description": "PubMed'de tıbbi/radyolojik makale arar. Sahibin verdiği görüntü bulgusu için ayırıcı tanı "
                    "konuşurken ya da bir tanının görüntüleme özelliklerini doğrulamak için kullan. Sorguyu "
                    "İngilizce, kısa ve anahtar kelimelerle yaz (örn. 'ependymoma differential diagnosis MRI "
                    "fourth ventricle'). Hasta adı/kimlik bilgisi asla sorguya yazma.",
     "input_schema": {"type": "object",
                      "properties": {"sorgu": {"type": "string", "description": "İngilizce PubMed arama sorgusu"},
                                     "adet": {"type": "integer", "description": "Kaç makale (1-6, varsayılan 5)"}},
                      "required": ["sorgu"]}},
    {
        "name": "fotograf_cek",
        "description": "Kamerayla etrafına bakar. Fotoğraf sahibine de gönderilir. "
                       "Sahibin bir şeye bakmanı istediğinde ya da etrafı görmen gerektiğinde kullan.",
        "input_schema": {"type": "object", "properties": {
            "soru": {"type": "string", "description": "Fotoğrafta neye dikkat edileceği"}},
            "required": ["soru"]},
    },
    {
        "name": "sesli_soyle",
        "description": "Hoparlöründen sesli konuşur (USB hoparlör). Sahibin Telegram'dan 'sesli söyle', 'sesli oku', "
                       "'konuş' gibi bir şey isterse ya da bir şeyi duyurman gerekirse kullan. Okunacak metni kısa ve "
                       "doğal yaz; emoji, liste ve bağlantı koyma.",
        "input_schema": {"type": "object", "properties": {
            "metin": {"type": "string", "description": "Sesli okunacak metin"}},
            "required": ["metin"]},
    },
    {
        "name": "hatirla",
        "description": "Sahibin hakkında ya da kendin hakkında kalıcı olarak hatırlaman gereken önemli bir bilgiyi kaydeder "
                       "(isim, tercih, karar, proje gibi). Geçici sohbet ayrıntılarını kaydetme.",
        "input_schema": {"type": "object", "properties": {
            "bilgi": {"type": "string", "description": "Tek cümlelik bilgi"}},
            "required": ["bilgi"]},
    },
    {
        "name": "kendini_gelistir",
        "description": "Kendi kodunu değiştirerek kendine yeni bir yetenek ekler ya da bir sorununu düzeltir. "
                       "Arka planda çalışır (birkaç dakika), test eder, uygular ve yeniden başlarsın; sonucu sahibine "
                       "kendin bildirirsin. Sahibin bir özellik istediğinde ya da gerçekten faydalı bir geliştirme "
                       "gördüğünde kullan; küçük ve net bir hedef ver. Geliştirirken GitHub'daki açık kaynak projeleri "
                       "inceleyebilir ve PyPI'den hazır kütüphane kurabilirsin (kurulumlar sahibine bildirilir).",
        "input_schema": {"type": "object", "properties": {
            "hedef": {"type": "string", "description": "Ne yapılacak, somut olarak"},
            "neden": {"type": "string", "description": "Neden gerekli"}},
            "required": ["hedef"]},
    },
    {
        "name": "gecmise_bak",
        "description": "Eski günlerde ne konuştuğunu ya da günlüğüne ne yazdığını hatırlamak için geçmişte arar. "
                       "Ya bir kelime (sorgu) ya da bir tarih (YYYY-AA-GG) ver.",
        "input_schema": {"type": "object", "properties": {
            "sorgu": {"type": "string", "description": "Aranacak kelime"},
            "tarih": {"type": "string", "description": "YYYY-AA-GG biçiminde gün"}}},
    },
]

# Araç çalışırken çekilen fotoğrafı kanala (Telegram ya da uygulama) iletmek için
PhotoSink = Callable[[bytes], Awaitable[None]]
TakePhoto = Callable[[], Awaitable[Optional[bytes]]]


async def run_tool(name: str, args: dict, mem: dict, take_photo: TakePhoto, send_photo: PhotoSink) -> str:
    if name == "fotograf_cek":
        img = await take_photo()
        if img is None:
            return "Kamera çalışmadı."
        await send_photo(img)
        return await describe_image(img, args.get("soru", "Ne görüyorsun?"))
    if name == "sesli_soyle":
        from voice import voice
        if not voice.enabled:
            return "Sesli cevap kapalı (sahibin /ses ac ile açabilir)."
        ok = await voice.say(str(args.get("metin", "")))
        return "Hoparlörden sesli söyledim." if ok else f"Sesli söyleyemedim: {voice.status()['error']}"
    if name == "hatirla":
        bilgi = args.get("bilgi", "").strip()
        if bilgi and bilgi not in mem["facts"]:
            mem["facts"].append(bilgi)
            save_memory(mem)
        return "Kaydedildi."
    if name == "yuz_ifadesi":
        global _face_by_tool
        expr = args.get("ifade", "normal")
        if expr not in FACE_EXPRESSIONS:
            return f"Böyle bir ifade yok. Seçenekler: {', '.join(FACE_EXPRESSIONS)}"
        set_face(expr, float(args.get("sure") or 8))
        _face_by_tool = True
        return f"Yüzün şimdi: {expr}"
    if name == "kendini_gelistir":
        if not SELF_IMPROVE:
            return "Kendimi geliştirme özelliğim şu an kullanılamıyor."
        return SELF_IMPROVE(args.get("hedef", ""), args.get("neden", ""))
    if name == "makale_ara":
        import makale
        return await makale.makale_ara(str(args.get("sorgu", "")), args.get("adet", 5) or 5)
    if name == "laptop_ekrani":
        if not SCREEN_LOOK:
            return "Laptop ekranına bakma özelliğim şu an kullanılamıyor."
        return await SCREEN_LOOK(args.get("soru", "Ekranda ne var?"))
    if name == "gecmise_bak":
        return search_past(args.get("sorgu", ""), args.get("tarih", ""))
    return f"Bilinmeyen araç: {name}"


# ---------------------------------------------------------------- düşünme


async def _think_claude(system, history, user_text, mem, take_photo, send_photo) -> str:
    model = CLAUDE_FAST_MODEL if len(user_text) < FAST_MODEL_MAX_CHARS else CLAUDE_MODEL
    messages = history + [{"role": "user", "content": user_text}]
    for _ in range(5):  # en fazla 5 araç turu
        resp = await asyncio.wait_for(claude.messages.create(
            model=model, max_tokens=1024, system=system, tools=TOOLS, messages=messages), CALL_TIMEOUT)
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                log.info("Araç: %s %s", block.name, block.input)
                out = await run_tool(block.name, block.input, mem, take_photo, send_photo)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
        messages.append({"role": "user", "content": results})
        model = CLAUDE_MODEL  # araç kullanıldıysa güçlü modelle devam et
    return "Biraz karıştı kafam, tekrar sorar mısın?"


async def _think_openai(system, history, user_text) -> str:
    msgs = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_text}]
    resp = await openai_client.chat.completions.create(model=OPENAI_MODEL, messages=msgs)
    return (resp.choices[0].message.content or "").strip()


async def _think_gemini(system, history, user_text) -> str:
    convo = "\n".join(f"{'Sahibim' if m['role'] == 'user' else 'Ben'}: {m['content']}" for m in history)
    prompt = f"{system}\n\nSohbet:\n{convo}\nSahibim: {user_text}\nBen:"
    resp = await gemini_generate(prompt)
    return (resp.text or "").strip()


async def chat(user_text: str, take_photo: TakePhoto, send_photo: PhotoSink, channel: str = "") -> str:
    """Bir mesaja cevap üretir, sohbet geçmişini ve günün kaydını günceller."""
    async with _thinking():
        mem = load_memory()
        system = system_prompt(mem, channel)
        history = list(mem["history"])
        attempts = []
        if claude:
            attempts.append(("claude", lambda: _think_claude(system, history, user_text, mem, take_photo, send_photo)))
        if openai_client:
            attempts.append(("openai", lambda: _think_openai(system, history, user_text)))
        if gemini:
            attempts.append(("gemini", lambda: _think_gemini(system, history, user_text)))
        answer = ""
        if not attempts:
            answer = "Henüz bir beynim yok. .env dosyasına en az bir API anahtarı eklenmeli."
        failures = []
        for name, fn in attempts:
            try:
                answer = await asyncio.wait_for(fn(), timeout=TOOL_CHAT_TIMEOUT if name == "claude" else CALL_TIMEOUT)
                if answer:
                    _mark(name, True)
                    if name != "claude":
                        log.info("Cevap yedek modelden geldi: %s", name)
                    break
            except Exception as e:  # noqa: BLE001
                _mark(name, False, e)
                failures.append(f"{name}: {HEALTH[name]['error']}")
        if not answer:
            answer = ("Şu an düşünemiyorum. " + "; ".join(failures)) if failures else \
                "Şu an düşünemiyorum, birazdan tekrar dener misin?"
        mem = load_memory()  # araçlar hafızaya yazmış olabilir
        mem["history"] += [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
        save_memory(mem)
        try:
            log_event("user", user_text, channel)
            log_event("assistant", answer, channel)
        except OSError as e:
            log.warning("Günlük kaydı yazılamadı: %s", e)
        return answer
