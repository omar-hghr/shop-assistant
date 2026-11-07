import json
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

from tools import check_stock, get_product_info

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    shop = json.load(f)

# Products are NOT pasted into the prompt anymore. The AI must use the tools.
shop_info = {k: v for k, v in shop.items() if k != "products"}

SYSTEM_PROMPT = f"""You are the customer support assistant for {shop["shop_name"]}, a clothing brand in Egypt.

Rules:
- Reply in the customer's language: Egyptian Arabic, Arabizi, or English. Keep replies short and friendly.
- For any product price, color, size or stock question, ALWAYS use the tools. Never answer from memory.
- For delivery, policies, payment and size guide, use ONLY the shop information below.
- Never invent prices, stock, sizes, or delivery times.
- If the answer is not available, say the team will confirm, and do not guess.
- Never promise discounts or refunds.

Shop information:
{json.dumps(shop_info, ensure_ascii=False, indent=2)}
"""

client = genai.Client()  # reads GEMINI_API_KEY from the environment
chat = client.chats.create(
    model="gemini-3.8-flash",
    config=types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[get_product_info, check_stock],
    ),
)


def ask(text, tries=3):
    """Send a message. If Google is busy, wait and try again."""
    for attempt in range(1, tries + 1):
        try:
            return chat.send_message(text).text
        except errors.APIError as e:
            print(f"[problem: {e.code}, attempt {attempt}/{tries}]")
            time.sleep(3 * attempt)
    return "Sorry, I'm having a technical problem. The team will reply to you soon."


if __name__ == "__main__":
    print("Assistant ready. Type 'exit' to stop.")
    while True:
        text = input("Customer: ")
        if text.strip().lower() == "exit":
            break
        print("Bot:", ask(text))