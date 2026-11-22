"""Handles one Instagram message (text and photos) after Meta already got its 200.

It is separate from webhook.py so it can be tested without a web server.
"""
import asyncio
import logging

import core
import instagram

MAX_PHOTOS_PER_MESSAGE = 5


async def safe_send(to, message):
    """Send a message without crashing if it fails."""
    try:
        await instagram.send_instagram_text(to, message)
    except Exception:
        logging.exception("Failed to send a message to %s", to)


async def process_event(parsed):
    """parsed comes from instagram.parse_event()."""
    sender_id = parsed["sender_id"]
    chat_key = "ig:" + sender_id
    text = parsed["text"]

    try:
        # Photos first, then the text, so the model sees the photo already saved.
        for url in parsed["images"][:MAX_PHOTOS_PER_MESSAGE]:
            path = await instagram.download_image(url, sender_id)
            reply = await asyncio.to_thread(core.handle_photo, chat_key, path, url)
            if reply and not text:   # if the customer also wrote text, one reply is enough
                await safe_send(sender_id, reply)

        if text:
            reply = await asyncio.to_thread(core.handle_text, chat_key, text)
            if reply:
                await safe_send(sender_id, reply)
        elif not parsed["images"]:
            reply = await asyncio.to_thread(core.handle_unsupported, chat_key)
            if reply:
                await safe_send(sender_id, reply)
    except Exception:
        logging.exception("Failed to handle a message from %s", chat_key)
