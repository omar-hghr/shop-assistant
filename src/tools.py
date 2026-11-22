"""The things the assistant can do: look up products, check stock, check an order,
and record exchange / return requests.

Rules built into the code (the model cannot skip them):
- An order is only shown after the order number AND the phone on the order match.
- After 5 wrong checks in one hour the chat is locked for orders.
- An exchange or a return needs a photo from the customer.
- The number of days is taken from Shopify when Shopify knows the delivery date.
- Nothing is approved here. A human always makes the final decision.
"""
import logging
from datetime import datetime, timezone

import ctx
import db
import shopify
from shopdata import SHOP
from textutil import digits_only, phones_match

VERIFY_FAIL_LIMIT = 5

NEED_HUMAN = "Tell the customer the team will confirm, and end your message with [HANDOFF: {why}]."


class CatalogUnavailable(Exception):
    pass


def _unavailable(why):
    return {"error": "The shop system is not available right now.", "action": NEED_HUMAN.format(why=why)}


# ---------------------------------------------------------------
# Catalog: Shopify if it is set up, shop.json only in demo mode
# ---------------------------------------------------------------

def _catalog_from_json():
    catalog = []
    for p in SHOP["products"]:
        colors = [c.capitalize() for c in p.get("colors", [])] or [""]
        stock = {size: {c: bool(v) for c in colors} for size, v in p["sizes"].items()}
        catalog.append({
            "name": p["name"],
            "price_egp": p["price_egp"],
            "colors": [c for c in colors if c],
            "stock": stock,  # {size: {color: in_stock_bool}}
            "description": p.get("description", ""),
        })
    return catalog


def _catalog_from_shopify():
    products = shopify.get_products()
    if products is None:
        raise CatalogUnavailable()
    catalog = []
    for p in products:
        colors, stock, prices = [], {}, []
        for v in p["variants"]:
            size, color = v["size"], v["color"]
            if color and color not in colors:
                colors.append(color)
            # qty is None when Shopify does not track inventory for this item: treat as available.
            available = v["qty"] is None or v["qty"] > 0
            # A product with no size option (a bag, a cap) is stored under the key "".
            stock.setdefault(size or "", {})[color] = available
            prices.append(v["price_egp"])
        price = min(prices) if prices else 0
        catalog.append({
            "name": p["name"],
            "price_egp": int(price) if float(price).is_integer() else price,
            "colors": colors,
            "stock": stock,
            "description": p["description"],
        })
    return catalog


def _get_catalog():
    """With Shopify set up we NEVER fall back to demo data: wrong stock is worse than no answer."""
    if shopify.is_configured():
        return _catalog_from_shopify()
    return _catalog_from_json()


def _find_product(name):
    """Find a product by name. Every word of the question must be in the product name."""
    words = (name or "").lower().split()
    if not words:
        return None, []
    matches = [p for p in _get_catalog() if all(w in p["name"].lower() for w in words)]
    exact = [p for p in matches if p["name"].lower() == " ".join(words)]
    if exact:
        return exact[0], []
    if len(matches) == 1:
        return matches[0], []
    return None, [p["name"] for p in matches]


# ---------------------------------------------------------------
# Product tools
# ---------------------------------------------------------------

def get_product_info(name: str) -> dict:
    """Return the price, colors, sizes and description of a product."""
    logging.info("[tool] get_product_info(%r)", name)
    try:
        product, several = _find_product(name)
    except CatalogUnavailable:
        return _unavailable("shop system down")
    if several:
        return {"found": False, "several_matches": several,
                "message": "More than one product matches. Ask the customer which one."}
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}
    return {
        "found": True,
        "name": product["name"],
        "price_egp": product["price_egp"],
        "colors": product["colors"],
        "sizes": [s for s in product["stock"] if s],
        "description": product["description"],
    }


