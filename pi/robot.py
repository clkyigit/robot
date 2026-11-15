"""Robotun ana programı: uygulama sunucusu (FastAPI) + Telegram botu, tek süreçte.

Çalıştırma:  python robot.py
Ayarlar .env dosyasından okunur (bkz. README).
"""

import asyncio
import contextlib
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

BASE = pathlib.Path(__file__).resolve().parent
WEB_DIR = BASE / "web"
OWNER_FILE = BASE / "owner.json"
API_TOKEN = os.environ.get("API_TOKEN", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
PORT = int(os.environ.get("ROBOT_PORT", "8080"))
STARTED = time.time()

# Uygulamaya gönderilen son fotoğraflar (bellekte, en fazla 20)
PHOTOS: "OrderedDict[str, bytes]" = OrderedDict()


def store_photo(img: bytes) -> str:
    pid = uuid.uuid4().hex[:12]
    PHOTOS[pid] = img
    while len(PHOTOS) > 20:
        PHOTOS.popitem(last=False)
    return pid


def cpu_temp() -> float | None:
    try:
        return int(pathlib.Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


# ================================================================ Telegram

def build_telegram():
    if not TELEGRAM_TOKEN:
        log.warning("TELEGRAM_TOKEN yok, Telegram botu kapalı")
        return None

    from telegram import Update
    from telegram.constants import ChatAction
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    def owner_id():
        if OWNER_FILE.exists():
            return json.loads(OWNER_FILE.read_text())["id"]
        return None

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

        reply = await brain.chat(text, camera.photo, send_photo)
        await reply_long(update, reply)

    async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await answer(update, context, update.message.text)

    async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        file = await update.message.photo[-1].get_file()
        img = bytes(await file.download_as_bytearray())
        caption = update.message.caption or "Bu fotoğrafa ne dersin?"
        seen = await brain.describe_image(img, caption)
        await answer(update, context, f"[Sana bir fotoğraf gönderdim. Gözlerin şunu görüyor: {seen}]\n{caption}")

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await guard(update):
            await update.message.reply_text("Buradayım. Bana yazabilir, fotoğraf gönderebilirsin. "
                                            "Komutlar: /foto /hafiza /sifirla /durum")

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

    async def cmd_durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await guard(update):
            return
        st = brain.model_status()
        lines = [f"{'✅' if v['active'] else '❌'} {k} ({v['model']})" for k, v in st.items()]
        t = cpu_temp()
        if t is not None:
            lines.append(f"🌡️ {t:.1f} °C")
        lines.append(f"📷 Kamera: {'açık' if camera.enabled else 'kapalı (gizlilik)'}")
        await update.message.reply_text("\n".join(lines))

    app = (Application.builder().token(TELEGRAM_TOKEN)
           .connect_timeout(30).read_timeout(30).write_timeout(60).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("foto", cmd_foto))
    app.add_handler(CommandHandler("hafiza", cmd_hafiza))
    app.add_handler(CommandHandler("sifirla", cmd_sifirla))
    app.add_handler(CommandHandler("durum", cmd_durum))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


# ================================================================ uygulama sunucusu

@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    tg = build_telegram()
    if tg:
        await tg.initialize()
        await tg.start()
        await tg.updater.start_polling()
    watcher = asyncio.create_task(camera.idle_watcher())
    st = brain.model_status()
    log.info("Robot başladı. Claude=%s Gemini=%s OpenAI=%s Telegram=%s API=%s port=%d",
             st["claude"]["active"], st["gemini"]["active"], st["openai"]["active"],
             bool(tg), bool(API_TOKEN), PORT)
    try:
        yield
    finally:
        watcher.cancel()
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


class LookIn(BaseModel):
    question: str = "Ne görüyorsun?"


class PrivacyIn(BaseModel):
    camera: bool


@api.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


@api.get("/api/status", dependencies=auth)
async def status():
    return {
        "models": brain.model_status(),
        "cpu_temp": cpu_temp(),
        "uptime_s": int(time.time() - STARTED),
        "camera": {"enabled": camera.enabled, "active": camera.active},
    }


@api.post("/api/chat", dependencies=auth)
async def chat(body: ChatIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Mesaj boş")
    photo_ids: list[str] = []

    async def send_photo(img: bytes) -> None:
        photo_ids.append(store_photo(img))

    reply = await brain.chat(text, camera.photo, send_photo)
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
    await camera.set_enabled(body.camera)
    return {"camera": camera.enabled}


@api.exception_handler(Exception)
async def unhandled(_request: Request, exc: Exception):
    log.exception("Beklenmeyen hata: %s", exc)
    return JSONResponse({"detail": "Robotta bir hata oluştu"}, status_code=500)


if __name__ == "__main__":
    uvicorn.run(api, host="0.0.0.0", port=PORT, log_level="warning")
