"""Main menu inline keyboard.

This is the bot's home screen, and it is **role-aware**: the buttons shown
depend on who is looking at it.

* **sudo** (owner)  → sees everything (converter + ping + restart + admin mgmt).
* **admin**         → sees ONLY «📦 تبدیل فایل کد رهگیری».
* everyone else     → never reaches the menu (blocked in bot/modules/start.py).

New feature buttons get added here as their modules are built — each
button's callback_data should point at a constant in bot.constants.CB.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.constants import CB
from bot import rbac


def main_menu_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    role = rbac.role(user_id)

    # Only sudo and admin ever reach the menu (bot/modules/start.py blocks the
    # rest); this guard is purely defensive — a plain user gets no buttons.
    if role == rbac.USER:
        return InlineKeyboardMarkup([])

    rows: list[list[InlineKeyboardButton]] = [
        # --- feature buttons ---
        [InlineKeyboardButton("📦 تبدیل فایل کد رهگیری", callback_data=CB.TRACKING_CONVERT)],
    ]

    if role == rbac.SUDO:
        # Sudo-only actions: diagnostics, restart and admin management are hidden
        # from admins.
        rows.append(
            [
                InlineKeyboardButton("🏓 Ping", callback_data=CB.PING),
                InlineKeyboardButton("🔄 ری‌استارت", callback_data=CB.RESTART_ASK),
            ]
        )
        rows.append(
            [InlineKeyboardButton("👥 مدیریت ادمین‌ها", callback_data=CB.ADMINS_LIST)]
        )

    return InlineKeyboardMarkup(rows)
