"""What a restock line means, turned into a diff of the shop's real values (plan 5.2 + 5.3).

The seller writes one compact line per product — «17promax سفید ۳، مشکی ۱» or «⛔ ناموجود» —
and this module is the only place that decides what it applies to. Three rules carry the
whole design:

* **nothing is applied to a variation it did not name.** A token that matches no model and no
  colour of *this* product goes to :attr:`RestockPlan.unmatched` and is shown in the diff.
  Inventing a target is how «مشکی ۵» ends up on a colour nobody ordered;
* **the baseline is the store, not our memory.** Every change is computed against what
  :func:`bot.services.product_match.read` returned, so «۰ → ۳» is a fact about the shop;
* **zero is a real answer here.** The extractor refuses to read «۰ عدد» as stock because a
  stray zero in a caption is noise; in a restock line the seller is *typing the number on
  purpose*, so 0 means 0 and stays 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace as _dc_replace

from bot.services import money
from bot.services.product_match import (
    ShopProduct,
    ShopVariation,
    matches_color,
    matches_model,
    status_text,
)

#: «۳ عدد», «۳ تا», «موجودی: ۳», «3 pcs» — a number the seller *called* a quantity.
_QTY_LABEL_RE = re.compile(r"(?i)(?:موجودی|stock|quantity)\s*[:=]?\s*([\d,\u0660-\u0669\u06f0-\u06f9]+)")
_QTY_SUFFIX_RE = re.compile(r"([\d,\u0660-\u0669\u06f0-\u06f9]{1,7})\s*(?:عدد|عددها|تا|دانه|عدد موجود|pcs)\b")
_OUT_RE = re.compile(r"(?i)(?:⛔|ناموجود|تمام\s*شده|بدون\s*موجودی|out\s*of\s*stock)")
_BACKORDER_RE = re.compile(r"(?i)(?:پیش[\s\u200c\u200f-]*سفارش|پیش[\s\u200c\u200f-]*فروش|backorder)")
#: An amount as sellers write it: Latin or Persian digits, optional thousands separators.
_NUM = r"[\d,\u0660-\u0669\u06f0-\u06f9]{1,15}"
#: Deliberately *not* anchored at the start: «مشکی قیمت ۴۹۸۰۰۰» is the same request, and a
#: price that is missed is a price that becomes a stock count one line below.
_PRICE_RE = re.compile(rf"(?i)(?:قیمت|price)(?:\s*(?:فروش|forosh))?\s*[:=]?\s*{_NUM}")
_SALE_RE = re.compile(rf"(?i)(?:قیمت\s*)?(?:فروش\s*)?ویژه\s*[:=]?\s*{_NUM}(?:\s*(?:تومان|toman))?")
#: «همه ۵» — the only way a seller says *every variation*, spelled out instead of inferred
#: from a tokenless line (a bare «۵» could be a colour code, a size, a leftover).
_ALL_RE = re.compile(r"(?i)(?:^|\s)(?:همه|همهٔ|همه‌ی|all)(?:\s|$)")
#: Same ceiling as the product builder: a "stock" of a million is a mistyped price.
MAX_QUANTITY = 100_000
#: Words that are part of the *instruction*, never the name of a model or colour. They are
#: dropped before matching so «قیمت ویژه ۴۹۸۰۰۰ مشکی» targets مشکی and does not complain that
#: the shop has no colour called «قیمت».
_KEYWORDS = frozenset({
    "موجودی", "موجود", "استوک", "ناموجود", "تمام", "شد", "شده", "بماند", "قیمت", "فروش", "ویژه",
    "پیشفروش", "پیش‌فروش", "پیشسفارش", "پیش‌سفارش", "سفارش", "تومان", "تو", "عدد", "عددها", "تا",
    "دانه", "همه", "همهٔ", "همه‌ی", "all", "stock", "price", "sale", "out", "of", "in", "qty",
    "quantity", "pcs", "forosh", "mvojoud",
})


@dataclass(frozen=True)
class VariationChange:
    """One variation, what it holds now, and what we will send."""

    variation_id: int
    label: str
    stock_from: int | None
    stock_to: int | None
    status_from: str
    status_to: str
    price_from: int
    price_to: int
    sale_from: int
    sale_to: int

    def render(self) -> str:
        bits: list[str] = []
        if self.stock_to is not None and self.stock_to != self.stock_from:
            bits.append(f"موجودی {self.stock_from if self.stock_from is not None else '—'} → {self.stock_to}")
        if self.status_to and self.status_to != self.status_from:
            bits.append(f"وضعیت {status_text(self.status_from)} → {status_text(self.status_to)}")
        if self.price_to and self.price_to != self.price_from:
            bits.append(f"قیمت {self.price_from:,} → {self.price_to:,}")
        if self.sale_to and self.sale_to != self.sale_from:
            bits.append(f"قیمت ویژه {self.sale_from:,} → {self.sale_to:,}")
        return f"{self.label}: " + " · ".join(bits)


@dataclass
class RestockPlan:
    """The whole answer to «چی عوض می‌شود» — including what will *not* be touched."""

    product_id: int
    changes: list[VariationChange] = field(default_factory=list)
    product_change: dict[str, object] = field(default_factory=dict)
    #: lines the seller wrote that matched no variation of this product. Never dropped.
    unmatched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: how many variations already had the value, so nothing is sent for them
    unchanged: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Nothing at all was understood.

        A plan with no change to send but a reason to say so (an unmatched line, an error, a
        variation that already had the value) is *not* empty: the seller has to see why, and a
        screen that stays silent is how «مشکی ۴» looks ignored.
        """
        return not (self.changes or self.product_change or self.unmatched or self.errors
                    or self.notes or self.unchanged)

    @property
    def blocking(self) -> bool:
        return bool(self.errors)

    @property
    def can_apply(self) -> bool:
        """There is something to send and nothing known to be wrong about it."""
        return not self.empty and not self.blocking

    def product_payload(self) -> dict[str, object]:
        """The body for ``PUT products/<id>``: the product-level half of the plan.

        Prices are sent as strings because WooCommerce returns them that way; a number here
        would come back as ``"700000"`` and our own verification would call it a mismatch.
        """
        out: dict[str, object] = {}
        for key, value in self.product_change.items():
            out[key] = str(value) if key in ("regular_price", "sale_price") else value
        return out

    def variation_payloads(self) -> list[dict[str, object]]:
        """The ``update`` list for ``variations/batch`` — only fields that change."""
        out: list[dict[str, object]] = []
        for change in self.changes:
            row: dict[str, object] = {"id": change.variation_id}
            if change.stock_to is not None and change.stock_to != change.stock_from:
                row["manage_stock"] = True
                row["stock_quantity"] = change.stock_to
            if change.status_to and change.status_to != change.status_from:
                row["stock_status"] = change.status_to
            if change.price_to and change.price_to != change.price_from:
                row["regular_price"] = str(change.price_to)
            if change.sale_to and change.sale_to != change.sale_from:
                row["sale_price"] = str(change.sale_to)
            if len(row) > 1:
                out.append(row)
        return out

    def render(self) -> str:
        """The screen the seller approves — the exact list of what will be sent."""
        lines = [f"📊 تغییرات روی محصول #{self.product_id}", ""]
        if self.changes:
            lines.extend(f"• {change.render()}" for change in self.changes[:12])
            if len(self.changes) > 12:
                lines.append(f"… و {len(self.changes) - 12} تغییر دیگر (همه اعمال می‌شود)")
        if self.product_change:
            lines.append("• خود محصول: " + " · ".join(f"{key} → {value}" for key, value in self.product_change.items()))
        if self.unchanged:
            lines.append(f"✓ {self.unchanged} واریژن همین مقدار را داشت و دست‌نخورده می‌ماند.")
        if self.unmatched:
            lines.append("")
            lines.append(f"⚠️ {len(self.unmatched)} خط اعمال نشد "
                         "(هیچ‌کدام از واژه‌هایش نامِ رنگ/مدلِ این محصول نیست):")
            lines.extend(f"— «{text}»" for text in self.unmatched[:6])
        for note in self.notes:
            lines.append(f"ℹ️ {note}")
        for error in self.errors:
            lines.append(f"⛔ {error}")
        return "\n".join(lines)


