"""Sudo-only runtime preferences screen (⚙️ تنظیمات).

Lets the owner flip, from inside the bot, whether certain feature buttons are
shown to admins. Values are persisted in data/preferences.json and survive
restarts (see bot/services/preferences.py).
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot import rbac
from bot.constants import CB
from bot.services import preferences

logger = logging.getLogger(__name__)


def _text() -> str:
    show = preferences.get("show_compress_to_admins")
    state = "✅ روشن" if show else "❌ خاموش"
    return (
        "⚙️ <b>تنظیمات</b>\n\n"
        "نمایش دکمه‌ها برای ادمین‌ها:\n\n"
        f"🗜️ فشرده‌سازی عکس‌ها — <b>{state}</b>"
    )


def _keyboard() -> InlineKeyboardMarkup:
    show = preferences.get("show_compress_to_admins")
    toggle_label = "🙈 مخفی از ادمین‌ها" if show else "👁️ نمایش به ادمین‌ها"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(toggle_label, callback_data=CB.SETTINGS_TOGGLE_COMPRESS)],
            [InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)],
        ]
    )


async def cb_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open the settings screen. Sudo only."""
    query = update.callback_query
    user = update.effective_user
    if not user or not rbac.is_sudo(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(_text(), reply_markup=_keyboard(), parse_mode="HTML")


async def cb_toggle_compress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Flip the admin visibility of the compress button and re-render."""
    query = update.callback_query
    user = update.effective_user
    if not user or not rbac.is_sudo(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    current = preferences.get("show_compress_to_admins")
    preferences.set_flag("show_compress_to_admins", not current)
    logger.info("Sudo %s toggled show_compress_to_admins → %s", user.id, not current)
    await query.answer("✅ به‌روزرسانی شد.")
    await query.edit_message_text(_text(), reply_markup=_keyboard(), parse_mode="HTML")


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_settings, pattern=f"^{CB.SETTINGS}$"))
    app.add_handler(
        CallbackQueryHandler(cb_toggle_compress, pattern=f"^{CB.SETTINGS_TOGGLE_COMPRESS}$")
    )
