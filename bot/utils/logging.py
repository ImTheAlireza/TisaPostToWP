"""Logging setup for the bot."""

from __future__ import annotations

import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        level=getattr(logging, level, logging.INFO),
    )
    # httpx (used by python-telegram-bot) is very chatty at INFO
    logging.getLogger("httpx").setLevel(logging.WARNING)
