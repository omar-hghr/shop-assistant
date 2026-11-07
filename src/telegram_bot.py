import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from assistant import create_chat

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0") or 0)

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # stops the token showing up in logs

HANDOFF_TAG = "[HANDOFF]"
FALLBACK = "حصلت مشكلة صغيرة عندنا، جرب تاني كمان شوية 🙏"

chats = {}  # one separate conversation per Telegram user


def get_chat(user_id):
    if user_id not in chats:
        chats[user_id] = create_chat()
    return chats[user_id]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أهلاً بيك! اسألني عن المنتجات أو الشحن أو الاستبدال.")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chats.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation reset.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        chat = get_chat(user.id)
        response = await asyncio.to_thread(chat.send_message, text)
        reply = response.text or ""
    except Exception:
        logging.exception("Model error")
        await update.message.reply_text(FALLBACK)
        return

    handoff = HANDOFF_TAG in reply
    reply = reply.replace(HANDOFF_TAG, "").strip() or FALLBACK
    await update.message.reply_text(reply)

    if handoff and OWNER_ID:
        await context.bot.send_message(
            OWNER_ID,
            f"Handoff needed\nCustomer: {user.full_name} (@{user.username}, id {user.id})\nLast message: {text}",
        )


def main():
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing in .env")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()