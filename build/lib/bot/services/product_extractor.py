"""AI-assisted extraction of product data from free-form Telegram messages."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from bot.config import settings
from bot.services import learning, money
from bot.services import postmodel as ev
from bot.services.color_matrix import (
    color_key,
    confirmed_colors,
    extract_colors,
    model_signature,
)

logger = logging.getLogger(__name__)


@dataclass
class ProductData:
    title: str = ""
    price: int = 0
    prices: dict[str, int] = field(default_factory=dict)
    sku_prefix: str = ""
    models: list[str] = field(default_factory=list)
    attributes: dict[str, list[str]] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)
    # Per-model color limits: model label -> colors actually in stock for it.
    # The color attribute still lists EVERY color; this only narrows the
    # variations that get built (see bot/services/color_matrix.py).
    model_colors: dict[str, list[str]] = field(default_factory=dict)
    # Filled in by bot/modules/product_flow.py from bot/services/plan.py so the
    # preview, the REST payload and the ZIP manifest all quote one number.
    variation_count: int = 0
    # Categories the store does not have (the importer creates nothing by
    # accident); surfaced as warnings instead of being dropped silently.
    rejected_categories: list[str] = field(default_factory=list)
    # field name -> where that value came from, with the quote that produced it
    # (bot/services/postmodel.py). The preview shows this so a wrong guess is
    # spotted at a glance instead of after the product is live.
    evidence: dict[str, ev.Evidence] = field(default_factory=dict)
    # policy decisions the user must see, e.g. an amount we refused as a price
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SYSTEM_PROMPT = """You extract WooCommerce variable-product data from informal Persian Telegram messages.
Return ONLY JSON with keys: title, price, prices, sku_prefix, attributes, model_colors, categories.
price is the fallback/common integer price in toman; if a bare 3-digit number is clearly in thousands, multiply by 1000. Read prices ONLY from explicit price/amount lines or amounts with a currency suffix such as 768t, 768 تومان, 768k. Never use a phone model number (for example the 17 in iPhone 17) as a price.
prices is an optional object for group pricing, using only keys iphone and android, for example {"iphone":698000,"android":598000}. When the text says «ایفون 698» and «اندروید 598», do not collapse them into one price.
sku_prefix is uppercase Latin letters such as BO. Do not invent values.
The phone models are supplied separately and must not be put in attributes.
attributes must be an object whose keys are Persian attribute names such as رنگ, طرح, جنس and whose values are arrays of distinct strings. Only create an attribute when it has at least TWO selectable values. A single value such as «زرد» is part of the product title/name, not an attribute. Words that describe the product name (for example «قاب پلومریا زرد») must stay in title and must not become attributes.
When the text lists colors per phone model (for example «17promax: سفید/مشکی/نارنجی» or «S25ultra (فقط سفید)» or a section scope such as «xiaomi (فقط سفید)»), the رنگ attribute must still contain EVERY color mentioned anywhere in the text — never only the colors of one model. The per-model limits belong in model_colors instead.
model_colors is an optional object mapping each phone model (use the exact label from PHONE MODELS) to the array of colors available for THAT model only. Fill it only for models whose colors the text states explicitly, and never invent a color that is not written in the text. Omit any model without an explicit color list.
Do not put product descriptions in the result. Do not guess categories from the product type or appearance: only return categories explicitly supported by the messages, plus the unavoidable phone-brand path inferred from detected models. Never choose چاپی unless the text explicitly says چاپ/چاپی/پرینت. categories must contain only exact paths from the supplied taxonomy, and never choose فروش ویژه, 💥 بلک فرایدی, or محصولات عمده.
The input has two labeled sources. PRODUCT INFO is the authoritative source for title, SKU, price and explicit attributes. Use CAPTION for those fields only when PRODUCT INFO does not contain them. Models may be merged from both sources. Never let a model number override an explicit price from either source.
"""


def _endpoint() -> str:
    base = settings.ai_base_url.rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _dict_field(obj: dict[str, Any], key: str) -> dict[str, Any]:
    """A dict member of an AI response, or ``{}`` (never ``None``)."""
    value = obj.get(key)
    return value if isinstance(value, dict) else {}


def _list_field(obj: dict[str, Any], key: str) -> list[Any]:
    value = obj.get(key)
    return value if isinstance(value, list) else []


def _json_object(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("AI returned invalid JSON") from None
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("AI response is not an object")
    return obj


def _digits(value: str) -> str:
    """Persian/Arabic digits → Latin (kept for callers/tests; see :mod:`bot.services.money`)."""
    return money.digits(value)


# Amount parsing lives in bot/services/money.py — one implementation for the
# deterministic path, the AI validation and the preview. ``_number_from_line``
# and ``_scan_prices`` stay as thin wrappers because tests and the flow import
# them by these names.
_number_from_line = money.parse_line_amount


def extract_accessory_models(text: str) -> list[str]:
    """Extract non-phone model families such as AirPods from product info.

    Phone normalization intentionally rejects accessories, so accessories need
    this small deterministic path. Slash notation remains one compatibility
    model (Airpods 1/2 and Airpods Pro/Pro2).
    """
    source = re.sub(r"[🌟•▪️*]+", " ", text or "")
    found: list[str] = []
    direct = re.findall(
        r"(?i)\bairpods?\s+(?:pro\s*/\s*pro\s*2|pro\s*3|[1-4](?:\s*/\s*[1-4])?)\b",
        source,
    )
    found.extend(re.sub(r"\s+", " ", value).strip() for value in direct)
    # A common Telegram format is `Airpods:` followed by bare values on the
    # next lines: 1/2, 3, 4, Pro/Pro2, Pro3.
    for match in re.finditer(r"(?im)^\s*airpods?\s*:\s*(.*)$", source):
        tail = match.group(1).strip()
        if tail:
            candidates = [tail]
        else:
            candidates = []
            for line in source[match.end():].splitlines():
                value = re.sub(r"^[^A-Za-z0-9]+", "", line).strip()
                if not value:
                    continue
                if re.fullmatch(r"(?i)(?:[1-4](?:\s*/\s*[1-4])?|pro\s*/\s*pro\s*2|pro\s*3)", value):
                    candidates.append(value)
                else:
                    break
        for value in candidates:
            normalized = re.sub(r"\s+", " ", value).strip()
            if re.fullmatch(r"(?i)(?:[1-4](?:\s*/\s*[1-4])?|pro\s*/\s*pro\s*2|pro\s*3)", normalized):
                found.append("Airpods " + normalized)
    return list(dict.fromkeys(found))


@dataclass
class PriceScan:
    """The prices one text block states, plus where each came from."""

    price: int = 0
    prices: dict[str, int] = field(default_factory=dict)
    evidence: dict[str, ev.Evidence] = field(default_factory=dict)
    #: numeric lines that were rejected as prices (weight, dates, SKUs)
    rejected: list[str] = field(default_factory=list)
    #: rejected lines where the seller *did* say «قیمت» — worth showing, because
    #: a missing price is what they are looking for; the rest would be noise.
    surprising: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.price or self.prices)


def _scan_prices(lines: list[str]) -> PriceScan:
    """Parse the prices out of ONE text block.

    Three rules, learned the hard way from real posts:

    * a line must *be* about a price. «وزن 250 گرم», «تاریخ 1403/01/01» and
      «SKU: BO147» all contain numbers and none of them is a price — they used
      to win, because «the last amount in the block wins» had no shape check;
    * within a block the last amount wins, so a follow-up correction
      («1098», then «قیمت 1098000 تومان») takes effect — with one asymmetry: a
      stated price is only replaced by another stated price, never by a bare
      number that happens to come after it;
    * every group mentioned on a line is read («ایفون 698 اندروید 598» used to
      return only the iPhone price, and Android silently inherited it).

    Blocks are merged by the caller with PRODUCT INFO ahead of the caption, so
    a caption amount can never override an explicit correction.
    """
    scan = PriceScan()
    price_explicit = False
    for line in lines:
        if not money.looks_like_price_line(line):
            if money.amounts_in_line(line):
                # It had a number and we still said no. Only a line that
                # mentioned a price is surfaced — otherwise every «قاب ۱۳» and
                # every date would add a paragraph to the preview.
                scan.rejected.append(line)
                if money.states_price_explicitly(line):
                    scan.surprising.append(line)
            continue
        value = money.parse_line_amount(line)
        if not value:
            continue
        if not money.in_accepted_range(value):
            scan.rejected.append(line)
            scan.surprising.append(line)
            continue
        groups = money.group_amounts(line)
        for group, group_value in groups.items():
            scan.prices[group] = group_value
            ev.merge(scan.evidence, "prices", ev.CAPTION, quote=line)
        if groups:
            continue
        explicit = money.states_price_explicitly(line)
        if explicit or not scan.price or not price_explicit:
            scan.price = value
            price_explicit = explicit
            ev.merge(scan.evidence, "price", ev.CAPTION, quote=line)
    return scan


def _clip_line(text: str, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _split_lines(text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", x).strip(" -–—") for x in (text or "").splitlines()]
    return [x for x in lines if x]


def _fallback(text: str, models: list[str], price_blocks: list[str] | None = None) -> ProductData:
    lines = _split_lines(text)
    scan = PriceScan()
    for block in price_blocks or [text]:
        block_scan = _scan_prices(_split_lines(block))
        if not scan.price and block_scan.price:
            scan.price = block_scan.price
        for name, item in block_scan.evidence.items():
            ev.merge(scan.evidence, name, item.source, quote=item.note)
        for group, value in block_scan.prices.items():
            scan.prices.setdefault(group, value)
        scan.rejected.extend(block_scan.rejected)
        scan.surprising.extend(block_scan.surprising)
    price, prices = scan.price, scan.prices
    if not price and prices:
        price = next(iter(prices.values()))
    prefix = ""
    for line in lines:
        if re.fullmatch(r"[A-Za-z]{1,12}", line):
            prefix = line.upper()
            break
    explicit_title = next((re.sub(r"^\s*(?:عنوان|نام\s*محصول)\s*[:：]\s*", "", x).strip() for x in lines if re.match(r"^\s*(?:عنوان|نام\s*محصول)\s*[:：]", x)), "")
    candidates = [
        x for x in lines
        if x != prefix
        and not re.search(r"تومان|تومن|هزار|قیمت|price", x, re.I)
        and not re.match(r"^\s*(?:sku|شناسه|کد|مدل|مدل‌ها|ویژگی|رنگ|عنوان|نام\s*محصول)\s*[:：]?", x, re.I)
        and not re.fullmatch(r"\d[\d,،.]*[tTkKت]?", _digits(x))
    ]
    title = explicit_title or next((x for x in candidates if not any(v in x for v in models)), "")
    attrs: dict[str, list[str]] = {}
    # Without the AI the only attribute that can be read reliably is the color
    # list. Prose and model lines are NOT a selectable attribute: dumping them
    # into a «ویژگی» axis used to multiply the variation count by the whole
    # caption. Colors are scanned across every line (also «رنگ: …» and model
    # lines such as «S26ultra (صورتی و سفید)»); the per-model limits are then
    # added by bot/services/color_matrix.py.
    colors: list[str] = []
    seen_colors: set[str] = set()
    for line in lines:
        if line == title:
            continue
        for color in extract_colors(line, allow_unknown=False):
            key = color_key(color)
            if key not in seen_colors:
                seen_colors.add(key)
                colors.append(color)
    if len(colors) >= 2:
        attrs["رنگ"] = colors
    evidence = dict(scan.evidence)
    notes: list[str] = []
    if title:
        ev.merge(evidence, "title", ev.CAPTION, quote=title)
    if prefix:
        ev.merge(evidence, "sku_prefix", ev.CAPTION, quote=prefix)
    if colors:
        ev.merge(evidence, "colors", ev.CAPTION, quote="، ".join(colors[:6]))
    if models:
        ev.merge(evidence, "models", ev.CAPTION, quote="، ".join(models[:4]))
    for rejected in scan.surprising[:2]:
        notes.append(f"«{_clip_line(rejected)}» عدد داشت ولی قیمت نشد (خارج از بازه یا بی‌واجه)")
    if price and len(prices) < 2:
        notes.append("قیمت از متن محصول گرفته شد و برای همهٔ رنگ‌ها یکسان است")
    return ProductData(title=title, price=price, prices=prices, sku_prefix=prefix,
                       models=models, attributes=attrs, evidence=evidence, notes=notes)


def _clean_model_colors(raw: dict[str, Any], models: list[str], source_text: str) -> dict[str, list[str]]:
    """Validate the AI's per-model colors against the real models and the text.

    A key must match a detected model by signature and every color must really
    appear in the source, so an AI guess can never delete a sellable variation
    or invent a color that is not in stock.
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    signatures = {model_signature(model): str(model) for model in models if model}
    out: dict[str, list[str]] = {}
    for key, values in raw.items():
        if not isinstance(values, list):
            continue
        target = signatures.get(model_signature(str(key)))
        if not target:
            continue
        bucket = out.setdefault(target, [])
        known = {color_key(item) for item in bucket}
        for color in confirmed_colors([str(v) for v in values if str(v).strip()], source_text):
            if color_key(color) not in known:
                known.add(color_key(color))
                bucket.append(color)
    return {label: colors for label, colors in out.items() if colors}


