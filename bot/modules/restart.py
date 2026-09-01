# -*- coding: utf-8 -*-
"""Restart the bot through supervisor.

Button «🔄 ری‌استارت» → confirmation screen → `supervisorctl restart <program>`.

The supervisorctl child is spawned in its own session so it survives the
bot process being stopped mid-restart. Before running it, we persist the
chat/message id to data/restart_pending.json; on the next startup
(post_init → notify_restart_complete) the bot edits that message to
"✅ back online" — real proof the restart cycle worked.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot.config import settings
from bot.constants import CB

logger = logging.getLogger(__name__)

# Anchored to the repo root so it works regardless of supervisor's cwd.
PENDING_FILE = Path(__file__).resolve().parents[2] / "data" / "restart_pending.json"
STARTUP_NOTIFY_MAX_AGE = 300  # seconds — ignore stale pending files


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ بله، ری‌استارت کن", callback_data=CB.RESTART_CONFIRM)],
            [InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)],
        ]
    )


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)]]
    )


async def cb_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Restart button → ask for confirmation."""
    query = update.callback_query
    user = update.effective_user
    if user and not settings.is_admin(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    await query.answer()
    await query.edit_message_text(
        "🔄 <b>ری‌استارت ربات</b>\n\n"
        f"ربات از طریق supervisor ری‌استارت می‌شود:\n"
        f"<code>{settings.supervisorctl_bin} restart {settings.supervisor_program}</code>\n\n"
        "مطمئنی؟",
        reply_markup=_confirm_keyboard(),
        parse_mode="HTML",
    )


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmed → persist pending-marker, run supervisorctl restart."""
    query = update.callback_query
    user = update.effective_user
    if user and not settings.is_admin(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return

    await query.answer()
    logger.warning("Restart requested by user %s via supervisor", user.id if user else "?")

    msg = await query.edit_message_text("♻️ در حال ری‌استارت از طریق supervisor…")

    # Marker so the freshly-started process can confirm success in this chat.
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(
        json.dumps(
            {"chat_id": msg.chat_id, "message_id": msg.message_id, "ts": time.time()}
        ),
        encoding="utf-8",
    )

    try:
        # start_new_session=True → detached from our process group, so it
        # survives supervisor stopping us and still issues the `start` part.
        proc = await asyncio.create_subprocess_exec(
            settings.supervisorctl_bin,
            "restart",
            settings.supervisor_program,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        out_b, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        output = (out_b or b"").decode(errors="replace").strip()
    except FileNotFoundError:
        PENDING_FILE.unlink(missing_ok=True)
        await msg.edit_text(
            f"❌ <code>{settings.supervisorctl_bin}</code> پیدا نشد.\n"
            "مسیر آن را در <code>SUPERVISORCTL_BIN</code> فایل .env تنظیم کن.",
            reply_markup=_menu_keyboard(),
            parse_mode="HTML",
        )
        return
    except asyncio.TimeoutError:
        # Most likely we're being stopped right now — let the restart proceed.
        return

    # If we're still alive here, the restart didn't take us down → something
    # is wrong (bad program name, supervisor not running, ...).
    logger.error("supervisorctl exited rc=%s but bot is still alive: %s", proc.returncode, output)
    PENDING_FILE.unlink(missing_ok=True)
    await msg.edit_text(
        "❌ ری‌استارت انجام نشد — ربات هنوز زنده است.\n\n"
        f"خروجی supervisorctl (rc={proc.returncode}):\n<code>{output[:700] or '—'}</code>\n\n"
        "نام برنامه در <code>SUPERVISOR_PROGRAM</code> فایل .env را چک کن.",
        reply_markup=_menu_keyboard(),
        parse_mode="HTML",
    )


async def notify_restart_complete(app: Application) -> None:
    """Called from post_init on every startup: if a restart was pending,
    edit the «در حال ری‌استارت» message to confirm the bot is back."""
    if not PENDING_FILE.exists():
        return
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        PENDING_FILE.unlink(missing_ok=True)
        return
    PENDING_FILE.unlink(missing_ok=True)

    if time.time() - float(data.get("ts", 0)) > STARTUP_NOTIFY_MAX_AGE:
        return  # stale marker from an old crash — don't ping anyone

    text = "✅ ربات با موفقیت ری‌استارت شد و دوباره آنلاین است."
    try:
        await app.bot.edit_message_text(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
            text=text,
            reply_markup=_menu_keyboard(),
        )
    except Exception:  # noqa: BLE001 — message may be gone; fall back to a new one
        try:
            await app.bot.send_message(
                chat_id=data["chat_id"], text=text, reply_markup=_menu_keyboard()
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not deliver restart confirmation")


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_ask, pattern=f"^{CB.RESTART_ASK}$"))
    app.add_handler(CallbackQueryHandler(cb_confirm, pattern=f"^{CB.RESTART_CONFIRM}$"))
