"""/start command and main menu navigation."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from bot import rbac
from bot.constants import CB, DENIED_TEXT, WELCOME_TEXT
from bot.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)


def _deny(update: Update) -> None:
    """Log + reply to a user who is not allowed to use the bot."""
    user = update.effective_user
    if user:
        logger.warning(
            "Denied access to user %s (%s) — role=%s",
            user.id, user.username, rbac.role(user.id),
        )
    if update.callback_query:
        update.callback_query.answer("⛔ دسترسی ندارید.", show_alert=True)
    elif update.effective_message:
        update.effective_message.reply_text(DENIED_TEXT)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /start shows the main menu (sudo/admin only)."""
    user = update.effective_user
    if not user or not rbac.is_allowed(user.id):
        _deny(update)
        return

    logger.info("User %s (%s) opened the main menu — role %s",
                user.id, user.username, rbac.role(user.id))
    await update.effective_message.reply_html(
        WELCOME_TEXT, reply_markup=main_menu_keyboard(user.id)
    )


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the main menu from any screen (callback button)."""
    query = update.callback_query
    user = update.effective_user
    if not user or not rbac.is_allowed(user.id):
        _deny(update)
        return

    await query.answer()
    await query.edit_message_text(
        WELCOME_TEXT, reply_markup=main_menu_keyboard(user.id), parse_mode="HTML"
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=f"^{CB.MAIN_MENU}$"))
