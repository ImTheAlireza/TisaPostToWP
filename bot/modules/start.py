"""/start command and main menu navigation."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from bot.config import settings
from bot.constants import CB, WELCOME_TEXT
from bot.keyboards import main_menu_keyboard

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /start shows the main menu."""
    user = update.effective_user
    if user and not settings.is_admin(user.id):
        logger.warning("Unauthorized access attempt by user %s (%s)", user.id, user.username)
        await update.effective_message.reply_text("⛔ You are not authorized to use this bot.")
        return

    logger.info("User %s (%s) opened the main menu", user.id if user else "?", user.username if user else "?")
    await update.effective_message.reply_html(WELCOME_TEXT, reply_markup=main_menu_keyboard())


async def cb_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Return to the main menu from any screen (callback button)."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML"
    )


def register(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
    app.add_handler(CallbackQueryHandler(cb_main_menu, pattern=f"^{CB.MAIN_MENU}$"))