def _quantity(segment: str) -> int | None:
    """The stock count a segment states, or ``None``.

    A labelled or suffixed number is always taken. A *bare* one is taken only when it is the
    single number in the segment — «سفید ۳» is clear, «13 14 ۳» is not, and guessing there is
    how a shop ends up with ۳ of the wrong thing.
    """
    text = money.digits(segment)
    labelled = _QTY_LABEL_RE.search(text)
    if labelled:
        return _int(labelled.group(1))
    suffixed = _QTY_SUFFIX_RE.search(text)
    if suffixed:
        return _int(suffixed.group(1))
    # Only a *standalone* number counts: in «17promax سفید ۳» the 17 belongs to the model, and
    # taking it (or taking either of two) is exactly the misread this function exists to avoid.
    numbers = re.findall(r"(?<![\w.,])\d{1,7}(?![\w.,])", text)
    if len(numbers) == 1:
        return int(numbers[0])
    return None


def _int(raw: object) -> int | None:
    digits = re.sub(r"[^\d]", "", money.digits(str(raw or "")))
    return int(digits) if digits.isdigit() else None


def _take_prices(segment: str) -> tuple[str, int, int]:
    """Cut the price phrases out of the segment; return ``(rest, regular, sale)``.

    The remainder matters as much as the amounts: a number that was a *price* must not be
    seen again by the stock parser two lines later. That is the whole reason this returns the
    text it consumed.
    """
    text = money.digits(segment)
    sale = 0
    match = _SALE_RE.search(text)
    if match:
        sale = money.parse_line_amount(match.group(0))
        text = f"{text[:match.start()]} {text[match.end():]}"
    regular = 0
    match = _PRICE_RE.search(text)
    if match:
        regular = money.parse_line_amount(match.group(0))
        text = f"{text[:match.start()]} {text[match.end():]}"
    return text, regular, sale


