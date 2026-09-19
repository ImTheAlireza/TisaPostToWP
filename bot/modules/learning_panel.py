"""Sudo-only self-learning memory screen (🧠 یادگیری‌ها).

The bot turns the owner's corrections into rules (see bot/services/learning.py).
A memory that cannot be inspected is a memory that cannot be trusted: one wrong
generalization would silently reshape every later product. So this screen lists
every rule with its usage count, deletes individual rules, shows the raw
correction log, and offers a guarded "forget everything".

Phase 6 added the part that makes the same promise *before* the damage: nothing a
rule touches until the owner confirms it. New rules arrive on the «⏳ در انتظار
تأیید» screen, each with the result of replaying it over the recent products
(«روی ۷ محصول آخر، ۲۳ واریژن کمتر می‌شد»), and the buttons here are the only way
to activate, disable or re-scope it.

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
from bot.services import metrics, learning, learning_impact
from bot.utils.text import clip_html

logger = logging.getLogger(__name__)

_DELETE_PREFIX = f"{CB.LEARNING_DELETE}:"
_CONFIRM_PREFIX = f"{CB.LEARNING_CONFIRM}:"
_DISABLE_PREFIX = f"{CB.LEARNING_DISABLE}:"
_ENABLE_PREFIX = f"{CB.LEARNING_ENABLE}:"
_SCOPE_PREFIX = f"{CB.LEARNING_SCOPE}:"

# A screen longer than a Telegram message is a screen that shows nothing.
_MAX_SHOWN = 12
_MAX_TEXT = 3600
# The pending screen's heading, used to answer «which screen was this tapped
# from?» — callback data carries a rule id, not where the button lives.
_PENDING_HEADING = "قاعده‌هایی که یاد گرفته‌ام"
# Everything this panel sends, by its first line. Editing is only allowed on our
# own messages.
_OWN_HEADINGS = (
    "🧠 <b>یادگیری‌ها</b>",
    _PENDING_HEADING,
    "📜 <b>تاریخچهٔ اصلاحات</b>",
    "🗑️ <b>فراموشی",
)


async def _sudo_only(query) -> bool:
    """Answer+reject non-sudo taps. Returns True when access is granted."""
    user_id = query.from_user.id if query.from_user else None
    if rbac.is_sudo(user_id):
        return True
    metrics.note_denial("learning_panel")
    await query.answer("⛔ فقط مالک (سودو) به یادگیری‌ها دسترسی دارد.", show_alert=True)
    return False


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _clip(text: str) -> str:
    clipped, _ = clip_html(text, limit=_MAX_TEXT)
    return clipped


def _usage(rule) -> str:
    """How much this rule has actually been relied on."""
    if not rule.hits:
        return "هنوز اعمال نشده"
    return f"{rule.hits}× اعمال شده"


def _rule_lines(rule) -> list[str]:
    lines = [
        f"• <code>{html.escape(rule.rule_id)}</code> — {_usage(rule)}",
        f"  {html.escape(rule.describe(), quote=False)}",
        f"  {html.escape(rule.status_line())} · دامنه: {html.escape(rule.scope_line(), quote=False)}",
    ]
    examples = learning.applied_examples(rule)[-2:]
    for item in examples:
        title = html.escape(item.get("title", "") or "—", quote=False)
        effect = html.escape(item.get("effect", ""), quote=False)
        lines.append(f"  ↳ روی «{title}»{(': ' + effect) if effect else ''}")
    return lines


def _rules_text() -> str:
    counts = learning.count_by_status()
    rules = learning.rules_sorted()
    lines = ["🧠 <b>یادگیری‌ها</b>", ""]
    if not rules:
        lines += [
            "هنوز قاعده‌ای یاد نگرفته‌ام.",
            "",
            "هر بار موقع افزودن محصول چیزی را اشتباه برداشت کنم و شما اصلاحش کنید، "
            "قاعدهٔ کلی‌اش را اینجا ذخیره می‌کنم؛ مثلاً اگر «1098» را ۱٬۰۹۸ تومان بفهمم "
            "و شما ۱٬۰۹۸٬۰۰۰ را بفرستید، یاد می‌گیرم عدد ۴ رقمیِ بدون پسوند یعنی هزار تومان.",
            "",
            "هر قاعدهٔ تازه اول <b>⏳ در انتظار تأیید</b> می‌ماند: "
            "تأثیرش روی محصولات آخر را نشان می‌دهم و تا تو نگویی «فعال شود»، "
            "هیچ محصولی با آن ساخته نمی‌شود.",
        ]
        return _clip("\n".join(lines))
    head = (
        f"<b>{counts[learning.STATUS_ACTIVE]} قاعدهٔ فعال</b>"
        f" · ⏳ {counts[learning.STATUS_PENDING]} در انتظار تأیید"
        f" · ⏸ {counts[learning.STATUS_DISABLED]} غیرفعال"
    )
    lines.append(head)
    lines.append("")
    lines.append("این قواعد روی محصولات بعدی اعمال می‌شوند (هم مسیر قطعی و هم هوش مصنوعی):")
    lines.append("")
    for rule in rules[:_MAX_SHOWN]:
        lines += _rule_lines(rule)
    if len(rules) > _MAX_SHOWN:
        lines.append(f"… و {len(rules) - _MAX_SHOWN} قاعدهٔ دیگر")
    lines += [
        "",
        "اگر قاعده‌ای اشتباه است، همین‌جا حذفش کن؛ اگر فقط برای یک خانوادهٔ محصول "
        "درست است، «🎯» دامنه‌اش را محدود کن.",
    ]
    return _clip("\n".join(lines))


def _rules_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pending = learning.count_by_status()[learning.STATUS_PENDING]
    if pending:
        rows.append([InlineKeyboardButton(
            f"⏳ {pending} قاعده در انتظار تأیید", callback_data=CB.LEARNING_PENDING
        )])
    for rule in learning.rules_sorted()[:_MAX_SHOWN]:
        sid = learning.short_id(rule.rule_id)
        row = [InlineKeyboardButton(f"❌ {rule.key[:18]}", callback_data=f"{_DELETE_PREFIX}{sid}")]
        if rule.status == learning.STATUS_ACTIVE:
            row.append(InlineKeyboardButton("⏸", callback_data=f"{_DISABLE_PREFIX}{sid}"))
        elif rule.status == learning.STATUS_PENDING:
            row.append(InlineKeyboardButton("✅ فعال کن", callback_data=f"{_CONFIRM_PREFIX}{sid}"))
        else:
            row.append(InlineKeyboardButton("▶️ فعالش کن", callback_data=f"{_ENABLE_PREFIX}{sid}"))
        if rule.origin:
            label = "🌐 همه‌جا" if rule.keyword else "🎯 فقط همین دسته"
            row.append(InlineKeyboardButton(label, callback_data=f"{_SCOPE_PREFIX}{sid}"))
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton("📜 تاریخچهٔ اصلاحات", callback_data=CB.LEARNING_CORRECTIONS),
            InlineKeyboardButton("🗑️ فراموشی همه", callback_data=CB.LEARNING_CLEAR_ASK),
        ]
    )
    rows.append([InlineKeyboardButton("⬅️ بازگشت به منو", callback_data=CB.MAIN_MENU)])
    return InlineKeyboardMarkup(rows)


def _pending_text() -> str:
    rules = learning.pending_rules()
    lines = ["⏳ <b>قاعده‌هایی که یاد گرفته‌ام و اعمال نکرده‌ام</b>", ""]
    if not rules:
        lines.append("چیزی در انتظار تأیید نیست.")
        lines.append("")
        lines.append("هر اصلاحی که لازم‌اش بدانم، اول اینجا می‌آید با اینکه روی "
                     "محصول‌های آخر چه اثری می‌گذاشته؛ بعد از تأیید تو فعال می‌شود.")
        return _clip("\n".join(lines))
    lines.append("تا تو تأیید نکنی، هیچ محصولی با این قواعد ساخته نمی‌شود. "
                 "زیر هر کدام نوشته‌ام اگر فعال می‌شد، روی محصولات آخر چه می‌کرد:")
    for rule in rules[:_MAX_SHOWN]:
        lines.append("")
        lines += _rule_lines(rule)
        preview = learning_impact.project(rule)
        lines.append("  " + html.escape(preview.summary(), quote=False))
        for example in preview.example_lines():
            lines.append("  " + html.escape(example, quote=False))
        if preview.dangerous:
            lines.append("  🛑 چیزی که فروخته می‌شود را کم می‌کند — مطمئن شو درست فهمیده‌ام.")
    if len(rules) > _MAX_SHOWN:
        lines.append(f"… و {len(rules) - _MAX_SHOWN} قاعدهٔ دیگر")
    return _clip("\n".join(lines))


def _pending_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for rule in learning.pending_rules()[:_MAX_SHOWN]:
        sid = learning.short_id(rule.rule_id)
        rows.append([
            InlineKeyboardButton(f"✅ فعال کن ({rule.key[:14]})", callback_data=f"{_CONFIRM_PREFIX}{sid}"),
            InlineKeyboardButton("❌ فراموشش کن", callback_data=f"{_DELETE_PREFIX}{sid}"),
        ])
    rows.append([InlineKeyboardButton("🧠 قواعد", callback_data=CB.LEARNING)])
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
    return _clip("\n".join(lines))


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


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _show(query, text: str, markup: InlineKeyboardMarkup) -> None:
    """Update in place on our own screens, answer with a new message elsewhere."""
    if _is_own_screen(query):
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    else:
        await query.message.reply_html(text, reply_markup=markup)


async def _render(query, *, pending: bool = False) -> None:
    """Re-show the screen a change was made from (never the wrong one)."""
    if pending:
        await _show(query, _pending_text(), _pending_keyboard())
    else:
        await _show(query, _rules_text(), _rules_keyboard())


def _is_own_screen(query) -> bool:
    """Is the tapped message one of this panel's own screens?

    «⏳ در انتظار تأیید» also sits on the product preview, and this screen edits
    in place to keep the chat tidy — which there would overwrite a half-built
    product the owner still has to confirm. So: edit our own cards, reply on
    somebody else's.
    """
    text = getattr(getattr(query, "message", None), "text", "") or ""
    return any(heading in text for heading in _OWN_HEADINGS)


def _on_pending_screen(query) -> bool:
    text = getattr(getattr(query, "message", None), "text", "") or ""
    return _PENDING_HEADING in text


def _rule_from_query(query, prefix: str):
    """Resolve ``<prefix><short_id>`` back to the rule it points at."""
    match = re.match(rf"^{re.escape(prefix)}([0-9a-f]+)$", query.data or "")
    if not match:
        return None
    return learning.rule_by_short_id(match.group(1))


async def cb_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await _show(query, _rules_text(), _rules_keyboard())


async def cb_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await _show(query, _pending_text(), _pending_keyboard())


async def cb_corrections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await _show(query, _corrections_text(), _corrections_keyboard())


async def cb_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Forget one rule and re-render the screen it was tapped from.

    A button on the pending screen points at a rule nobody activated yet, so the
    honest "gone" message is different there than on the rules list.
    """
    query = update.callback_query
    if not await _sudo_only(query):
        return
    rule = _rule_from_query(query, _DELETE_PREFIX)
    on_pending = _on_pending_screen(query)
    if rule is None:
        await query.answer("⚠️ این قاعده قبلاً حذف شده است.", show_alert=True)
        await _render(query, pending=on_pending)
        return
    learning.delete_rule(rule.rule_id)
    logger.info("Sudo %s deleted learned rule %s", query.from_user.id, rule.rule_id)
    await query.answer(f"🗑️ حذف شد: {rule.key}")
    await _render(query, pending=on_pending)