def _apply_learned_terms(data: ProductData) -> ProductData:
    """Apply the owner's learned term corrections to titles and attribute values.

    Prices are handled inside ``_number_from_line`` (a learned scale is a numeric
    rule, not a text substitution); SKU prefixes are left alone because the owner
    sets those deliberately.
    """
    before = data.title
    data.title = learning.apply_terms(data.title)
    if data.title != before:
        ev.merge(data.evidence, "title", ev.LEARNED, quote=f"«{before}» ← «{data.title}»")
    if data.attributes:
        for name, values in data.attributes.items():
            applied = learning.apply_terms_to_values(values)
            if applied != values:
                ev.merge(data.evidence, name, ev.LEARNED,
                         quote=f"«{'، '.join(values[:3])}» ← «{'، '.join(applied[:3])}»")
            data.attributes[name] = applied
    return data


async def extract_product(text: str, models: list[str], taxonomy: str, caption: str = "", info_text: str = "") -> ProductData:
    # Keep one AI request, but preserve provenance. The deterministic parser
    # receives PRODUCT INFO first so its title/SKU/price precedence is stable.
    source_for_fallback = "\n".join(part for part in (info_text, caption) if part.strip()) or text
    # PRODUCT INFO stays authoritative over the caption, but within it the
    # owner's newest line is a correction of the older ones (see _scan_prices).
    fallback = _fallback(source_for_fallback, models, price_blocks=[info_text, caption])
    # Learned term corrections apply to the deterministic result as well, so a
    # shop with no AI configured still honors what the owner taught the bot.
    _apply_learned_terms(fallback)
    if not (settings.ai_base_url and settings.ai_token and settings.ai_model):
        return fallback
    # The AI is told the same rules explicitly, so the two paths cannot disagree
    # about a corrected term.
    learned_rules = learning.rules_for_prompt()
    rules_block = ""
    if learned_rules:
        rules_block = (
            "=== LEARNED OWNER RULES / قواعد یادگرفته‌شده از اصلاحات مالک ===\n"
            f"{learned_rules}\n\n"
        )
    user_message = (
        f"TAXONOMY:\n{taxonomy}\n\n"
        f"PHONE MODELS:\n{json.dumps(models, ensure_ascii=False)}\n\n"
        f"{rules_block}"
        f"=== CAPTION / کپشن عکس‌ها ===\n{caption or '<خالی>'}\n\n"
        f"=== PRODUCT INFO / متن اطلاعات محصول ===\n{info_text or '<خالی>'}\n\n"
        f"=== END INPUT ==="
    )
    payload = {
        "model": settings.ai_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.ai_timeout_seconds) as client:
            response = await client.post(_endpoint(), headers={"Authorization": f"Bearer {settings.ai_token}"}, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        obj = _json_object(content)
        attrs = _dict_field(obj, "attributes")
        clean_attrs = {
            str(k): list(dict.fromkeys(str(v) for v in vals if str(v).strip()))
            for k, vals in attrs.items()
            if isinstance(vals, list) and len({str(v).strip() for v in vals if str(v).strip()}) >= 2
        }
        raw_model_colors = _dict_field(obj, "model_colors")
        model_colors = _clean_model_colors(raw_model_colors, models, source_for_fallback)
        raw_prices = _dict_field(obj, "prices")
        prices = {}
        for key, value in raw_prices.items():
            group = str(key).casefold().strip()
            if group in {"iphone", "ایفون", "آیفون"}:
                group = "iphone"
            elif group in {"android", "اندروید", "samsung", "xiaomi", "شیائومی"}:
                group = "android"
            else:
                continue
            parsed = _number_from_line(str(value))
            if not parsed:
                try:
                    parsed = int(_digits(str(value)).replace(",", ""))
                except ValueError:
                    parsed = 0
            if parsed:
                prices[group] = parsed
        if not prices:
            prices = fallback.prices
        categories = _list_field(obj, "categories")
        ai_price = int(_digits(str(obj.get("price") or 0)).replace(",", "") or 0)
        # A bare amount such as `768t` is deterministic and must win over an
        # AI hallucination based on a model number (for example iPhone 17).
        final_price = fallback.price if fallback.price else ai_price
        if fallback.price and not fallback.prices:
            prices = {}
        # Provenance: which field came from the model, and which AI answer the
        # deterministic reading of the text overrode. Everything the AI adds is
        # still an interpretation — the preview must not dress it as a fact.
        evidence = dict(fallback.evidence)
        notes = list(fallback.notes)
        ai_title = str(obj.get("title") or "").strip()
        if ai_title and ai_title != fallback.title:
            ev.merge(evidence, "title", ev.AI, quote=ai_title, overwrite=True)
            notes.append("عنوان را هوش مصنوعی نوشته؛ اگر لازم شد اصلاحش کن")
        if fallback.price and ai_price and ai_price != fallback.price:
            notes.append("قیمت هوش مصنوعی کنار گذاشته شد؛ عددی که خودت نوشتی معتبرتر است")
        if fallback.price and prices and not fallback.prices:
            notes.append("قیمت گروهی هوش مصنوعی حذف شد چون کپشن یک قیمت صریح داشت")
        if not fallback.prices and prices:
            ev.merge(evidence, "prices", ev.AI, quote="قیمت جدا برای هر گروه", overwrite=True)
        if clean_attrs and not fallback.attributes:
            for name, values in clean_attrs.items():
                ev.merge(evidence, name if name in ev.PREVIEW_FIELDS else "colors",
                         ev.AI, quote="، ".join(values[:4]), overwrite=True)
        if [str(x) for x in categories] and not fallback.categories:
            ev.merge(evidence, "category", ev.AI, quote="، ".join(str(x) for x in categories)[:60], overwrite=True)
        if str(obj.get("description") or "").strip():
            ev.merge(evidence, "description", ev.AI, quote="نوشتهٔ هوش مصنوعی", overwrite=True)
        result = ProductData(
            title=str(obj.get("title") or fallback.title).strip(),
            price=final_price,
            prices=prices,
            sku_prefix=re.sub(r"[^A-Za-z0-9]", "", str(obj.get("sku_prefix") or fallback.sku_prefix)).upper(),
            models=models,
            attributes=clean_attrs,
            categories=[str(x) for x in categories],
            model_colors=model_colors,
            evidence=evidence,
            notes=notes,
        )
        return _apply_learned_terms(result)
    except Exception as exc:
        # Silent failure used to look like «the bot misread me»; say what
        # happened in the log and in the preview so the user knows the text was
        # read without the model's help.
        logger.warning("AI extraction failed (%s); continuing from the text alone", exc)
        fallback.notes.append("هوش مصنوعی در دسترس نبود؛ فقط متن خودت خوانده شد")
        return fallback
