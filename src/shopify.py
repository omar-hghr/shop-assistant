import os
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

STORE = os.getenv("SHOPIFY_STORE")
CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")
VERSION = os.getenv("SHOPIFY_API_VERSION")

CACHE_SECONDS = 60

_token = {"value": None, "expires_at": 0}
_cache = {"products": None, "time": 0}

QUERY = """
{
  products(first: 50) {
    edges {
      node {
        title
        description
        status
        variants(first: 100) {
          edges {
            node {
              price
              inventoryQuantity
              selectedOptions { name value }
            }
          }
        }
      }
    }
  }
}
"""


def _get_token():
    # Reuse the token until 5 minutes before it expires
    if _token["value"] and time.time() < _token["expires_at"] - 300:
        return _token["value"]
    r = httpx.post(
        f"https://{STORE}.myshopify.com/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + int(data.get("expires_in") or 82800)
    return _token["value"]


def _fetch_from_shopify():
    r = httpx.post(
        f"https://{STORE}.myshopify.com/admin/api/{VERSION}/graphql.json",
        json={"query": QUERY},
        headers={"X-Shopify-Access-Token": _get_token()},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(body["errors"])

    products = []
    for edge in body["data"]["products"]["edges"]:
        node = edge["node"]
        if node["status"] != "ACTIVE":
            continue
        variants = []
        for v_edge in node["variants"]["edges"]:
            v = v_edge["node"]
            opts = {o["name"].lower(): o["value"] for o in v["selectedOptions"]}
            variants.append({
                "size": opts.get("size", ""),
                "color": opts.get("color", ""),
                "price_egp": float(v["price"]),
                "qty": v["inventoryQuantity"],
            })
        products.append({
            "name": node["title"],
            "description": node["description"],
            "variants": variants,
        })
    return products


def get_products():
    """Returns the product list, or None if Shopify fails."""
    now = time.time()
    if _cache["products"] is not None and now - _cache["time"] < CACHE_SECONDS:
        return _cache["products"]
    try:
        products = _fetch_from_shopify()
    except Exception as e:
        print(f"[shopify] error: {e}")
        return None
    _cache["products"] = products
    _cache["time"] = now
    return products


if __name__ == "__main__":
    items = get_products()
    if items is None:
        raise SystemExit("Shopify failed, see the error above")
    for p in items:
        print(p["name"])
        for v in p["variants"]:
            status = "in stock" if v["qty"] > 0 else "OUT"
            print(f"   {v['size']} / {v['color']}  {v['price_egp']} EGP  {status} ({v['qty']})")