import json
from pathlib import Path

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


def get_product_info(name):
    """Return the price, colors, sizes and description of a product."""
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


def check_stock(name, size):
    """Say if a product is in stock in a given size."""
    product = _find_product(name)
    if product is None:
        return {"found": False, "message": "Product not found in the catalog."}
    size = size.strip().upper()
    if size not in product["sizes"]:
        return {"found": True, "name": product["name"], "size_exists": False,
                "available_sizes": list(product["sizes"].keys())}
    return {"found": True, "name": product["name"], "size_exists": True,
            "size": size, "in_stock": product["sizes"][size]}