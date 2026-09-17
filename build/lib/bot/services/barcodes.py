"""Barcode/tracking-code helpers shared by ``processor.py`` and the bot's tests.

The Tisa systems export a 24-digit barcode, but Excel happily turns such a long
number into a float and destroys it (``1.93E+23``). Detecting that is cheap;
the important part is what we do about it — **nothing may enter the output CSV
that we know is broken**, because the file is imported straight into the
tracking system.
"""
from __future__ import annotations

import re

from bot.config import settings

_PERSIAN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_SCIENTIFIC_RE = re.compile(r"[eE][+-]?\d+|\.\d*[1-9]$|^\d+\.\d+$")


def digits_only(value: object) -> str:
    text = str(value if value is not None else "").translate(_PERSIAN)
    return re.sub(r"[\s\u00a0\u200c\u200f\u202a\u202b,،.]", "", text)


# IEEE-754 doubles keep ~15-17 significant digits. A 24-digit barcode stored as
# a *number* is therefore always rounded — and openpyxl hands it back as a plain
# int, so the cell type alone is not enough: length is the reliable signal.
_FLOAT_SAFE_DIGITS = 16


def stored_as_number(raw: object) -> bool:
    return isinstance(raw, (int, float)) and not isinstance(raw, bool)


def looks_float_destroyed(raw: object, cleaned: str) -> bool:
    """True when a barcode was stored as a number and lost its digits."""
    if cleaned and _SCIENTIFIC_RE.search(cleaned):
        return True                       # «1.93E+23» survived as text
    return stored_as_number(raw) and len(cleaned or "") > _FLOAT_SAFE_DIGITS


def number_is_suspicious(raw: object, cleaned: str) -> bool:
    """Numeric cell that is short enough to be exact, but still worth a warning."""
    return stored_as_number(raw) and bool(cleaned) and len(cleaned) <= _FLOAT_SAFE_DIGITS


def barcode_is_valid(cleaned: str) -> bool:
    return bool(re.fullmatch(r"\d+", cleaned or "")) and len(cleaned) in settings.barcode_lengths


def order_code_is_valid(cleaned: str) -> bool:
    return bool(re.fullmatch(r"\d{6}", cleaned or ""))


def order_code_is_short(cleaned: str) -> bool:
    return bool(re.fullmatch(r"\d{5}", cleaned or ""))


__all__ = [
    "barcode_is_valid",
    "digits_only",
    "looks_float_destroyed",
    "number_is_suspicious",
    "order_code_is_short",
    "order_code_is_valid",
    "stored_as_number",
]
