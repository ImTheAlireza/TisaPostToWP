"""«🔄 شارژ محصول موجود» — find the product in the shop, show the diff, apply it (plan 5.1…5.5).

The old behaviour of this button was to open the product builder in ``update`` mode, which only
ever produced a ZIP for the importer: to change a number the seller re-uploaded images and
re-typed attributes. This flow is the short way round:

* :func:`bot.services.product_match.find` looks the product up **in the shop** (exact SKU, then
  title), so nobody has to remember a product id;
* :func:`bot.services.restock_plan.plan_for` turns one compact line per colour into a diff of
  the values the shop actually has;
* the seller approves that diff, and :func:`bot.services.restock_apply.apply_plan` sends it.

The states live in :mod:`bot.modules.product_flow`'s conversation — the handlers registered in
that one :class:`ConversationHandler` return them — because handing a chat from one conversation
to another would leave the framework's state pointing at the flow the user just left, and their
next message would talk to nobody. What is shared is the *conversation*, not the logic: nothing
in here builds a product, and nothing in the builder writes stock.

The ZIP route stays reachable — «📦 فایل/ZIP» on every screen — because changing attributes or
adding images needs the media pipeline. A keyboard that promised a stock line could do that
would be a lie.
"""
from __future__ import annotations

import functools
import html
import logging
from dataclasses import dataclass, field
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.constants import CB
from bot.services import flow_state, product_match, products_ledger, restock_apply, restock_plan
from bot.services.woo_client import Audit, WooCommerceAPIError
from bot.utils.text import clip

logger = logging.getLogger(__name__)

#: The three steps of this flow. They are states of product_flow's conversation (see above).
RESTOCK_MATCH = 5
RESTOCK_LINE = 6
RESTOCK_DIFF = 7

#: How many candidates to offer as buttons — more is a list nobody reads.
MAX_CANDIDATES = 8
#: The button-text budget; the full line is always in the message above the buttons.
BUTTON_LABEL = 52


@dataclass
class RestockSession:
    """What this chat is in the middle of doing. It does not survive a restart, on purpose:
    a half-approved diff against values read minutes ago is worth less than starting over."""

    chat_id: int | None = None
    thread_id: int | None = None
    candidates: list[product_match.Candidate] = field(default_factory=list)
    product: product_match.ShopProduct | None = None
    plan: restock_plan.RestockPlan | None = None
    #: the seller's own line, kept so «✏️ یک خط دیگر» does not lose what was typed
    line: str = ""
    dry_run: bool = False


sessions: dict[int, RestockSession] = {}


def cleanup(user_id: int) -> bool:
    """Drop this user's restock session; True when something was open.

    Called by the builder's ``_cleanup`` and registered with :mod:`bot.services.flow_guard`, so
    starting any other flow cannot leave an applyable diff behind.
    """
    flow_state.clear(user_id)
    return sessions.pop(user_id, None) is not None


# — entry —

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                closed: list[str] | None = None) -> int:
    """The «🔄 شارژ محصول موجود» tap; product_flow's entry calls this with the query in hand.

    ``closed`` is what the flow guard dropped to make room here — said out loud, because «my
    other screen vanished» must never be a mystery.
    """
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None:
        return ConversationHandler.END
    message = query.message
    session = RestockSession(dry_run=bool(settings.woo_dry_run),
                             chat_id=message.chat_id if message else user.id,
                             thread_id=getattr(message, "message_thread_id", None) if message else None)
    sessions[user.id] = session
    await _log(context, f"[restock:{user.id}] ورود به جریان شارژ")
    prompt = _search_prompt(session.dry_run)
    if closed:
        prompt += "\n\n↩️ جریان «" + "»، «".join(closed) + "» قبلی‌ات بسته شد."
    await query.edit_message_text(prompt, reply_markup=await _search_keyboard(user.id))
    return RESTOCK_MATCH


