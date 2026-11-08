import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS_FILE = ROOT / "events.log"


def log_event(kind: str, user_id=None, text: str = ""):
    """Write one line to events.log. kind is 'message' or 'handoff'."""
    event = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "user": user_id,
        "text": text,
    }
    with open(EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")