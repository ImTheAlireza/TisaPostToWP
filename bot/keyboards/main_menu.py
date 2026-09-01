"""Main menu inline keyboard.

This is the bot's home screen. New feature buttons get added here as
their modules are built — each button's callback_data should point at
a constant in bot.constants.CB.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.constants import CB


def main_menu_keyboard() -> InlineKeyboardMarkup:
    rows = [
        # --- feature buttons will be added here, row by row ---
        [InlineKeyboardButton("🏓 Ping", callback_data=CB.PING)],
    ]
    return InlineKeyboardMarkup(rows)
