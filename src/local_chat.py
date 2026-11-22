"""Talk to the bot in the terminal, to test it without Instagram.

    python local_chat.py

Type  /photo  to pretend you sent a photo, /reset to start over, exit to stop.
The Shopify and Groq settings from .env are used. Emails are sent for real if the
email settings are filled in, so use a test address.
"""
import core
import db

CHAT_KEY = "local:test"


def main():
    db.init()
    print("Local chat. Type 'exit' to stop, '/photo' to send a fake photo, '/reset' to start over.")
    while True:
        text = input("Customer: ").strip()
        if text.lower() == "exit":
            break
        if text == "/reset":
            db.reset_chat(CHAT_KEY)
            print("[chat reset]")
            continue
        if text == "/photo":
            reply = core.handle_photo(CHAT_KEY, None, "local-test-photo")
        else:
            reply = core.handle_text(CHAT_KEY, text)
        print("Bot:", reply if reply is not None else "(silent: a human has this chat)")


if __name__ == "__main__":
    main()
