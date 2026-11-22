"""Shopify (read only): products, stock and orders.

Login: if SHOPIFY_ACCESS_TOKEN is set it is used as it is. If not, the
client credentials login is used (works only for a store in your own
Shopify organization, for example a client who made the app in their own
Dev Dashboard, or your dev store). That token lasts 24 hours, so a new one
is requested when needed.
"""
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from textutil import digits_only

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CACHE_SECONDS = 60
MAX_PRODUCT_PAGES = 10   # 10 pages x 50 = 500 products

_token = {"value": None, "expires_at": 0}
_cache = {"products": None, "time": 0}


class ShopifyError(Exception):
    """Shopify could not be reached or refused the request."""


def _cfg(name):
    return (os.getenv(name) or "").strip()


def _store():
    return _cfg("SHOPIFY_STORE").replace(".myshopify.com", "")


def is_configured():
    """False in demo mode (no Shopify settings in .env)."""
    has_login = _cfg("SHOPIFY_ACCESS_TOKEN") or (_cfg("SHOPIFY_CLIENT_ID") and _cfg("SHOPIFY_CLIENT_SECRET"))
    return bool(_store() and _cfg("SHOPIFY_API_VERSION") and has_login)


def _get_token():
    static = _cfg("SHOPIFY_ACCESS_TOKEN")
    if static:
        return static
    # Reuse the token until 5 minutes before it expires
    if _token["value"] and time.time() < _token["expires_at"] - 300:
        return _token["value"]
    try:
        r = httpx.post(
            f"https://{_store()}.myshopify.com/admin/oauth/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": _cfg("SHOPIFY_CLIENT_ID"),
                "client_secret": _cfg("SHOPIFY_CLIENT_SECRET"),
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ShopifyError(f"Could not get a Shopify token: {type(e).__name__}") from e
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + int(data.get("expires_in") or 82800)
    return _token["value"]


def _graphql(query, variables=None):
    """Run one GraphQL request. Returns (data, errors). Raises ShopifyError if there is no data."""
    if not is_configured():
        raise ShopifyError("Shopify is not configured in .env")
    url = f"https://{_store()}.myshopify.com/admin/api/{_cfg('SHOPIFY_API_VERSION')}/graphql.json"
    try:
        r = httpx.post(
            url,
            json={"query": query, "variables": variables or {}},
            headers={"X-Shopify-Access-Token": _get_token()},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise ShopifyError(f"Shopify request failed: {type(e).__name__}") from e
    data = body.get("data")
    errors = body.get("errors") or []
    if not data:
        raise ShopifyError(f"Shopify returned errors: {str(errors)[:300]}")
    return data, errors


# ---------------------------------------------------------------
# Products and stock
# ---------------------------------------------------------------

PRODUCTS_QUERY = """
query($cursor: String) {
  products(first: 50, after: $cursor) {
    pageInfo { hasNextPage endCursor }
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


def _fetch_products():
    products, cursor = [], None
    for _page in range(MAX_PRODUCT_PAGES):
        data, _errors = _graphql(PRODUCTS_QUERY, {"cursor": cursor})
        page = data["products"]
        for edge in page["edges"]:
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
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    return products


def get_products():
    """Returns the product list, or None if Shopify fails."""
    now = time.time()
    if _cache["products"] is not None and now - _cache["time"] < CACHE_SECONDS:
        return _cache["products"]
    try:
        products = _fetch_products()
    except ShopifyError as e:
        logging.error("[shopify] %s", e)
        return None
    _cache["products"] = products
    _cache["time"] = now
    return products


# ---------------------------------------------------------------
# Orders (read_orders gives the last 60 days only)
# ---------------------------------------------------------------

# The phone fields are "protected customer data". If the app is not approved
# for them, Shopify sends null for them (and an entry in "errors").
ORDER_QUERY = """
query($q: String!) {
  orders(first: 5, query: $q) {
    edges {
      node {
        name
        createdAt
        cancelledAt
        displayFinancialStatus
        displayFulfillmentStatus
        phone
        shippingAddress { phone }
        billingAddress { phone }
        lineItems(first: 50) {
          edges { node { title variantTitle quantity } }
        }
        fulfillments(first: 10) {
          displayStatus
          createdAt
          deliveredAt
          trackingInfo(first: 5) { company number url }
        }
      }
    }
  }
}
"""


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_order(node):
    phones = []
    for p in (
        node.get("phone"),
        (node.get("shippingAddress") or {}).get("phone"),
        (node.get("billingAddress") or {}).get("phone"),
    ):
        if p:
            phones.append(p)

    items = [
        {"title": e["node"]["title"], "variant": e["node"].get("variantTitle") or "", "qty": e["node"]["quantity"]}
        for e in (node.get("lineItems") or {}).get("edges", [])
    ]

    delivered, shipped, tracking = [], [], []
    for f in node.get("fulfillments") or []:
        d = _parse_dt(f.get("deliveredAt"))
        if d:
            delivered.append(d)
        s = _parse_dt(f.get("createdAt"))
        if s:
            shipped.append(s)
        for t in f.get("trackingInfo") or []:
            tracking.append({"company": t.get("company"), "number": t.get("number"), "url": t.get("url")})

    return {
        "name": node["name"],
        "created_at": node["createdAt"],
        "cancelled": bool(node.get("cancelledAt")),
        "payment_status": node.get("displayFinancialStatus"),
        "fulfillment_status": node.get("displayFulfillmentStatus"),
        "phones": phones,
        "items": items,
        "delivered_at": max(delivered) if delivered else None,
        "shipped_at": min(shipped) if shipped else None,
        "tracking": tracking,
    }


def get_order(order_number):
    """Find an order by its number (1001 or #1001). Returns a dict, or None if not found.

    Raises ShopifyError if Shopify cannot be reached or refuses the request.
    """
    digits = digits_only(order_number)
    if not digits:
        return None
    data, errors = _graphql(ORDER_QUERY, {"q": f"name:#{digits}"})
    if data.get("orders") is None:
        raise ShopifyError(f"Shopify did not return orders: {str(errors)[:300]}")
    if errors:
        # Usually: the app is not approved for protected customer data (phone).
        logging.warning("[shopify] order query returned errors: %s", str(errors)[:300])
    for edge in data["orders"]["edges"]:
        node = edge["node"]
        if digits_only(node["name"]) == digits:
            return _normalize_order(node)
    return None


if __name__ == "__main__":
    items = get_products()
    if items is None:
        raise SystemExit("Shopify failed, see the error above")
    for p in items:
        print(p["name"])
        for v in p["variants"]:
            status = "in stock" if v["qty"] > 0 else "OUT"
            print(f"   {v['size']} / {v['color']}  {v['price_egp']} EGP  {status} ({v['qty']})")
