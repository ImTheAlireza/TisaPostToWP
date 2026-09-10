"""High-precision phone-model parser for real-world Telegram product posts.

Design goals:
- Treat the post as semi-structured data, not as a plain bag of words.
- Use section context (Apple / Samsung / Xiaomi) to reduce false positives.
- Preserve meaningful sub-brand prefixes such as Redmi, while removing the
  generic output prefixes Samsung and Xiaomi.
- Preserve compatibility groups written with slash notation as ONE output item.
- Normalize common messy spacing/casing (17promax, 14Pro, Xsmax, A21 s, ...).
- Never turn a Samsung A21s into A21, and never drop Redmi from Redmi Note.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

EMOJI_RANGES_RE = re.compile(
    "["
    "\\U0001F1E6-\\U0001F1FF"
    "\\U0001F300-\\U0001F5FF"
    "\\U0001F600-\\U0001F64F"
    "\\U0001F680-\\U0001F6FF"
    "\\U0001F700-\\U0001F77F"
    "\\U0001F780-\\U0001F7FF"
    "\\U0001F800-\\U0001F8FF"
    "\\U0001F900-\\U0001F9FF"
    "\\U0001FA00-\\U0001FAFF"
    "\\U00002600-\\U000027BF"
    "\\U00002300-\\U000023FF"
    "]+"
)

INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")


def remove_emojis(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = INVISIBLE_RE.sub("", text)
    text = EMOJI_RANGES_RE.sub(" ", text)
    out: list[str] = []
    for ch in text:
        category = unicodedata.category(ch)
        if category in {"So", "Sk"} or ch in {"\ufe0e", "\ufe0f", "\u20e3", "\u200d"}:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _clean(text: str) -> str:
    text = remove_emojis(text or "")
    text = text.replace("⁩", " ").replace("⁦", " ").replace("：", ":")
    text = text.replace("–", "-").replace("—", "-")
    return text


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhoneModel:
    brand: str
    key: tuple[int, int, int, int]
    label: str


BRAND_ORDER = {"iPhone": 0, "Samsung": 1, "Xiaomi": 2}

# ---------------------------------------------------------------------------
# iPhone
# ---------------------------------------------------------------------------

IPHONE_VARIANTS = {
    "": (10, ""),
    "mini": (20, "Mini"),
    "air": (25, "Air"),
    "plus": (30, "Plus"),
    "pro": (40, "Pro"),
    "pro max": (50, "Pro Max"),
}


def _iphone_variant(raw: str) -> tuple[str, int, str]:
    compact = re.sub(r"[^a-z+]", "", (raw or "").lower())
    aliases = {
        "": "",
        "mini": "mini",
        "air": "air",
        "plus": "plus",
        "+": "plus",
        "pro": "pro",
        "promax": "pro max",
        "pro+max": "pro max",
        "max": "pro max",
    }
    normalized = aliases.get(compact, "")
    rank, display = IPHONE_VARIANTS[normalized]
    return normalized, rank, display


def _iphone_key(generation: int, rank: int) -> tuple[int, int, int, int]:
    return (generation, rank, 0, 0)


def _roman_key(token: str) -> tuple[int, int, int, int] | None:
    compact = re.sub(r"[^a-z]", "", token.lower())
    mapping = {
        "x": (10, 5),
        "xs": (10, 15),
        "xr": (10, 20),
        "xsmax": (10, 25),
    }
    value = mapping.get(compact)
    return _iphone_key(*value) if value else None


def _parse_single_iphone(raw: str) -> PhoneModel | None:
    token = raw.strip().strip("|,;:.-")
    token = re.sub(r"(?i)^iphone\s*", "", token)
    token = re.sub(r"\s+", "", token)
    if not token:
        return None

    if re.fullmatch(r"(?i)x(?:smax|s|r)?", token):
        key = _roman_key(token)
        if not key:
            return None
        compact = token.lower()
        labels = {"x": "iPhone X", "xs": "iPhone XS", "xr": "iPhone XR", "xsmax": "iPhone XS Max"}
        return PhoneModel("iPhone", key, labels[compact])

    match = re.fullmatch(r"(?i)(\d{1,2})(mini|air|promax|pro\s*max|pro|plus|\+)?", token)
    if not match:
        return None

    generation = int(match.group(1))
    if not 1 <= generation <= 30:
        return None

    normalized, rank, display = _iphone_variant(match.group(2) or "")
    label = f"iPhone {generation}" + (f" {display}" if display else "")
    return PhoneModel("iPhone", _iphone_key(generation, rank), label)


def _combine_iphone_group(models: list[PhoneModel]) -> PhoneModel | None:
    if not models:
        return None
    if len(models) == 1:
        return models[0]

    # Slash notation is intentionally ONE variable/item.
    # Example: 7+/8+ -> iPhone 7 Plus/8 Plus
    ordered = sorted(models, key=lambda m: m.key)
    labels = [m.label.removeprefix("iPhone ") for m in ordered]
    # If variants are equal, compact as "7/8"; otherwise preserve each side.
    label = "iPhone " + "/".join(labels)
    first = ordered[0]
    last = ordered[-1]
    return PhoneModel("iPhone", (first.key[0], first.key[1], 1, last.key[0]), label)


def extract_iphone_models(text: str) -> list[PhoneModel]:
    cleaned = _clean(text)
    found: dict[str, PhoneModel] = {}
    in_apple = False

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()

        # Section/context markers.
        if re.search(r"\b(apple|iphone)\s*[:：]?\s*$", low):
            in_apple = True
            continue
        if re.search(r"^\s*(samsung|xiaomi|redmi|poco)\b", low):
            in_apple = False
            continue

        if "iphone" not in low and not in_apple:
            continue

        # In real Telegram posts the section header is often `iPhone:` and
        # the following lines contain only `17 Pro Max`, without repeating
        # the word iPhone. Treat those lines as iPhone models while the Apple
        # section is active.
        if "iphone" in low:
            match = re.search(r"(?i)iphone\s*(.*)$", line)
            if not match:
                continue
            tail = match.group(1).strip()
        else:
            tail = line.strip()
        # Remove trailing marketing prose after the first clearly model-like group.
        # Usually one line contains one group: iphone 17promax or iphone 7/8.
        # A defensive split on pipes/commas/semicolons handles several groups.
        for group in re.split(r"\s*[|,;]\s*", tail):
            parts = [p.strip() for p in re.split(r"\s*/\s*", group) if p.strip()]
            models: list[PhoneModel] = []
            for part in parts:
                # Keep only a model token at the beginning of each slash component.
                m = re.match(r"(?i)(x(?:smax|s|r)?|\d{1,2})(?:\s*(?:mini|air|pro\s*max|promax|pro|plus|\+))?", part)
                if not m:
                    continue
                model = _parse_single_iphone(m.group(0))
                if model:
                    models.append(model)
            grouped = _combine_iphone_group(models)
            if grouped:
                found[grouped.label.casefold()] = grouped

    return list(found.values())


# ---------------------------------------------------------------------------
# Samsung
# ---------------------------------------------------------------------------

SAMSUNG_VARIANT_RANK = {"": 0, "plus": 20, "fe": 30, "ultra": 40}


def _samsung_suffix(raw: str) -> tuple[str, int]:
    compact = re.sub(r"\s+", "", (raw or "").lower())
    if compact in {"ultra"}:
        return "Ultra", 40
    if compact in {"fe", "fе"}:
        return "FE", 30
    if compact in {"plus", "+"}:
        return "Plus", 20
    return "", 0


def extract_samsung_models(text: str) -> list[PhoneModel]:
    cleaned = _clean(text)
    found: dict[str, PhoneModel] = {}
    samsung_context = False

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        if re.fullmatch(r"(?:samsung|galaxy\s+samsung)?\s*", low):
            samsung_context = True
            continue
        if re.match(r"(?i)^\s*(apple|iphone|xiaomi|redmi|poco)\b", line):
            samsung_context = False
            continue

        if not samsung_context and not re.search(r"(?i)(?<![A-Za-z0-9])[SA]\d{1,3}", line):
            continue

        # Start-of-model or clearly delimited model occurrences only.
        for m in re.finditer(
            r"(?i)(?<![A-Za-z0-9])(S\d{1,3}|A\d{1,3})"
            r"\s*(ultra|plus|\+|fe)?"
            r"(?:\s*(\d\s*G))?"
            r"(?:\s*(s))?",
            line,
        ):
            base = m.group(1).upper()
            suffix_raw = m.group(2) or ""
            network_raw = re.sub(r"\s+", "", m.group(3) or "").upper()
            trailing_s = bool(m.group(4)) and base.startswith("A")

            suffix, rank = _samsung_suffix(suffix_raw)
            # Galaxy A21s is a real distinct model; never discard the s.
            if trailing_s:
                suffix = "s"
                rank = 10

            label = base
            if suffix == "s":
                label += "s"
            elif suffix:
                label += f" {suffix}"
            if network_raw:
                label += f" {network_raw}"

            family_rank = 0 if base.startswith("S") else 1
            number = int(base[1:])
            key = (family_rank, number, rank, 0)
            found[label.casefold()] = PhoneModel("Samsung", key, label)

    return list(found.values())


# ---------------------------------------------------------------------------
# Xiaomi / Redmi / POCO
# ---------------------------------------------------------------------------

XIAOMI_VARIANT_RANK = {"": 0, "s": 10, "plus": 20, "pro": 30, "pro plus": 40}


def _xiaomi_variant(raw: str) -> tuple[str, int]:
    compact = re.sub(r"\s+", "", (raw or "").lower())
    if compact in {"proplus", "pro+"}:
        return "Pro Plus", 40
    if compact == "pro":
        return "Pro", 30
    if compact == "plus":
        return "Plus", 20
    if compact == "s":
        return "S", 10
    return "", 0


def extract_xiaomi_models(text: str) -> list[PhoneModel]:
    cleaned = _clean(text)
    found: dict[str, PhoneModel] = {}
    xiaomi_context = False

    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()

        if re.match(r"^\s*xiaomi\s*$", low):
            xiaomi_context = True
            continue
        if re.match(r"^\s*(apple|iphone|samsung)\b", low):
            xiaomi_context = False
            continue

        # Accept Note models in Xiaomi context, and also explicit Xiaomi/Redmi/POCO forms.
        if not xiaomi_context and not re.search(r"(?i)\b(?:xiaomi\s+)?(?:redmi\s+)?note\s*\d", line):
            continue

        rx = re.compile(
            r"(?i)\b(?:(xiaomi)\s+)?(?:(redmi)\s+)?"
            r"note\s*(\d{1,3})"
            r"\s*(s)?"
            r"\s*(pro\s*plus|pro\+|pro|plus)?"
            r"\s*(4\s*G|5\s*G)?"
        )
        for m in rx.finditer(line):
            explicit_xiaomi = bool(m.group(1))
            redmi = bool(m.group(2))
            number = int(m.group(3))
            if not 1 <= number <= 100:
                continue

            variant_raw = m.group(5) or (m.group(4) or "")
            variant, rank = _xiaomi_variant(variant_raw)
            network = re.sub(r"\s+", "", m.group(6) or "").upper()

            # Generic Xiaomi is removed from output. Redmi is meaningful and preserved.
            # In Xiaomi/Redmi Note listings, the product family is Redmi Note.
            # Keep Redmi in the canonical name even when the source shortens it to only Note.
            label = "Redmi Note " + str(number)
            if variant:
                label += f" {variant}"
            if network:
                label += f" {network}"

            key = (number, rank, 0 if not redmi else 1, 0)
            found[label.casefold()] = PhoneModel("Xiaomi", key, label)

    return list(found.values())


# ---------------------------------------------------------------------------
# Combined extraction / output
# ---------------------------------------------------------------------------


def extract_phone_models(text: str) -> list[PhoneModel]:
    if not text:
        return []

    items = extract_iphone_models(text) + extract_samsung_models(text) + extract_xiaomi_models(text)
    unique: dict[tuple[str, str], PhoneModel] = {}
    for item in items:
        unique[(item.brand, item.label.casefold())] = item

    return sorted(
        unique.values(),
        key=lambda item: (BRAND_ORDER[item.brand], item.key, item.label.casefold()),
    )


def normalize_caption(text: str) -> str:
    return " | ".join(item.label for item in extract_phone_models(text))


__all__ = [
    "PhoneModel",
    "remove_emojis",
    "extract_iphone_models",
    "extract_samsung_models",
    "extract_xiaomi_models",
    "extract_phone_models",
    "normalize_caption",
]
