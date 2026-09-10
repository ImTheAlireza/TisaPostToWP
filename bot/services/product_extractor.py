"""AI-assisted extraction of product data from free-form Telegram messages."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from bot.config import settings
from bot.services.phone_parser import normalize_caption


@dataclass
class ProductData:
    title: str = ""
    price: int = 0
    prices: dict[str, int] = field(default_factory=dict)
    sku_prefix: str = ""
    models: list[str] = field(default_factory=list)
    attributes: dict[str, list[str]] = field(default_factory=dict)
    categories: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SYSTEM_PROMPT = """You extract WooCommerce variable-product data from informal Persian Telegram messages.
Return ONLY JSON with keys: title, price, prices, sku_prefix, attributes, categories.
price is the fallback/common integer price in toman; if a bare 3-digit number is clearly in thousands, multiply by 1000. Read prices ONLY from explicit price/amount lines or amounts with a currency suffix such as 768t, 768 تومان, 768k. Never use a phone model number (for example the 17 in iPhone 17) as a price.
prices is an optional object for group pricing, using only keys iphone and android, for example {"iphone":698000,"android":598000}. When the text says «ایفون 698» and «اندروید 598», do not collapse them into one price.
sku_prefix is uppercase Latin letters such as BO. Do not invent values.
The phone models are supplied separately and must not be put in attributes.
attributes must be an object whose keys are Persian attribute names such as رنگ, طرح, جنس and whose values are arrays of distinct strings. Only create an attribute when it has at least TWO selectable values. A single value such as «زرد» is part of the product title/name, not an attribute. Words that describe the product name (for example «قاب پلومریا زرد») must stay in title and must not become attributes.
Do not put product descriptions in the result. Do not guess categories from the product type or appearance: only return categories explicitly supported by the messages, plus the unavoidable phone-brand path inferred from detected models. Never choose چاپی unless the text explicitly says چاپ/چاپی/پرینت. categories must contain only exact paths from the supplied taxonomy, and never choose فروش ویژه, 💥 بلک فرایدی, or محصولات عمده.
The input has two labeled sources. PRODUCT INFO is the authoritative source for title, SKU, price and explicit attributes. Use CAPTION for those fields only when PRODUCT INFO does not contain them. Models may be merged from both sources. Never let a model number override an explicit price from either source.
"""


def _endpoint() -> str:
    base = settings.ai_base_url.rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def _json_object(text: str) -> dict[str, Any]:
    try:
        obj = json.loads(text.strip())
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError("AI returned invalid JSON")
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("AI response is not an object")
    return obj


def _digits(value: str) -> str:
    return value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def _number_from_line(line: str) -> int:
    match = re.search(r"(?<!\d)([۰-۹٠-٩\d][۰-۹٠-٩\d,،.]*)\s*(تومان|تومن|هزار|ت|t|k)?\b", line, re.I)
    if not match:
        return 0
    raw = _digits(match.group(1)).replace(",", "").replace("،", "").replace(".", "")
    suffix = (match.group(2) or "").casefold()
    try:
        value = int(raw)
        if suffix in {"k", "هزار", "ت", "t"} or (value < 10000 and len(raw) == 3):
            value *= 1000
        return value
    except ValueError:
        return 0


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


def _fallback(text: str, models: list[str]) -> ProductData:
    lines = [re.sub(r"\s+", " ", x).strip(" -–—") for x in text.splitlines()]
    lines = [x for x in lines if x]
    price = 0
    prices: dict[str, int] = {}
    for line in lines:
        low = line.casefold()
        # Model numbers must never become prices: iPhone 17, S24, AirPods 3,
        # and model-list lines are deliberately ignored here.
        explicit_price = bool(re.search(r"قیمت|price|تومان|تومن|هزار|\d\s*[tTkKت]\b", line, re.I))
        value = _number_from_line(line)
        if not value:
            continue
        model_line = bool(re.search(r"\b(?:iphone|airpods?|samsung|galaxy|redmi|poco|xiaomi)\b|(?:^|\s)[sa]\d{1,3}\b", low, re.I))
        # A labelled group such as `698 ایفون` is a price even though it
        # contains the word iPhone/ایفون; model numbers are much smaller.
        group_price_line = bool(re.search(r"ایفون|آیفون|iphone|اندروید|android|سامسونگ|samsung|شیائومی|xiaomi|redmi|poco", low)) and value >= 100000
        if model_line and not explicit_price and not group_price_line:
            continue
        # Accept a standalone three-digit shop amount such as `758`, but do
        # not accept incidental one/two-digit numbers from descriptive text.
        raw_digits = re.sub(r"\D", "", _digits(line))
        if not explicit_price and len(raw_digits) < 3:
            continue
        if re.search(r"ایفون|آیفون|iphone", low):
            prices["iphone"] = value
        elif re.search(r"اندروید|android|سامسونگ|samsung|شیائومی|xiaomi|redmi|poco", low):
            prices["android"] = value
        elif not price:
            price = value
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
    remaining = [x for x in candidates if x != title]
    if len(remaining) >= 2:
        attrs["رنگ" if all(len(x.split()) <= 3 for x in remaining) else "ویژگی"] = remaining
    return ProductData(title=title, price=price, prices=prices, sku_prefix=prefix, models=models, attributes=attrs)


async def extract_product(text: str, models: list[str], taxonomy: str, caption: str = "", info_text: str = "") -> ProductData:
    # Keep one AI request, but preserve provenance. The deterministic parser
    # receives PRODUCT INFO first so its title/SKU/price precedence is stable.
    source_for_fallback = "\n".join(part for part in (info_text, caption) if part.strip()) or text
    fallback = _fallback(source_for_fallback, models)
    if not (settings.ai_base_url and settings.ai_token and settings.ai_model):
        return fallback
    payload = {
        "model": settings.ai_model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"TAXONOMY:\n{taxonomy}\n\nPHONE MODELS:\n{json.dumps(models, ensure_ascii=False)}\n\n=== CAPTION / کپشن عکس‌ها ===\n{caption or '<خالی>'}\n\n=== PRODUCT INFO / متن اطلاعات محصول ===\n{info_text or '<خالی>'}\n\n=== END INPUT ==="},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(_endpoint(), headers={"Authorization": f"Bearer {settings.ai_token}"}, json=payload)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
        obj = _json_object(content)
        attrs = obj.get("attributes") if isinstance(obj.get("attributes"), dict) else {}
        clean_attrs = {
            str(k): list(dict.fromkeys(str(v) for v in vals if str(v).strip()))
            for k, vals in attrs.items()
            if isinstance(vals, list) and len(set(str(v).strip() for v in vals if str(v).strip())) >= 2
        }
        raw_prices = obj.get("prices") if isinstance(obj.get("prices"), dict) else {}
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
                try: parsed = int(_digits(str(value)).replace(",", ""))
                except ValueError: parsed = 0
            if parsed: prices[group] = parsed
        if not prices: prices = fallback.prices
        categories = obj.get("categories") if isinstance(obj.get("categories"), list) else []
        ai_price = int(_digits(str(obj.get("price") or 0)).replace(",", "") or 0)
        # A bare amount such as `768t` is deterministic and must win over an
        # AI hallucination based on a model number (for example iPhone 17).
        final_price = fallback.price if fallback.price else ai_price
        if fallback.price and not fallback.prices:
            prices = {}
        result = ProductData(
            title=str(obj.get("title") or fallback.title).strip(),
            price=final_price,
            prices=prices,
            sku_prefix=re.sub(r"[^A-Za-z0-9]", "", str(obj.get("sku_prefix") or fallback.sku_prefix)).upper(),
            models=models,
            attributes=clean_attrs,
            categories=[str(x) for x in categories],
        )
        return result
    except Exception:
        return fallback
