"""Ping — diagnostics button to verify the bot is alive and responsive."""

from __future__ import annotations

import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.constants import CB

logger = logging.getLogger(__name__)


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏓 Ping again", callback_data=CB.PING)],
            [InlineKeyboardButton("⬅️ Main menu", callback_data=CB.MAIN_MENU)],
        ]
    )


async def cb_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer the Ping button with a round-trip time measurement."""
    query = update.callback_query

    started = time.perf_counter()
    await query.answer("Pong! 🏓")
    rtt_ms = (time.perf_counter() - started) * 1000

    logger.info("Ping from user %s — %.0f ms", update.effective_user.id, rtt_ms)

    await query.edit_message_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"API round-trip: <code>{rtt_ms:.0f} ms</code>\n"
        f"Bot is up and responding. ✅",
        reply_markup=_back_keyboard(),
        parse_mode="HTML",
    )


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_ping, pattern=f"^{CB.PING}$"))
