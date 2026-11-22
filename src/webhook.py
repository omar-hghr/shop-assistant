"""The web server Meta talks to. Instagram only.

Run it from the src folder:
    uvicorn webhook:app --host 0.0.0.0 --port 8000

Use ONE worker (no --workers option): the bot keeps per-customer locks in memory.
"""
import hmac
import html
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

import db
import ig_handler
import instagram
import reports

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # keeps tokens out of the logs

# The old WHATSAPP_* names still work, so an existing .env keeps running.
VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN") or os.getenv("WHATSAPP_VERIFY_TOKEN")
APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET") or os.getenv("WHATSAPP_APP_SECRET")
OWN_ID = (os.getenv("INSTAGRAM_ACCOUNT_ID") or "").strip()


@asynccontextmanager
async def lifespan(app):
    db.init()
    if os.getenv("DISABLE_REPORTS") != "1":
        reports.start_scheduler()
    if not APP_SECRET:
        logging.warning("INSTAGRAM_APP_SECRET is not set: every webhook will be rejected")
    yield


app = FastAPI(title="LocalBrands Instagram bot", lifespan=lifespan)


@app.get("/")
async def home():
    return {"status": "running"}


@app.get("/instagram")
async def verify_instagram(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token and VERIFY_TOKEN and hmac.compare_digest(token, VERIFY_TOKEN):
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/instagram")
async def receive_instagram(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not instagram.verify_signature(body, signature, APP_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        data = await request.json()
        for event in instagram.extract_events(data):
            parsed = instagram.parse_event(event, OWN_ID)
            if parsed is None:
                continue
            # Meta sometimes sends the same message twice
            if not db.mark_processed(parsed["mid"]):
                continue
            background_tasks.add_task(ig_handler.process_event, parsed)
        return {"status": "received"}
    except Exception:
        logging.exception("Instagram webhook processing failed")
        # Return 200 anyway, otherwise Meta keeps resending the same message
        return {"status": "error"}


# ---------------------------------------------------------------
# The owner's "let the bot answer again" link (from the handoff email)
# ---------------------------------------------------------------

def _page(message, button=None):
    form = f'<form method="post"><button style="font-size:18px;padding:10px 20px">{button}</button></form>' if button else ""
    return HTMLResponse(
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<body style="font-family:sans-serif;max-width:420px;margin:40px auto;padding:0 16px"><p>{message}</p>{form}</body>'
    )


@app.get("/resume/{token}")
async def resume_page(token: str):
    handoff = db.get_handoff_by_token(token)
    if not handoff:
        return _page("This link is not valid.")
    if handoff["resumed"]:
        return _page("The bot is already answering this customer.")
    return _page(f"Let the bot answer this customer again?<br><small>{html.escape(handoff['reason'] or '')}</small>",
                 button="Yes, resume the bot")


@app.post("/resume/{token}")
async def resume_do(token: str):
    if not db.get_handoff_by_token(token):
        return _page("This link is not valid.")
    db.resume_handoff(token)
    return _page("Done. The bot will answer this customer again.")
