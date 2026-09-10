"""Conversation-driven product package builder.

The bot never calls the shared-host WordPress installation. It prepares a ZIP
which the future WordPress importer can turn into a draft variable product.
"""
from __future__ import annotations

import asyncio
import html
import io
import json
import re
import shutil
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Message, Update
from telegram.error import TimedOut, NetworkError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters

from bot import rbac
from bot.constants import CB
from bot.config import settings
from bot.services.ai_normalizer import ai_normalize
from bot.services.category_taxonomy import FORBIDDEN, TAXONOMY
from bot.services.phone_parser import normalize_caption
from bot.services.image_compressor import compress_image
from bot.services.product_extractor import ProductData, extract_accessory_models, extract_product
from bot.services.woocommerce_direct import create_draft, product_description

WAITING = 0
TEMP_DIR = Path("/tmp/tisaposttowp-products")

@dataclass
class ProductSession:
    files: list[Path] = field(default_factory=list)
    model_text: str = ""
    info_text: str = ""
    models: list[str] = field(default_factory=list)
    data: ProductData | None = None
    status_message_id: int | None = None
    mode: str = "new"
    image_mode: str = "keep"
    processing_media: bool = False

sessions: dict[int, ProductSession] = {}
album_buffers: dict[tuple[int, str], list[Message]] = {}
album_tasks: dict[tuple[int, str], asyncio.Task] = {}


class _ExtractionLog:
    """Small adapter for the migrated OPTION AI normalizer."""
    def add(self, level, message, *args):
        return None


EXTRACTION_LOG = _ExtractionLog()


