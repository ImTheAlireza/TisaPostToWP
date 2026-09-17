"""Sudo-only self-learning memory screen (🧠 یادگیری‌ها).

The bot turns the owner's corrections into rules (see bot/services/learning.py).
A memory that cannot be inspected is a memory that cannot be trusted: one wrong
generalization would silently reshape every later product. So this screen lists
every rule with its usage count, deletes individual rules, shows the raw
correction log, and offers a guarded "forget everything".

Only sudo can learn and only sudo can manage: a rule rewrites parsing globally,
so it must not be creatable from a shared admin account.
"""

from __future__ import annotations

import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from bot import rbac
from bot.constants import CB
from bot.services import learning

logger = logging.getLogger(__name__)

_DELETE_PREFIX = f"{CB.LEARNING_DELETE}:"


async def _sudo_only(query) -> bool:
    """Answer+reject non-sudo taps. Returns True when access is granted."""
    user_id = query.from_user.id if query.from_user else None
    if rbac.is_sudo(user_id):
        return True
    await query.answer("⛔ فقط مالک (سودو) به یادگیری‌ها دسترسی دارد.", show_alert=True)
    return False


def _rules_text() -> str:
    rules = learning.rules_sorted()
    lines = ["🧠 <b>یادگیری‌ها</b>", ""]
    if not rules:
        lines += [
            "هنوز قاعده‌ای یاد نگرفته‌ام.",
            "",
            "هر بار موقع افزودن محصول چیزی را اشتباه برداشت کنم و شما اصلاحش کنید، "
            "قاعدهٔ کلی‌اش را اینجا ذخیره می‌کنم؛ مثلاً اگر «1098» را ۱٬۰۹۸ تومان بفهمم "
            "و شما ۱٬۰۹۸٬۰۰۰ را بفرستید، یاد می‌گیرم عدد ۴ رقمیِ بدون پسوند یعنی هزار تومان.",
        ]
        return "\n".join(lines)
    lines.append(f"<b>{len(rules)} قاعدهٔ فعال</b> — از اصلاحات شما یاد گرفته شده:")
    lines.append("")
    for rule in rules:
        usage = f" · {rule.hits}× اعمال شده" if rule.hits else " · هنوز اعمال نشده"
        lines.append(f"• <code>{html.escape(rule.rule_id)}</code>{usage}")
        lines.append(f"  {rule.describe()}")
    lines += [
        "",
        "این قواعد روی همهٔ محصولات بعدی اعمال می‌شوند (هم مسیر قطعی و هم هوش مصنوعی).",
        "اگر قاعده‌ای اشتباه است، همین‌جا حذفش کنید.",
    ]
    return "\n".join(lines)


def _rules_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for rule in learning.rules_sorted():
        rows.append(
            [
                InlineKeyboardButton(
                    f"❌ {rule.kind}: {rule.key[:24]}",
                    callback_data=f"{_DELETE_PREFIX}{learning.short_id(rule.rule_id)}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("📜 تاریخچهٔ اصلاحات", callback_data=CB.LEARNING_CORRECTIONS),
            InlineKeyboardButton("🗑️ فراموشی همه", callback_data=CB.LEARNING_CLEAR_ASK),
        ]
    )
    rows.append([InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)])
    return InlineKeyboardMarkup(rows)


def _corrections_text() -> str:
    items = learning.recent_corrections(limit=12)
    lines = ["📜 <b>تاریخچهٔ اصلاحات</b>", ""]
    if not items:
        lines.append("هنوز اصلاحی ثبت نشده است.")
    else:
        lines.append("آخرین چیزهایی که به من گفتید (جدیدترین بالا):")
        lines.append("")
        for item in items:
            lines.append(f"• {html.escape(item.describe())}")
    lines += ["", "«بدون قاعدهٔ قابل تعمیم» یعنی تغییر را فهمیدم و اعمال کردم، "
                 "ولی چیزی که به درد محصول بعدی هم بخورد از آن درنیامد."]
    return "\n".join(lines)


def _corrections_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧠 قواعد", callback_data=CB.LEARNING)],
            [InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)],
        ]
    )


def _clear_ask_text() -> str:
    count = len(learning.rules_sorted())
    return (
        "🗑️ <b>فراموشی همهٔ قواعد</b>\n\n"
        f"اکنون <b>{count}</b> قاعدهٔ یادگرفته‌شده دارید. با حذف همه، ربات دوباره مثل "
        "روز اول قیمت‌ها و واژه‌ها را برداشت می‌کند و باید دوباره یادش بدهید.\n\n"
        "تاریخچهٔ اصلاحات پاک نمی‌شود؛ فقط قواعد حذف می‌شوند.\n\nمطمئنید؟"
    )


def _clear_ask_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ بله، همه را فراموش کن", callback_data=CB.LEARNING_CLEAR_YES),
                InlineKeyboardButton("❌ انصراف", callback_data=CB.LEARNING_CLEAR_NO),
            ]
        ]
    )


async def cb_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await query.edit_message_text(
        _rules_text(), reply_markup=_rules_keyboard(), parse_mode="HTML"
    )


async def cb_corrections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await query.edit_message_text(
        _corrections_text(), reply_markup=_corrections_keyboard(), parse_mode="HTML"
    )


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forget one rule and re-render the list."""
    query = update.callback_query
    if not await _sudo_only(query):
        return
    match = re.match(rf"^{re.escape(_DELETE_PREFIX)}([0-9a-f]+)$", query.data or "")
    sid = match.group(1) if match else ""
    rule = learning.rule_by_short_id(sid) if sid else None
    if rule is None:
        await query.answer("⚠️ این قاعده قبلاً حذف شده است.", show_alert=True)
        await query.edit_message_text(
            _rules_text(), reply_markup=_rules_keyboard(), parse_mode="HTML"
        )
        return
    learning.delete_rule(rule.rule_id)
    logger.info("Sudo %s deleted learned rule %s", query.from_user.id, rule.rule_id)
    await query.answer(f"🗑️ حذف شد: {rule.key}")
    await query.edit_message_text(
        _rules_text(), reply_markup=_rules_keyboard(), parse_mode="HTML"
    )


async def cb_clear_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await query.edit_message_text(
        _clear_ask_text(), reply_markup=_clear_ask_keyboard(), parse_mode="HTML"
    )


async def cb_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    count = learning.clear_rules()
    logger.info("Sudo %s cleared %d learned rules", query.from_user.id, count)
    await query.answer(f"🗑️ {count} قاعده فراموش شد.")
    await query.edit_message_text(
        _rules_text(), reply_markup=_rules_keyboard(), parse_mode="HTML"
    )


async def cb_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer("❌ لغو شد؛ چیزی حذف نشد.")
    await query.edit_message_text(
        _rules_text(), reply_markup=_rules_keyboard(), parse_mode="HTML"
    )


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_learning, pattern=f"^{CB.LEARNING}$"))
    app.add_handler(
        CallbackQueryHandler(cb_corrections, pattern=f"^{CB.LEARNING_CORRECTIONS}$")
    )
    app.add_handler(
        CallbackQueryHandler(cb_delete, pattern=rf"^{re.escape(_DELETE_PREFIX)}[0-9a-f]+$")
    )
    app.add_handler(CallbackQueryHandler(cb_clear_ask, pattern=f"^{CB.LEARNING_CLEAR_ASK}$"))
    app.add_handler(CallbackQueryHandler(cb_clear_yes, pattern=f"^{CB.LEARNING_CLEAR_YES}$"))
    app.add_handler(CallbackQueryHandler(cb_clear_no, pattern=f"^{CB.LEARNING_CLEAR_NO}$"))
