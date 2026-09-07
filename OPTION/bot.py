from __future__ import annotations

import asyncio
import io
import logging
import mimetypes
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from telegram import Message, Update
from telegram.error import TelegramError
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.request import HTTPXRequest

from ai_normalizer import ai_normalize
from config import (
    ALBUM_WAIT_SECONDS,
    BOT_TOKEN,
    LOG_LEVEL,
    MAX_DOWNLOAD_MB,
    OWNER_USER_ID,
    OWNER_USER_IDS,
    TEMP_DIR,
)
from parser import normalize_caption
from image_compressor import compress_image

LOGGER = logging.getLogger("tisa_iphone_bot")

STATUS = {
    "received": "📥 دریافت شد...",
    "waiting": "⏳ در حال جمع‌کردن عکس‌های آلبوم...",
    "downloading": "📦 در حال دریافت عکس‌ها از تلگرام...",
    "compressing": "🗜️ در حال فشرده‌سازی عکس‌ها...",
    "parsing": "🔎 در حال استخراج مدل‌های گوشی...",
    "ai": "🤖 در حال بررسی و استانداردسازی با AI...",
    "validating": "🛡️ در حال اعتبارسنجی خروجی...",
    "uploading": "📤 در حال ارسال فایل‌ها...",
    "done": "✅ انجام شد.",
}


@dataclass
class JobLog:
    stream: io.StringIO = field(default_factory=io.StringIO)
    handler: Optional[logging.Handler] = None

    def start(self) -> None:
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
        LOGGER.addHandler(self.handler)

    def stop(self) -> None:
        if self.handler:
            LOGGER.removeHandler(self.handler)
            self.handler.close()
            self.handler = None

    def add(self, level: int, message: str, *args) -> None:
        LOGGER.log(level, message, *args)

    def text(self) -> str:
        return self.stream.getvalue()


@dataclass
class AlbumBuffer:
    chat_id: int
    user_id: int
    media_group_id: str
    messages: list[Message] = field(default_factory=list)
    last_update_at: float = field(default_factory=time.monotonic)
    task: Optional[asyncio.Task] = None
    status_message_id: Optional[int] = None


albums: dict[tuple[int, str], AlbumBuffer] = {}
albums_lock = asyncio.Lock()


def is_allowed(user_id: int | None) -> bool:
    return user_id is not None and user_id in OWNER_USER_IDS


def safe_filename(name: str, fallback: str) -> str:
    name = Path(name).name if name else fallback
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or fallback


def message_media(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return message.photo[-1].file_id, f"telegram_{message.message_id}.jpg"
    if message.document and (message.document.mime_type or "").lower().startswith("image/"):
        filename = safe_filename(message.document.file_name or "", f"telegram_{message.message_id}")
        if "." not in filename:
            filename += mimetypes.guess_extension(message.document.mime_type or "image/jpeg") or ".jpg"
        return message.document.file_id, filename
    return None


def combined_caption(messages: list[Message]) -> str:
    captions: list[str] = []
    for message in sorted(messages, key=lambda m: m.message_id):
        text = message.caption or message.text
        if text:
            captions.append(text)
    return "\n".join(captions)


async def set_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int | None, text: str) -> None:
    if not message_id:
        return
    try:
        await context.bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text)
    except TelegramError:
        # Status updates must never kill the real job.
        LOGGER.debug("Could not update status message", exc_info=True)


async def create_status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str = STATUS["received"]) -> int | None:
    try:
        msg = await context.bot.send_message(chat_id=chat_id, text=text)
        return msg.message_id
    except TelegramError:
        LOGGER.debug("Could not create status message", exc_info=True)
        return None


async def owner_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.effective_chat:
        return
    await update.effective_chat.send_message(
        f"Telegram User ID: {update.effective_user.id}\n"
        "این عدد را در OWNER_USER_ID داخل فایل .env قرار بده.\n\n"
        "برای چند کاربر، IDها را با کاما جدا کن."
    )


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.effective_user:
        return
    if not OWNER_USER_IDS:
        await update.effective_chat.send_message(
            "ربات بالا است، ولی OWNER_USER_ID تنظیم نشده.\n\nاول /id را بزن، سپس یک یا چند ID را در .env قرار بده و ربات را دوباره اجرا کن."
        )
        return
    if not is_allowed(update.effective_user.id):
        return
    await update.effective_chat.send_message(
        "✅ ربات آماده است.\n\nپست/آلبوم را Forward کن. مراحل پردازش روی یک پیام وضعیت نمایش داده می‌شود."
    )


