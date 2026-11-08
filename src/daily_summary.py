import json
import os
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

from stats import EVENTS_FILE

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")
# Use SUMMARY_EMAILS if it exists, otherwise the same emails as the handoffs.
_emails = os.getenv("SUMMARY_EMAILS") or os.getenv("HANDOFF_EMAILS", "")
SUMMARY_EMAILS = [e.strip() for e in _emails.split(",") if e.strip()]


def build_summary(day=None):
    day = (day or date.today()).isoformat()
    messages = 0
    customers = set()
    handoffs = []

    if EVENTS_FILE.exists():
        with open(EVENTS_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if not event["time"].startswith(day):
                    continue
                if event["kind"] == "message":
                    messages += 1
                    customers.add(event["user"])
                elif event["kind"] == "handoff":
                    handoffs.append(event["text"])

    lines = [
        f"Daily summary - {day}",
        f"Customers: {len(customers)}",
        f"Messages: {messages}",
        f"Handed to you: {len(handoffs)}",
    ]
    if handoffs:
        lines.append("Reasons:")
        lines += [f"- {reason}" for reason in handoffs[:10]]
    return "\n".join(lines)


def send_email(subject, body):
    """Send the summary by email. Prints a clear message if something is missing."""
    if not (EMAIL_SENDER and EMAIL_APP_PASSWORD and SUMMARY_EMAILS):
        print("[email not configured: check EMAIL_SENDER, EMAIL_APP_PASSWORD and SUMMARY_EMAILS in .env]")
        return False
    msg = EmailMessage()
    msg["From"] = EMAIL_SENDER
    msg["To"] = ", ".join(SUMMARY_EMAILS)
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        print(f"[could not send the email: {type(e).__name__}]")
        return False
    print("[email sent]")
    return True


if __name__ == "__main__":
    summary = build_summary()
    print(summary)
    send_email(summary.splitlines()[0], summary)