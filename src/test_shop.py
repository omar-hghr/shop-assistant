import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # the project folder (one level above src)
with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    shop = json.load(f)

print(shop["shop_name"])
print(len(shop["products"]), "products")