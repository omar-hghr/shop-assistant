"""Sends emails to the brand owner (handoffs, requests with photos, daily report)."""
import logging
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

MAX_FILE_BYTES = 5 * 1024 * 1024      # one attachment
MAX_TOTAL_BYTES = 18 * 1024 * 1024    # all attachments (Gmail stops at 25 MB)


def owner_emails():
    """OWNER_EMAILS is the one to use. The older names still work."""
    raw = os.getenv("OWNER_EMAILS") or os.getenv("HANDOFF_EMAILS") or os.getenv("SUMMARY_EMAILS") or ""
    return [e.strip() for e in raw.split(",") if e.strip()]


def _one_line(text):
    return " ".join(str(text).split())


def send_email(subject, body, attachments=None):
    """Send one email to the owner(s). Blocking. Returns True if it was sent."""
    sender = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_APP_PASSWORD")
    recipients = owner_emails()
    if not (sender and password and recipients):
        logging.warning("Email not configured: check EMAIL_SENDER, EMAIL_APP_PASSWORD and OWNER_EMAILS")
        return False

    files, skipped, total = [], [], 0
    for path in attachments or []:
        if not path:
            continue
        try:
            p = Path(path)
            size = p.stat().st_size
        except (OSError, TypeError):
            skipped.append(str(path))
            continue
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            skipped.append(p.name)
            continue
        files.append(p)
        total += size

    if skipped:
        body += "\n\nSome photos could not be attached: " + ", ".join(skipped)

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = _one_line(subject)[:120]
    msg.set_content(body)
    for p in files:
        ctype, _ = mimetypes.guess_type(p.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as server:
            server.login(sender, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        logging.error("Could not send the email: %s", type(e).__name__)
        return False
    return True