async def _telegram_log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Send detailed, plain-text processing logs to the configured log group."""
    if not settings.log_chat_id:
        return
    try:
        # Telegram messages are limited to 4096 characters.
        for start in range(0, len(text), 3900):
            await context.bot.send_message(chat_id=settings.log_chat_id, text=text[start:start + 3900])
    except Exception:
        # Logging must never break the user's product flow.
        return


def _keyboard(session: ProductSession | None = None) -> InlineKeyboardMarkup:
    confirm_label = "✅ تأیید و ساخت پیش‌نویس" if not session or session.mode == "new" else "✅ تأیید و ساخت ZIP"
    rows = [[InlineKeyboardButton(confirm_label, callback_data="product:confirm")]]
    if session and session.mode == "update":
        keep = "✅ تصاویر فعلی" if session.image_mode == "keep" else "تصاویر فعلی"
        replace = "✅ جایگزینی تصاویر" if session.image_mode == "replace" else "جایگزینی تصاویر"
        rows.insert(0, [
            InlineKeyboardButton(keep, callback_data=CB.PHONE_IMAGE_KEEP),
            InlineKeyboardButton(replace, callback_data=CB.PHONE_IMAGE_REPLACE),
        ])
    rows.append([InlineKeyboardButton("✏️ اصلاح اطلاعات", callback_data="product:edit"), InlineKeyboardButton("❌ لغو", callback_data="product:cancel")])
    return InlineKeyboardMarkup(rows)


def _safe(name: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._")
    return name or fallback


def _media(message: Message) -> tuple[str, str] | None:
    if message.photo:
        return message.photo[-1].file_id, f"image_{message.message_id}.jpg"
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return message.document.file_id, _safe(message.document.file_name or "image.jpg", f"image_{message.message_id}.jpg")
    return None


def _caption(messages: list[Message]) -> str:
    return "\n".join((m.caption or m.text or "") for m in sorted(messages, key=lambda x: x.message_id) if (m.caption or m.text))


def _category_outline(categories: list[str]) -> list[str]:
    """Render taxonomy paths as a nested Telegram-friendly bullet list."""
    tree: dict[str, dict] = {}
    for raw_path in categories:
        parts = [part.strip() for part in re.split(r"\s*(?:>|&gt;)\s*", html.unescape(raw_path)) if part.strip()]
        branch = tree
        for part in parts:
            branch = branch.setdefault(part, {})

    lines: list[str] = []
    def walk(branch: dict[str, dict], depth: int = 0) -> None:
        for name, children in branch.items():
            # Telegram HTML does not support the nbsp entity reliably and
            # renders it as literal text. Use visible Unicode indentation.
            indent = "　" * (depth * 2)
            marker = "•" if depth == 0 else "◦"
            lines.append(f"{indent}{marker} {html.escape(name)}")
            walk(children, depth + 1)
    walk(tree)
    return lines


def _preview(data: ProductData) -> str:
    attrs = {}
    if len(data.models) >= 2:
        attrs["مدل"] = data.models
    attrs.update({name: values for name, values in data.attributes.items() if len(values) >= 2})
    lines = ["📦 <b>پیش‌نمایش محصول</b>", "", f"<b>عنوان:</b> {data.title or '⚠️ تشخیص داده نشد'}", f"<b>قیمت:</b> {(' | '.join(f'{k}: {v:,} تومان' for k, v in data.prices.items()) if data.prices else (f'{data.price:,} تومان' if data.price else 'تغییری ندارد / دریافت نشده'))}", f"<b>پیشوند SKU:</b> {data.sku_prefix or '⚠️ تشخیص داده نشد'}", "", "<b>ویژگی‌ها:</b>"]
    count = 1
    for name, values in attrs.items():
        values = list(dict.fromkeys(values))
        count *= max(1, len(values))
        lines.append(f"<b>{name}:</b> " + " | ".join(values))
    categories = [x for x in data.categories if x not in FORBIDDEN]
    lines += ["", f"<b>تعداد variation:</b> {count}", "<b>دسته‌بندی‌ها:</b>"]
    lines.extend(_category_outline(categories) or ["- تشخیص داده نشد"])
    lines += ["", "اطلاعات را بررسی کن و تأیید بزن."]
    return "\n".join(lines)


async def _extract(session: ProductSession) -> ProductData:
    # The first message is the media caption and is used only for model
    # extraction. Product title/price/SKU/other attributes must come from the
    # later information messages, otherwise a descriptive caption such as
    # «قاب پلنگی لنز» can incorrectly win over the actual title.
    model_source = (session.model_text + "\n" + session.info_text).strip()
    deterministic = normalize_caption(model_source)
    # The old OPTION bot's AI normalizer is now the primary phone detector.
    # Accessory families such as AirPods are handled separately because the
    # phone normalizer deliberately rejects them.
    ai_models = await ai_normalize(model_source, deterministic, EXTRACTION_LOG)
    models = [x.strip() for x in (ai_models or deterministic).split(" | ") if x.strip()]
    for accessory in extract_accessory_models(model_source):
        if accessory.casefold() not in {item.casefold() for item in models}:
            models.append(accessory)
    session.models = models
    # Do not make an unnecessary second AI request while only the photos are
    # being processed. It runs as soon as the information message arrives.
    # Product details may be split between the media caption and later
    # Telegram messages. The extractor must receive both texts in one request
    # so title, SKU, price, colors and models can complement each other.
    combined_text = "\n".join(part for part in (session.model_text, session.info_text) if part.strip())
    session.data = (await extract_product(
        combined_text,
        models,
        TAXONOMY,
        caption=session.model_text,
        info_text=session.info_text,
    ) if combined_text.strip() else ProductData(models=models))
    # AI is allowed to classify categories, but brand subcategories are
    # deterministic from the detected models. This prevents an AI response
    # containing only the parent category from losing «آیفون iphone».
    categories = [x.strip() for x in session.data.categories if x.strip() not in FORBIDDEN]
    parent = "قاب و کاور گوشی و تبلت"
    brand_paths = {
        "iphone": parent + " > آیفون iphone",
        "samsung": parent + " > سامسونگ samsung",
        "xiaomi": parent + " > شیائومی xiaomi",
    }
    model_text = " ".join(session.models).casefold()
    brand_detected = {
        "iphone": bool(re.search(r"\biphone\b", model_text)),
        "samsung": bool(re.search(r"\b(?:s\d{1,3}|a\d{1,3})(?:\s|$)|\b(?:samsung|galaxy|ultra|fe)\b", model_text)),
        "xiaomi": bool(re.search(r"\b(?:redmi|poco|xiaomi|mi)\b", model_text)),
    }
    for brand, path in brand_paths.items():
        if brand_detected[brand]:
            # Always use one canonical hierarchy. Remove AI's standalone
            # parent/leaf entries so the preview cannot show duplicates.
            leaf = path.split(" > ")[-1]
            categories = [
                x for x in categories
                if x not in {parent, leaf, path} and not x.startswith(path + " > ")
            ]
            categories.append(path)
    source = (session.model_text + "\n" + session.info_text).casefold()
    explicit_category_words = {
        "چاپی": r"چاپی|چاپ|پرینت",
        "سیلیکونی": r"سیلیکونی|سیلیکون",
        "عروسکی": r"عروسکی|عروسک",
        "ست دو نفره": r"ست\s*دو\s*نفره",
        "قاب تبلت": r"قاب\s*تبلت|تبلت",
        "گلس": r"گلس|محافظ\s*صفحه",
        "کیف سیلیکونی": r"کیف\s*سیلیکونی",
    }
    normalized_categories = []
    for category in categories:
        category = category.replace("&gt;", ">")
        # AI sometimes uses slash instead of the hierarchy separator.
        category = re.sub(r"\s*/\s*", " > ", category).strip()
        leaf = category.split(">")[-1].strip()
        if leaf in explicit_category_words and not re.search(explicit_category_words[leaf], source):
            continue
        normalized_categories.append(category)
    # Keep one canonical path; a parent by itself is redundant when its child
    # path is already present.
    normalized_categories = list(dict.fromkeys(normalized_categories))
    for category in list(normalized_categories):
        if " > " in category:
            parent_name = category.split(" > ", 1)[0].strip()
            normalized_categories = [x for x in normalized_categories if x != parent_name]
    accessory_models = [x for x in session.models if x.casefold().startswith("airpods ")]
    if accessory_models:
        airpods_root = "لوازم جانبی ایرپاد > Apple AirPods"
        normalized_categories = [airpods_root]
        airpods_children = {
            "1/2": "Airpods 1/2", "3": "Airpods 3", "4": "Airpods 4",
            "pro": "Airpods Pro", "pro2": "Airpods Pro 2", "pro3": "Airpods Pro 3",
        }
        for model in accessory_models:
            compact = re.sub(r"\s+", "", model.casefold())
            for key, leaf in airpods_children.items():
                if key in compact:
                    path = airpods_root + " > " + leaf
                    if path not in normalized_categories:
                        normalized_categories.append(path)
    session.data.categories = normalized_categories
    session.data.attributes = {k: v for k, v in session.data.attributes.items() if k.strip().casefold() not in {"مدل", "model"}}
    return session.data


async def _status(context: ContextTypes.DEFAULT_TYPE, chat_id: int, session: ProductSession, text: str) -> None:
    """Keep one live progress message and mirror every stage to the log group."""
    await _telegram_log(context, f"[product:{chat_id}] {text}")
    try:
        if session.status_message_id:
            await context.bot.edit_message_text(chat_id=chat_id, message_id=session.status_message_id, text=text)
        else:
            msg = await context.bot.send_message(chat_id=chat_id, text=text)
            session.status_message_id = msg.message_id
    except Exception:
        pass


async def _download_with_retry(context: ContextTypes.DEFAULT_TYPE, file_id: str, target: Path, attempts: int = 3):
    """Telegram downloads can time out on shared hosting; retry only network timeouts."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            tg_file = await context.bot.get_file(file_id)
            if tg_file.file_size and tg_file.file_size > settings.max_download_mb * 1024 * 1024:
                raise ValueError(f"فایل بزرگ‌تر از سقف مجاز ({settings.max_download_mb:g} MB) است.")
            await tg_file.download_to_drive(custom_path=target)
            return tg_file.file_size or 0
        except (TimedOut, NetworkError, asyncio.TimeoutError, TimeoutError) as exc:
            last_error = exc
            if attempt == attempts:
                raise
            await asyncio.sleep(attempt * 1.5)
    if last_error:
        raise last_error


