import json
import time
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    shop = json.load(f)

SYSTEM_PROMPT = f"""You are the customer support assistant for {shop["shop_name"]}, a clothing brand in Egypt.

Rules:
- Reply in the customer's language: Egyptian Arabic, Arabizi, or English. Keep replies short and friendly.
- Use ONLY the shop information below. Never invent prices, stock, sizes, or delivery times.
- If the answer is not in the information, say the team will confirm, and do not guess.
- Never promise discounts or refunds.

Shop information:
{json.dumps(shop, ensure_ascii=False, indent=2)}
"""

client = genai.Client()  # reads GEMINI_API_KEY from the environment
chat = client.chats.create(
    model="gemini-3.8-flash",
    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
)


def ask(text, tries=3):
    """Send a message. If Google is busy, wait and try again."""
    for attempt in range(1, tries + 1):
        try:
            return chat.send_message(text).text
        except errors.APIError as e:
            print(f"[problem: {e.code}, attempt {attempt}/{tries}]")
            time.sleep(3 * attempt)  # wait 3s, then 6s, then 9s
    return "Sorry, I'm having a technical problem. The team will reply to you soon."


print("Assistant ready. Type 'exit' to stop.")
while True:
    text = input("Customer: ")
    if text.strip().lower() == "exit":
        break
    print("Bot:", ask(text))