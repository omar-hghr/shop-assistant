import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    SHOP = json.load(f)


def _find_product(name):
    """Find a product by name. Ignores capital letters."""
    name = name.strip().lower()
    for product in SHOP["products"]:
        if name in product["name"].lower():
            return product
    return None


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
        "sizes": list(product["sizes"].keys()),
        "description": product["description"],
    }


def check_stock(name: str, size: str) -> dict:
    """Say if a product is in stock in a given size."""
    print(f"[tool called: check_stock({name!r}, {size!r})]")
    product = _find_product(name)
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}
    size = size.strip().upper()
    if size not in product["sizes"]:
        return {"found": True, "name": product["name"], "size_exists": False,
                "available_sizes": list(product["sizes"].keys())}
    return {"found": True, "name": product["name"], "size_exists": True,
            "size": size, "in_stock": product["sizes"][size]}
    
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