async def _prepare_files(user_id: int, messages: list[Message], context: ContextTypes.DEFAULT_TYPE) -> None:
    session = sessions[user_id]
    session.processing_media = True
    root = TEMP_DIR / f"{user_id}_{int(time.time() * 1000)}"
    root.mkdir(parents=True, exist_ok=True)
    session.files.clear()
    await _telegram_log(context, f"[product:{user_id}] شروع پردازش رسانه؛ تعداد پیام‌ها: {len(messages)}")
    await _telegram_log(context, f"[product:{user_id}] کپشن کامل رسانه:\n{_caption(messages) or '<بدون کپشن>'}")
    await _status(context, user_id, session, "📥 مرحله ۱ از ۴: دریافت عکس‌ها از تلگرام...")
    media_items = [(m, _media(m)) for m in sorted(messages, key=lambda x: x.message_id)]
    media_items = [(m, item) for m, item in media_items if item]
    semaphore = asyncio.Semaphore(3)

    async def download_one(index, item):
        message, media = item
        file_id, original = media
        target = root / f"{index:02d}_{_safe(original, 'image.jpg')}"
        async with semaphore:
            await _download_with_retry(context, file_id, target)
        size = target.stat().st_size if target.exists() else 0
        await _telegram_log(context, f"[product:{user_id}] عکس {index}/{len(media_items)} دریافت شد: {original} ({size} bytes)")
        return target, original, size

    await _status(context, user_id, session, f"📥 مرحله ۱ از ۴: دریافت هم‌زمان {len(media_items)} عکس...")
    downloaded = await asyncio.gather(*(download_one(i, item) for i, item in enumerate(media_items, 1)))
    await _status(context, user_id, session, f"🗜️ مرحله ۲ از ۴: فشرده‌سازی هم‌زمان {len(downloaded)} عکس...")

    async def compress_one(index, item):
        target, original, original_size = item
        async with semaphore:
            compressed = await asyncio.to_thread(compress_image, target, root / "compressed")
        compressed_size = compressed.stat().st_size if compressed.exists() else 0
        await _telegram_log(context, f"[product:{user_id}] عکس {index} فشرده شد: {original_size} -> {compressed_size} bytes")
        return compressed

    session.files = list(await asyncio.gather(*(compress_one(i, item) for i, item in enumerate(downloaded, 1))))
    await _status(context, user_id, session, "🤖 مرحله ۳ از ۴: تشخیص مدل‌ها و اطلاعات با AI...")
    session.model_text = _caption(messages)
    await _extract(session)
    session.processing_media = False
    await _telegram_log(context, f"[product:{user_id}] مدل‌های نهایی تشخیص‌داده‌شده:\n{chr(10).join(session.models) or '<هیچ مدلی تشخیص داده نشد>'}")
    combined_text = "\n".join(part for part in (session.model_text, session.info_text) if part.strip())
    await _telegram_log(context, f"[product:{user_id}] متن ترکیبی کپشن و اطلاعات:\n{combined_text or '<خالی>'}")
    await _telegram_log(context, f"[product:{user_id}] داده استخراج‌شده:\n{json.dumps(session.data.to_dict() if session.data else {}, ensure_ascii=False, indent=2)}")
    await _status(context, user_id, session, "✅ مرحله ۴ از ۴: اطلاعات آماده شد؛ در انتظار بررسی شما...")
    if session.info_text:
        await context.bot.send_message(user_id, _preview(session.data), parse_mode="HTML", reply_markup=_keyboard(session))
    else:
        await context.bot.send_message(user_id, "✅ عکس‌ها دریافت و فشرده شدند. حالا متن اطلاعات محصول را بفرست.")


