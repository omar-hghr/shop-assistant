"""The heart of the bot. It does not know about Instagram's web format.

handle_text / handle_photo / handle_unsupported take one customer message and
return the text to send back, or None when the bot must stay silent.
They are blocking, so the webhook runs them in a thread.
"""
import logging
import os
import re
import secrets
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import assistant
import ctx
import db
import instagram
import mailer
from shopdata import SHOP

HANDOFF_RE = re.compile(r"\[HANDOFF(?::\s*([^\]]*))?\]")

FALLBACK = assistant.FALLBACK
WAIT_MSG = "تمام، الفريق هيرد عليك هنا في أقرب وقت 🙏\nOkay, the team will reply to you here soon."
PHOTO_MSG = "وصلتنا الصورة ✅\nGot your photo ✅"
UNSUPPORTED_MSG = ("ممكن تكتبلي سؤالك؟ مش بقدر أسمع الرسائل الصوتية أو أفتح الفيديوهات 🙏\n"
                   "Could you type your question? I can't open voice notes or videos.")
INTRO = f"🤖 المساعد الآلي لـ {SHOP['shop_name']} | Automated assistant\n"
PHOTO_NOTE = "[The customer sent a photo. It is saved.]"

MAX_TEXT_CHARS = 1500


def _int_env(name, default):
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def _settings():
    """Read each time, so tests (and a restart-free .env change) work."""
    return {
        "max_per_hour": _int_env("MAX_MESSAGES_PER_HOUR", 40),
        "auto_resume": _int_env("HANDOFF_AUTO_RESUME_HOURS", 12),
        "public_url": (os.getenv("PUBLIC_URL") or "").strip().rstrip("/"),
        "tz": os.getenv("REPORT_TZ") or "Africa/Cairo",
    }


# One lock per customer: their messages are handled one at a time, in order.
_locks = {}
_locks_guard = threading.Lock()


def _chat_lock(chat_key):
    with _locks_guard:
        return _locks.setdefault(chat_key, threading.Lock())


def _clean_reason(text):
    return " ".join(str(text or "not specified").split())[:80] or "not specified"


# ---------------------------------------------------------------
# Handoff: tell the owner, and stop answering this customer
# ---------------------------------------------------------------

def _request_lines(req):
    kind = "Exchange" if req["kind"] == "exchange" else "Return"
    days_note = "checked in Shopify" if req["days_verified"] else "customer's word, NOT verified"
    lines = [
        f"{kind} request {req['code']} - order {req['order']}",
        f"  Item: {req['item']}",
    ]
    if req["kind"] == "exchange":
        lines.append(f"  Wanted instead: {req['wanted']}")
    lines += [
        f"  Reason: {req['reason']}",
        f"  Days since delivery: {req['days']} ({days_note})",
        f"  Phone on the order: {req['phone']}",
        f"  Order found in Shopify and phone matched: {'yes' if req['order_verified'] else 'NO (not checked)'}",
    ]
    return lines


