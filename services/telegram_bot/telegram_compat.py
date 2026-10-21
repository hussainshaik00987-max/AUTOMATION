"""Compatibility layer for python-telegram-bot.

Uses official python-telegram-bot (v20+) when available; provides
lightweight compatible mock classes for test and offline environments.
"""
from typing import List, Optional, Any, Dict

try:
    import telegram
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
    )
    HAS_PTB = True
except ImportError:
    HAS_PTB = False

    class InlineKeyboardButton:
        def __init__(self, text: str, callback_data: str = None, url: str = None):
            self.text = text
            self.callback_data = callback_data
            self.url = url

        def to_dict(self) -> Dict[str, Any]:
            d = {"text": self.text}
            if self.callback_data is not None:
                d["callback_data"] = self.callback_data
            if self.url is not None:
                d["url"] = self.url
            return d

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard: List[List[InlineKeyboardButton]]):
            self.inline_keyboard = inline_keyboard

        def to_dict(self) -> Dict[str, Any]:
            return {
                "inline_keyboard": [
                    [btn.to_dict() if hasattr(btn, "to_dict") else btn for btn in row]
                    for row in self.inline_keyboard
                ]
            }

    class Update:
        def __init__(
            self,
            update_id: int = 1,
            effective_user=None,
            effective_chat=None,
            message=None,
            callback_query=None,
        ):
            self.update_id = update_id
            self.effective_user = effective_user
            self.effective_chat = effective_chat
            self.message = message
            self.callback_query = callback_query

    class ContextTypes:
        class DEFAULT_TYPE:
            def __init__(self, bot=None):
                self.bot = bot

    class CommandHandler:
        def __init__(self, command: str, callback):
            self.command = command
            self.callback = callback

    class CallbackQueryHandler:
        def __init__(self, callback, pattern=None):
            self.callback = callback
            self.pattern = pattern

    class Application:
        def __init__(self, token: str):
            self.token = token
            self.handlers = []

        @classmethod
        def builder(cls):
            class Builder:
                def __init__(self):
                    self._token = ""

                def token(self, token: str):
                    self._token = token
                    return self

                def build(self):
                    return Application(self._token)
            return Builder()

        def add_handler(self, handler):
            self.handlers.append(handler)

        def run_polling(self):
            print("Running in mock mode: python-telegram-bot is not installed.")
