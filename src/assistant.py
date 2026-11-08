import json
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from tools import check_stock, get_product_info

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = "gemini-3.8-flash"

with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    shop = json.load(f)

# Products come only from the tools, not from the prompt.
shop_info = {k: v for k, v in shop.items() if k != "products"}

SYSTEM_PROMPT = f"""You are the customer support assistant for {shop["shop_name"]}, a clothing brand in Egypt.

Rules:
- Reply in the customer's language: Egyptian Arabic, Arabizi, or English. Keep replies short and friendly.
- For any product price, color, size or stock question, ALWAYS use the tools. Never answer from memory.
- For delivery, policies, payment and size guide, use ONLY the shop information below.
- Never invent prices, stock, sizes, or delivery times.
- If the answer is not available, say the team will confirm, and do not guess.
- Never promise discounts or refunds.
- If you cannot answer, or the customer is angry, asks for a human, or asks about a refund or a discount, reply briefly that the team will contact them, and add the exact tag [HANDOFF] at the end of your message. Never use [HANDOFF] in any other case.
- If you cannot answer from the information, or the customer is angry, asks for a human, or asks about a refund, reply briefly that the team will contact them, and end your message with the tag [HANDOFF: reason], where reason is 2 to 5 words in English, for example [HANDOFF: refund request]. Never use this tag in any other case.

Shop information:
{json.dumps(shop_info, ensure_ascii=False, indent=2)}
"""

FALLBACK = "حصلت مشكلة تقنية صغيرة عندنا. الفريق هيرد عليك قريب 🙏"
RETRY_CODES = {429, 500, 502, 503, 504}

client = genai.Client()  # reads GEMINI_API_KEY from the environment


def create_chat():
    """Creates a NEW conversation with its own memory (one per customer)."""
    return client.chats.create(
        model=MODEL,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=[get_product_info, check_stock],
        ),
    )


def ask(chat, text, tries=3):
    """Send a message to this customer's chat. If Google is busy, wait and retry."""
    for attempt in range(1, tries + 1):
        try:
            return chat.send_message(text).text or FALLBACK
        except errors.APIError as e:
            print(f"[problem: {e.code}, attempt {attempt}/{tries}]")
            if e.code not in RETRY_CODES or attempt == tries:
                break
            time.sleep(3 * attempt)
    return FALLBACK


def main():
    chat = create_chat()
    print("Assistant ready. Type 'exit' to stop.")
    while True:
        text = input("Customer: ")
        if text.strip().lower() == "exit":
            break
        print("Bot:", ask(chat, text))


if __name__ == "__main__":
    main()