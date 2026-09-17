"""The card a publish ends with — shared by the flow and the history screen.

Kept here (instead of inside ``bot.modules.product_flow``) because two screens
render it: right after a publish, and later from «🧾 آخرین محصولات». The card is
built from a :mod:`bot.services.products_ledger` entry, so both show the same
facts and the history view works after a restart.
"""

from __future__ import annotations

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.constants import CB


def price_line(entry: dict[str, object]) -> str:
    from bot.services import products_ledger

    return products_ledger.price_range(entry)


def result_card(entry: dict[str, object]) -> str:
    """What really happened: id, link, counts, and the warnings that remain."""
    status = str(entry.get("status") or "")
    title = html.escape(str(entry.get("title") or "—"), quote=False)
    lines: list[str] = []
    if status == "failed":
        lines.append("🎯 <b>ساخت ناموفق بود</b>")
        lines.append(f"⚠️ {html.escape(str(entry.get('error') or ''), quote=False)}")
    elif status == "zip":
        lines.append("🎯 <b>فایل ZIP آماده شد</b>")
        lines.append("📤 این فایل را در افزونه وردپرس آپلود کن؛ محصول پس از آپلود ساخته می‌شود.")
    else:
        lines.append("🎯 <b>پیش‌نویس ساخته شد</b>")
        lines.append(f"🆔 id: <code>{html.escape(str(entry.get('product_id')), quote=False)}</code> · پیش‌نویس")
        if entry.get("edit_url"):
            lines.append(f"🔗 {html.escape(str(entry.get('edit_url')), quote=False)}")
        lines.append("🌐 انتشار نهایی فقط از داخل سایت انجام می‌شود.")
    lines.append("")
    lines.append(f"عنوان: {title}")
    lines.append(
        f"🎨 {entry.get('variations', 0)} واریژن · 🖼 {entry.get('images', 0)} تصویر · "
        f"💰 {price_line(entry)}"
    )
    if entry.get("sku_prefix"):
        lines.append(f"🏷 پیشوند SKU: <code>{html.escape(str(entry['sku_prefix']), quote=False)}</code>")
    warnings = [str(x) for x in (entry.get("warnings") or [])]
    if warnings:
        lines.append("")
        lines.append(f"📎 {len(warnings)} نکته‌ای که باید بدانی:")
        lines.extend(f"• {html.escape(text, quote=False)}" for text in warnings[:4])
    return "\n".join(lines)


def result_keyboard(entry: dict[str, object]) -> InlineKeyboardMarkup:
    """Act on the result — or start the next product without leaving the chat."""
    rows: list[list[InlineKeyboardButton]] = []
    if entry.get("edit_url"):
        rows.append([InlineKeyboardButton("🌐 ویرایش در سایت", url=str(entry["edit_url"]))])
    mode = "update" if str(entry.get("mode")) == "update" else "new"
    rows.append([InlineKeyboardButton("📦 محصول بعدی (همان تنظیمات)", callback_data=f"product:next:{mode}")])
    rows.append([
        InlineKeyboardButton("🧾 گزارش همین محصول", callback_data=f"{CB.PRODUCTS_OPEN}:{entry.get('key')}")
    ])
    return InlineKeyboardMarkup(rows)