def check_stock(name: str, size: str = "", color: str = "") -> dict:
    """Say if a product is in stock in a given size (and color, if given)."""
    logging.info("[tool] check_stock(%r, %r, %r)", name, size, color)
    try:
        product, several = _find_product(name)
    except CatalogUnavailable:
        return _unavailable("shop system down")
    if several:
        return {"found": False, "several_matches": several,
                "message": "More than one product matches. Ask the customer which one."}
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}

    sizes = {s.upper(): s for s in product["stock"]}
    size_key = (size or "").strip().upper()
    if size_key not in sizes:
        if "" in product["stock"]:          # one-size product
            size_key = ""
        else:
            return {"found": True, "name": product["name"], "size_exists": False,
                    "available_sizes": [s for s in product["stock"] if s]}

    by_color = product["stock"][sizes[size_key]]  # {color: bool}

    color = (color or "").strip().lower()
    if color:
        match = {c.lower(): c for c in by_color}
        if color not in match:
            return {"found": True, "name": product["name"], "size": size_key,
                    "color_exists": False,
                    "available_colors": [c for c in by_color if c]}
        return {"found": True, "name": product["name"], "size": size_key,
                "size_exists": True, "color": match[color],
                "in_stock": by_color[match[color]]}

    result = {"found": True, "name": product["name"], "size_exists": True,
              "size": size_key, "in_stock": any(by_color.values())}
    if len([c for c in by_color if c]) > 1:
        result["by_color"] = {c: v for c, v in by_color.items() if c}
    return result


# ---------------------------------------------------------------
# Order checks
# ---------------------------------------------------------------

def _chat_key():
    turn = ctx.current_turn.get()
    return turn.chat_key if turn else None


def _date(dt):
    return dt.date().isoformat() if dt else None


def _days_since(dt):
    return (datetime.now(timezone.utc) - dt).days


def _verify_order(chat_key, order_number, phone):
    """Find the order and check the phone.

    Returns {"order": order_or_None} on success (None only in demo mode),
    or {"stop": result_for_the_model} when the model must stop.
    """
    if not shopify.is_configured():
        return {"order": None}      # demo mode: nothing to check against

    if db.verify_locked(chat_key, VERIFY_FAIL_LIMIT):
        return {"stop": {"verified": False, "locked": True,
                         "action": NEED_HUMAN.format(why="order check locked")}}

    try:
        order = shopify.get_order(order_number)
    except shopify.ShopifyError as e:
        logging.error("[tool] order lookup failed: %s", e)
        return {"stop": _unavailable("shop system down")}

    if order is not None and not order["phones"]:
        # The order exists but Shopify hides its phone numbers, so we cannot check the customer.
        return {"stop": {"verified": False, "can_verify": False,
                         "action": NEED_HUMAN.format(why="cannot verify order")}}

    if order is None or not any(phones_match(phone, p) for p in order["phones"]):
        # Same answer for "no such order" and "wrong phone", so nobody can probe for orders.
        fails = db.verify_failed(chat_key)
        return {"stop": {"verified": False,
                         "message": "No order matches that order number and phone number. "
                                    "Ask the customer to check both. Do not say which one is wrong.",
                         "attempts_left": max(0, VERIFY_FAIL_LIMIT - fails)}}

    db.verify_ok(chat_key)
    return {"order": order}


def get_order_status(order_number: str, phone: str) -> dict:
    """Return the status of an order after checking the phone number on it."""
    logging.info("[tool] get_order_status(%r)", order_number)
    chat_key = _chat_key()
    if chat_key is None:
        return {"error": "No active chat."}
    check = _verify_order(chat_key, order_number, phone)
    if "stop" in check:
        return check["stop"]
    order = check["order"]
    if order is None:
        return {"verified": False, "message": "Order lookup is not available. Tell the customer the team will check.",
                "action": NEED_HUMAN.format(why="order status")}

    return {
        "verified": True,
        "order_number": order["name"],
        "cancelled": order["cancelled"],
        "payment_status": order["payment_status"],
        "shipping_status": order["fulfillment_status"],
        "items": order["items"],
        "shipped_on": _date(order["shipped_at"]),
        "delivered_on": _date(order["delivered_at"]),
        "tracking": order["tracking"],
    }


# ---------------------------------------------------------------
# Exchange and return requests
# ---------------------------------------------------------------

