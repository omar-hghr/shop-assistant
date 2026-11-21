
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v26.0")

BASE_URL = f"https://graph.facebook.com/{API_VERSION}"


async def send_text(to: str, message: str):
    if not TOKEN or not PHONE_NUMBER_ID:
        raise RuntimeError("WhatsApp credentials are missing from .env")

    print(f"SENDING REPLY TO: {to}")

    url = f"{BASE_URL}/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message,
        },
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        if not response.is_success:
            print("WHATSAPP ERROR:", response.status_code)
            print("ERROR DETAILS:", response.text)

        print("META RESPONSE:", response.text)
        response.raise_for_status()

        return response.json()
