"""Robotun beyni: Telegram botu ve uygulama sunucusu aynı beyni kullanır.

Claude: ana sohbet, kişilik, araç kullanma
Gemini: görme (fotoğraf anlama), yedek
OpenAI: yedek

Ayarlar .env dosyasından okunur.
"""

import asyncio
import base64
import datetime
import json
import logging
import os
import pathlib
from typing import Awaitable, Callable, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger("robot.brain")

BASE = pathlib.Path(__file__).resolve().parent
MEMORY_FILE = BASE / "memory.json"
PERSONA_FILE = BASE / "persona.md"
PERSONA_EXAMPLE = BASE / "persona.example.md"
TZ = ZoneInfo(os.environ.get("ROBOT_TZ", "Europe/Istanbul"))

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
CLAUDE_FAST_MODEL = os.environ.get("CLAUDE_FAST_MODEL", "claude-haiku-5-5")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

HISTORY_LIMIT = 30          # hafızada tutulan son mesaj sayısı
FAST_MODEL_MAX_CHARS = 40   # bundan kısa mesajlar hızlı modele gider

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
    gemini = genai.Client(api_key=GEMINI_KEY)

openai_client = None
if OPENAI_KEY:
    import openai
    openai_client = openai.AsyncOpenAI(api_key=OPENAI_KEY)

# Telegram ve uygulama aynı anda yazarsa hafıza bozulmasın diye tek kilit
_think_lock = asyncio.Lock()


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


def system_prompt(mem: dict) -> str:
    if PERSONA_FILE.exists():
        persona = PERSONA_FILE.read_text(encoding="utf-8")
    elif PERSONA_EXAMPLE.exists():
        persona = PERSONA_EXAMPLE.read_text(encoding="utf-8")
    else:
        persona = "Sen bir masa robotusun."
    now = datetime.datetime.now(TZ).strftime("%d.%m.%Y %H:%M, %A")
    facts = "\n".join(f"- {f}" for f in mem["facts"]) or "(henüz yok)"
    return f"{persona}\n\nŞu an: {now}\n\nHatırladıkların:\n{facts}"


# ---------------------------------------------------------------- görme


async def describe_image(img: bytes, question: str) -> str:
    """Gemini ile, yoksa Claude ile görüntüyü anlat."""
    prompt = (f"Bu görüntü bir masa robotunun kamerasından ya da sahibinden geldi. Türkçe ve kısaca anlat. "
              f"Özellikle şu soruya odaklan: {question}")
    if gemini:
        try:
            resp = await gemini.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=[gtypes.Part.from_bytes(data=img, mime_type="image/jpeg"), prompt])
            if resp.text:
                return resp.text.strip()
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini görme hatası: %s", e)
    if claude:
        resp = await claude.messages.create(
            model=CLAUDE_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(img).decode()}},
                {"type": "text", "text": prompt}]}])
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    return "Görüntüyü anlayacak bir model bağlı değil."


# ---------------------------------------------------------------- araçlar

TOOLS = [
    {
        "name": "fotograf_cek",
        "description": "Kamerayla etrafına bakar. Fotoğraf sahibine de gönderilir. "
                       "Sahibin bir şeye bakmanı istediğinde ya da etrafı görmen gerektiğinde kullan.",
        "input_schema": {"type": "object", "properties": {
            "soru": {"type": "string", "description": "Fotoğrafta neye dikkat edileceği"}},
            "required": ["soru"]},
    },
    {
        "name": "hatirla",
        "description": "Sahibin hakkında ya da kendin hakkında kalıcı olarak hatırlaman gereken önemli bir bilgiyi kaydeder "
                       "(isim, tercih, karar, proje gibi). Geçici sohbet ayrıntılarını kaydetme.",
        "input_schema": {"type": "object", "properties": {
            "bilgi": {"type": "string", "description": "Tek cümlelik bilgi"}},
            "required": ["bilgi"]},
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
    if name == "hatirla":
        bilgi = args.get("bilgi", "").strip()
        if bilgi and bilgi not in mem["facts"]:
            mem["facts"].append(bilgi)
            save_memory(mem)
        return "Kaydedildi."
    return f"Bilinmeyen araç: {name}"


# ---------------------------------------------------------------- düşünme


async def _think_claude(system, history, user_text, mem, take_photo, send_photo) -> str:
    model = CLAUDE_FAST_MODEL if len(user_text) < FAST_MODEL_MAX_CHARS else CLAUDE_MODEL
    messages = history + [{"role": "user", "content": user_text}]
    for _ in range(5):  # en fazla 5 araç turu
        resp = await claude.messages.create(
            model=model, max_tokens=1024, system=system, tools=TOOLS, messages=messages)
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
    resp = await gemini.aio.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return (resp.text or "").strip()


async def chat(user_text: str, take_photo: TakePhoto, send_photo: PhotoSink) -> str:
    """Bir mesaja cevap üretir, sohbet geçmişini günceller."""
    async with _think_lock:
        mem = load_memory()
        system = system_prompt(mem)
        history = list(mem["history"])
        attempts = []
        if claude:
            attempts.append(("Claude", lambda: _think_claude(system, history, user_text, mem, take_photo, send_photo)))
        if openai_client:
            attempts.append(("OpenAI", lambda: _think_openai(system, history, user_text)))
        if gemini:
            attempts.append(("Gemini", lambda: _think_gemini(system, history, user_text)))
        answer = ""
        if not attempts:
            answer = "Henüz bir beynim yok. .env dosyasına en az bir API anahtarı eklenmeli."
        for name, fn in attempts:
            try:
                answer = await asyncio.wait_for(fn(), timeout=90)
                if answer:
                    if name != "Claude":
                        log.info("Cevap yedek modelden geldi: %s", name)
                    break
            except Exception as e:  # noqa: BLE001
                log.warning("%s hatası: %s", name, e)
        if not answer:
            answer = "Şu an düşünemiyorum, bağlantımda bir sorun var. Birazdan tekrar dener misin?"
        mem = load_memory()  # araçlar hafızaya yazmış olabilir
        mem["history"] += [{"role": "user", "content": user_text}, {"role": "assistant", "content": answer}]
        save_memory(mem)
        return answer
