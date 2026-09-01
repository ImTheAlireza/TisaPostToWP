# -*- coding: utf-8 -*-
"""تبدیل فایل کد رهگیری — conversation flow.

Button «📦 تبدیل فایل کد رهگیری» → bot asks for an order file
(.xlsx / .csv / .pdf) → user sends it as a Document → bot replies with
tracking.csv (+ problems.txt if needed) and waits for the next file,
until the user cancels or goes back to the menu.

Processing logic lives in bot/services/processor.py (no Telegram code).
"""

from __future__ import annotations

import asyncio
import io
import logging
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

from bot.config import settings
from bot.constants import CB, WELCOME_TEXT
from bot.keyboards import main_menu_keyboard
from bot.services import processor

logger = logging.getLogger(__name__)

# Conversation states
ASK_FILE = 0

ALLOWED_EXTS = {".xlsx", ".csv", ".pdf"}

INSTRUCTIONS = (
    "📦 <b>تبدیل فایل کد رهگیری</b>\n\n"
    "یک فایل با یکی از این فرمت‌ها بفرست (به‌صورت Document، نه عکس):\n"
    "📊 اکسل (<code>.xlsx</code>) — خروجی جدول سفارش‌ها\n"
    "📄 CSV (<code>.csv</code>)\n"
    "📑 PDF (<code>.pdf</code>) — خروجی مستقیم سامانه تیساکیس / تیسا چاپ\n\n"
    "ربات این کارها را می‌کند:\n"
    "1️⃣ ستون‌های «بارکد» و «کد سفارش» را پیدا می‌کند\n"
    "2️⃣ مشکلات را گزارش می‌دهد (خالی، تکراری، فرمت اشتباه، …)\n"
    "3️⃣ فایل <code>tracking.csv</code> با ستون‌های "
    "<code>order_id,tracking_code</code> می‌سازد\n\n"
    "⚠️ کد سفارش‌های خالی در CSV خالی می‌مانند تا خودت تکمیل کنی."
)

NEXT_FILE_TEXT = "📤 فایل بعدی را بفرست، یا برگرد به منو."


def _cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.TRACKING_CANCEL)]]
    )


# --- Flow steps ---------------------------------------------------------------

async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Button pressed → show instructions, wait for a file."""
    query = update.callback_query
    user = update.effective_user
    if user and not settings.is_admin(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END

    await query.answer()
    logger.info("User %s entered tracking-converter flow", user.id if user else "?")
    await query.edit_message_text(
        INSTRUCTIONS, reply_markup=_cancel_keyboard(), parse_mode="HTML"
    )
    return ASK_FILE


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A document arrived while we're waiting — process it."""
    msg = update.effective_message
    doc = msg.document
    fname = doc.file_name or "file"
    ext = Path(fname).suffix.lower()

    if ext not in ALLOWED_EXTS:
        await msg.reply_text(
            "❌ فقط فایل‌های xlsx / csv / pdf پشتیبانی می‌شوند. دوباره بفرست.",
            reply_markup=_cancel_keyboard(),
        )
        return ASK_FILE

    status = await msg.reply_text("⏳ در حال پردازش…")
    tmp: Path | None = None
    try:
        tg_file = await doc.get_file()
        tmp = Path("/tmp") / f"input_{doc.file_unique_id}{ext}"
        await tg_file.download_to_drive(str(tmp))

        # پردازش در thread جدا تا ربات مسدود نشود
        csv_text, summary, problems_text = await asyncio.to_thread(
            processor.process_file, str(tmp), fname
        )

        await msg.reply_document(
            document=io.BytesIO(csv_text.encode("utf-8")),
            filename="tracking.csv",
            caption=summary[:950],  # محدودیت کپشن تلگرام ~1024
        )
        if problems_text:
            await msg.reply_document(
                document=io.BytesIO(problems_text.encode("utf-8")),
                filename="problems.txt",
                caption="📋 گزارش کامل مشکلات",
            )
        await status.delete()
        await msg.reply_text(NEXT_FILE_TEXT, reply_markup=_cancel_keyboard())
    except Exception as e:  # noqa: BLE001 — surface the reason to the user
        logger.exception("Processing failed for %s", fname)
        try:
            await status.delete()
        except Exception:  # noqa: BLE001
            pass
        await msg.reply_text(
            f"❌ خطا در پردازش:\n{e}\n\nفایل دیگری بفرست یا برگرد به منو.",
            reply_markup=_cancel_keyboard(),
        )
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass

    return ASK_FILE  # stay in the flow — user may send more files


async def on_wrong_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Text/photo/etc. while waiting for a file — nudge."""
    await update.effective_message.reply_text(
        "لطفاً فایل را به‌صورت Document (گیره 📎 → File) بفرست — xlsx / csv / pdf.",
        reply_markup=_cancel_keyboard(),
    )
    return ASK_FILE


# --- Exits ---------------------------------------------------------------------

async def cb_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«بازگشت به منو» button → end flow, show main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        WELCOME_TEXT, reply_markup=main_menu_keyboard(), parse_mode="HTML"
    )
    return ConversationHandler.END


async def cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """/cancel, /start or /menu during the flow → end it, show main menu."""
    await update.effective_message.reply_html(
        WELCOME_TEXT, reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END


# --- Registration ----------------------------------------------------------------

def register(app: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(entry, pattern=f"^{CB.TRACKING_CONVERT}$"),
        ],
        states={
            ASK_FILE: [
                MessageHandler(filters.Document.ALL, on_document),
                CallbackQueryHandler(cb_cancel, pattern=f"^{CB.TRACKING_CANCEL}$"),
                MessageHandler(~filters.COMMAND, on_wrong_input),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_exit),
            CommandHandler("start", cmd_exit),
            CommandHandler("menu", cmd_exit),
        ],
        name="tracking_converter",
    )
    app.add_handler(conv)
