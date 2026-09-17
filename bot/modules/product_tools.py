"""🧾 آخرین محصولات و 🔍 تست پارسر — the two screens that make the bot checkable.

After a publish the owner wants the id, the link and the numbers (the result card
in :mod:`bot.keyboards.cards`), and the day after that they want to know what was
shipped at all. Before a rule change is trusted, they want to see why the bot
read a text the way it did.

Both screens are read-only: neither creates a product, nor touches a file, so an
admin can use them without a sudo gate. The parser test deliberately calls
:func:`bot.modules.product_flow.analyze`, i.e. the flow's own pipeline — a sandbox
that ran a second, simpler parser would answer the wrong question.
"""

from __future__ import annotations

import html
import logging
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from bot.constants import CB
from bot.keyboards.cards import result_card
from bot.services import postmodel as ev
from bot.services import products_ledger

logger = logging.getLogger(__name__)

#: one-shot flag for «تست پارسر» — a text message that arrives while it is set is
#: read as the sample instead of going to the main menu.
_PENDING_KEY = "parser_test_until"
_PENDING_TTL_SECONDS = 300
_MAX_REPORT = 3500


def _clip(text: str, parse_mode: str | None = "HTML") -> tuple[str, str | None]:
    """Fit a message into Telegram's limit without breaking its markup.

    A raw slice can cut an HTML tag in half, and Telegram answers that with a
    400 — the failure would look like «the bot is broken» while it is one
    character of truncation. Past the limit we therefore drop the tags and send
    plain text, which cannot be mis-nested.
    """
    if len(text) <= _MAX_REPORT:
        return text, parse_mode
    plain = re.sub(r"<[^>]+>", "", text)[:_MAX_REPORT]
    return plain + "\n… (میان‌بر برای رسیدن به حد تلگرام)", None


async def cb_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The last few cards, newest first, each one openable."""
    query = update.callback_query
    await query.answer()
    entries = products_ledger.recent(10)
    if not entries:
        await query.edit_message_text(
            "🧾 هنوز محصولی از این ربات ساخته نشده است.\n"
            "بعد از هر ساخت موفق (یا ناموفق)، همین‌جا قابل دیدن خواهد بود."
        )
        return
    lines = ["🧾 <b>آخرین محصولات</b>", ""]
    rows: list[list[InlineKeyboardButton]] = []
    for index, entry in enumerate(entries, 1):
        lines.append(f"{index}. " + html.escape(products_ledger.summary(entry), quote=False))
        label = str(entry.get("title") or "—")[:32]
        rows.append([InlineKeyboardButton(f"{index}. {label}", callback_data=f"{CB.PRODUCTS_OPEN}:{entry.get('key')}")])
    rows.append([InlineKeyboardButton("↩️ منو", callback_data=CB.MAIN_MENU)])
    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
    )


async def cb_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """One stored card: the numbers as they were approved, not as they read today."""
    query = update.callback_query
    await query.answer()
    key = (query.data or "").rsplit(":", 1)[-1]
    entry = products_ledger.find(key)
    if entry is None:
        await query.message.reply_text("این کارت پیدا نشد؛ احتمالاً تاریخچه پاک شده است.")
        return
    report = str(entry.get("report") or "")
    body = result_card(entry)
    if report:
        body += "\n\n——— پیش‌نمایشی که تأیید شد ———\n" + report
    text, mode = _clip(body)
    await query.message.reply_text(text, **({"parse_mode": mode} if mode else {}))


async def cb_parser_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data[_PENDING_KEY] = time.time() + _PENDING_TTL_SECONDS
    await query.message.reply_text(
        "🔍 متن نمونهٔ پست را بفرست (قیمت، مدل‌ها، رنگ‌ها…).\n\n"
        "همان مسیری اجرا می‌شود که یک پست واقعی می‌رود — با این تفاوت که هیچ "
        "محصولی ساخته نمی‌شود و هیچ فایلی نوشته نمی‌شود.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ انصراف", callback_data=CB.PARSER_TEST_CANCEL)
        ]]),
    )


async def cb_parser_test_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.pop(_PENDING_KEY, None)
    await query.message.reply_text("↩️ تست پارسر لغو شد.")


def _data_report(data: object) -> tuple[str, str | None]:
    """The extracted draft as (text, parse_mode), rendered like the preview."""
    payload = data.to_dict() if hasattr(data, "to_dict") else {}
    lines = ["🔍 <b>خروجی پارسر</b>", ""]

    def field(label: str, value: object) -> None:
        if value in (None, "", [], {}, 0):
            return
        if isinstance(value, (list, tuple)):
            shown = " | ".join(str(x) for x in value) or "—"
        elif isinstance(value, dict):
            shown = " | ".join(f"{k}: {v}" for k, v in value.items()) or "—"
        else:
            shown = str(value)
        lines.append(f"<b>{label}:</b> {html.escape(shown, quote=False)}")

    field("عنوان", payload.get("title"))
    field("قیمت", f"{payload.get('price'):,}" if payload.get("price") else "")
    field("قیمت گروهی", payload.get("prices"))
    field("مدل‌ها", payload.get("models"))
    field("رنگ‌ها", (payload.get("attributes") or {}).get("رنگ"))
    for name, values in (payload.get("attributes") or {}).items():
        if name != "رنگ":
            field(f"ویژگی {name}", values)
    field("محدودیت رنگ هر مدل", payload.get("model_colors"))
    field("پیشوند SKU", payload.get("sku_prefix"))
    field("دسته‌ها", payload.get("categories"))
    field("واریژن", payload.get("variation_count"))
    field("هشدار کاتالوگ", payload.get("warnings"))
    suggestions = payload.get("suggestions") or []
    if suggestions:
        field(
            "پیشنهاد",
            [f"{x.get('word')} ← {x.get('target')}؟" for x in suggestions if isinstance(x, dict)],
        )
    evidence = getattr(data, "evidence", None) or {}
    notes = list(payload.get("notes") or [])
    if evidence:
        lines.append("")
        lines.append(ev.preview_html(evidence, notes))
    elif notes:
        lines.append("")
        lines.append("<b>نکته‌ها:</b> " + html.escape("؛ ".join(notes), quote=False))
    return _clip("\n".join(lines))


async def on_parser_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Read the sample with the flow's own pipeline. Never creates anything."""
    message = update.effective_message
    if not message or not message.text:
        return
    until = float(context.user_data.get(_PENDING_KEY) or 0)
    if time.time() > until:
        return
    context.user_data.pop(_PENDING_KEY, None)
    from bot.modules.product_flow import analyze  # local: keeps this module importable alone

    try:
        data = await analyze(message.text)
    except Exception as exc:                       # pragma: no cover — parser bug
        logger.exception("parser test failed")
        await message.reply_text(f"⚠️ پارسر خطا داد: {type(exc).__name__}: {exc}")
        return
    text, mode = _data_report(data)
    await message.reply_text(text, **({"parse_mode": mode} if mode else {}))


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_recent, pattern=f"^{CB.PRODUCTS_RECENT}$"))
    app.add_handler(CallbackQueryHandler(cb_open, pattern=f"^{CB.PRODUCTS_OPEN}:\\w+$"))
    app.add_handler(CallbackQueryHandler(cb_parser_test, pattern=f"^{CB.PARSER_TEST}$"))
    app.add_handler(CallbackQueryHandler(cb_parser_test_cancel, pattern=f"^{CB.PARSER_TEST_CANCEL}$"))
    # Group 1, not 0: while a product conversation is open its handlers win, so
    # a seller typing product info never has it hijacked by a pending test.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_parser_text), group=1)


__all__ = ["register"]
