import json
import os
import time
import threading
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq, APIStatusError, APIConnectionError

from tools import check_stock, create_exchange_request, get_product_info

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

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
- If you cannot answer from the information, or the customer is angry, asks for a human, or asks about a refund or a discount, reply briefly that the team will contact them, and end your message with the tag [HANDOFF: reason], where reason is 2 to 5 words in English, for example [HANDOFF: refund request]. Never use this tag in any other case.
- For an exchange request: ask the customer, one or two questions at a time, for the order number, the phone number on the order, the item, the reason, what they want instead (check that it is in stock with the tools), how many days ago they received it, and whether it is unworn with tags attached. Never guess any of these. Then call create_exchange_request. If it is submitted, tell the customer the team will review it and confirm (never promise approval), and end your message with [HANDOFF: exchange EX-0001, order 1001, Black Hoodie L to XL] using the real request id, order number and items. If it is not submitted, explain the reason politely based on the policy.

Shop information:
{json.dumps(shop_info, ensure_ascii=False, indent=2)}
"""

FALLBACK = "حصلت مشكلة تقنية صغيرة عندنا. الفريق هيرد عليك قريب 🙏"
RETRY_CODES = {429, 500, 502, 503, 504}

# Wait times (seconds) before retry 2, 3 and 4.
WAITS = [10, 20, 30]

MAX_TOOL_ROUNDS = 5   # max tool calls loops for one customer message
MAX_HISTORY = 40      # messages kept per customer

# At most 2 model requests at the same time, so messages don't fight over the quota.
model_slots = threading.Semaphore(2)

client = Groq()  # reads GROQ_API_KEY from the environment


# ---------------------------------------------------------------
# Tool descriptions (Groq needs these written out)
# ---------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_product_info",
            "description": "Return the price, colors, sizes and description of a product.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Product name, for example 'black hoodie'"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_stock",
            "description": "Say if a product is in stock in a given size.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Product name"},
                    "size": {"type": "string", "description": "Size, for example M, L or XL"},
                },
                "required": ["name", "size"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_exchange_request",
            "description": (
                "Record an exchange request for the team to review. Call it ONLY after you "
                "have ALL of these from the customer: order number, phone number on the order, "
                "the item, the reason, what they want instead, how many days ago they received "
                "the order, and whether the item is unworn with tags attached. Never guess any "
                "of them. This does NOT approve the exchange. A human decides."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "order_number": {"type": "string"},
                    "phone": {"type": "string", "description": "Phone number on the order"},
                    "item": {"type": "string"},
                    "reason": {"type": "string"},
                    "wanted": {"type": "string", "description": "What the customer wants instead"},
                    "days_since_delivery": {"type": "integer"},
                    "unworn_with_tags": {"type": "boolean"},
                },
                "required": [
                    "order_number", "phone", "item", "reason",
                    "wanted", "days_since_delivery", "unworn_with_tags",
                ],
            },
        },
    },
]

FUNCTIONS = {
    "get_product_info": get_product_info,
    "check_stock": check_stock,
    "create_exchange_request": create_exchange_request,
}


def run_tool(name, raw_args):
    """Run one tool the model asked for. Errors go back to the model, not crash."""
    func = FUNCTIONS.get(name)
    if func is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        args = json.loads(raw_args or "{}")

        # Some models send numbers/booleans as text, so fix the types.
        if name == "create_exchange_request":
            if "days_since_delivery" in args:
                args["days_since_delivery"] = int(args["days_since_delivery"])
            value = args.get("unworn_with_tags")
            if isinstance(value, str):
                args["unworn_with_tags"] = value.strip().lower() in ("true", "yes", "1")

        return func(**args)
    except Exception as e:
        return {"error": f"Tool call failed: {e}"}


# ---------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------

def create_chat():
    """Creates a NEW conversation for one customer. It is a list of messages."""
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _trim_history(chat):
    """Keep the system prompt and the last messages, starting from a user message."""
    if len(chat) <= MAX_HISTORY + 1:
        return
    system = chat[0]
    rest = chat[-MAX_HISTORY:]
    while rest and rest[0]["role"] != "user":
        rest.pop(0)
    chat[:] = [system] + rest


def _run_turn(chat, text):
    """One customer message: call the model, run tools, return the final text."""
    chat.append({"role": "user", "content": text})

    for _ in range(MAX_TOOL_ROUNDS):
        with model_slots:
            response = client.chat.completions.create(
                model=MODEL,
                messages=chat,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.3,
                max_tokens=700,
            )
        message = response.choices[0].message

        if not message.tool_calls:
            return message.content or ""

        chat.append({
            "role": "assistant",
            "content": message.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in message.tool_calls
            ],
        })

        for call in message.tool_calls:
            result = run_tool(call.function.name, call.function.arguments)
            chat.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    return ""  # too many tool rounds


def ask(chat, text, tries=4):
    """Send a message to this customer's chat. If the API is busy, wait and retry."""
    start = len(chat)

    for attempt in range(1, tries + 1):
        try:
            reply = _run_turn(chat, text).strip()
            _trim_history(chat)
            return reply or FALLBACK

        except (APIStatusError, APIConnectionError) as e:
            del chat[start:]  # undo the half-finished turn
            code = getattr(e, "status_code", None)
            print(f"[problem: {code}, attempt {attempt}/{tries}] {str(e)[:200]}")

            retryable = (
                code is None
                or code in RETRY_CODES
                or "tool_use_failed" in str(e)
            )
            if not retryable or attempt == tries:
                break

            wait = WAITS[attempt - 1]
            print(f"[waiting {wait}s before retry]")
            time.sleep(wait)

        except Exception as e:
            del chat[start:]
            print(f"[unexpected error: {e!r}]")
            break

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