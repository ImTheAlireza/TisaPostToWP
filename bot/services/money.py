"""Single source of truth for reading money out of Persian shop chatter.

Why this module exists
----------------------
Price parsing used to be split over two regexes in ``product_extractor`` that
disagreed with each other. The result was silent, expensive mistakes — all of
them reproduced in the code review:

    «وزن 250 گرم»                → price 250,000   (a weight became the price)
    «1403/01/01»                 → price 1,403     (a date became the price)
    «SKU: BO147»                 → price 147,000   (a code became the price)
    «S24 اولترا 768t»             → price 24        (the model number, not 768t)
    «قیمت ایفون 698 اندروید 598»  → only iPhone survived

The rules here are deliberately conservative: **a number is an amount only when
nothing else explains it**, and a line is price-bearing only when it says so or
is nothing but an amount. When we are not sure we return 0 and let the caller
ask the owner — a question costs one tap, a wrong price on the store costs
orders.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bot.config import settings
from bot.services import learning

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

# Words people actually type. «تومان»/«تومن» name the currency and NEVER scale.
_UNIT_FACTORS: tuple[tuple[str, int], ...] = (
    ("میلیارد", 10**9),
    ("میلیون", 10**6),
    ("هزار", 10**3),
    ("تومان", 1),
    ("تومن", 1),
    ("t", 10**3),
    ("k", 10**3),
    ("ت", 10**3),
)
_CURRENCY_WORDS = {"تومان", "تومن"}

# Digits with thousands grouping («1.098.000», «1,098,000») or a short fraction
# («1.5» میلیون). The lookarounds keep model tokens out: «BO147», «17promax» and
# «A21s» are not amounts, they are names.
# Three shapes, tried in this order: grouped thousands («1.098.000»,
# «698,000»), a short fraction («1.5» میلیون), then a plain number. The order
# matters: with the plain alternative first, «1.5 میلیون» matched «1» and the
# unit attached to nothing.
_AMOUNT_NUMBER = r"\d+(?:[.,]\d{3})+|\d+\.\d{1,2}|\d+"
_AMOUNT_UNIT = r"میلیارد|میلیون|هزار|تومان|تومن|[tkت]"
TOKEN_PATTERN = (
    r"(?<![0-9A-Za-z.])"
    + f"({_AMOUNT_NUMBER})"
    + r"\s*"
    + f"({_AMOUNT_UNIT})?"
    + r"(?![0-9A-Za-z\d])"
)
_TOKEN_RE = re.compile(TOKEN_PATTERN, re.I)

# Labels that mean «this number is not the price» — the shop writes weights,
# dates, order codes and dimensions in the same message as prices.
_NON_PRICE_LABEL = (
    r"(?:وزن|تاریخ|ساعت|زمان|ردیف|شماره|کد|شناسه|sku|تعداد|عدد|ابعاد|اندازه|سایز"
    r"|گارانتی|محدودیت|موجودی|تلفن|موبایل|شهر|استان|آدرس|پست|بارکد|رهگیری"
    r"|هزینه|ارسال|پس\s*کرایه|تخفیف|قیمت\s*قبل(?:ی)?|مدل|مدل‌ها|مدلها|برند|رنگ"
    r"|دسته|جنس|طرح|ساخت|تولید|موجود|ناموجود|ساختار|توضیحات)"
)
_NON_PRICE_LABEL_RE = re.compile(rf"^\s*{_NON_PRICE_LABEL}\b\s*[:：=]?", re.I)
_PRICE_HINT_RE = re.compile(r"قیمت|مبلغ|نرخ|price", re.I)
_DATE_RE = re.compile(r"\d{3,4}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{1,4}")
_BARE_AMOUNT_LINE_RE = re.compile(
    r"^\s*(?:قیمت|مبلغ|price)?\s*[:：=]?\s*"
    r"[\d۰-۹٠-۹][\d۰-۹٠-۹,،.\s\-–—]*(?:تومان|تومن|هزار|[tkت])?\s*$",
    re.I,
)

# Price groups the shop sells by (iPhone vs everything-else pricing).
_GROUP_WORDS: dict[str, str] = {
    "ایفون": "iphone", "آیفون": "iphone", "iphone": "iphone", "اپل": "iphone",
    "apple": "iphone",
    "اندروید": "android", "android": "android", "سامسونگ": "android",
    "samsung": "android", "گلکسی": "android", "شیائومی": "android",
    "xiaomi": "android", "ردمی": "android", "redmi": "android", "پوکو": "android",
    "poco": "android", "هواوی": "android", "vivo": "android", "ناتیلوس": "android",
}


@dataclass(frozen=True)
class Amount:
    """One parsed amount plus the evidence it came from."""

    value: int
    raw: str
    unit: str = ""
    start: int = 0
    end: int = 0

    @property
    def is_bare(self) -> bool:
        return not self.unit

    @property
    def is_currency(self) -> bool:
        return self.unit in _CURRENCY_WORDS


def digits(value: object) -> str:
    """Persian/Arabic digits → Latin. Length-preserving, so offsets stay valid."""
    return str(value or "").translate(_DIGITS)


def _factor_of(unit: str) -> int:
    unit = (unit or "").strip().casefold()
    for word, factor in _UNIT_FACTORS:
        if word == unit:
            return factor
    return 1


def _value_of(raw: str, factor: int) -> int:
    """Turn a numeric token into an integer amount, honouring grouping/dots."""
    text = digits(raw)
    if "." in text or "," in text:
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", text):
            text = text.replace(".", "").replace(",", "")
        elif re.fullmatch(r"\d+[.,]\d{1,2}", text):
            try:
                return round(float(text.replace(",", ".")) * factor)
            except ValueError:
                return 0
    text = re.sub(r"[.,]", "", text)
    if not text.isdigit():
        return 0
    return int(text) * factor


def amounts_in_line(line: str) -> list[Amount]:
    """Every amount a line states, in order of appearance."""
    text = digits(line or "")
    out: list[Amount] = []
    for match in _TOKEN_RE.finditer(text):
        unit = (match.group(2) or "").casefold()
        value = _value_of(match.group(1), _factor_of(unit))
        if value <= 0:
            continue
        out.append(Amount(value=value, raw=match.group(1), unit=unit,
                          start=match.start(1), end=match.end(1)))
    return out


def apply_bare_policy(amount: Amount) -> Amount:
    """Scale a bare number using the shop's learned rule / built-in heuristic.

    Only bare numbers are touched: an explicit suffix («768t», «۷۶۸ هزار») or a
    currency word («۱۰۹۸ تومان») is already unambiguous and must never be
    scaled twice.
    """
    if not amount.is_bare:
        return amount
    raw_digits = digits(amount.raw)
    multiplier = learning.price_multiplier(len(raw_digits), has_suffix=False)
    if multiplier > 1:
        scaled = amount.value * multiplier
        if learning.scaled_price_is_sane(scaled):
            return Amount(scaled, amount.raw, "هزار", amount.start, amount.end)
    if (
        settings.bare_three_digit_means_thousands
        and len(raw_digits) == 3
        and amount.value < 10_000
    ):
        return Amount(amount.value * 1000, amount.raw, "هزار", amount.start, amount.end)
    return amount


# Only real *words* join into a compound amount. The one-letter abbreviations
# (ت / t / k) are excluded: «ت» matched inside «تومان» turned «1098 تومان» into
# 1,098,000 — the exact opposite of what writing a currency word means.
_COMPOUND_UNITS: tuple[tuple[str, int], ...] = (
    ("میلیارد", 10**9),
    ("میلیون", 10**6),
    ("هزار", 10**3),
)


def compound_unit_amount(line: str) -> int:
    """Total of a `<number> <unit>` expression, e.g. «۱ میلیون و ۹۸ هزار».

    Each unit may appear at most once. «۶۹۸ هزار و ۵۹۸ هزار» is two amounts on
    one line (iPhone + Android); summing them would invent a total nobody wrote,
    so we return 0 and let the per-token path read the first one.
    """
    text = digits(line or "")
    for word, _factor in _COMPOUND_UNITS:
        if len(re.findall(word, text, re.I)) > 1:
            return 0
    total = 0
    for word, factor in _COMPOUND_UNITS:
        for match in re.finditer(rf"(\d+(?:[.,]\d+)?)\s*{word}", text, re.I):
            total += _value_of(match.group(1), factor)
    if not total:
        return 0
    tail = re.search(r"و\s*(\d+)\s*(?:تومان|تومن)?\s*$", text)
    if tail:
        total += _value_of(tail.group(1), 1)
    return total


def states_price_explicitly(line: str) -> bool:
    """Whether the line *says* it is a price (label or currency word).

    Used for the recency asymmetry: a stated price always wins, a bare number
    only replaces another bare number — so a trailing «کد 1098» can never
    repaint a price the owner wrote on purpose.
    """
    text = (line or "").strip()
    if not text:
        return False
    if _PRICE_HINT_RE.search(text):
        return True
    return any(amount.is_currency for amount in amounts_in_line(text))


def in_accepted_range(value: int) -> bool:
    return settings.price_min <= value <= settings.price_max


def looks_like_price_line(line: str) -> bool:
    """Whether a line is about a price at all.

    Rejects lines whose label explains the number («وزن …», «تاریخ …», «کد …»,
    «SKU …») and accepts anything that says «قیمت», carries a unit/currency
    suffix, or is nothing but an amount.
    """
    text = (line or "").strip()
    if not text:
        return False
    if _NON_PRICE_LABEL_RE.match(text):
        return False
    if _PRICE_HINT_RE.search(text):
        return True
    if _DATE_RE.search(text):
        return False          # 1403/01/01 and 1403-01-01 are dates, not price lists
    if any(not amount.is_bare for amount in amounts_in_line(text)):
        return True
    # «698 ایفون» / «ایفون: 698000» name a price group: that IS the shop's way
    # of writing a price, even without a «قیمت» word or a unit suffix. The
    # bare-number policy has to run first — «اندروید 598» means 598,000, and
    # checking 598 against PRICE_MIN would reject the line as too small.
    if group_of(text):
        scaled = [apply_bare_policy(a) for a in amounts_in_line(text)]
        if any(in_accepted_range(a.value) for a in scaled):
            return True
    return bool(_BARE_AMOUNT_LINE_RE.match(text))


def parse_line_amount(line: str) -> int:
    """The amount a single line states, or 0 when it states none.

    Priority: a compound unit expression, then an amount written *together with*
    its unit (this is what stops «S24 اولترا 768t» from reading the model number),
    then a bare number.
    """
    text = (line or "").strip()
    if not text:
        return 0
    compound = compound_unit_amount(text)
    if compound:
        return compound
    amounts = [apply_bare_policy(a) for a in amounts_in_line(text)]
    if not amounts:
        return 0
    for amount in amounts:
        if not amount.is_bare:
            return amount.value
    return amounts[0].value


def group_of(line: str) -> str:
    """The price group («iphone» / «android») a line mentions, else ``""``."""
    lowered = digits(line).casefold()
    for word, group in _GROUP_WORDS.items():
        if word in lowered:
            return group
    return ""


def group_amounts(line: str) -> dict[str, int]:
    """All `<group> ↔ amount` pairs stated on one line.

    Real posts put both groups on one line, so this scans every group token and
    gives each the *nearest* amount instead of stopping at the first match.
    """
    text = digits(line or "")
    lowered = text.casefold()
    mentions: list[tuple[int, str]] = []
    for word, group in _GROUP_WORDS.items():
        for match in re.finditer(re.escape(word), lowered):
            mentions.append((match.start(), group))
    if not mentions:
        return {}
    amounts = [apply_bare_policy(a) for a in amounts_in_line(text)]
    if not amounts:
        return {}
    out: dict[str, int] = {}
    for pos, group in mentions:
        after = [a for a in amounts if a.start >= pos and in_accepted_range(a.value)]
        before = [a for a in amounts if a.end <= pos and in_accepted_range(a.value)]
        # The amount that follows the word is its price («اندروید 598»); when
        # nothing follows, the nearest one before it is («698 ایفون»).
        best = after[0] if after else (before[-1] if before else None)
        if best is not None:
            out.setdefault(group, best.value)
    return out


def is_modelish(line: str) -> bool:
    """A line whose numbers are probably model numbers, not money."""
    return bool(
        re.search(
            r"\b(?:iphone|airpods?|samsung|galaxy|redmi|poco|xiaomi|pixel|huawei|honor)\b"
            r"|(?:^|[\s/,(])(?:s|a|m|f|note)\s?\d{1,3}\b",
            digits(line).casefold(),
            re.I,
        )
    )


def format_toman(value: int) -> str:
    return f"{value:,} تومان" if value else "—"


__all__ = [
    "Amount",
    "amounts_in_line",
    "apply_bare_policy",
    "compound_unit_amount",
    "digits",
    "format_toman",
    "group_amounts",
    "group_of",
    "in_accepted_range",
    "is_modelish",
    "looks_like_price_line",
    "parse_line_amount",
    "states_price_explicitly",
]