def _search_prompt(dry_run: bool = False) -> str:
    text = (
        "🔄 محصول را از خودِ فروشگاه پیدا می‌کنم.\n\n"
        "یا SKU را بنویس (دقیق)، یا بخشی از عنوانش را — مثلاً «IP13 پرو مکس».\n"
        "بعد فقط می‌گویی کدام رنگ/مدل چند تا دارد؛ بقیهٔ محصول دست نمی‌خورد."
    )
    if dry_run:
        # In a rehearsal there is no catalogue to search; saying «دمو» is the only way the
        # screen can be reached at all, and hiding that would make the dry run untestable.
        text += "\n\n🧪 حالت آزمایشی: فروشگاه واقعی صدا زده نمی‌شود. برای دیدن مسیر، «دمو» را بنویس."
    return text


async def _search_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """This seller's recent cards, so a product the bot itself made is one tap away."""
    rows: list[list[InlineKeyboardButton]] = []
    for entry in products_ledger.recent(8):
        product_id = entry.get("product_id")
        if not product_id or str(entry.get("status")) not in ("created", "restocked", "dry"):
            continue
        if str(entry.get("user_id")) != str(user_id):
            continue
        title = html.unescape(str(entry.get("title") or ""))[:30] or "محصول"
        rows.append([InlineKeyboardButton(
            f"🧾 {title} · #{product_id}", callback_data=f"{CB.RESTOCK_PICK}:{product_id}")])
    rows.append([InlineKeyboardButton("⏹ انصراف", callback_data=CB.RESTOCK_CANCEL)])
    return InlineKeyboardMarkup(rows)


# — step 1: find it —

async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A SKU or a title in the chat: answer with the products it could be."""
    message = update.effective_message
    session = _session(update)
    if session is None or message is None:
        return ConversationHandler.END
    wanted = " ".join((message.text or "").split())
    if not wanted:
        await message.reply_text("یک SKU یا بخشی از عنوان محصول را بنویس.")
        return RESTOCK_MATCH
    note = await message.reply_text("🔎 دنبال می‌گردم…")
    audit = Audit()
    try:
        found = await product_match.find(wanted, limit=MAX_CANDIDATES, audit=audit,
                                         dry_run=session.dry_run)
    except WooCommerceAPIError as exc:
        await _fail(context, message, note, f"فروشگاه جواب نداد: {exc}", audit)
        return RESTOCK_MATCH
    await note.delete()
    if not found:
        await message.reply_text(
            f"چیزی با «{wanted}» در فروشگاه پیدا نکردم.\n\n"
            "اگر SKU را می‌دانی همان را دقیق بنویس؛ اگر عنوان عوض شده، با یک کلمهٔ دیگر جستجو کن.\n"
            "برای عکس و ویژگی‌ها (که این مسیر انجام نمی‌دهد) باید از فایل/ZIP بروی.",
            reply_markup=_kb([[InlineKeyboardButton("📦 با فایل/ZIP انجامش بده",
                                                     callback_data=CB.RESTOCK_ZIP)]]),
        )
        return RESTOCK_MATCH
    session.candidates = found
    head = ("📊 این محصول را پیدا کردم:" if len(found) == 1
            else f"📊 {len(found)} محصول پیدا شد؛ کدام است؟")
    body = "\n".join(f"{index + 1}. {html.escape(candidate.label())}"
                     for index, candidate in enumerate(found))
    rows = [[InlineKeyboardButton(
        _short(candidate.label()), callback_data=f"{CB.RESTOCK_PICK}:{candidate.product_id}")]
        for candidate in found]
    rows.append([InlineKeyboardButton("🔎 جستجوی دوباره", callback_data=CB.RESTOCK_RETRY_SEARCH),
                 InlineKeyboardButton("⏹ انصراف", callback_data=CB.RESTOCK_CANCEL)])
    await message.reply_text(f"{head}\n\n{body}", reply_markup=_kb(rows))
    return RESTOCK_MATCH


async def pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A candidate was tapped: read it with its variations, then ask for the stock line."""
    query = update.callback_query
    session = _session(update)
    if query is None or session is None:
        return ConversationHandler.END
    try:
        product_id = int(str(query.data or "").rsplit(":", 1)[-1])
    except ValueError:
        await query.answer("دکمهٔ قدیمی است؛ دوباره جستجو کن.", show_alert=True)
        return RESTOCK_MATCH
    await query.answer()
    note = await query.edit_message_text("📄 واریژن‌های محصول را می‌خوانم…")
    audit = Audit()
    try:
        product = await product_match.read(product_id, audit=audit, dry_run=session.dry_run)
    except WooCommerceAPIError as exc:
        await note.edit_text(f"❌ محصول {product_id} خوانده نشد: {html.escape(str(exc))}")
        return RESTOCK_MATCH
    session.product = product
    session.plan = None
    return await _ask_line(note, session)


