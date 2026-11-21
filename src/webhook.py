import os
import hmac
import hashlib
import logging
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

from assistant import create_chat, ask, FALLBACK
from whatsapp import send_text
from instagram import send_instagram_text

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")
APP_SECRET = os.getenv("WHATSAPP_APP_SECRET")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET") or APP_SECRET
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")

app = FastAPI(title="LocalBrands Webhook")

logging.basicConfig(level=logging.INFO)

# Temporary conversation memory.
# We will move this to persistent storage later.
chats = {}

# Message ids we already handled (Meta sometimes sends the same message twice)
processed_ids = set()


def get_chat(chat_key):
    if chat_key not in chats:
        chats[chat_key] = create_chat()
    return chats[chat_key]


def verify_signature(body: bytes, signature: str, secret: str):
    if not secret or not signature:
        return False

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature)


async def safe_send(sender, to: str, message: str):
    """Send a message without crashing if it fails."""
    try:
        await sender(to, message)
    except Exception:
        logging.exception("Failed to send message to %s", to)


async def handle_message(chat_key: str, customer_id: str, text: str, sender):
    """Runs in the background, after Meta already got its 200."""
    try:
        chat = get_chat(chat_key)

        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(ask, chat, text),
                timeout=40
            )
        except asyncio.TimeoutError:
            logging.error("Gemini timed out for %s", chat_key)
            reply = FALLBACK

        if reply:
            await safe_send(sender, customer_id, reply)

    except Exception:
        logging.exception("Failed to handle message from %s", chat_key)


def verify_challenge(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token and VERIFY_TOKEN:
        if hmac.compare_digest(token, VERIFY_TOKEN):
            return PlainTextResponse(challenge or "")

    raise HTTPException(status_code=403, detail="Verification failed")


# ---------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------

@app.get("/webhook")
async def verify_webhook(request: Request):
    return verify_challenge(request)


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(body, signature, APP_SECRET):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    try:
        data = await request.json()

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # Delivery updates: sent / delivered / read / failed
                for status in value.get("statuses", []):
                    logging.info(
                        "STATUS: %s to %s %s",
                        status.get("status"),
                        status.get("recipient_id"),
                        status.get("errors", ""),
                    )

                for message in value.get("messages", []):
                    customer_id = message.get("from")
                    message_id = message.get("id")
                    message_type = message.get("type")

                    if not customer_id:
                        continue

                    # Skip messages Meta sent twice
                    if message_id in processed_ids:
                        continue
                    processed_ids.add(message_id)

                    if message_type == "text":
                        text = message.get("text", {}).get("body", "")

                        if not text:
                            continue

                        logging.info(
                            "WhatsApp message received from %s: %s",
                            customer_id,
                            text
                        )

                        background_tasks.add_task(
                            handle_message,
                            "wa:" + customer_id,
                            customer_id,
                            text,
                            send_text,
                        )

                    elif message_type == "image":
                        background_tasks.add_task(
                            safe_send,
                            send_text,
                            customer_id,
                            "We received your image. Our team will review it and get back to you."
                        )

                    else:
                        logging.info(
                            "Unsupported message type: %s",
                            message_type
                        )

        return {"status": "received"}

    except Exception:
        logging.exception("Webhook processing failed")
        # Return 200 anyway, otherwise Meta keeps resending the same message
        return {"status": "error"}


# ---------------------------------------------------------------
# Instagram
# ---------------------------------------------------------------

@app.get("/instagram")
async def verify_instagram(request: Request):
    return verify_challenge(request)


def extract_instagram_events(data: dict):
    """Instagram can send events under 'messaging' or under 'changes'."""
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            yield event
        for change in entry.get("changes", []):
            if change.get("field") == "messages":
                yield change.get("value", {})


@app.post("/instagram")
async def receive_instagram(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_signature(body, signature, INSTAGRAM_APP_SECRET):
        raise HTTPException(
            status_code=401,
            detail="Invalid webhook signature"
        )

    try:
        data = await request.json()
        logging.info("INSTAGRAM PAYLOAD: %s", data)

        for event in extract_instagram_events(data):
            sender_id = event.get("sender", {}).get("id")
            message = event.get("message")

            if not sender_id or not message:
                continue

            # Ignore our own messages (echoes) and messages from our own account
            if message.get("is_echo") or sender_id == INSTAGRAM_ACCOUNT_ID:
                continue

            message_id = message.get("mid")
            if message_id in processed_ids:
                continue
            processed_ids.add(message_id)

            text = message.get("text")

            if text:
                logging.info(
                    "Instagram message received from %s: %s",
                    sender_id,
                    text
                )
                background_tasks.add_task(
                    handle_message,
                    "ig:" + sender_id,
                    sender_id,
                    text,
                    send_instagram_text,
                )
            else:
                background_tasks.add_task(
                    safe_send,
                    send_instagram_text,
                    sender_id,
                    "We received your message. Our team will review it and get back to you."
                )

        return {"status": "received"}

    except Exception:
        logging.exception("Instagram webhook processing failed")
        return {"status": "error"}


@app.get("/")
async def home():
    return {"status": "LocalBrands server is running"}