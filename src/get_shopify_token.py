import os
import httpx
from dotenv import load_dotenv

load_dotenv()

STORE = os.getenv("SHOPIFY_STORE")
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

missing = [n for n, v in [("SHOPIFY_STORE", STORE),
                          ("SHOPIFY_CLIENT_ID", CLIENT_ID),
                          ("SHOPIFY_CLIENT_SECRET", CLIENT_SECRET)] if not v]
if missing:
    raise SystemExit(f"Missing in .env: {', '.join(missing)}")

r = httpx.post(
    f"https://{STORE}.myshopify.com/admin/oauth/access_token",
    data={
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    },
    timeout=30,
)
print(r.status_code)
data = r.json()
print("scope:", data.get("scope"))
print("expires_in:", data.get("expires_in"))
token = data.get("access_token")
# Never print the token itself: it can end up in screenshots and logs.
print("token received:", bool(token))