# — step 2: the compact line —

async def handle_line(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """The seller's stock/price line(s) → the diff they have to approve."""
    message = update.effective_message
    session = _session(update)
    if session is None or message is None:
        return ConversationHandler.END
    if session.product is None:                      # a restart, or a button from an old screen
        return RESTOCK_MATCH
    text = (message.text or "").strip()
    if not text:
        await message.reply_text("یک خط بنویس، مثلاً «مشکی ۵» یا «⛔ ناموجود».")
        return RESTOCK_LINE
    session.line = text
    # Every non-empty line gets the diff screen: it is the one place that can say *why* a line
    # changed nothing (a colour this product has never heard of, a value already set), and a
    # generic «نفهمیدم» would hide that.
    plan = restock_plan.plan_for(session.product, text)
    session.plan = plan
    if plan.empty:
        await message.reply_text(
            "از این خط نفهمیدم چه چیزی روی کدام رنگ/مدل بنویسم.\n\n" + _line_help(session.product),
            reply_markup=_kb([[InlineKeyboardButton("↩️ همان خط را درست بنویس",
                                                    callback_data=CB.RESTOCK_LINE)]]),
        )
        return RESTOCK_LINE
    await _show_diff(message, session, plan)
    return RESTOCK_DIFF


async def _ask_line(target: Any, session: RestockSession) -> int:
    """Show what the shop has, and how to say what should change."""
    product = session.product
    if product is None:                              # pragma: no cover - guarded by pick()
        return RESTOCK_MATCH
    head = [f"🛍 {html.escape(product.title)}",
            f"#{product.product_id} · "
            + ("محصول متغیر" if product.is_variable else "محصول ساده")
            + (f" · SKU {html.escape(product.sku)}" if product.sku else "")]
    if product.is_variable:
        colors = "، ".join(product.color_values())
        models = "، ".join(product.model_values())
        if colors:
            head.append(f"رنگ‌ها: {html.escape(colors)}")
        if models:
            head.append(f"مدل‌ها: {html.escape(models)}")
        for variation in product.variations[:10]:
            head.append(f"— {html.escape(variation.label())}: {variation.stock_text()}")
        if len(product.variations) > 10:
            head.append(f"… و {len(product.variations) - 10} واریژن دیگر")
    else:
        head.append(f"موجودی فعلی: {product.variations[0].stock_text() if product.variations else '—'}")
    for note in product.notes:
        head.append(f"⚠️ {html.escape(note)}")
    head.append("")
    head.append(_line_help(product))
    await target.edit_text("\n".join(head), reply_markup=_kb(
        [[InlineKeyboardButton("🔎 محصول دیگر", callback_data=CB.RESTOCK_RETRY_SEARCH),
          InlineKeyboardButton("📦 فایل/ZIP", callback_data=CB.RESTOCK_ZIP)],
         [InlineKeyboardButton("⏹ انصراف", callback_data=CB.RESTOCK_CANCEL)]]))
    return RESTOCK_LINE


def _line_help(product: product_match.ShopProduct | None) -> str:
    scope = ("روی هر واریژنی که اسمش را بنویسی نوشته می‌شود"
             if product is not None and product.is_variable else "روی خود محصول نوشته می‌شود")
    return ("✍️ موجودی و قیمت را در یک خط بنویس؛ هر خط فقط روی همان رنگ/مدل می‌نشیند "
            f"({scope}):\n"
            "• «مشکی ۵» → ۵ عدد\n"
            "• «سفید ناموجود» → ⛔\n"
            "• «مشکی موجودی ۱۲، قیمت ویژه 498000»\n"
            "• «همه ۲» → روی همهٔ واریژن‌ها\n\n"
            "عدد را با «عدد/تا/دانه» یا برچسب «موجودی» هم بنویسی درست خوانده می‌شود. "
            "۰ یعنی صفر، نه بی‌خیالی.")


async def _show_diff(message: Any, session: RestockSession, plan: restock_plan.RestockPlan) -> None:
    rows: list[list[InlineKeyboardButton]] = []
    if plan.can_apply:
        rows.append([InlineKeyboardButton(
            "🧪 اجرای آزمایشی (چیزی عوض نمی‌شود)" if session.dry_run else "✅ اعمال",
            callback_data=CB.RESTOCK_APPLY)])
        if not session.dry_run:
            rows.append([InlineKeyboardButton("🔄 با مقدارهای الانِ فروشگاه بسنج",
                                              callback_data=CB.RESTOCK_REFRESH)])
    rows.append([InlineKeyboardButton("✏️ یک خط دیگر", callback_data=CB.RESTOCK_LINE),
                 InlineKeyboardButton("📦 فایل/ZIP", callback_data=CB.RESTOCK_ZIP)])
    rows.append([InlineKeyboardButton("⏹ انصراف", callback_data=CB.RESTOCK_CANCEL)])
    text = plan.render()
    if plan.blocking:
        text += "\n\n⛔ تا این‌ها درست نشوند اعمال نمی‌کنم."
    await message.reply_text(_clip(text), reply_markup=_kb(rows))


async def edit_line(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """«✏️ یک خط دیگر» — back to the line, with the diff still readable above."""
    query = update.callback_query
    session = _session(update)
    if query is None or session is None:
        return ConversationHandler.END
    await query.answer()
    if query.message:
        await query.message.edit_text("خط جدید را بنویس (خط قبلی بالا می‌ماند):\n\n"
                                      + _line_help(session.product))
    return RESTOCK_LINE


async def back_to_diff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    session = _session(update)
    if query is None or session is None or session.plan is None:
        return ConversationHandler.END
    await query.answer()
    if query.message:
        await _show_diff(query.message, session, session.plan)
    return RESTOCK_DIFF


async def resync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Re-read the shop and re-plan, so the diff describes the store as it is now."""
    query = update.callback_query
    session = _session(update)
    if query is None or session is None or session.product is None or not session.line:
        return ConversationHandler.END
    await query.answer()
    try:
        fresh = await product_match.read(session.product.product_id, audit=Audit(),
                                         dry_run=session.dry_run)
    except WooCommerceAPIError as exc:
        if query.message:
            await query.message.reply_text(f"❌ فروشگاه خوانده نشد: {html.escape(str(exc))}")
        return RESTOCK_DIFF
    session.product = fresh
    session.plan = restock_plan.plan_for(fresh, session.line)
    if query.message:
        await _show_diff(query.message, session, session.plan)
    return RESTOCK_DIFF


# — step 3: apply —

async def apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send the approved plan, then report exactly what the shop confirmed."""
    query = update.callback_query
    user = update.effective_user
    session = _session(update)
    if query is None or session is None or user is None:
        return ConversationHandler.END
    plan, product = session.plan, session.product
    if plan is None or product is None:
        await query.answer("این طرح دیگر در دسترس نیست.", show_alert=True)
        return ConversationHandler.END
    if not plan.can_apply:
        await query.answer("تا خطاها درست نشوند اعمال نمی‌کنم.", show_alert=True)
        return RESTOCK_DIFF
    await query.answer()
    note = await query.message.edit_text("⏳ دارم می‌نویسم…") if query.message else None
    audit = Audit()
    try:
        # A retry of the *same* plan on purpose: re-planning here could move a number the
        # seller never approved. What is sent is what the diff showed; the freshness check is
        # «🔄 با مقدارهای الانِ فروشگاه بسنج», a visible step, not a silent re-read.
        result = await restock_apply.apply_plan(plan, product, audit=audit,
                                                dry_run=session.dry_run)
    except WooCommerceAPIError as exc:
        await _fail(context, query.message, note, f"نوشتن در فروشگاه ناموفق بود: {exc}", audit)
        return RESTOCK_DIFF
    except ValueError as exc:                       # pragma: no cover - guarded above
        await _fail(context, query.message, note, str(exc), audit)
        return RESTOCK_DIFF

    changed = len(result.confirmed) + int(result.product_updated)
    # A rehearsal must not look like a write: the same «dry» card the publish path uses, so
    # the history never claims a number was charged when nothing was sent.
    status = "failed" if not changed else ("dry" if result.dry_run else "restocked")
    entry = products_ledger.record(
        user_id=user.id,
        status=status,
        product_id=product.product_id,
        mode="restock",
        title=product.title,
        variations=len(result.confirmed),
        warnings=[*plan.notes, *plan.unmatched[:4], *result.errors[:2]],
        error=_failure_text(result),
        report="\n".join([plan.render(), "", "خط به خط:", *audit.lines[-24:]])[:products_ledger.REPORT_LIMIT],
    )
    if note is None:                                  # pragma: no cover - Telegram always has one
        await query.answer(result.summary(plan)[:190], show_alert=True)
    else:
        await note.edit_text(html.escape(result.summary(plan), quote=False), reply_markup=_kb([
            [InlineKeyboardButton("🧾 گزارش همین محصول",
                                  callback_data=f"{CB.PRODUCTS_OPEN}:{entry['key']}")],
            [InlineKeyboardButton("🔄 شارژ بعدی", callback_data=CB.PHONE_RESTOCK),
             InlineKeyboardButton("🏴 منو", callback_data=CB.MAIN_MENU)],
        ]))
    await _log(context, f"[restock:{user.id}] " + result.summary(plan).replace("\n", " | "))
    cleanup(user.id)
    return ConversationHandler.END


def _failure_text(result: restock_apply.ApplyResult) -> str:
    """What the history says when nothing was confirmed — in the shop's own terms.

    «فرستاد و تأیید نشد» و «رد شد» دو چیزند: اولی ممکن است نوشته باشد و باید در سایت دیده
    شود، دومی قطعاً ننوشته است.
    """
    if result.errors:
        return "؛ ".join(result.errors[:2])
    if result.unconfirmed:
        return (f"{len(result.unconfirmed)} خط فرستاده شد و فروشگاه مقدار جدید را "
                "برنگرداند — در سایت چک کن")
    return "فروشگاه چیزی تأیید نکرد"


async def offer_zip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Hand the chat to the builder's update mode: attributes and images need that pipeline."""
    query = update.callback_query
    session = _session(update)
    if query is None or session is None:
        return ConversationHandler.END
    from bot.modules import product_flow            # lazy: product_flow owns our states
    # Deliberately no query.answer() here: begin_update runs the builder's entry, which
    # answers the same callback — a second answer is a BadRequest, and it would kill the
    # handover this button exists to perform.
    return await product_flow.begin_update(update, context)


async def retry_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    session = _session(update)
    if query is None or session is None or user is None:
        return ConversationHandler.END
    await query.answer()
    session.candidates, session.product, session.plan, session.line = [], None, None, ""
    if query.message:
        await query.message.edit_text(_search_prompt(session.dry_run),
                                      reply_markup=await _search_keyboard(user.id))
    return RESTOCK_MATCH


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user = update.effective_user
    if user is not None:
        cleanup(user.id)
    if query is not None:
        await query.answer()
        if query.message:
            await query.message.reply_text("انصراف داده شد؛ چیزی در فروشگاه عوض نشد.")
    return ConversationHandler.END


async def ignore_media(update: Update, context: ContextTypes.DEFAULT_TYPE, *,
                       stay: int = RESTOCK_MATCH) -> int:
    """A photo or file here: say what to do instead of staying silent.

    PTB has no «keep my state» sentinel for a callback handler (returning ``None`` ends the
    conversation), so the caller says which state it was registered for.
    """
    message = update.effective_message
    if message is None:
        return stay
    await message.reply_text(
        "این جریان متن می‌گیرد: اول SKU یا عنوان، بعد یک خط مثل «مشکی ۵».\n"
        "فایل (عکس‌ها و ویژگی‌ها) کارِ مسیر فایل/ZIP است.",
        reply_markup=_kb([[InlineKeyboardButton("📦 فایل/ZIP", callback_data=CB.RESTOCK_ZIP)]]))
    return stay


# — plumbing —

def _session(update: Update) -> RestockSession | None:
    user = update.effective_user
    return sessions.get(user.id) if user else None


def _short(label: str) -> str:
    text = html.unescape(label)
    return text if len(text) <= BUTTON_LABEL else text[:BUTTON_LABEL - 1] + "…"


def _clip(text: str) -> str:
    # A 50-variation diff must not sink the whole screen; the shared `clip` is what
    # knows Telegram's limit and always leaves room for the «ادامه دارد» line itself.
    return clip(text, limit=3600, note="\n\n… (ادامهٔ تغییرات در گزارش)")


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


async def _fail(context: ContextTypes.DEFAULT_TYPE, message: Any, note: Any, text: str,
                audit: Audit) -> None:
    body = f"❌ {html.escape(text, quote=False)}"
    tail = "\n".join(audit.lines[-6:])
    if tail:
        body += "\n\n" + html.escape(tail, quote=False)
    if note is not None:
        try:
            await note.edit_text(body)
        except Exception:                           # pragma: no cover - Telegram flakiness
            if message is not None:
                await message.reply_text(body)
    elif message is not None:
        await message.reply_text(body)
    await _log(context, f"[restock] {body[:900]}")


async def _log(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    from bot.modules.product_flow import _telegram_log
    await _telegram_log(context, text)


def callbacks() -> list[Any]:
    """Buttons shared by the three screens, so «انصراف» works wherever it is pressed."""
    return [
        CallbackQueryHandler(edit_line, pattern=rf"^{CB.RESTOCK_LINE}$"),
        CallbackQueryHandler(back_to_diff, pattern=rf"^{CB.RESTOCK_DIFF}$"),
        CallbackQueryHandler(resync, pattern=rf"^{CB.RESTOCK_REFRESH}$"),
        CallbackQueryHandler(offer_zip, pattern=rf"^{CB.RESTOCK_ZIP}$"),
        CallbackQueryHandler(retry_search, pattern=rf"^{CB.RESTOCK_RETRY_SEARCH}$"),
        CallbackQueryHandler(cancel, pattern=rf"^{CB.RESTOCK_CANCEL}$"),
    ]


def states() -> dict[int, list[Any]]:
    """The states product_flow's conversation adds for this flow."""
    return {
        RESTOCK_MATCH: [
            CallbackQueryHandler(pick, pattern=rf"^{CB.RESTOCK_PICK}:[0-9]+$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_search),
            MessageHandler(filters.PHOTO | filters.Document.ALL,
                           functools.partial(ignore_media, stay=RESTOCK_MATCH)),
            *callbacks(),
        ],
        RESTOCK_LINE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_line),
            MessageHandler(filters.PHOTO | filters.Document.ALL,
                           functools.partial(ignore_media, stay=RESTOCK_LINE)),
            *callbacks(),
        ],
        RESTOCK_DIFF: [
            CallbackQueryHandler(apply, pattern=rf"^{CB.RESTOCK_APPLY}$"),
            *callbacks(),
        ],
    }


__all__ = ["RESTOCK_DIFF", "RESTOCK_LINE", "RESTOCK_MATCH", "RestockSession", "callbacks",
           "cancel", "cleanup", "pick", "states"]
