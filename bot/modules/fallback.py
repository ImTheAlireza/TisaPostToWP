"""Fallback handlers — catch anything no other module handled.

Registered LAST (see bot/modules/__init__.py) and in a later handler
group, so real features always win.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)


async def cb_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """An inline button whose callback data no module recognizes
    (e.g. a stale keyboard from an older bot version)."""
    query = update.callback_query
    logger.warning("Unknown callback data: %r", query.data)
    await query.answer("This button is no longer available. Use /start to refresh.", show_alert=True)


async def msg_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Free text outside of any conversation — nudge the user to the menu."""
    await update.effective_message.reply_text(
        "I work with buttons — send /start to open the menu."
    )


async def doc_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A document sent outside of any flow — point to the converter button."""
    await update.effective_message.reply_text(
        "برای تبدیل فایل، اول از منو دکمه «📦 تبدیل فایل کد رهگیری» را بزن.\n"
        "منو: /start"
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler: log the exception, tell the user something broke."""
    logger.exception("Unhandled exception while processing update: %s", update, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Something went wrong. Try /start.")
        except Exception:  # noqa: BLE001 — never raise from the error handler
            pass


def register(app: Application) -> None:
    # Same group (0) as everything else — order of registration decides,
    # and this module is registered last.
    app.add_handler(CallbackQueryHandler(cb_unknown))
    app.add_handler(MessageHandler(filters.Document.ALL, doc_unknown))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, msg_unknown))
    app.add_error_handler(on_error)
