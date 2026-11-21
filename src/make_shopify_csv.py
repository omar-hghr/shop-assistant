import csv
import json
import re

# Your shop.json only says in stock (true) or out (false).
# Shopify needs a number, so in-stock sizes get this placeholder quantity.
IN_STOCK_QTY = 10

with open("data/shop.json", encoding="utf-8") as f:
    shop = json.load(f)

products = shop["products"]
vendor = shop.get("name", "LocalBrands")

HEADER = [
    "Handle", "Title", "Body (HTML)", "Vendor", "Type", "Published",
    "Option1 Name", "Option1 Value", "Option2 Name", "Option2 Value",
    "Variant SKU", "Variant Inventory Tracker", "Variant Inventory Qty",
    "Variant Inventory Policy", "Variant Price", "Variant Requires Shipping",
    "Variant Taxable", "Status",
]


def make_handle(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


rows = []
for i, p in enumerate(products, start=1):
    handle = make_handle(p["name"])
    colors = p.get("colors") or ["default"]
    first = True
    for color in colors:
        for size, in_stock in p["sizes"].items():
            qty = IN_STOCK_QTY if in_stock else 0
            rows.append([
                handle,
                p["name"] if first else "",
                p.get("description", "") if first else "",
                vendor if first else "",
                "Clothing" if first else "",
                "TRUE" if first else "",
                "Size" if first else "",
                size,
                "Color" if first else "",
                color.capitalize(),
                f"P{i:03d}-{size}-{color[:3].upper()}",
                "shopify",
                qty,
                "deny",
                p["price_egp"],
                "TRUE",
                "TRUE",
                "active" if first else "",
            ])
            first = False

with open("shopify_products.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    w.writerows(rows)

print(f"Done: {len(products)} products, {len(rows)} variant rows -> shopify_products.csv")