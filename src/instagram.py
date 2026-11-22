"""Instagram messaging: sending, photos, and reading Meta's webhook data."""
import hashlib
import hmac
import logging
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = (os.getenv("INSTAGRAM_ACCESS_TOKEN") or "").strip()
ACCOUNT_ID = (os.getenv("INSTAGRAM_ACCOUNT_ID") or "").strip()
API_VERSION = os.getenv("INSTAGRAM_API_VERSION", "v25.0")
PHOTOS_DIR = Path(os.getenv("PHOTOS_DIR") or ROOT / "photos")

MAX_PHOTO_BYTES = 8 * 1024 * 1024
# Instagram limits one message (about 1000 bytes). Arabic letters take 2 bytes,
# so we measure bytes, not letters, and stay below the limit.
MAX_MESSAGE_BYTES = 900


def get_base_url():
    # Tokens starting with "IG" work on graph.instagram.com.
    # System-user tokens (starting with "EAA") work on graph.facebook.com.
    if TOKEN.startswith("IG"):
        return f"https://graph.instagram.com/{API_VERSION}"
    return f"https://graph.facebook.com/{API_VERSION}"


# ---------------------------------------------------------------
# Sending
# ---------------------------------------------------------------

def _cut_point(text, max_bytes):
    """How many characters fit in max_bytes (UTF-8)."""
    used = 0
    for i, ch in enumerate(text):
        used += len(ch.encode("utf-8"))
        if used > max_bytes:
            return i
    return len(text)


def split_text(text, max_bytes=MAX_MESSAGE_BYTES):
    """Cut a long reply into several messages, each below the size limit.

    It cuts at line breaks when it can, so lists stay together.
    """
    text = (text or "").strip()
    if not text:
        return []
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate.encode("utf-8")) <= max_bytes:
            current = candidate
            continue
        if current.strip():
            chunks.append(current.strip())
        current = line
        while len(current.encode("utf-8")) > max_bytes:
            cut = max(1, _cut_point(current, max_bytes))
            chunks.append(current[:cut].strip())
            current = current[cut:]
    if current.strip():
        chunks.append(current.strip())
    return [c for c in chunks if c]


async def send_instagram_text(to, message):
    if not TOKEN:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is missing from .env")
    url = f"{get_base_url()}/{ACCOUNT_ID or 'me'}/messages"
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    last = None
    async with httpx.AsyncClient(timeout=20) as client:
        for chunk in split_text(message):
            payload = {"recipient": {"id": to}, "message": {"text": chunk}}
            response = await client.post(url, headers=headers, json=payload)
            if not response.is_success:
                logging.error("Instagram send failed: %s %s", response.status_code, response.text[:300])
            response.raise_for_status()
            last = response.json()
    return last


# ---------------------------------------------------------------
# Photos and profile
# ---------------------------------------------------------------

async def download_image(url, prefix):
    """Download a photo the customer sent. Returns the file path, or None if it failed."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code in (401, 403) and TOKEN:
                r = await client.get(url, headers={"Authorization": f"Bearer {TOKEN}"})
            r.raise_for_status()
            data = r.content
            ctype = r.headers.get("content-type", "")
    except Exception as e:  # network problems must never crash the chat
        logging.warning("Could not download a photo: %s", type(e).__name__)
        return None
    if not data or len(data) > MAX_PHOTO_BYTES:
        logging.warning("Photo skipped: empty or too big")
        return None
    ext = {
        "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif",
    }.get(ctype.split(";")[0].strip().lower(), ".jpg")
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", prefix)[:40]
    path = PHOTOS_DIR / f"{safe}_{int(time.time() * 1000)}{ext}"
    path.write_bytes(data)
    return str(path)


def get_profile_sync(igsid):
    """Name and username of a customer, so the owner can find the chat. Best effort: {} if it fails."""
    if not TOKEN or not igsid:
        return {}
    try:
        r = httpx.get(
            f"{get_base_url()}/{igsid}",
            params={"fields": "name,username"},
            headers={"Authorization": f"Bearer {TOKEN}"},
            timeout=10,
        )
        if r.is_success:
            data = r.json()
            return {"name": data.get("name"), "username": data.get("username")}
    except Exception as e:
        logging.warning("Could not read the customer profile: %s", type(e).__name__)
    return {}


# ---------------------------------------------------------------
# Reading Meta's webhook data (no web framework here, so it is easy to test)
# ---------------------------------------------------------------

def verify_signature(body, signature, secret):
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_events(data):
    """Instagram can send events under 'messaging' or under 'changes'."""
    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            yield event
        for change in entry.get("changes", []):
            if change.get("field") == "messages":
                yield change.get("value", {})


def parse_event(event, own_id=""):
    """Turn one webhook event into a simple dict, or None if the bot should ignore it.

    Ignored: our own messages (echoes, which include what the owner types in the app)
    and events that are not messages.
    """
    sender_id = (event.get("sender") or {}).get("id")
    message = event.get("message")
    if not sender_id or not isinstance(message, dict):
        return None
    if message.get("is_echo") or (own_id and sender_id == own_id):
        return None
    images, other = [], []
    for att in message.get("attachments") or []:
        kind = att.get("type") or "file"
        url = (att.get("payload") or {}).get("url")
        if kind == "image" and url:
            images.append(url)
        else:
            other.append(kind)
    return {
        "sender_id": sender_id,
        "mid": message.get("mid"),
        "text": (message.get("text") or "").strip(),
        "images": images,
        "other": other,
    }
