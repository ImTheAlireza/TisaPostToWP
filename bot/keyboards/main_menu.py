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

from bot.constants import BOT_NAME, CB, ROLE_BADGE
from bot import rbac


def _display_name(user) -> str:
    """A friendly display name for the greeting, or a fallback."""
    if user is None:
        return "عزیز"
    name = (user.first_name or "").strip()
    if not name and user.username:
        name = user.username
    return name or "عزیز"


def main_menu_text(user_id: int | None = None, user=None) -> str:
    """Personalised, role-aware welcome shown on the main-menu screen."""
    role = rbac.role(user_id)
    lines = [
        f"👋 سلام <b>{_display_name(user)}</b> عزیز!",
        f"به ربات مدیریت <b>{BOT_NAME}</b> خوش اومدی. 🌟",
        "",
        f"نقش شما: {ROLE_BADGE.get(role, '')}",
        "",
        "از دکمه‌های زیر یک گزینه رو انتخاب کن:",
    ]
    return "\n".join(lines)


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

    if role in (rbac.SUDO, rbac.ADMIN):
        # Product creation is available to approved admins as well as sudo.
        rows.append([
            InlineKeyboardButton("🆕 محصول جدید", callback_data=CB.PHONE_NEW),
            InlineKeyboardButton("🔄 شارژ محصول موجود", callback_data=CB.PHONE_RESTOCK),
        ])

    if role == rbac.SUDO:
        # Diagnostics, restart and admin management remain sudo-only.
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
