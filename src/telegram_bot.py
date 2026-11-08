import asyncio
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from stats import log_event

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from assistant import create_chat, ask, FALLBACK

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0") or 0)
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
HANDOFF_EMAILS = [e.strip() for e in os.getenv("HANDOFF_EMAILS", "").split(",") if e.strip()]

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # keeps the token out of the logs

HANDOFF_RE = re.compile(r"\[HANDOFF(?::\s*([^\]]*))?\]")
WAIT_MSG = "تمام، الفريق هيرد عليك هنا في أقرب وقت 🙏"
BACK_MSG = "لو محتاج أي حاجة تانية أنا موجود 😊"
PHOTO_MSG = "وصلتنا الصورة، وهنحولها للفريق وهيرد عليك قريب 🙏"

chats = {}          # customer id -> Gemini chat (one per customer)
transcripts = {}    # customer id -> list of (who, text)
handed_off = set()  # customers waiting for a human
relay_map = {}      # message id in the owner's chat -> customer id
human_notes = {}    # customer id -> what the team told them


def one_line(s):
    return " ".join(str(s).split())


def get_chat(user_id):
    if user_id not in chats:
        chats[user_id] = create_chat()
    return chats[user_id]


def log(user_id, who, text):
    transcripts.setdefault(user_id, []).append((who, text))


def recent(user_id, n=6):
    items = transcripts.get(user_id, [])[-n:]
    return "\n".join(f"{who}: {text[:300]}" for who, text in items)


def send_email(subject, body):
    """Blocking. Always call it with asyncio.to_thread."""
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and HANDOFF_EMAILS):
        logging.warning("Email not configured, skipping")
        return
    msg = EmailMessage()
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(HANDOFF_EMAILS)
    msg["Subject"] = one_line(subject)[:120]
    msg.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as server:
        server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
        server.send_message(msg)


async def notify_handoff(context, user, reason):
    reason = one_line(reason)
    log_event("handoff", user.id, reason)
    who = user.full_name + (f" (@{user.username})" if user.username else "")
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    body = (
        f"Handoff needed ({when})\n"
        f"Customer: {who} | Telegram id: {user.id}\n"
        f"Reason: {reason}\n\n"
        f"Last messages:\n{recent(user.id)}\n\n"
        "To answer: reply to the notification in Telegram.\n"
        "Reply with /resume when the bot can take over again."
    )

    # 1) Email to you and your friend (runs in a thread so the bot never freezes)
    try:
        await asyncio.to_thread(send_email, f"Handoff: {reason}", body)
    except Exception:
        logging.exception("Could not send handoff email")

    # 2) Telegram notification to the owner (the reply flow works from here)
    if OWNER_ID:
        msg = await context.bot.send_message(OWNER_ID, "🔔 " + body)
        relay_map[msg.message_id] = user.id
    else:
        logging.warning("OWNER_TELEGRAM_ID is not set, no Telegram notification")


async def forward_to_owner(context, user, text):
    if not OWNER_ID:
        return
    msg = await context.bot.send_message(OWNER_ID, f"💬 {user.full_name}: {text}")
    relay_map[msg.message_id] = user.id


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بيك! اسألني عن المنتجات أو الشحن أو الاستبدال.")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # TESTING ONLY: remove or make owner-only before real clients use it
    uid = update.effective_user.id
    for store in (chats, transcripts, human_notes):
        store.pop(uid, None)
    handed_off.discard(uid)
    await update.message.reply_text("Conversation reset.")


async def resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != OWNER_ID:
        return  # customers cannot use owner commands
    target = msg.reply_to_message
    customer_id = relay_map.get(target.message_id) if target else None
    if not customer_id:
        await msg.reply_text("Reply to a customer notification with /resume.")
        return
    handed_off.discard(customer_id)
    await msg.reply_text("✅ The bot will answer this customer again.")
    await context.bot.send_message(customer_id, BACK_MSG)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    text = msg.text
    if not text:
        return

    # 1) The owner replies to a notification: send the answer to that customer
    if user.id == OWNER_ID and msg.reply_to_message and msg.reply_to_message.message_id in relay_map:
        customer_id = relay_map[msg.reply_to_message.message_id]
        try:
            await context.bot.send_message(customer_id, text)
        except Exception:
            logging.exception("Could not deliver to customer")
            await msg.reply_text("⚠️ Could not deliver the message to the customer.")
            return
        log(customer_id, "Team", text)
        human_notes.setdefault(customer_id, []).append(text)
        await msg.reply_text("✅ Sent to the customer.")
        return

        # 2) Everyone else is a customer
    log(user.id, "Customer", text)
    log_event("message", user.id)

    if user.id in handed_off:  # waiting for a human: the bot stays quiet
        await forward_to_owner(context, user, text)
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    notes = human_notes.pop(user.id, None)
    prompt = text if not notes else (
        "[Internal note: the team already told this customer: " + " | ".join(notes) + "]\n" + text
    )
    try:
        reply = await asyncio.to_thread(ask, get_chat(user.id), prompt)
    except Exception:
        logging.exception("Unexpected error")
        await msg.reply_text(FALLBACK)
        return

    match = HANDOFF_RE.search(reply)
    reply = HANDOFF_RE.sub("", reply).strip()
    if not reply:
        reply = WAIT_MSG if match else FALLBACK
    await msg.reply_text(reply)
    log(user.id, "Bot", reply)

    if match:
        handed_off.add(user.id)
        await notify_handoff(context, user, (match.group(1) or "not specified").strip())


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Photos and files: the bot can't read them, so a human takes over."""
    user = update.effective_user
    msg = update.effective_message
    if user.id == OWNER_ID:
        return
    if user.id not in handed_off:
        handed_off.add(user.id)
        log(user.id, "Customer", "[sent a photo or file]")
        await notify_handoff(context, user, "customer sent a photo or file")
        await msg.reply_text(PHOTO_MSG)
    if OWNER_ID:
        fwd = await msg.forward(OWNER_ID)
        relay_map[fwd.message_id] = user.id


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing in .env")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("resume", resume))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_media))
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()