async def _flush_album(key: tuple[int, str], context: ContextTypes.DEFAULT_TYPE) -> None:
    await asyncio.sleep(settings.album_wait_seconds)
    messages = album_buffers.pop(key, [])
    album_tasks.pop(key, None)
    if messages:
        try:
            await _prepare_files(key[0], messages, context)
        except Exception as exc:
            details = traceback.format_exc()
            await _telegram_log(context, f"[product:{key[0]}] خطا در پردازش آلبوم: {type(exc).__name__}: {exc}\n{details}")
            await context.bot.send_message(key[0], f"❌ خطا در پردازش عکس‌ها: {type(exc).__name__}: {exc}")


async def entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    query = update.callback_query
    if not user or not rbac.is_allowed(user.id):
        await query.answer("⛔ دسترسی ندارید.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    mode = "update" if query.data == CB.PHONE_RESTOCK else "new"
    sessions[user.id] = ProductSession(mode=mode)
    await _telegram_log(context, f"[product:{user.id}] ورود به جریان محصول: {mode}")
    prompt = ("🔄 عکس‌ها و مدل‌های محصول موجود را بفرست. سپس قیمت و ویژگی‌های جدید را ارسال کن. "
              "عنوان و SKU محصول موجود تغییر نمی‌کند." if mode == "update" else
              "📦 عکس‌های محصول را بفرست. کپشن عکس‌ها باید مدل‌های گوشی باشد؛ بعد از آن متن قیمت، عنوان، پیشوند SKU و ویژگی‌های دیگر را ارسال کن.")
    await query.edit_message_text(prompt)
    return WAITING


async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return WAITING
    sessions.setdefault(user.id, ProductSession())
    if message.media_group_id:
        key = (user.id, message.media_group_id)
        album_buffers.setdefault(key, []).append(message)
        if key not in album_tasks:
            album_tasks[key] = asyncio.create_task(_flush_album(key, context))
    else:
        await _prepare_files(user.id, [message], context)
    return WAITING


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return WAITING
    session = sessions.setdefault(user.id, ProductSession())
    incoming = message.text or ""
    session.info_text = (session.info_text + "\n" + incoming).strip()
    await _telegram_log(context, f"[product:{user.id}] متن جدید دریافت شد:\n{incoming}")
    # The text may arrive immediately after the album, before the delayed
    # album collector has finished downloading/compressing it. Keep the text
    # in the session and let _prepare_files render the final preview later.
    if not session.files or session.processing_media:
        await message.reply_text("✅ متن دریافت شد؛ پردازش عکس‌ها و تشخیص مدل‌ها ادامه دارد. بعد از پایان، اطلاعات کامل به‌روزرسانی می‌شود.")
        return WAITING
    data = await _extract(session)
    await _telegram_log(context, f"[product:{user.id}] پیش‌نمایش به‌روزرسانی شد:\n{json.dumps(data.to_dict(), ensure_ascii=False, indent=2)}")
    await message.reply_html(_preview(data), reply_markup=_keyboard(session))
    return WAITING


async def set_image_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    session = sessions.get(user.id if user else 0)
    if not session or session.mode != "update":
        await query.answer("این گزینه فقط برای شارژ محصول موجود است.", show_alert=True)
        return WAITING
    session.image_mode = "replace" if query.data == CB.PHONE_IMAGE_REPLACE else "keep"
    await query.answer("حالت تصاویر ذخیره شد.")
    if session.data:
        await query.edit_message_text(_preview(session.data), parse_mode="HTML", reply_markup=_keyboard(session))
    return WAITING


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    session = sessions.get(user.id if user else 0)
    if not session or not session.files or not session.data:
        await query.answer("اول عکس و اطلاعات محصول را بفرست.", show_alert=True)
        return WAITING
    data = session.data
    if session.mode == "new":
        # Accessories can be valid products without a model attribute; title,
        # SKU and price are sufficient when regular attributes such as color
        # are present.
        required_missing = (not data.price or not data.title or not data.sku_prefix)
    else:
        # In restock mode, an omitted field means "leave the current product
        # value unchanged". At least one actual change must be supplied.
        required_missing = (not data.price and not data.models and not data.attributes)
    if required_missing:
        await query.answer("اطلاعات قابل اعمال برای این عملیات وجود ندارد.", show_alert=True)
        return WAITING
    await query.answer("در حال ساخت پیش‌نویس مستقیم..." if session.mode == "new" else "در حال ساخت فایل ZIP...")
    await _telegram_log(context, f"[product:{user.id}] تأیید نهایی دریافت شد؛ داده نهایی:\n{json.dumps(data.to_dict(), ensure_ascii=False, indent=2)}")
    if session.mode == "new":
        try:
            await _status(context, user.id, session, "📤 در حال آپلود عکس‌ها و ساخت پیش‌نویس مستقیم در ووکامرس...")
            product_id, edit_url = await create_draft(data.to_dict(), session.files)
            await _telegram_log(context, f"[product:{user.id}] پیش‌نویس مستقیم ساخته شد: {product_id}")
            await context.bot.send_message(user.id, f"✅ پیش‌نویس محصول ساخته شد.\n\n🔗 {edit_url}\n\nانتشار نهایی فقط از داخل سایت انجام می‌شود.")
            _cleanup(user.id)
            return ConversationHandler.END
        except Exception as exc:
            await _telegram_log(context, f"[product:{user.id}] ساخت مستقیم ناموفق بود: {type(exc).__name__}: {exc}")
            await query.edit_message_text(f"❌ ساخت مستقیم محصول ناموفق بود:\n{type(exc).__name__}: {exc}")
            return WAITING
    await _telegram_log(context, f"[product:{user.id}] حالت ZIP/شارژ انتخاب شد؛ ساخت ZIP شروع شد.")
    zip_path = TEMP_DIR / f"product_{user.id}_{int(time.time())}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    usable_attributes = {name: values for name, values in data.attributes.items() if len(values) >= 2}
    if len(data.models) >= 2:
        usable_attributes = {"مدل": data.models, **usable_attributes}
    manifest = {"mode": session.mode, "title": data.title, "price": data.price, "prices": data.prices, "sku_prefix": data.sku_prefix, "models": data.models, "attributes": usable_attributes, "categories": data.categories, "description": product_description(data.to_dict()), "product_type": "variable" if usable_attributes else "simple", "image_mode": session.image_mode}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("product.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for index, path in enumerate(session.files, 1):
            archive.write(path, f"images/{index:02d}_{path.name}")
    await _telegram_log(context, f"[product:{user.id}] ZIP ساخته شد: {zip_path.name}؛ تعداد تصاویر: {len(session.files)}")
    with zip_path.open("rb") as handle:
        await context.bot.send_document(user.id, handle, filename="product.zip", caption="✅ فایل محصول آماده شد. این فایل را در افزونه وردپرس آپلود کن.")
    await _telegram_log(context, f"[product:{user.id}] ZIP برای کاربر ارسال شد.")
    await query.edit_message_text("✅ ZIP ساخته و ارسال شد. برای ساخت محصول بعدی دوباره از منوی اصلی وارد شو.")
    _cleanup(user.id)
    return ConversationHandler.END


def _cleanup(user_id: int) -> None:
    session = sessions.pop(user_id, None)
    if session:
        for path in session.files:
            shutil.rmtree(path.parent, ignore_errors=True)


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("✏️ اصلاحاتت را به‌صورت متن بفرست؛ اطلاعات جدید روی اطلاعات قبلی اعمال می‌شود.")
    return WAITING


async def exit_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if user:
        _cleanup(user.id)
    await update.effective_message.reply_text("❌ ساخت محصول لغو شد. برای شروع دوباره /start را بزن.")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    await update.callback_query.answer()
    if user:
        _cleanup(user.id)
    await update.callback_query.edit_message_text("❌ ساخت محصول لغو شد. برای شروع دوباره /start را بزن.")
    return ConversationHandler.END


def register(app: Application) -> None:
    conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(entry, pattern=f"^({CB.PHONE_POST}|{CB.PHONE_NEW}|{CB.PHONE_RESTOCK})$"),
        ],
        states={WAITING: [
            MessageHandler(filters.PHOTO | filters.Document.IMAGE, on_media),
            MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
            CallbackQueryHandler(confirm, pattern=r"^product:confirm$"),
            CallbackQueryHandler(set_image_mode, pattern=f"^({CB.PHONE_IMAGE_KEEP}|{CB.PHONE_IMAGE_REPLACE})$"),
            CallbackQueryHandler(edit, pattern=r"^product:edit$"),
            CallbackQueryHandler(cancel, pattern=r"^product:cancel$"),
        ]},
        fallbacks=[
            CallbackQueryHandler(cancel, pattern=r"^product:cancel$"),
            CommandHandler(["cancel", "start", "menu"], exit_command),
        ],
        name="product_builder",
    )
    app.add_handler(conv)
