"""Robotun ana programı: uygulama sunucusu (FastAPI) + Telegram botu, tek süreçte.

Çalıştırma:  python robot.py
Ayarlar .env dosyasından okunur (bkz. README).
"""

import asyncio
import contextlib
import datetime
import json
import logging
import os
import pathlib
import secrets
import time
import uuid
from collections import OrderedDict

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("robot")

import brain  # noqa: E402  (logging ayarından sonra)
from camera import camera  # noqa: E402
from ears import WAKE_WORDS, ears, has_wake, strip_wake  # noqa: E402
import body  # noqa: E402
from initiative import initiative  # noqa: E402
from gaze import gaze  # noqa: E402
from selfdev import selfdev  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parent
WEB_DIR = BASE / "web"
OWNER_FILE = BASE / "owner.json"
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
PORT = int(os.environ.get("ROBOT_PORT", "8080"))
STARTED = time.time()

TG = None  # Telegram uygulaması (kulakların cevabı sahibine yazabilmesi için)
EARS_NOTIFY_WAKE = os.environ.get("EARS_NOTIFY_WAKE", "0").strip() == "1"
EARS_VERIFY = os.environ.get("EARS_VERIFY", "1").strip() != "0"   # yazıda da kelime yoksa yanlış alarm say
_wake_msg = None   # silinebilsin diye son "Dinliyorum" mesajı
# Sesli konuşmaların son kayıtları (uygulama /api/ears ile görür)
VOICE_LOG: list = []
# Uygulamanın göreceği olay akışı: robotun kendiliğinden yazdıkları, sesli konuşmalar, uyarılar, günlükler
EVENTS: list = []
BOOT_ID = uuid.uuid4().hex[:8]   # robot yeniden başlayınca uygulama sayacı sıfırlasın diye


def record_event(kind: str, text: str, heard: str = "", photo_ids: list | None = None) -> None:
    eid = (EVENTS[-1]["id"] + 1) if EVENTS else 1
    EVENTS.append({"id": eid, "at": time.time(), "kind": kind, "text": text, "heard": heard,
                   "photos": [f"/api/photos/{p}" for p in (photo_ids or [])]})
    del EVENTS[:-100]

# Uygulamaya gönderilen son fotoğraflar (bellekte, en fazla 20)
PHOTOS: "OrderedDict[str, bytes]" = OrderedDict()


def store_photo(img: bytes) -> str:
    pid = uuid.uuid4().hex[:12]
    PHOTOS[pid] = img
    while len(PHOTOS) > 20:
        PHOTOS.popitem(last=False)
    return pid


def cpu_temp() -> float | None:
    return body.cpu_temp()


DIARY_TIME = os.environ.get("DIARY_TIME", "23:30")          # robot her gece bu saatte günlüğünü yazar
DIARY_SHARE = os.environ.get("DIARY_SHARE", "1").strip() != "0"   # günlüğü Telegram'dan paylaşsın mı
TEMP_ALERT = float(os.environ.get("TEMP_ALERT", "75"))      # bu sıcaklığı geçince sahibini uyarır


def self_state() -> str:
    """Beyne verilen anlık durum: beden + duyular."""
    extra = [f"Kameram (gözüm) {'açık' if camera.enabled else 'gizlilik için kapalı'}."]
    gs = gaze.status()
    if camera.enabled and gs["enabled"]:
        extra.append("Ekrandaki gözlerim kamerayla önümdekine bakıyor; "
                     + ("şu an önümde biri var." if gs["present"]
                        else f"{gs['last_seen_s'] // 60} dakikadır kimseyi görmedim, uyukluyorum."))
    st = ears.status()
    if not st["enabled"]:
        extra.append("Kulaklarım (mikrofon) sahibim tarafından kapatıldı.")
    elif st["running"]:
        extra.append(f"Kulaklarım açık; biri \"{st['wake_words'][0]}\" deyince dinliyorum.")
    else:
        extra.append(f"Kulaklarım çalışmıyor ({st['error'] or 'başlıyor'}).")
    bad = [f"{k} ({v['error']})" for k, v in brain.health().items() if v["ok"] is False]
    if bad:
        extra.append("Düşünme modellerimden çalışmayanlar: " + ", ".join(bad) + ".")
    return body.describe(extra)