def _segments(text: str) -> list[str]:
    """Lines split on «،»/`,`/`;`/` و `, so «17promax سفید ۳، مشکی ۱» is two requests."""
    out: list[str] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[،,;]|(?:\s+و\s+)", line)
        out.extend(part.strip() for part in parts if part.strip())
    return out


def _is_keyword(token: str) -> bool:
    flat = token.replace("\u200c", "").replace("-", "").lower().strip()
    return flat in _KEYWORDS or token.lower().strip() in _KEYWORDS


def _named_tokens(segment: str) -> list[str]:
    """Words that could name a model or a colour: everything but the numbers and the labels."""
    text = money.digits(_QTY_LABEL_RE.sub(" ", segment))
    text = re.sub(r"[^\w\u0600-\u06FF\s-]", " ", text, flags=re.UNICODE)
    return [token for token in text.split()
            if len(token) >= 2 and not token.isdigit() and not _is_keyword(token)]


def _targets(product: ShopProduct, segment: str) -> tuple[list[ShopVariation], list[str]]:
    """Which variations a segment names, and the tokens it used.

    Matching is by signature (`model_signature`, `color_key`) so «17promax» hits
    «iPhone 17 Pro Max» — but a token that matches *both* a model and a colour of different
    variations is refused rather than resolved by whoever came first.
    """
    tokens = _named_tokens(segment)
    if _ALL_RE.search(money.digits(segment)):
        return list(product.variations), []
    if not tokens:
        return list(product.variations), []
    models: list[str] = []
    colors: list[str] = []
    unknown: list[str] = []
    for token in tokens:
        hit_models = [value for value in product.model_values() if matches_model(token, value)]
        hit_colors = [value for value in product.color_values() if matches_color(token, value)]
        if hit_models and hit_colors:
            # Ambiguous words are the seller's to resolve, not ours to guess.
            unknown.append(token)
            continue
        models.extend(hit_models)
        colors.extend(hit_colors)
        if not hit_models and not hit_colors:
            unknown.append(token)
    wanted_models = list(dict.fromkeys(models))
    wanted_colors = list(dict.fromkeys(colors))
    if unknown and not wanted_models and not wanted_colors:
        # A word the shop does not have is not «no constraint»: widening the scope here is
        # how «قرمز ۴» (on a product with no red) would end up written on every colour.
        return [], unknown
    picked = [
        variation
        for variation in product.variations
        if (not wanted_models or any(matches_model(variation.model, model) for model in wanted_models))
        and (not wanted_colors or any(matches_color(variation.color, color) for color in wanted_colors))
    ]
    return picked, unknown


