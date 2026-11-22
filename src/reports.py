"""The daily report: an email to the owner at 12 AM (Cairo time) about the day that just ended.

It is checked every 30 seconds by a background thread. If the server was off at
midnight, the missed report is sent when the server starts again (last 3 days).

Manual use (from the src folder):
    python reports.py                    shows yesterday's report on screen
    python reports.py 2026-11-21         shows that day
    python reports.py 2026-11-21 --send  sends it by email now
"""
import logging
import os
import sys
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import db
import mailer
from shopdata import SHOP

RETRY_AFTER_SECONDS = 600
_next_try = 0.0


def _tz():
    return ZoneInfo(os.getenv("REPORT_TZ") or "Africa/Cairo")


def day_bounds(day):
    """Start and end (unix seconds) of one local day."""
    tz = _tz()
    start = datetime.combine(day, dtime.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=tz)
    return start.timestamp(), end.timestamp()


def build_report(day):
    """Returns (subject, body) for one local day."""
    c = db.report_counts(*day_bounds(day))
    lines = [
        f"Daily report - {day.isoformat()} (Cairo time)",
        f"Shop: {SHOP['shop_name']}",
        "",
        f"Customers who chatted with the bot: {c['chats']}",
        f"Messages received: {c['messages']}",
        f"Handed to a human (not counting exchange/return requests): {len(c['handoffs'])}",
    ]
    lines += [f"   - {reason}" for reason in c["handoffs"][:15]]
    lines.append(f"Exchange requests: {len(c['exchanges'])}")
    lines += [f"   - {r['code']} order {r['order_number']}: {r['item']} -> {r['wanted']}" for r in c["exchanges"][:15]]
    lines.append(f"Return requests: {len(c['returns'])}")
    lines += [f"   - {r['code']} order {r['order_number']}: {r['item']} ({r['reason']})" for r in c["returns"][:15]]
    lines.append(f"Photos received: {c['photos']}")
    lines.append(f"Technical problems: {c['errors']}")
    if c["errors"] or c["email_failed"]:
        lines.append("")
        if c["errors"]:
            lines.append("Warning: some customers got an error message. Check the server logs.")
        if c["email_failed"]:
            lines.append(f"Warning: {c['email_failed']} alert email(s) could not be sent. Check the email settings.")
    lines += ["", "Every exchange and return was sent to you by email when it happened."]
    subject = (f"Daily report {day.isoformat()}: {c['chats']} chats, {len(c['handoffs'])} handoffs, "
               f"{len(c['exchanges'])} exchanges, {len(c['returns'])} returns")
    return subject, "\n".join(lines)


def check_and_send(now=None):
    """Send any report that is due. Returns the list of days sent."""
    global _next_try
    tz = _tz()
    now = now or datetime.now(tz)
    if time.time() < _next_try:
        return []
    sent = []
    for back in (3, 2, 1):
        day = now.date() - timedelta(days=back)
        key = day.isoformat()
        if not db.claim_report(key):
            continue                      # already sent (or being sent)
        start, end = day_bounds(day)
        first = db.first_activity()
        if first is None or first >= end:
            continue                      # the bot did not exist yet that day: no email
        subject, body = build_report(day)
        if mailer.send_email(subject, body):
            sent.append(key)
        else:
            db.release_report(key)        # try again later
            _next_try = time.time() + RETRY_AFTER_SECONDS
            logging.error("Daily report for %s could not be emailed. Will retry.", key)
            break
    return sent


def _loop():
    while True:
        try:
            check_and_send()
        except Exception:
            logging.exception("Report scheduler problem")
        time.sleep(30)


_started = False


def start_scheduler():
    """Start the background thread (once)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="daily-report", daemon=True).start()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = date.fromisoformat(args[0]) if args else datetime.now(_tz()).date() - timedelta(days=1)
    subject, body = build_report(target)
    print(subject, "\n")
    print(body)
    if "--send" in sys.argv:
        print("\n[sent]" if mailer.send_email(subject, body) else "\n[could not send, check .env]")