brain.STATE_PROVIDER = self_state
brain.SELF_IMPROVE = lambda goal, reason="": selfdev.request(goal, reason)


# ================================================================ laptop ekranı (onaylı)
# Laptoptaki RobotEkran programı /api/screen/poll ile sorar; istek varsa sahibine onay penceresi açar.
SCREEN: dict = {"req": None, "future": None, "seen": 0.0}


async def screen_look(question: str) -> str:
    if time.time() - SCREEN["seen"] > 15:
        return "Laptoptaki ekran paylaşım programı (RobotEkran) açık değil ya da bana bağlanmıyor."
    if SCREEN["req"] is not None:
        return "Laptopta zaten bekleyen bir onay isteğim var."
    fut = asyncio.get_running_loop().create_future()
    SCREEN["req"] = {"id": uuid.uuid4().hex[:10], "reason": question[:300]}
    SCREEN["future"] = fut
    try:
        res = await asyncio.wait_for(fut, timeout=45)
    except asyncio.TimeoutError:
        return "Laptopta onay penceresine cevap gelmedi."
    finally:
        SCREEN["req"], SCREEN["future"] = None, None
    if not res.get("approved"):
        return res.get("error") or "Sahibim ekranına bakmama izin vermedi; buna saygı göster."
    import base64
    import binascii
    try:
        img = base64.b64decode(res.get("image", ""), validate=True)
    except (binascii.Error, ValueError):
        return "Ekran görüntüsü bozuk geldi."
    brain.set_face("merakli", 6)
    return "Laptop ekranına baktın. Gördüğün: " + await brain.describe_image(
        img, f"Bu, sahibimin bilgisayar ekranının görüntüsü. {question} "
             "Ekranda kişisel ya da hasta bilgisi (ad, TC no, protokol) varsa bunları tekrar etme.")


brain.SCREEN_LOOK = screen_look


def owner_id():
    if OWNER_FILE.exists():
        return json.loads(OWNER_FILE.read_text())["id"]
    return None


# ================================================================ Telegram