def plan_for(product: ShopProduct, text: str) -> RestockPlan:
    """Turn the seller's lines into the changes that will actually be sent."""
    plan = RestockPlan(product_id=product.product_id)
    seen: dict[int, VariationChange] = {}

    for segment in _segments(text):
        clean = segment.strip()
        if not clean:
            continue
        rest, regular, sale = _take_prices(clean)
        quantity = _quantity(rest)
        out = bool(_OUT_RE.search(clean))
        backorder = bool(_BACKORDER_RE.search(clean))
        status = "outofstock" if out else ("onbackorder" if backorder else "")

        if not regular and not sale and quantity is None and not status:
            plan.unmatched.append(clean)
            continue

        targets, unknown = _targets(product, clean)
        for token in unknown:
            plan.unmatched.append(f"{clean}  ← «{token}» در این محصول نیست")

        if quantity is not None and quantity > MAX_QUANTITY:
            # A number this big is nearly always a typo, but it may also be a real warehouse
            # count. Warning and writing it beats a dead end with no way through.
            plan.notes.append(f"موجودی {quantity:,} از {MAX_QUANTITY:,} بیشتر است؛ باز هم می‌نویسیمش.")

        if product.is_variable:
            if not targets:
                if not unknown:
                    plan.unmatched.append(clean)
                continue
            if (quantity is not None and len(product.variations) > 1 and not _named_tokens(clean)
                    and not _ALL_RE.search(money.digits(clean))):
                plan.errors.append(
                    f"«{clean}» مدل یا رنگ ندارد؛ روی همهٔ {len(product.variations)} واریژن "
                    "نمی‌نویسیم — بگو کدام (مثلاً «مشکی ۳»). اگر واقعاً همه، بنویس «همه ۳»."
                )
                continue
            if not _named_tokens(clean) and len(targets) > 1:
                plan.notes.append(
                    f"این خط مدل/رنگ نداشت و روی هر {len(targets)} واریژن نوشته می‌شود"
                )
            for variation in targets:
                previous = seen.get(variation.variation_id)
                change = previous or VariationChange(
                    variation_id=variation.variation_id,
                    label=variation.label(),
                    stock_from=variation.stock,
                    stock_to=None,
                    status_from=variation.stock_status,
                    status_to="",
                    price_from=variation.regular_price,
                    price_to=0,
                    sale_from=variation.sale_price,
                    sale_to=0,
                )
                if quantity is not None:
                    change = _replace(change, stock_to=quantity, status_to=status or "instock")
                elif status:
                    change = _replace(change, status_to=status)
                if regular:
                    change = _replace(change, price_to=regular)
                if sale:
                    change = _replace(change, sale_to=sale)
                ceiling = change.price_to or variation.regular_price
                if change.sale_to and ceiling and change.sale_to >= ceiling:
                    plan.errors.append(
                        f"قیمت ویژهٔ {change.sale_to:,} از قیمت {ceiling:,} کمتر نیست "
                        f"({variation.label()}) — فروشگاه آن را تخفیف نمی‌شمارد."
                    )
                    continue
                seen[variation.variation_id] = change
            continue

        # A simple product has no variations to aim at: the line goes on the product itself.
        if quantity is not None:
            plan.product_change["stock_quantity"] = quantity
            plan.product_change["manage_stock"] = True
            plan.product_change["stock_status"] = status or "instock"
        elif status:
            plan.product_change["stock_status"] = status
        if regular:
            plan.product_change["regular_price"] = regular
        if sale:
            ceiling = regular or product.regular_price
            if sale >= ceiling:
                plan.errors.append(f"قیمت ویژهٔ {sale:,} از قیمت {ceiling:,} کمتر نیست.")
            else:
                plan.product_change["sale_price"] = sale
        if not plan.product_change:
            plan.unmatched.append(clean)

    for variation in product.variations:
        pending = seen.get(variation.variation_id)
        if pending is None:
            continue
        if _is_no_change(pending):
            plan.unchanged += 1
            continue
        plan.changes.append(pending)

    return plan


def _replace(change: VariationChange, **fields: object) -> VariationChange:
    return _dc_replace(change, **fields)    # type: ignore[arg-type]


def _is_no_change(change: VariationChange) -> bool:
    return (
        (change.stock_to is None or change.stock_to == change.stock_from)
        and (not change.status_to or change.status_to == change.status_from)
        and (not change.price_to or change.price_to == change.price_from)
        and (not change.sale_to or change.sale_to == change.sale_from)
    )


__all__ = ["MAX_QUANTITY", "RestockPlan", "VariationChange", "plan_for"]