async def send_document_with_retry(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    path: Path,
    attempts: int = 4,
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with path.open("rb") as file_handle:
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_handle,
                    filename=path.name,
                    read_timeout=120.0,
                    write_timeout=240.0,
                    connect_timeout=30.0,
                    pool_timeout=30.0,
                )
            return
        except (TelegramError, OSError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = 2 ** (attempt - 1)
            LOGGER.warning("Upload failed for %s (attempt %d/%d): %s; retrying in %ss", path.name, attempt, attempts, exc, delay)
            await asyncio.sleep(delay)
    if last_error:
        raise last_error


async def send_error_log(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    job_log: JobLog,
    error: BaseException,
    run_dir: Path,
) -> None:
    text = job_log.text()
    if not text:
        text = "No log output was captured."
    report = (
        "Tisa Bot Error Report\n"
        "=====================\n\n"
        f"Error: {type(error).__name__}: {error}\n\n"
        "Traceback:\n"
        f"{traceback.format_exc()}\n"
        "Captured log:\n"
        f"{text}\n"
    )
    log_path = run_dir / "error_log.txt"
    log_path.write_text(report, encoding="utf-8")
    try:
        await send_document_with_retry(context, chat_id, log_path, attempts=2)
    except Exception:
        LOGGER.exception("Could not send error log to owner")


async def process_job(
    chat_id: int,
    messages: list[Message],
    context: ContextTypes.DEFAULT_TYPE,
    status_message_id: int | None,
) -> None:
    run_dir = TEMP_DIR / f"job_{chat_id}_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    job_log = JobLog()
    job_log.start()

    try:
        job_log.add(logging.INFO, "Job started. messages=%d", len(messages))
        await set_status(context, chat_id, status_message_id, STATUS["downloading"])

        files: list[Path] = []
        seen_file_ids: set[str] = set()
        media_messages = [m for m in sorted(messages, key=lambda m: m.message_id) if message_media(m)]
        job_log.add(logging.INFO, "Found %d media messages.", len(media_messages))

        for index, message in enumerate(media_messages, start=1):
            media = message_media(message)
            if not media:
                continue
            file_id, original_name = media
            if file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            job_log.add(logging.INFO, "Downloading image %d/%d: %s", index, len(media_messages), original_name)
            tg_file = await context.bot.get_file(file_id)
            expected_size = tg_file.file_size or 0
            if expected_size and expected_size > MAX_DOWNLOAD_MB * 1024 * 1024:
                raise ValueError(f"{original_name} بزرگ‌تر از سقف دانلود تنظیم‌شده ({MAX_DOWNLOAD_MB:g} MB) است.")
            suffix = Path(original_name).suffix.lower() or ".jpg"
            target = run_dir / f"{index:02d}_{safe_filename(Path(original_name).stem, 'image')}{suffix}"
            await tg_file.download_to_drive(custom_path=target)
            files.append(target)

        await set_status(context, chat_id, status_message_id, STATUS["compressing"])
        compressed_files: list[Path] = []
        compression_dir = run_dir / "compressed"
        for index, source in enumerate(files, start=1):
            job_log.add(logging.INFO, "Compressing image %d/%d: %s", index, len(files), source.name)
            compressed = compress_image(source, compression_dir)
            try:
                original_size = source.stat().st_size
                compressed_size = compressed.stat().st_size
                ratio = (1 - compressed_size / original_size) * 100 if original_size else 0.0
                job_log.add(logging.INFO, "Compressed %s: %.1f KB -> %.1f KB (%.1f%% smaller)", source.name, original_size / 1024, compressed_size / 1024, ratio)
            except OSError:
                pass
            compressed_files.append(compressed)
        files = compressed_files

        raw_caption = combined_caption(messages)
        job_log.add(logging.INFO, "Raw caption length=%d", len(raw_caption))
        await set_status(context, chat_id, status_message_id, STATUS["parsing"])

        deterministic = normalize_caption(raw_caption)
        job_log.add(logging.INFO, "Deterministic parser output: %s", deterministic or "<empty>")

        await set_status(context, chat_id, status_message_id, STATUS["ai"])
        models = await ai_normalize(raw_caption, deterministic, job_log)

        await set_status(context, chat_id, status_message_id, STATUS["validating"])
        if models:
            job_log.add(logging.INFO, "Final model output: %s", models)
        else:
            job_log.add(logging.INFO, "Final model output is empty.")

        if not files and not models:
            await set_status(context, chat_id, status_message_id, "⚠️ چیزی قابل پردازش پیدا نشد.")
            return

        await set_status(context, chat_id, status_message_id, STATUS["uploading"])
        for index, path in enumerate(files, start=1):
            await set_status(context, chat_id, status_message_id, f"📤 در حال ارسال فایل‌ها... ({index}/{len(files)})")
            await send_document_with_retry(context, chat_id, path)

        if models:
            await context.bot.send_message(chat_id=chat_id, text=models)
        else:
            await context.bot.send_message(chat_id=chat_id, text="⚠️ مدل گوشی از متن پیدا نشد.")
        await set_status(context, chat_id, status_message_id, STATUS["done"])
        job_log.add(logging.INFO, "Job completed successfully.")

    except Exception as exc:
        job_log.add(logging.ERROR, "Processing failed: %s", exc)
        job_log.add(logging.ERROR, "%s", traceback.format_exc())
        LOGGER.exception("Processing failed")
        await set_status(context, chat_id, status_message_id, f"❌ خطا در پردازش: {type(exc).__name__}")
        try:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ خطا: {exc}\n📄 لاگ کامل را هم ارسال می‌کنم.")
        except TelegramError:
            LOGGER.debug("Could not send error summary", exc_info=True)
        await send_error_log(context, chat_id, job_log, exc, run_dir)
    finally:
        job_log.stop()
        shutil.rmtree(run_dir, ignore_errors=True)


async def flush_album(key: tuple[int, str], context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        while True:
            async with albums_lock:
                buffer = albums.get(key)
                if buffer is None:
                    return
                idle = time.monotonic() - buffer.last_update_at
                if idle >= ALBUM_WAIT_SECONDS:
                    messages = list(buffer.messages)
                    status_id = buffer.status_message_id
                    del albums[key]
                    break
            await asyncio.sleep(max(0.15, ALBUM_WAIT_SECONDS - idle))
        await process_job(key[0], messages, context, status_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception("Album flush failed")


async def queue_album(message: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
    key = (message.chat_id, message.media_group_id or "")
    async with albums_lock:
        buffer = albums.get(key)
        if buffer is None:
            buffer = AlbumBuffer(
                chat_id=message.chat_id,
                user_id=message.from_user.id if message.from_user else 0,
                media_group_id=message.media_group_id or "",
            )
            albums[key] = buffer
            buffer.status_message_id = await create_status(context, message.chat_id, STATUS["waiting"])
        if all(existing.message_id != message.message_id for existing in buffer.messages):
            buffer.messages.append(message)
        buffer.last_update_at = time.monotonic()
        if buffer.task is None or buffer.task.done():
            buffer.task = asyncio.create_task(flush_album(key, context))


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_allowed(user.id):
        return
    if message.media_group_id:
        await queue_album(message, context)
        return
    status_id = await create_status(context, message.chat_id)
    await process_job(message.chat_id, [message], context, status_id)


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user or not is_allowed(user.id) or not message.text:
        return
    status_id = await create_status(context, message.chat_id)
    job_log = JobLog()
    run_dir = TEMP_DIR / f"text_{message.chat_id}_{int(time.time() * 1000)}"
    run_dir.mkdir(parents=True, exist_ok=True)
    job_log.start()
    try:
        await set_status(context, message.chat_id, status_id, STATUS["parsing"])
        deterministic = normalize_caption(message.text)
        await set_status(context, message.chat_id, status_id, STATUS["ai"])
        models = await ai_normalize(message.text, deterministic, job_log)
        if models:
            await message.reply_text(models)
        else:
            await message.reply_text("مدل گوشی از این متن پیدا نشد.")
        await set_status(context, message.chat_id, status_id, STATUS["done"])
    except Exception as exc:
        LOGGER.exception("Text processing failed")
        await set_status(context, message.chat_id, status_id, f"❌ خطا: {type(exc).__name__}")
        try:
            await context.bot.send_message(chat_id=message.chat_id, text=f"❌ خطا: {exc}")
        except TelegramError:
            pass
        await send_error_log(context, message.chat_id, job_log, exc, run_dir)
    finally:
        job_log.stop()
        shutil.rmtree(run_dir, ignore_errors=True)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Unhandled Telegram error: %s", context.error, exc_info=context.error)


async def post_init(application: Application) -> None:
    await application.bot.delete_webhook(drop_pending_updates=False)
    me = await application.bot.get_me()
    LOGGER.info("Bot connected: @%s (%s)", me.username, me.id)


def build_application() -> Application:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Put it in .env")
    if not OWNER_USER_IDS:
        LOGGER.warning("OWNER_USER_ID is not set. Use /id and configure it before processing posts.")

    request = HTTPXRequest(
        connection_pool_size=10,
        connect_timeout=30.0,
        read_timeout=120.0,
        write_timeout=240.0,
        pool_timeout=30.0,
    )
    get_updates_request = HTTPXRequest(
        connection_pool_size=5,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(post_init)
        .build()
    )
    application.add_handler(CommandHandler("id", owner_id_command))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, media_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    LOGGER.info("Starting local Telegram bot...")
    application = build_application()
    application.run_polling(
        poll_interval=0.0,
        timeout=10,
        bootstrap_retries=-1,
        drop_pending_updates=False,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()