async def cb_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """✅ Let a proposed rule start changing products (its preview was shown)."""
    query = update.callback_query
    if not await _sudo_only(query):
        return
    rule = _rule_from_query(query, _CONFIRM_PREFIX)
    if rule is None:
        await query.answer("⚠️ این قاعده دیگر وجود ندارد.", show_alert=True)
        await _render(query)
        return
    on_pending = _on_pending_screen(query)
    learning.confirm_rule(rule.rule_id)
    left = len(learning.pending_rules())
    logger.info("Sudo %s activated learned rule %s", query.from_user.id, rule.rule_id)
    await query.answer(f"✅ فعال شد: {rule.key}" + (f" · {left} تای دیگر مانده" if left else ""))
    await _render(query, pending=on_pending)


async def cb_disable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⏸ Stop using a rule without losing what it taught the bot."""
    query = update.callback_query
    if not await _sudo_only(query):
        return
    rule = _rule_from_query(query, _DISABLE_PREFIX)
    if rule is None:
        await query.answer("⚠️ این قاعده دیگر وجود ندارد.", show_alert=True)
        await _render(query)
        return
    learning.disable_rule(rule.rule_id)
    await query.answer(f"⏸ غیرفعال شد: {rule.key}")
    await _render(query)


async def cb_enable(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    rule = _rule_from_query(query, _ENABLE_PREFIX)
    if rule is None:
        await query.answer("⚠️ این قاعده دیگر وجود ندارد.", show_alert=True)
        await _render(query)
        return
    learning.enable_rule(rule.rule_id)
    await query.answer(f"▶️ دوباره فعال شد: {rule.key}")
    await _render(query)


async def cb_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🎯 Narrow a rule to the product family it was learned from, or widen it."""
    query = update.callback_query
    if not await _sudo_only(query):
        return
    rule = _rule_from_query(query, _SCOPE_PREFIX)
    if rule is None:
        await query.answer("⚠️ این قاعده دیگر وجود ندارد.", show_alert=True)
        await _render(query)
        return
    changed = learning.toggle_scope(rule.rule_id)
    if changed is None:
        await query.answer(
            "این قاعده محصول مشخصی نداشته که بشود دامنه‌اش را محدود کرد.", show_alert=True
        )
        await _render(query)
        return
    await query.answer(f"دامنه: {changed.scope_line()}")
    await _render(query)


