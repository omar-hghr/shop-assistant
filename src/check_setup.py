"""Checks that your .env works, before you go live. It never prints secrets.

    python check_setup.py
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

problems = 0


def say(ok, text):
    global problems
    if not ok:
        problems += 1
    print(("  OK   " if ok else "  FIX  ") + text)


def has(name):
    return bool((os.getenv(name) or "").strip())


print("1) Model")
say(has("GROQ_API_KEY"), "GROQ_API_KEY is set")

print("2) Email (owner alerts and the daily report)")
say(has("EMAIL_SENDER") and has("EMAIL_APP_PASSWORD"), "EMAIL_SENDER and EMAIL_APP_PASSWORD are set")
say(has("OWNER_EMAILS") or has("HANDOFF_EMAILS"), "OWNER_EMAILS is set")

print("3) Instagram")
say(has("INSTAGRAM_ACCESS_TOKEN") and has("INSTAGRAM_ACCOUNT_ID"), "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID are set")
say(has("INSTAGRAM_APP_SECRET") or has("WHATSAPP_APP_SECRET"), "INSTAGRAM_APP_SECRET is set (needed to accept Meta's messages)")
say(has("INSTAGRAM_VERIFY_TOKEN") or has("WHATSAPP_VERIFY_TOKEN"), "INSTAGRAM_VERIFY_TOKEN is set")
say(has("PUBLIC_URL"), "PUBLIC_URL is set (used for the 'resume the bot' link in emails)")
if has("INSTAGRAM_ACCESS_TOKEN"):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import instagram
    try:
        r = httpx.get(f"{instagram.get_base_url()}/me", params={"fields": "id,username"},
                      headers={"Authorization": f"Bearer {instagram.TOKEN}"}, timeout=15)
        data = r.json() if r.content else {}
        say(r.is_success, f"Instagram token works (account: {data.get('username', '?')})" if r.is_success
            else f"Instagram token was refused: {str(data)[:150]}")
    except Exception as e:
        say(False, f"Could not reach Instagram: {type(e).__name__}")

print("4) Shopify")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shopify

if not shopify.is_configured():
    say(False, "Shopify is not set up (SHOPIFY_STORE, SHOPIFY_API_VERSION and a login). The bot would run in DEMO mode with data/shop.json")
else:
    products = shopify.get_products()
    say(products is not None, f"Products read: {len(products)}" if products is not None else "Could not read products (see the error above)")
    order_no = input("   Type a real order number from the shop to test (or press Enter to skip): ").strip()
    if order_no:
        try:
            order = shopify.get_order(order_no)
            if order is None:
                say(False, "Order not found (orders older than 60 days are not visible without the read_all_orders permission)")
            else:
                say(True, f"Order found. Items: {len(order['items'])}. Tracking numbers: {len(order['tracking'])}")
                say(bool(order["phones"]),
                    "The order phone number is visible, so customers can be checked" if order["phones"]
                    else "The order phone number is HIDDEN by Shopify. The bot cannot check customers. Allow protected customer data (phone) for the app in the Shopify Dev Dashboard")
                say(bool(order["delivered_at"]) or bool(order["shipped_at"]),
                    "Delivery or shipping date is available" if (order["delivered_at"] or order["shipped_at"])
                    else "This order has no shipping date yet (fine if it is not shipped)")
        except shopify.ShopifyError as e:
            say(False, f"Shopify refused the order request: {e}")

print()
print("All good." if problems == 0 else f"{problems} thing(s) to fix before going live.")
