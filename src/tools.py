import json
from pathlib import Path
from datetime import datetime

import shopify

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    SHOP = json.load(f)


# ---------- Catalog: Shopify first, shop.json as fallback ----------

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
        return None
    catalog = []
    for p in products:
        colors, stock, prices = [], {}, []
        for v in p["variants"]:
            size, color = v["size"], v["color"]
            if color and color not in colors:
                colors.append(color)
            if size:
                stock.setdefault(size, {})[color] = v["qty"] > 0
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
    catalog = _catalog_from_shopify()
    if catalog is None:
        print("[catalog] Shopify failed, using data/shop.json")
        return _catalog_from_json()
    return catalog


def _find_product(name):
    """Find a product by name. Ignores capital letters."""
    name = name.strip().lower()
    for product in _get_catalog():
        if name in product["name"].lower():
            return product
    return None


# ---------- Tools ----------

def get_product_info(name: str) -> dict:
    """Return the price, colors, sizes and description of a product."""
    print(f"[tool called: get_product_info({name!r})]")
    product = _find_product(name)
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}
    return {
        "found": True,
        "name": product["name"],
        "price_egp": product["price_egp"],
        "colors": product["colors"],
        "sizes": list(product["stock"].keys()),
        "description": product["description"],
    }


def check_stock(name: str, size: str, color: str = "") -> dict:
    """Say if a product is in stock in a given size (and color, if given)."""
    print(f"[tool called: check_stock({name!r}, {size!r}, {color!r})]")
    product = _find_product(name)
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}

    size = size.strip().upper()
    sizes = {s.upper(): s for s in product["stock"]}
    if size not in sizes:
        return {"found": True, "name": product["name"], "size_exists": False,
                "available_sizes": list(product["stock"].keys())}

    by_color = product["stock"][sizes[size]]  # {color: bool}

    color = color.strip().lower()
    if color:
        match = {c.lower(): c for c in by_color}
        if color not in match:
            return {"found": True, "name": product["name"], "size": size,
                    "color_exists": False,
                    "available_colors": [c for c in by_color if c]}
        return {"found": True, "name": product["name"], "size": size,
                "size_exists": True, "color": match[color],
                "in_stock": by_color[match[color]]}

    result = {"found": True, "name": product["name"], "size_exists": True,
              "size": size, "in_stock": any(by_color.values())}
    if len(by_color) > 1:
        result["by_color"] = {c: v for c, v in by_color.items() if c}
    return result


EXCHANGES_FILE = ROOT / "exchanges.log"


def _next_exchange_id() -> str:
    count = 0
    if EXCHANGES_FILE.exists():
        with open(EXCHANGES_FILE, encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
    return f"EX-{count + 1:04d}"


def create_exchange_request(order_number: str, phone: str, item: str, reason: str,
                            wanted: str, days_since_delivery: int,
                            unworn_with_tags: bool) -> dict:
    """Record an exchange request for the team to review. Call it ONLY after you
    have ALL of these from the customer: order number, phone number on the order,
    the item, the reason, what they want instead, how many days ago they received
    the order, and whether the item is unworn with tags attached. Never guess any
    of them. This does NOT approve the exchange. A human decides."""
    print(f"[tool called: create_exchange_request({order_number!r})]")

    window = SHOP["policies"]["exchange_window_days"]
    if days_since_delivery > window:
        return {"submitted": False,
                "reason": f"Outside the {window}-day exchange window."}
    if not unworn_with_tags:
        return {"submitted": False,
                "reason": "The item must be unworn with tags attached."}

    request_id = _next_exchange_id()
    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "request_id": request_id,
        "order_number": order_number,
        "phone": phone,
        "item": item,
        "reason": reason,
        "wanted": wanted,
        "days_since_delivery": days_since_delivery,
    }
    with open(EXCHANGES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"submitted": True, "request_id": request_id,
            "next": "Tell the customer the team will review it and confirm. Do NOT promise approval."}


if __name__ == "__main__":
    print(get_product_info("hoodie"))
    print(check_stock("hoodie", "XL"))
    print(check_stock("t-shirt", "L"))
    print(check_stock("t-shirt", "L", "white"))