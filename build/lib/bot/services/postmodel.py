"""Where every product field came from — the "why" the preview must show.

The bot assembles a product from four sources (caption, filenames, OCR, the AI
model) and previously the result looked identical no matter how it was
produced. A wrong price and a correct one were indistinguishable, so the user
had to re-read everything instead of checking the two fields they were unsure
about.

:func:`bot.services.postmodel.evidence` is the single place that records a
source and a short quote for each field, which buys three things:

* the preview can say «قیمت از خط «قیمت 698» در کپشن» — a one-glance check;
* a policy decision (for example «وزن 250 گرم» not counted as a price) is
  stated out loud instead of silently overriding the user;
* corrections can be attributed: when the user edits a field whose source was
  ``caption``, that is the pattern to learn from (see
  :mod:`bot.services.learning`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

CAPTION = "caption"
FILENAME = "filename"
OCR = "ocr"
AI = "ai"
USER = "user"
POLICY = "policy"
LEARNED = "learned"
UPDATE = "update"
VOCAB = "vocabulary"

#: Trust ladder, least to most trusted. The seller's own words beat any machine
#: reading, a policy decision beats raw text, a learned correction beats the
#: caption, and what the user typed now beats everything (see merge()).
SOURCES = (AI, OCR, FILENAME, POLICY, VOCAB, CAPTION, LEARNED, UPDATE, USER)

#: What the preview shows, in the order a seller checks them.
PREVIEW_FIELDS = (
    "title",
    "model",
    "price",
    "prices",
    "colors",
    "models",
    "sku_prefix",
    "category",
    "tags",
    "stock",
    "barcode",
    "description",
)

_QUOTES = re.compile(r"[«”][^«»”]{1,80}[»“]")
_WHITESPACE = re.compile(r"\s+")


def _clip(text: str, limit: int = 90) -> str:
    text = _WHITESPACE.sub(" ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class Evidence:
    """Where a value came from, with the quote that produced it."""

    source: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "note": self.note}

    def line(self) -> str:
        """One Persian line for the preview."""
        label = SOURCE_LABELS.get(self.source, self.source)
        return f"{label}: {self.note}" if self.note else label


SOURCE_LABELS = {
    CAPTION: "متن کپشن",
    FILENAME: "نام فایل",
    OCR: "تصویر (OCR)",
    AI: "هوش مصنوعی",
    USER: "ویرایش شما",
    POLICY: "قاعده",
    LEARNED: "از اصلاح قبلی شما",
    UPDATE: "محصول موجود (برگشت از انبار)",
    VOCAB: "واژه‌نامهٔ فروشگاه",
}


def _trust(source: str) -> int:
    """Rank of a source; anything unknown is treated as the least trusted."""
    try:
        return SOURCES.index(source)
    except ValueError:
        return -1


def note(source: str, text: str = "", *, quote: str = "") -> Evidence:
    """Build evidence; ``quote`` is clipped and trimmed to the useful fragment.

    When a whole caption line is available we keep only the part around the
    quoted amount — the preview must stay readable.
    """
    text = (text or "").strip()
    if not text:
        return Evidence(source, _clip(quote))
    if quote and quote in text:
        return Evidence(source, _clip(quote))
    return Evidence(source, _clip(text))


def best_quote(line: str, needle: str, *, window: int = 46) -> str:
    """A short slice of ``line`` around ``needle`` («…قیمت 698 تومان…»)."""
    if not line:
        return ""
    index = line.find(needle) if needle else -1
    if index < 0:
        return _clip(line, window)
    start = max(0, index - 12)
    end = min(len(line), index + len(needle) + window - 12)
    prefix = "…" if start else ""
    suffix = "…" if end < len(line) else ""
    return f"{prefix}{_WHITESPACE.sub(' ', line[start:end].strip())}{suffix}"


def merge(target: dict[str, Evidence], field_name: str, source: str,
          text: str = "", *, quote: str = "", overwrite: bool = False) -> None:
    """Record evidence for ``field_name`` unless a higher-trust source owns it.

    Trust follows :data:`SOURCES`: an AI guess cannot explain away the
    seller's own sentence, and nothing explains away what the user just
    typed. Pass ``overwrite`` when the payload really did change.
    """
    current = target.get(field_name)
    if current is not None and not overwrite and _trust(current.source) >= _trust(source):
        return
    target[field_name] = note(source if source in SOURCES else POLICY, text, quote=quote)


def from_dict(data: Any) -> dict[str, Evidence]:
    if not isinstance(data, dict):
        return {}
    out: dict[str, Evidence] = {}
    for key, value in data.items():
        if isinstance(value, Evidence):
            out[str(key)] = value
        elif isinstance(value, dict) and value.get("source") in SOURCES:
            out[str(key)] = Evidence(str(value["source"]), str(value.get("note") or ""))
    return out


def to_dict(evidence: dict[str, Evidence]) -> dict[str, Any]:
    return {key: value.to_dict() for key, value in evidence.items()}


def preview_lines(evidence: dict[str, Evidence]) -> list[str]:
    """The «منبع» block for the preview card: one line per explained field."""
    lines: list[str] = []
    for name in PREVIEW_FIELDS:
        item = evidence.get(name)
        if item is None:
            continue
        if not item.note and item.source in (CAPTION, FILENAME, OCR):
            continue          # no story worth telling
        lines.append(f"{_FIELD_LABELS.get(name, name)} ← {item.line()}")
    return lines


_FIELD_LABELS = {
    "title": "عنوان",
    "model": "مدل",
    "price": "قیمت",
    "prices": "قیمت گروه‌ها",
    "colors": "رنگ‌ها",
    "models": "مدل‌ها",
    "sku_prefix": "پیشوند SKU",
    "category": "دسته",
    "tags": "تگ‌ها",
    "stock": "موجودی",
    "barcode": "بارکد",
    "description": "توضیحات",
}


def explained(evidence: dict[str, Evidence], notes: list[str]) -> str:
    """Render the full explanation block: sources plus policy notes."""
    parts = preview_lines(evidence)
    parts.extend(f"ℹ️ {text}" for text in notes)
    if not parts:
        return ""
    return "🧭 از کجا می‌دانم:\n" + "\n".join(f"• {part}" for part in parts) + "\n\n"


__all__ = [
    "AI",
    "CAPTION",
    "FILENAME",
    "LEARNED",
    "OCR",
    "POLICY",
    "PREVIEW_FIELDS",
    "SOURCES",
    "SOURCE_LABELS",
    "UPDATE",
    "USER",
    "VOCAB",
    "Evidence",
    "best_quote",
    "explained",
    "from_dict",
    "merge",
    "note",
    "preview_html",
    "preview_lines",
    "to_dict",
]


def preview_html(evidence: dict[str, Evidence], notes: list[str]) -> str:
    """The provenance block, HTML-escaped and ready for a Telegram message.

    Empty string when there is nothing to explain, so callers can append it
    unconditionally without leaving a blank section in the preview.
    """
    import html as _html

    parts: list[str] = []
    for name in PREVIEW_FIELDS:
        item = evidence.get(name)
        if item is None:
            continue
        if not item.note and item.source in (CAPTION, FILENAME, OCR):
            continue                      # nothing interesting to say
        label = _FIELD_LABELS.get(name, name)
        parts.append(f"• <b>{_html.escape(label)}</b> ← {_html.escape(item.line())}")
    for text in notes:
        parts.append(f"ℹ️ {_html.escape(str(text))}")
    if not parts:
        return ""
    return "\n<b>🧭 از کجا می‌دانم:</b>\n" + "\n".join(parts) + "\n"