def _item_in_order(item, order):
    """True if the item the customer named is one of the order's items."""
    text = (item or "").lower()
    for line in order["items"]:
        words = line["title"].lower().split()
        if words and all(w in text for w in words):
            return True
    return False


def _windows():
    policies = SHOP.get("policies", {})
    exchange = policies.get("exchange_window_days", 14)
    return {"exchange": exchange, "return": policies.get("return_window_days", exchange)}


def _submit(kind, order_number, phone, item, reason, wanted, days_since_delivery, unworn_with_tags):
    chat_key = _chat_key()
    if chat_key is None:
        return {"submitted": False, "error": "No active chat."}

    # 1) Who is asking? The order and phone must match.
    check = _verify_order(chat_key, order_number, phone)
    if "stop" in check:
        out = dict(check["stop"])
        out["submitted"] = False
        return out
    order = check["order"]
    order_verified = order is not None

    # 2) Is the item really in that order?
    if order is not None:
        if order["cancelled"]:
            return {"submitted": False, "reason": "This order was cancelled."}
        if not _item_in_order(item, order):
            return {"submitted": False, "item_not_in_order": True,
                    "order_items": order["items"],
                    "next": "Ask which of these items the customer means, then call again "
                            "with the item name exactly as written in the order."}

    # 3) Days since delivery: Shopify first, the customer's word only if Shopify does not know.
    days, days_verified = None, False
    if order is not None and order["delivered_at"]:
        days, days_verified = _days_since(order["delivered_at"]), True
    elif order is not None and not order["shipped_at"]:
        return {"submitted": False,
                "reason": "This order has not been shipped yet, so it cannot be exchanged or returned."}
    elif days_since_delivery is not None and days_since_delivery >= 0:
        days = int(days_since_delivery)
    else:
        return {"submitted": False, "need": "days_since_delivery",
                "next": "Ask the customer how many days ago they received the order."}

    window = _windows()[kind]
    if days > window:
        return {"submitted": False,
                "reason": f"Outside the {window}-day window for a {kind}."}
    if not unworn_with_tags:
        return {"submitted": False,
                "reason": "The item must be unworn with tags attached."}

    # 4) Already sent? Do not make a second request for the same order.
    existing = db.find_recent_request(kind, chat_key, str(digits_only(order_number)))
    if existing:
        return {"submitted": True, "already_submitted": True, "request_id": existing["code"],
                "next": "Tell the customer the request was already sent and the team will confirm."}

    # 5) A photo is required.
    photos = db.unattached_photos(chat_key)
    if not photos:
        return {"submitted": False, "need_photo": True,
                "next": "Ask the customer to send a clear photo of the item here in the chat "
                        "(and of the problem, if there is one). Then call this tool again."}

    # 6) Save it. The team reviews it. Nothing is approved here.
    order_digits = str(digits_only(order_number))
    code = db.create_request(kind, chat_key, order_digits, phone, item, reason, wanted,
                             days, days_verified, order_verified)
    db.attach_photos(code, [p["id"] for p in photos])

    turn = ctx.current_turn.get()
    turn.requests.append({
        "code": code, "kind": kind, "order": order_digits, "phone": phone, "item": item,
        "reason": reason, "wanted": wanted, "days": days, "days_verified": days_verified,
        "order_verified": order_verified,
    })
    return {"submitted": True, "request_id": code,
            "next": "Tell the customer the team will review it and confirm. Do NOT promise approval "
                    "or a refund. Give them the request id."}


def create_exchange_request(order_number: str, phone: str, item: str, reason: str, wanted: str,
                            unworn_with_tags: bool, days_since_delivery: int = -1) -> dict:
    """Record an exchange request for the team to review."""
    logging.info("[tool] create_exchange_request(%r)", order_number)
    return _submit("exchange", order_number, phone, item, reason, wanted,
                   days_since_delivery, unworn_with_tags)


def create_return_request(order_number: str, phone: str, item: str, reason: str,
                          unworn_with_tags: bool, days_since_delivery: int = -1) -> dict:
    """Record a return request for the team to review."""
    logging.info("[tool] create_return_request(%r)", order_number)
    return _submit("return", order_number, phone, item, reason, "",
                   days_since_delivery, unworn_with_tags)
