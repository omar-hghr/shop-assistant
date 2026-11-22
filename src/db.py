"""The bot's memory: one small SQLite file (bot.db).

It keeps chats, events (for the daily report), handoffs, requests and photos,
so nothing is lost when the server restarts. Every function takes the same
lock, so it is safe to call from several threads.
"""
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_key TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    chat_key TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_key TEXT NOT NULL,
    reason TEXT,
    token TEXT NOT NULL UNIQUE,
    created REAL NOT NULL,
    resumed REAL
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    ts REAL NOT NULL,
    chat_key TEXT NOT NULL,
    order_number TEXT,
    phone TEXT,
    item TEXT,
    reason TEXT,
    wanted TEXT,
    days INTEGER,
    days_verified INTEGER,
    order_verified INTEGER
);
CREATE TABLE IF NOT EXISTS photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    chat_key TEXT NOT NULL,
    path TEXT,
    url TEXT,
    request_code TEXT
);
CREATE TABLE IF NOT EXISTS processed (
    msg_id TEXT PRIMARY KEY,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS verify_fails (
    chat_key TEXT PRIMARY KEY,
    count INTEGER NOT NULL,
    first_ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports_sent (
    day TEXT PRIMARY KEY,
    ts REAL NOT NULL
);
"""

REQUEST_PREFIX = {"exchange": "EX", "return": "RT"}
VERIFY_WINDOW_SECONDS = 3600


# ---------------------------------------------------------------
# Basics
# ---------------------------------------------------------------

def init(path=None):
    """Open the database. Tests pass a path. Normal runs use DB_PATH or bot.db."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        target = str(path or os.getenv("DB_PATH") or ROOT / "bot.db")
        _conn = sqlite3.connect(target, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        _conn.commit()


def _db():
    if _conn is None:
        init()
    return _conn


def _run(sql, params=()):
    with _lock:
        cur = _db().execute(sql, params)
        _db().commit()
        return cur


def _all(sql, params=()):
    with _lock:
        return [dict(r) for r in _db().execute(sql, params).fetchall()]


def _one(sql, params=()):
    rows = _all(sql, params)
    return rows[0] if rows else None


# ---------------------------------------------------------------
# Chats (what the model remembers about each customer)
# ---------------------------------------------------------------

def load_chat(chat_key):
    row = _one("SELECT messages FROM chats WHERE chat_key=?", (chat_key,))
    return json.loads(row["messages"]) if row else None


def save_chat(chat_key, messages):
    _run(
        "INSERT INTO chats(chat_key, messages, updated) VALUES(?,?,?) "
        "ON CONFLICT(chat_key) DO UPDATE SET messages=excluded.messages, updated=excluded.updated",
        (chat_key, json.dumps(messages, ensure_ascii=False), time.time()),
    )


def reset_chat(chat_key):
    """Forget one customer (used by the local test chat)."""
    with _lock:
        _run("DELETE FROM chats WHERE chat_key=?", (chat_key,))
        _run("UPDATE handoffs SET resumed=? WHERE chat_key=? AND resumed IS NULL", (time.time(), chat_key))
        _run("DELETE FROM verify_fails WHERE chat_key=?", (chat_key,))


def recent_transcript(chat_key, n=14):
    """The last messages of a chat as plain text (for emails to the owner)."""
    lines = []
    for m in load_chat(chat_key) or []:
        content = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(content, str):
            content = re.sub(r"\[HANDOFF[^\]]*\]", "", content)
            content = " ".join(content.split())
            if content:
                who = "Customer" if m["role"] == "user" else "Bot"
                lines.append(f"{who}: {content[:300]}")
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------
# Events (the daily report counts these)
# ---------------------------------------------------------------

def log_event(kind, chat_key=None, text="", ts=None):
    """kind: message, handoff, error, email_failed."""
    _run(
        "INSERT INTO events(ts, kind, chat_key, text) VALUES(?,?,?,?)",
        (ts or time.time(), kind, chat_key, text),
    )


def count_recent_messages(chat_key, seconds=3600):
    row = _one(
        "SELECT COUNT(*) AS n FROM events WHERE kind='message' AND chat_key=? AND ts>?",
        (chat_key, time.time() - seconds),
    )
    return row["n"]


# ---------------------------------------------------------------
# Handoffs (a human has the chat, the bot stays silent)
# ---------------------------------------------------------------

def _open_handoff_row(chat_key):
    return _one(
        "SELECT * FROM handoffs WHERE chat_key=? AND resumed IS NULL ORDER BY id DESC LIMIT 1",
        (chat_key,),
    )


def open_handoff(chat_key, reason, token):
    """Start a handoff. Returns (handoff, created). If one is already open, created is False."""
    with _lock:
        existing = _open_handoff_row(chat_key)
        if existing:
            return existing, False
        _run(
            "INSERT INTO handoffs(chat_key, reason, token, created) VALUES(?,?,?,?)",
            (chat_key, reason, token, time.time()),
        )
        return _one("SELECT * FROM handoffs WHERE token=?", (token,)), True


def is_paused(chat_key, auto_resume_hours=12):
    """True while a human has this chat. After auto_resume_hours the bot takes it back."""
    with _lock:
        row = _open_handoff_row(chat_key)
        if not row:
            return False
        if auto_resume_hours and time.time() - row["created"] > auto_resume_hours * 3600:
            _run("UPDATE handoffs SET resumed=? WHERE id=?", (time.time(), row["id"]))
            return False
        return True


def get_handoff_by_token(token):
    return _one("SELECT * FROM handoffs WHERE token=?", (token,))


def resume_handoff(token):
    """Give the chat back to the bot. True if something changed."""
    cur = _run("UPDATE handoffs SET resumed=? WHERE token=? AND resumed IS NULL", (time.time(), token))
    return cur.rowcount > 0


# ---------------------------------------------------------------
# Exchange and return requests
# ---------------------------------------------------------------

def create_request(kind, chat_key, order_number, phone, item, reason, wanted, days,
                   days_verified, order_verified, ts=None):
    """Save a request and return its code, for example EX-0001 or RT-0001."""
    with _lock:
        n = _one("SELECT COUNT(*) AS n FROM requests WHERE kind=?", (kind,))["n"] + 1
        code = f"{REQUEST_PREFIX[kind]}-{n:04d}"
        _run(
            "INSERT INTO requests(code, kind, ts, chat_key, order_number, phone, item, reason, wanted, "
            "days, days_verified, order_verified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, kind, ts or time.time(), chat_key, order_number, phone, item, reason, wanted,
             days, 1 if days_verified else 0, 1 if order_verified else 0),
        )
        return code


def find_recent_request(kind, chat_key, order_number, within_days=7):
    return _one(
        "SELECT * FROM requests WHERE kind=? AND chat_key=? AND order_number=? AND ts>? ORDER BY id DESC LIMIT 1",
        (kind, chat_key, order_number, time.time() - within_days * 86400),
    )


# ---------------------------------------------------------------
# Photos sent by customers
# ---------------------------------------------------------------

def add_photo(chat_key, path, url, ts=None):
    cur = _run(
        "INSERT INTO photos(ts, chat_key, path, url) VALUES(?,?,?,?)",
        (ts or time.time(), chat_key, path, url),
    )
    return cur.lastrowid


def unattached_photos(chat_key, within_seconds=86400):
    """Photos this customer sent that are not part of a request yet."""
    return _all(
        "SELECT * FROM photos WHERE chat_key=? AND request_code IS NULL AND ts>? ORDER BY id",
        (chat_key, time.time() - within_seconds),
    )


def attach_photos(code, photo_ids):
    if not photo_ids:
        return
    marks = ",".join("?" for _ in photo_ids)
    _run(f"UPDATE photos SET request_code=? WHERE id IN ({marks})", (code, *photo_ids))


def photos_for_request(code):
    return _all("SELECT * FROM photos WHERE request_code=? ORDER BY id", (code,))


# ---------------------------------------------------------------
# Duplicate messages (Meta sometimes sends the same message twice)
# ---------------------------------------------------------------

def mark_processed(msg_id):
    """True the first time we see this id, False if we already handled it."""
    if not msg_id:
        return True
    cur = _run("INSERT OR IGNORE INTO processed(msg_id, ts) VALUES(?,?)", (msg_id, time.time()))
    if cur.rowcount == 0:
        return False
    _run("DELETE FROM processed WHERE ts<?", (time.time() - 3 * 86400,))
    return True


# ---------------------------------------------------------------
# Order check attempts (so nobody can guess phone numbers)
# ---------------------------------------------------------------

def verify_failed(chat_key):
    """Count one failed check. Returns how many failures in the last hour."""
    with _lock:
        row = _one("SELECT * FROM verify_fails WHERE chat_key=?", (chat_key,))
        now = time.time()
        if row and now - row["first_ts"] < VERIFY_WINDOW_SECONDS:
            _run("UPDATE verify_fails SET count=count+1 WHERE chat_key=?", (chat_key,))
            return row["count"] + 1
        _run(
            "INSERT OR REPLACE INTO verify_fails(chat_key, count, first_ts) VALUES(?,?,?)",
            (chat_key, 1, now),
        )
        return 1


def verify_locked(chat_key, limit=5):
    row = _one("SELECT * FROM verify_fails WHERE chat_key=?", (chat_key,))
    return bool(row and time.time() - row["first_ts"] < VERIFY_WINDOW_SECONDS and row["count"] >= limit)


def verify_ok(chat_key):
    _run("DELETE FROM verify_fails WHERE chat_key=?", (chat_key,))


# ---------------------------------------------------------------
# Daily report
# ---------------------------------------------------------------

def report_counts(start, end):
    """Numbers for the period [start, end) (unix seconds)."""
    p = (start, end)
    msg = _one(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT chat_key) AS chats FROM events "
        "WHERE kind='message' AND ts>=? AND ts<?", p)
    handoffs = [
        r["text"] or "not specified"
        for r in _all("SELECT text FROM events WHERE kind='handoff' AND ts>=? AND ts<? ORDER BY ts", p)
    ]
    errors = _one("SELECT COUNT(*) AS n FROM events WHERE kind='error' AND ts>=? AND ts<?", p)["n"]
    email_failed = _one("SELECT COUNT(*) AS n FROM events WHERE kind='email_failed' AND ts>=? AND ts<?", p)["n"]
    exchanges = _all(
        "SELECT code, order_number, item, wanted FROM requests WHERE kind='exchange' AND ts>=? AND ts<? ORDER BY ts", p)
    returns = _all(
        "SELECT code, order_number, item, reason FROM requests WHERE kind='return' AND ts>=? AND ts<? ORDER BY ts", p)
    photos = _one("SELECT COUNT(*) AS n FROM photos WHERE ts>=? AND ts<?", p)["n"]
    return {
        "chats": msg["chats"],
        "messages": msg["n"],
        "handoffs": handoffs,
        "exchanges": exchanges,
        "returns": returns,
        "photos": photos,
        "errors": errors,
        "email_failed": email_failed,
    }


def first_activity():
    """Time of the very first event or request ever saved, or None on a brand new install."""
    a = _one("SELECT MIN(ts) AS t FROM events")["t"]
    b = _one("SELECT MIN(ts) AS t FROM requests")["t"]
    times = [t for t in (a, b) if t is not None]
    return min(times) if times else None


def claim_report(day):
    """True if this process may send the report for `day` (nobody did it before)."""
    cur = _run("INSERT OR IGNORE INTO reports_sent(day, ts) VALUES(?,?)", (day, time.time()))
    return cur.rowcount > 0


def release_report(day):
    """Undo claim_report, used when the email could not be sent, so it is tried again later."""
    _run("DELETE FROM reports_sent WHERE day=?", (day,))