async def cb_clear_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer()
    await _show(query, _clear_ask_text(), _clear_ask_keyboard())


async def cb_clear_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    count = learning.clear_rules()
    logger.info("Sudo %s cleared %d learned rules", query.from_user.id, count)
    await query.answer(f"🗑️ {count} قاعده فراموش شد.")
    await _show(query, _rules_text(), _rules_keyboard())


async def cb_clear_no(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not await _sudo_only(query):
        return
    await query.answer("❌ لغو شد؛ چیزی حذف نشد.")
    await _show(query, _rules_text(), _rules_keyboard())


def register(app: Application) -> None:
    app.add_handler(CallbackQueryHandler(cb_learning, pattern=f"^{CB.LEARNING}$"))
    app.add_handler(CallbackQueryHandler(cb_pending, pattern=f"^{CB.LEARNING_PENDING}$"))
    app.add_handler(
        CallbackQueryHandler(cb_corrections, pattern=f"^{CB.LEARNING_CORRECTIONS}$")
    )
    for prefix, handler in (
        (_DELETE_PREFIX, cb_delete),
        (_CONFIRM_PREFIX, cb_confirm),
        (_DISABLE_PREFIX, cb_disable),
        (_ENABLE_PREFIX, cb_enable),
        (_SCOPE_PREFIX, cb_scope),
    ):
        app.add_handler(
            CallbackQueryHandler(handler, pattern=rf"^{re.escape(prefix)}[0-9a-f]+$")
        )
    app.add_handler(CallbackQueryHandler(cb_clear_ask, pattern=f"^{CB.LEARNING_CLEAR_ASK}$"))
    app.add_handler(CallbackQueryHandler(cb_clear_yes, pattern=f"^{CB.LEARNING_CLEAR_YES}$"))
    app.add_handler(CallbackQueryHandler(cb_clear_no, pattern=f"^{CB.LEARNING_CLEAR_NO}$"))
