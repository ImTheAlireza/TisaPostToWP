"""Application factory — builds the bot and wires up all modules."""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder

from bot.config import settings
from bot.modules import register_all

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Open the main menu"),
    BotCommand("menu", "Open the main menu"),
]


async def _post_init(app: Application) -> None:
    """Runs once after the bot connects — set the command list shown in Telegram."""
    await app.bot.set_my_commands(BOT_COMMANDS)
    me = await app.bot.get_me()
    logger.info("Bot started as @%s (id=%s)", me.username, me.id)


def build_application() -> Application:
    app = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(_post_init)
        .build()
    )
    register_all(app)
    return app
