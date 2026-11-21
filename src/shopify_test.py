import os
import httpx
from dotenv import load_dotenv

load_dotenv()

STORE = os.getenv("SHOPIFY_STORE")
TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
VERSION = os.getenv("SHOPIFY_API_VERSION")

missing = [n for n, v in [("SHOPIFY_STORE", STORE),
                          ("SHOPIFY_ACCESS_TOKEN", TOKEN),
                          ("SHOPIFY_API_VERSION", VERSION)] if not v]
if missing:
    raise SystemExit(f"Missing in .env: {', '.join(missing)}")

URL = f"https://{STORE}.myshopify.com/admin/api/{VERSION}/graphql.json"

QUERY = """
{
  products(first: 5) {
    edges {
      node {
        title
        status
        variants(first: 20) {
          edges {
            node {
              title
              price
              inventoryQuantity
            }
          }
        }
      }
    }
  }
}
"""

r = httpx.post(
    URL,
    json={"query": QUERY},
    headers={"X-Shopify-Access-Token": TOKEN},
    timeout=30,
)
print(r.status_code)
print(r.text)