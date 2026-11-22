"""Loads data/shop.json (shop name, delivery, policies, size guide)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

with open(ROOT / "data" / "shop.json", encoding="utf-8") as f:
    SHOP = json.load(f)
