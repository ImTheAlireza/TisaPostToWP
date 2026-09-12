"""Image compression-only utility (فشرده‌سازی عکس‌ها).

A single button starts a short conversation: the user forwards (or sends)
messages containing photos / image documents, the bot downloads them, runs
them through ``compress_image``, and sends the compressed files back. No
WordPress / product involvement — just local image processing.

Admins can use it only while the sudo owner has it enabled for them (see the
«⚙️ تنظیمات» screen and bot/services/preferences.py).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import rbac
from bot.config import settings
from bot.constants import CB
from bot.keyboards import main_menu_keyboard, main_menu_text
from bot.services import preferences
from bot.services.image_compressor import compress_image

logger = logging.getLogger(__name__)

WAITING = 0
TEMP_DIR = Path("/tmp/tisaposttowp-compress")

INSTRUCTION = (
    "🗜️ <b>فشرده‌سازی عکس‌ها</b>\n\n"
    "پیام‌هایی که عکس دارند را <b>فوروارد</b> کن (یا مستقیم بفرست).\n"
    "هر عکس دانلود و فشرده می‌شود و به‌صورت فایل برایت ارسال می‌شود.\n\n"
    "برای پایان: /cancel"
)


def _can_compress(user_id: int | None) -> bool:
    """True for sudo, and for admins only while the preference allows it."""
    role = rbac.role(user_id)
    return role == rbac.SUDO or (
        role == rbac.ADMIN and preferences.get("show_compress_to_admins")
    )


def _safe_name(name: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return name or fallback


def _media(message) -> tuple[str, str] | None:
    """Return (file_id, filename) for a photo or image document, else None."""
    if message.photo:
        return message.photo[-1].file_id, f"image_{message.message_id}.jpg"
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return (
            message.document.file_id,
            _safe_name(
                message.document.file_name or "image.jpg",
                f"image_{message.message_id}.jpg",
            ),
        )
    return None


async def _download(context: ContextTypes.DEFAULT_TYPE, file_id: str, target: Path) -> int:
    tg_file = await context.bot.get_file(file_id)
    if tg_file.file_size and tg_file.file_size > settings.max_download_mb * 1024 * 1024:
        raise ValueError(f"فایل بزرگ‌تر از سقف مجاز ({settings.max_download_mb:g} MB) است.")
    await tg_file.download_to_drive(custom_path=target)
    return tg_file.file_size or 0


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«فشرده‌سازی عکس‌ها» pressed → wait for media messages."""
    query = update.callback_query
    user = update.effective_user
    if not user or not _can_compress(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text(
        INSTRUCTION,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)]]
        ),
        parse_mode="HTML",
    )
    return WAITING


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Download, compress and send back every image in the incoming message."""
    message = update.effective_message
    user = update.effective_user
    if not user or not message:
        return WAITING

    media = _media(message)
    if not media:
        await message.reply_text("❗ عکسی در این پیام پیدا نکردم. پیامی با عکس فوروارد کن.")
        return WAITING

    file_id, name = media
    root = TEMP_DIR / f"{user.id}_{int(time.time() * 1000)}"
    root.mkdir(parents=True, exist_ok=True)
    src = root / _safe_name(name, "image.jpg")
    status = await message.reply_text("⬇️ در حال دانلود عکس...")
    try:
        await _download(context, file_id, src)
        await status.edit_text("🗜️ در حال فشرده‌سازی...")
        compressed = await asyncio.to_thread(compress_image, src, root / "out")
        await status.edit_text("📤 در حال ارسال...")
        with compressed.open("rb") as handle:
            await context.bot.send_document(
                user.id,
                document=handle,
                filename=compressed.name,
                caption=f"🗜️ فشرده شد: {name}",
            )
        await status.delete()
    except Exception as exc:  # noqa: BLE001 — report to the user, keep the flow alive
        logger.exception("Compress flow failed for user %s", user.id)
        try:
            await status.edit_text(f"❌ خطا: {type(exc).__name__}: {exc}")
        except Exception:  # noqa: BLE001
            pass
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return WAITING


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A stray text while waiting → remind the user what to send."""
    await update.effective_message.reply_text(
        "🗜️ پیامی با عکس فوروارد کن تا فشرده شود.\nبرای پایان: /cancel"
    )
    return WAITING


async def cb_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The in-flow «بازگشت به منو» button."""
    query = update.callback_query
    user = update.effective_user
    await query.answer()
    await query.edit_message_text(
        main_menu_text(user.id if user else None, user),
        reply_markup=main_menu_keyboard(user.id if user else None),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel, /start or /menu during the flow → back to the main menu."""
    user = update.effective_user
    await update.effective_message.reply_html(
        main_menu_text(user.id if user else None, user),
        reply_markup=main_menu_keyboard(user.id if user else None),
    )
    return ConversationHandler.END


def register(app: Application) -> None:
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(entry, pattern=f"^{CB.COMPRESS}$")],
        states={
            WAITING: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_media),
                CallbackQueryHandler(cb_back_to_menu, pattern=f"^{CB.MAIN_MENU}$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_exit),
            CommandHandler("start", cmd_exit),
            CommandHandler("menu", cmd_exit),
        ],
        name="image_compress",
    )
    app.add_handler(conv)
