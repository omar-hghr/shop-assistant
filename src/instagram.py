import os
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = (os.getenv("INSTAGRAM_ACCESS_TOKEN") or "").strip()
ACCOUNT_ID = (os.getenv("INSTAGRAM_ACCOUNT_ID") or "").strip()
API_VERSION = "v25.0"


def get_base_url():
    # Tokens starting with "IG" work on graph.instagram.com.
    # System-user tokens (starting with "EAA") work on graph.facebook.com.
    if TOKEN.startswith("IG"):
        return f"https://graph.instagram.com/{API_VERSION}"
    return f"https://graph.facebook.com/{API_VERSION}"


async def send_instagram_text(to: str, message: str):
    if not TOKEN:
        raise RuntimeError("INSTAGRAM_ACCESS_TOKEN is missing from .env")

    print(f"SENDING INSTAGRAM REPLY TO: {to}")

    url = f"{get_base_url()}/{ACCOUNT_ID or 'me'}/messages"

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "recipient": {"id": to},
        "message": {"text": message[:1000]},  # Instagram limit
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(url, headers=headers, json=payload)

    print("INSTAGRAM RESPONSE:", response.status_code, response.text)
    response.raise_for_status()
    return response.json()