def build_telegram():
    if not TELEGRAM_TOKEN:
        log.warning("TELEGRAM_TOKEN yok, Telegram botu kapalı")
        return None

    from telegram import Update
    from telegram.constants import ChatAction
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    async def guard(update: Update) -> bool:
        uid = update.effective_user.id
        oid = owner_id()
        if oid is None:
            OWNER_FILE.write_text(json.dumps({"id": uid}))
            await update.message.reply_text("Merhaba! Seni sahibim olarak kaydettim. Artık sadece sana cevap vereceğim.")
            return True
        return uid == oid

    async def reply_long(update: Update, text: str) -> None:
        for i in range(0, len(text), 4000):
            await update.message.reply_text(text[i:i + 4000])

    async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

        async def send_photo(img: bytes) -> None:
            await update.message.reply_photo(img)

        reply = await brain.chat(text, camera.photo, send_photo, "telegram")
        await reply_long(update, reply)

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await answer(update, context, update.message.text)

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        msg = update.message
        try:
            if msg.photo:
                file = await msg.photo[-1].get_file()      # en büyük boyut
            elif msg.document and (msg.document.mime_type or "").startswith("image/"):
                file = await msg.document.get_file()       # dosya olarak gönderilen görsel
            else:
                await msg.reply_text("⚠️ Mesajda görsel bulamadım, bu yüzden bir şey göremedim.")
                return
            img = bytes(await file.download_as_bytearray())
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram fotoğrafı indirilemedi: %s", e)
            await msg.reply_text(f"⚠️ Fotoğrafı Telegram'dan indiremedim, göremiyorum: {e}")
            return
        if not img:
            await msg.reply_text("⚠️ Fotoğraf boş geldi, göremiyorum. Tekrar gönderir misin?")
            return
        caption = msg.caption or "Bu fotoğrafa ne dersin?"
        try:
            # Görsel doğrudan ana modele verilir
            reply = await brain.chat_image(caption, img, "telegram")
            await reply_long(update, reply)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("Fotoğraf doğrudan modele verilemedi: %s", e)
            why = str(e)
        # Yedek yol: görseli doğrudan veremedik; yalnızca yazılı açıklamayla devam et ve bunu açıkça söyle
        try:
            seen = await brain.describe_image(img, caption)
        except Exception as e:  # noqa: BLE001
            await msg.reply_text(f"⚠️ Fotoğrafı göremedim ({why}). Açıklama yolu da çalışmadı: {e}")
            return
        await msg.reply_text(f"⚠️ Fotoğrafı doğrudan göremedim ({why}); yalnızca açıklamasına dayanarak cevap veriyorum.")
        await answer(update, context, f"[Sana bir fotoğraf gönderdim. Gözlerin şunu görüyor: {seen}]\n{caption}")

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await update.message.reply_text("Buradayım. Bana yazabilir, fotoğraf gönderebilirsin. "
                                            "Komutlar: /foto /hafiza /gunluk /kimlik /sifirla /durum /test /dinle /sus /oto "
                                            "/gelisim /gelistir /geri_al")

    async def cmd_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text("Bakıyorum...")
        img = await camera.photo()
        if img is None:
            await update.message.reply_text("Kamera çalışmadı ya da kapalı.")
            return
        await update.message.reply_photo(img)
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        await reply_long(update, await brain.describe_image(img, "Ne görüyorsun?"))

    async def cmd_hafiza(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        facts = brain.load_memory()["facts"]
        text = "\n".join(f"{i}. {f}" for i, f in enumerate(facts, 1)) or "Henüz kalıcı bir şey hatırlamıyorum."
        await reply_long(update, text)

    async def cmd_sifirla(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            brain.clear_history()
            await update.message.reply_text("Sohbet geçmişini temizledim. Kalıcı hatıralarım duruyor (/hafiza).")

    def health_line(name: str, v: dict) -> str:
        if v["ok"] is True:
            icon, note = "✅", f"{v['ms']} ms" if v.get("ms") else "çalışıyor"
        elif v["ok"] is False:
            icon, note = "❌", v["error"]
        else:
            icon, note = "⚪", "henüz denenmedi (/test)"
        return f"{icon} {name} ({v['model']}): {note}"

    async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await update.message.reply_text("Modelleri deniyorum...")
        res = await brain.self_test()
        await update.message.reply_text("\n".join(health_line(k, v) for k, v in res.items()))

    async def cmd_dinle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        ears.set_enabled(True)
        st = ears.status()
        if st["error"] and not st["running"]:
            await update.message.reply_text(f"Kulaklarımı açmaya çalıştım ama: {st['error']}")
        else:
            await update.message.reply_text(f"👂 Dinliyorum. Bana seslenmek için \"{st['wake_words'][0]}\" de.")

    async def cmd_gunluk(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        arg = (context.args[0] if context.args else "").strip()
        if arg == "yaz":
            await update.message.reply_text("Bugünkü günlüğümü yazıyorum...")
            text = await brain.write_diary()
            await reply_long(update, f"📔 {text}" if text else "Yazamadım, birazdan tekrar dener misin?")
            return
        days = brain.recent_diaries(1) if not arg else [(arg, brain.diary_text(arg) or "")]
        if not days or not days[0][1]:
            await update.message.reply_text("Henüz günlük yazmadım. Her gece " + DIARY_TIME +
                                            "'da yazıyorum; şimdi yazmamı istersen: /gunluk yaz")
            return
        d, t = days[-1]
        await reply_long(update, f"📔 {d}\n\n{t}")

    async def cmd_kimlik(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        f = brain.IDENTITY_FILE
        text = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        await reply_long(update, f"🪞 Kendimi şöyle tanıyorum:\n\n{text}" if text else
                         "Kendimi henüz tanımıyorum. İlk günlüğümü yazınca burası dolmaya başlayacak.")

    async def cmd_oto(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        arg = (context.args[0].lower() if context.args else "")
        if arg in ("ac", "aç", "on"):
            initiative.enabled = True
        elif arg in ("kapat", "off"):
            initiative.enabled = False
        elif arg in ("kamera", "goz", "göz"):
            initiative.presence_on = not initiative.presence_on
        await update.message.reply_text(
            oto_line() + "\n\n/oto ac · /oto kapat · /oto kamera (hareket algılamayı aç/kapat)")

    async def cmd_gelisim(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        arg = (context.args[0].lower() if context.args else "")
        if arg in ("ac", "aç"):
            selfdev.enabled = True
        elif arg == "kapat":
            selfdev.enabled = False
        names = {"uygulandi": "✅ uyguladım", "vazgecti": "🤷 vazgeçtim", "test_gecmedi": "❌ test geçmedi",
                 "geri_alindi": "↩️ açılamadı, geri döndüm", "sahibim_geri_aldi": "↩️ sen geri aldın",
                 "onaylandi": "👍 sorunsuz çalıştı", "sir_engellendi": "🛑 sır engellendi",
                 "gizlilik_engellendi": "🛑 kişisel bilgi engellendi"}
        hist = "\n".join(
            f"{datetime.datetime.fromtimestamp(h['at'], brain.TZ):%d.%m %H:%M} {names.get(h.get('kind'), h.get('kind'))}: "
            f"{h.get('summary') or h.get('goal') or h.get('subject', '')}" for h in selfdev.history(8)) or "Henüz yok."
        await reply_long(update, selfdev_line() + "\n\nSon geliştirmelerim:\n" + hist +
                         "\n\n/gelistir <ne istediğin> · /geri_al · /gelisim kapat|ac")

    async def cmd_gelistir(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        goal = " ".join(context.args).strip()
        if not goal:
            await update.message.reply_text("Ne geliştireyim? Örnek: /gelistir hava durumunu söyleyebileyim")
            return
        msg = selfdev.request(goal, "Sahibim istedi")
        if not msg.startswith("Kendimi geliştirmeye başladım"):   # başladıysa zaten haber veriyor
            await update.message.reply_text(msg)

    async def cmd_geri_al(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await update.message.reply_text(await selfdev.undo_last())

    async def cmd_sus(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            ears.set_enabled(False)
            await update.message.reply_text("🙉 Kulaklarımı kapattım. Açmak için /dinle")

    async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        lines = [health_line(k, v) for k, v in brain.health().items()]
        lines.append("📟 " + body.live_report())   # anlık ölçüm: birim ve zamanla
        lines.append(f"📷 Kamera: {'açık' if camera.enabled else 'kapalı (gizlilik)'}")
        lines.append(ears_line())
        lines.append(oto_line())
        lines.append(selfdev_line())
        await update.message.reply_text("\n".join(lines))

    app = (Application.builder().token(TELEGRAM_TOKEN)
           .connect_timeout(30).read_timeout(30).write_timeout(60).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("foto", cmd_foto))
    app.add_handler(CommandHandler("hafiza", cmd_hafiza))
    app.add_handler(CommandHandler("sifirla", cmd_sifirla))
    app.add_handler(CommandHandler("durum", cmd_durum))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("dinle", cmd_dinle))
    app.add_handler(CommandHandler("oto", cmd_oto))
    app.add_handler(CommandHandler("gelisim", cmd_gelisim))
    app.add_handler(CommandHandler("gelistir", cmd_gelistir))
    app.add_handler(CommandHandler("geri_al", cmd_geri_al))
    app.add_handler(CommandHandler("gunluk", cmd_gunluk))
    app.add_handler(CommandHandler("kimlik", cmd_kimlik))
    app.add_handler(CommandHandler("sus", cmd_sus))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


# ================================================================ kulaklar


def ears_line() -> str:
    st = ears.status()
    if not st["enabled"]:
        return "👂 Kulak: kapalı (/dinle)"
    if st["running"]:
        return f"👂 Kulak: dinliyor (\"{st['wake_words'][0]}\" de)"
    return f"👂 Kulak: çalışmıyor ({st['error'] or 'başlıyor'})"


def selfdev_line() -> str:
    st = selfdev.status()
    if not st["enabled"]:
        return "🛠️ Kendini geliştirme: kapalı (/gelisim ac)"
    if st["busy"]:
        return f"🛠️ Kendini geliştirme: şu an çalışıyor ({st['current'][:60]})"
    limit = f"/{st['max_per_day']}" if st["max_per_day"] > 0 else ""
    gh = "GitHub'a gönderiyor" if st["push"] else "sadece Pi'de (GitHub anahtarı yok)"
    return f"🛠️ Kendini geliştirme: açık, tam serbest (bugün {st['today']}{limit}, {gh})"


def oto_line() -> str:
    st = initiative.status()
    if not st["enabled"]:
        return "💭 Kendiliğinden davranma: kapalı (/oto ac)"
    eye = "açık" if st["presence"] else "kapalı"
    extra = f", son olay {st['last_event']}" if st["last_event"] else ""
    quiet = f", {st['quiet_hours']} arası sessiz" if st["quiet_hours"] else ""
    return (f"💭 Kendiliğinden davranma: açık (bugün {st['today']} mesaj{quiet}, "
            f"hareket algılama {eye}{extra})")


async def tell_owner(text: str = "", photo: bytes | None = None, kind: str = "robot", record: bool = True):
    """Sahibine Telegram'dan yazar ve uygulamanın olay akışına ekler; son Telegram mesajını döner."""
    if record and (text or photo is not None):
        record_event(kind, text, photo_ids=[store_photo(photo)] if photo is not None else [])
    oid = owner_id()
    if not TG or oid is None:
        return None
    msg = None
    try:
        if photo is not None:
            msg = await TG.bot.send_photo(oid, photo)
        for i in range(0, len(text), 4000):
            msg = await TG.bot.send_message(oid, text[i:i + 4000])
    except Exception as e:  # noqa: BLE001
        log.warning("Telegram'a yazılamadı: %s", e)
    return msg


async def on_wake() -> None:
    global _wake_msg
    brain.set_face("dinliyor", 15)
    if EARS_NOTIFY_WAKE:
        _wake_msg = await tell_owner("👂 Dinliyorum...", record=False)


async def _drop_wake_msg() -> None:
    global _wake_msg
    if _wake_msg is not None:
        with contextlib.suppress(Exception):
            await _wake_msg.delete()
    _wake_msg = None


async def on_heard(wav: bytes) -> None:
    """Uyandırma kelimesinden sonra söylenen cümle: yazıya çevir, düşün, cevabı sahibine yaz."""
    try:
        raw = await brain.transcribe(wav)
    except Exception as e:  # noqa: BLE001
        await tell_owner(f"🎤 Seni duydum ama sesi yazıya çeviremedim: {e}", kind="uyari")
        return
    if EARS_VERIFY and raw and not has_wake(raw):
        log.info("Yanlış alarm sayıldı (kelime yazıda yok): %s", raw[:80])
        await _drop_wake_msg()
        return
    text = strip_wake(raw)
    if not text:
        await tell_owner("🎤 Efendim? Seslendin ama devamını anlayamadım.", kind="uyari")
        return
    ears.heard(text)
    log.info("Sesli: %s", text)
    photo_ids: list[str] = []

    async def send_photo(img: bytes) -> None:
        photo_ids.append(store_photo(img))
        await tell_owner(photo=img, record=False)

    reply = await brain.chat(text, camera.photo, send_photo, "ses")
    record_event("ses", reply, heard=text, photo_ids=photo_ids)
    VOICE_LOG.append({"at": time.time(), "heard": text, "reply": reply,
                      "photos": [f"/api/photos/{p}" for p in photo_ids]})
    del VOICE_LOG[:-20]
    await tell_owner(f"🎤 {text}\n\n🤖 {reply}", record=False)


# ================================================================ arka plan: günlük ve beden takibi


async def diary_loop() -> None:
    """Her gece DIARY_TIME'da günlük yazar; kaçırılan dünkü günlüğü de açılışta tamamlar."""
    import datetime
    await asyncio.sleep(60)
    yesterday = (datetime.datetime.now(brain.TZ) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if brain.day_events(yesterday) and not brain.diary_text(yesterday):
        with contextlib.suppress(Exception):
            await brain.write_diary(yesterday)
    hh, mm = (int(x) for x in DIARY_TIME.split(":"))
    while True:
        now = datetime.datetime.now(brain.TZ)
        today = now.strftime("%Y-%m-%d")
        if (now.hour, now.minute) >= (hh, mm) and not brain.diary_text(today):
            try:
                text = await brain.write_diary(today)
                if text and DIARY_SHARE:
                    await tell_owner(f"📔 Bugünkü günlüğüm:\n\n{text}", kind="gunluk")
                if text:
                    await selfdev.reflect()   # günlükten sonra: kendimde geliştirmek istediğim bir şey var mı?
            except Exception as e:  # noqa: BLE001
                log.warning("Günlük yazılamadı: %s", e)
        await asyncio.sleep(300)


async def body_watch() -> None:
    """Bedenini izler: aşırı ısınırsa sahibini uyarır (saatte en fazla bir kez)."""
    last_alert = 0.0
    while True:
        t = body.cpu_temp()
        if t is not None and t >= TEMP_ALERT and time.time() - last_alert > 3600:
            last_alert = time.time()
            await tell_owner(f"🥵 Çok ısındım: {t:.0f} °C. Fanım çalışmıyor; beni serin bir yere koyabilir "
                             "ya da biraz dinlendirebilir misin?", kind="uyari")
        await asyncio.sleep(60)


# ================================================================ uygulama sunucusu

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    global TG
    tg = TG = build_telegram()
    if tg:
        await tg.initialize()
        await tg.start()
        await tg.updater.start_polling()
    watcher = asyncio.create_task(camera.idle_watcher())
    asyncio.create_task(brain.self_test())  # açılışta modelleri bir kez dene
    ears.start(asyncio.get_running_loop(), on_heard, on_wake)
    bg = [asyncio.create_task(diary_loop()), asyncio.create_task(body_watch())]

    async def keep_photo(img: bytes) -> None:
        store_photo(img)

    bg += initiative.start(tell_owner, camera, keep_photo)
    bg += gaze.start(camera)
    bg += selfdev.start(tell_owner)
    st = brain.model_status()
    log.info("Robot başladı. Claude=%s Gemini=%s OpenAI=%s Telegram=%s API=%s port=%d",
             st["claude"]["active"], st["gemini"]["active"], st["openai"]["active"],
             bool(tg), bool(API_TOKEN), PORT)
    try:
        yield
    finally:
        watcher.cancel()
        for t in bg:
            t.cancel()
        ears.stop()
        if tg:
            await tg.updater.stop()
            await tg.stop()
            await tg.shutdown()
        await camera.set_enabled(False)


api = FastAPI(title="Robot", lifespan=lifespan)


def require_token(request: Request) -> None:
    """Uygulama her istekte anahtarı gönderir: 'Authorization: Bearer ...' ya da ?token=..."""
    if not API_TOKEN:
        raise HTTPException(503, "API_TOKEN .env dosyasında ayarlanmamış")
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
        or request.query_params.get("token", "")
    if not secrets.compare_digest(given.encode(), API_TOKEN.encode()):
        raise HTTPException(401, "Geçersiz anahtar")


auth = [Depends(require_token)]


class ChatIn(BaseModel):
    text: str


class ChatPhotoIn(BaseModel):
    image: str          # base64 JPEG
    text: str = ""


class LookIn(BaseModel):
    question: str = "Ne görüyorsun?"


class ScreenAnswerIn(BaseModel):
    id: str
    approved: bool
    image: str = ""
    error: str = ""


class SelfDevIn(BaseModel):
    goal: str


class InitiativeIn(BaseModel):
    enabled: bool | None = None
    presence: bool | None = None


class PrivacyIn(BaseModel):
    camera: bool | None = None
    mic: bool | None = None


@api.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@api.get("/api/status", dependencies=auth)
async def status():
    return {
        "models": brain.health(),
        "cpu_temp": cpu_temp(),
        "uptime_s": int(time.time() - STARTED),
        "camera": {"enabled": camera.enabled, "active": camera.active},
        "ears": ears.status(),
        "initiative": initiative.status(),
        "selfdev": selfdev.status(),
    }


@api.post("/api/selftest", dependencies=auth)
async def selftest():
    """Her modele kısa bir istek gönderip gerçekten çalışıp çalışmadığını döner."""
    return {"models": await brain.self_test()}


@api.post("/api/chat", dependencies=auth)
async def chat(body: ChatIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Mesaj boş")
    photo_ids: list[str] = []

    async def send_photo(img: bytes) -> None:
        photo_ids.append(store_photo(img))

    reply = await brain.chat(text, camera.photo, send_photo, "uygulama")
    return {"reply": reply, "photos": [f"/api/photos/{p}" for p in photo_ids]}


@api.post("/api/chat_photo", dependencies=auth)
async def chat_photo(body: ChatPhotoIn):
    """Uygulamadan gönderilen fotoğrafa bakıp (istersen yazdığın mesajla birlikte) cevap verir."""
    import base64
    import binascii
    try:
        img = base64.b64decode(body.image, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Fotoğraf okunamadı")
    if not img:
        raise HTTPException(400, "Fotoğraf boş")
    if len(img) > 8 * 1024 * 1024:
        raise HTTPException(413, "Fotoğraf çok büyük (en fazla 8 MB)")
    caption = body.text.strip() or "Bu fotoğrafa ne dersin?"
    seen = await brain.describe_image(img, caption)
    photo_ids: list[str] = []

    async def send_photo(p: bytes) -> None:
        photo_ids.append(store_photo(p))

    reply = await brain.chat(f"[Sana bir fotoğraf gönderdim. Gözlerin şunu görüyor: {seen}]\n{caption}",
                             camera.photo, send_photo, "uygulama")
    return {"reply": reply, "photos": [f"/api/photos/{p}" for p in photo_ids]}


@api.post("/api/look", dependencies=auth)
async def look(body: LookIn):
    img = await camera.photo()
    if img is None:
        raise HTTPException(503, "Kamera çalışmıyor ya da kapalı")
    pid = store_photo(img)
    return {"description": await brain.describe_image(img, body.question), "photo": f"/api/photos/{pid}"}


@api.get("/api/photo", dependencies=auth)
async def photo():
    img = await camera.photo()
    if img is None:
        raise HTTPException(503, "Kamera çalışmıyor ya da kapalı")
    return Response(img, media_type="image/jpeg")


@api.get("/api/photos/{pid}", dependencies=auth)
async def stored_photo(pid: str):
    img = PHOTOS.get(pid)
    if img is None:
        raise HTTPException(404, "Fotoğraf bulunamadı")
    return Response(img, media_type="image/jpeg")


@api.get("/api/stream", dependencies=auth)
async def stream(request: Request):
    """Canlı görüntü (MJPEG). Tarayıcıda <img src> ile, uygulamada da doğrudan açılabilir."""
    if not camera.enabled:
        raise HTTPException(503, "Kamera kapalı (gizlilik)")

    async def frames():
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        while not await request.is_disconnected():
            img = await camera.frame()
            if img is None:
                break
            yield boundary + img + b"\r\n"
            await asyncio.sleep(1 / 10)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@api.get("/api/memory", dependencies=auth)
async def memory():
    mem = brain.load_memory()
    return {"facts": mem["facts"], "history": mem["history"]}


@api.delete("/api/history", dependencies=auth)
async def delete_history():
    brain.clear_history()
    return {"ok": True}


@api.post("/api/privacy", dependencies=auth)
async def privacy(body: PrivacyIn):
    if body.camera is not None:
        await camera.set_enabled(body.camera)
    if body.mic is not None:
        ears.set_enabled(body.mic)
    return {"camera": camera.enabled, "mic": ears.enabled}


@api.get("/api/events", dependencies=auth)
async def events(since: int = 0, boot: str = ""):
    """Robotun kendiliğinden yazdıkları, sesli konuşmalar, uyarılar ve günlükler (since'ten sonrakiler).
    boot farklıysa robot yeniden başlamıştır; uygulama sayacını sıfırlamalı."""
    if boot and boot != BOOT_ID:
        since = 0
    return {"boot": BOOT_ID, "events": [e for e in EVENTS if e["id"] > since][-50:],
            "last": EVENTS[-1]["id"] if EVENTS else 0}


@api.post("/api/initiative", dependencies=auth)
async def set_initiative(body: InitiativeIn):
    if body.enabled is not None:
        initiative.enabled = body.enabled
    if body.presence is not None:
        initiative.presence_on = body.presence
    return initiative.status()


@api.get("/api/selfdev", dependencies=auth)
async def selfdev_info():
    return {**selfdev.status(), "history": selfdev.history(20)}


@api.post("/api/selfdev", dependencies=auth)
async def selfdev_request(body: SelfDevIn):
    if not body.goal.strip():
        raise HTTPException(400, "Hedef boş")
    return {"message": selfdev.request(body.goal.strip(), "Sahibim uygulamadan istedi")}


@api.post("/api/selfdev/undo", dependencies=auth)
async def selfdev_undo():
    return {"message": await selfdev.undo_last()}


@api.post("/api/diary/write", dependencies=auth)
async def diary_write():
    text = await brain.write_diary()
    if not text:
        raise HTTPException(503, "Günlük yazılamadı, birazdan tekrar dene")
    return {"day": brain._day(), "text": text}


@api.get("/api/diary", dependencies=auth)
async def diary(day: str = ""):
    if day:
        return {"day": day, "text": brain.diary_text(day) or ""}
    d = brain.recent_diaries(1)
    return {"day": d[-1][0], "text": d[-1][1]} if d else {"day": "", "text": ""}


@api.get("/api/screen/poll", dependencies=auth)
async def screen_poll():
    """Laptoptaki RobotEkran programı birkaç saniyede bir sorar."""
    SCREEN["seen"] = time.time()
    return {"request": SCREEN["req"]} if SCREEN["req"] else {}


@api.post("/api/screen/answer", dependencies=auth)
async def screen_answer(body: ScreenAnswerIn):
    req, fut = SCREEN["req"], SCREEN["future"]
    if not req or body.id != req["id"] or fut is None or fut.done():
        raise HTTPException(409, "Bekleyen istek yok")
    if len(body.image) > 12 * 1024 * 1024:
        raise HTTPException(413, "Görüntü çok büyük")
    fut.set_result(body.model_dump())
    return {"ok": True}


@api.get("/api/self", dependencies=auth)
async def self_info():
    """Robotun kendini nasıl gördüğü: beden durumu, kimliği, son günlüğü."""
    f = brain.IDENTITY_FILE
    diaries = brain.recent_diaries(1)
    return {"body": body.snapshot(), "state": self_state(),
            "identity": f.read_text(encoding="utf-8") if f.exists() else "",
            "diary": {"day": diaries[-1][0], "text": diaries[-1][1]} if diaries else None}


@api.get("/api/ears", dependencies=auth)
async def ears_status():
    """Kulakların durumu ve son sesli konuşmalar."""
    return {**ears.status(), "log": VOICE_LOG}


@api.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("Beklenmeyen hata: %s", exc)
    return JSONResponse({"detail": "Robotta bir hata oluştu"}, status_code=500)


if __name__ == "__main__":
    uvicorn.run(api, host="0.0.0.0", port=PORT, log_level="warning")
