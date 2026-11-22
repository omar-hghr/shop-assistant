"""Which customer chat the current tool call belongs to.

core.py creates a Turn before the model runs, and tools.py reads it. This way
the model never chooses the customer: a customer cannot trick the bot into
working on someone else's chat or someone else's photos.
"""
import contextvars


class Turn:
    """One customer message being handled."""

    def __init__(self, chat_key):
        self.chat_key = chat_key
        self.requests = []   # exchange/return requests saved during this turn


current_turn = contextvars.ContextVar("current_turn", default=None)