def _start_handoff(chat_key, reason, requests):
    """Open a handoff and email the owner. Runs inside the customer's lock."""
    s = _settings()
    token = secrets.token_urlsafe(16)
    handoff, created = db.open_handoff(chat_key, reason, token)
    if not created:
        return  # a human already has this chat

    if not requests:
        db.log_event("handoff", chat_key, reason)

    igsid = chat_key.split(":", 1)[-1]
    profile = instagram.get_profile_sync(igsid)
    who = profile.get("name") or "unknown name"
    if profile.get("username"):
        who += f" (@{profile['username']})"

    tz = ZoneInfo(s["tz"])
    when = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    attachments, no_file = [], []
    body = [f"Handoff needed ({when})", f"Customer: {who} | Instagram id: {igsid}", f"Reason: {reason}", ""]
    for req in requests:
        body += _request_lines(req)
        photos = db.photos_for_request(req["code"])
        body.append(f"  Photos: {len(photos)}")
        for p in photos:
            if p["path"]:
                attachments.append(p["path"])
            else:
                no_file.append(p["url"])
        body.append("")
    if no_file:
        body.append("Some photos could not be saved. Open them quickly, the links expire:")
        body += [f"  {u}" for u in no_file] + [""]

    body += ["Last messages:", db.recent_transcript(chat_key) or "(none)", "",
             "Reply to the customer in the Instagram app. The bot stays quiet in this chat."]
    if s["public_url"]:
        body.append(f"When the bot should answer again, open: {s['public_url']}/resume/{token}")
    if s["auto_resume"]:
        body.append(f"Otherwise the bot takes the chat back by itself after {s['auto_resume']} hours.")

    if requests:
        first = requests[0]
        label = "Exchange" if first["kind"] == "exchange" else "Return"
        subject = f"{label} request {first['code']} - order {first['order']}"
    else:
        subject = f"Handoff: {reason}"

    if not mailer.send_email(subject, "\n".join(body), attachments):
        db.log_event("email_failed", chat_key, reason)


# ---------------------------------------------------------------
# Messages
# ---------------------------------------------------------------

def handle_text(chat_key, text):
    """One text message from a customer. Returns the reply, or None to stay silent."""
    text = (text or "").strip()
    if not text:
        return None
    s = _settings()
    with _chat_lock(chat_key):
        db.log_event("message", chat_key)

        if db.is_paused(chat_key, s["auto_resume"]):
            return None  # a human has this chat

        if db.count_recent_messages(chat_key) > s["max_per_hour"]:
            db.log_event("error", chat_key, "too many messages in one hour")
            return None  # protects the model quota from spam

        chat = db.load_chat(chat_key)
        is_new = chat is None
        if is_new:
            chat = assistant.create_chat()
        else:
            # Always use the newest prompt, so changes in shop.json reach old chats too.
            chat[0] = {"role": "system", "content": assistant.SYSTEM_PROMPT}

        turn = ctx.Turn(chat_key)
        token = ctx.current_turn.set(turn)
        try:
            reply = assistant.ask(chat, text[:MAX_TEXT_CHARS])
        finally:
            ctx.current_turn.reset(token)
        db.save_chat(chat_key, chat)

        if reply == FALLBACK:
            db.log_event("error", chat_key, "model or system problem")

        match = HANDOFF_RE.search(reply)
        reply = HANDOFF_RE.sub("", reply).strip()

        reason = None
        if turn.requests:
            first = turn.requests[0]
            reason = f"{first['kind']} {first['code']}, order {first['order']}"
        elif match:
            reason = _clean_reason(match.group(1))

        if reason is not None:
            if not reply:
                reply = WAIT_MSG
            try:
                _start_handoff(chat_key, reason, turn.requests)
            except Exception:
                logging.exception("Handoff failed for %s", chat_key)
                db.log_event("error", chat_key, "handoff failed")

        if is_new:
            reply = INTRO + reply
        return reply


def handle_photo(chat_key, path, url):
    """A photo from a customer. It is saved. Returns a short thanks, or None to stay silent."""
    s = _settings()
    with _chat_lock(chat_key):
        db.log_event("message", chat_key, "[photo]")
        db.add_photo(chat_key, path, url)
        if db.is_paused(chat_key, s["auto_resume"]):
            return None
        chat = db.load_chat(chat_key)
        if chat is not None:   # no chat yet: nothing to tell the model, the tools read the photo table
            chat.append({"role": "user", "content": PHOTO_NOTE})
            db.save_chat(chat_key, chat)
        return PHOTO_MSG


def handle_unsupported(chat_key):
    """Voice notes, videos, shared posts: the bot cannot read them."""
    s = _settings()
    with _chat_lock(chat_key):
        db.log_event("message", chat_key, "[unsupported]")
        if db.is_paused(chat_key, s["auto_resume"]):
            return None
        return UNSUPPORTED_MSG
