import json
from pathlib import Path
from google import genai
from google.genai import types

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

client = genai.Client()  # reads GEMINI_API_KEY
chat = client.chats.create(
    model="gemini-2.5-flash",  # use the current model name from AI Studio
    config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
)

print("Assistant ready. Type 'exit' to stop.")
while True:
    text = input("Customer: ")
    if text.strip().lower() == "exit":
        break
    print("Bot:", chat.send_message(text).text)