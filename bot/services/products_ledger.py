"""What the bot published recently — and the card that explains it.

A publish used to end in one line of «✅ ساخته شد» that scrolled away: no id, no
link, no variation count, and no way back to the draft afterwards. This ledger is
the small durable store of those result cards (the last few), read by the card
itself («🧾 گزارش») and by «🧾 آخرین محصولات» in the main menu.

Only facts go in here — values that were really sent to WooCommerce, never what
the bot hoped for. A failed publish is recorded too (``status="failed"`` with the
error), because «سایت که خالی است» is the most common question after a crash, and
the honest answer is in this file.

The store is plain JSON on purpose (see :mod:`bot.services.jsonstore`): it is
small, human-readable, and deleting it costs nothing but history.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from collections.abc import Iterable

from bot.services.jsonstore import lock_for, read_json, write_json

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FILE = DATA_DIR / "recent_products.json"

#: How many cards we keep. A longer history belongs in the shop's own database.
MAX_ENTRIES = 20
#: The preview text stored per card, so «گزارش» works after a restart too.
REPORT_LIMIT = 6000

_lock = lock_for(FILE)


def _load() -> list[dict[str, Any]]:
    data = read_json(FILE, None)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def new_key(user_id: int | str) -> str:
    """Identifier used by ``products:open:<key>`` — time-based, unique per second."""
    return f"{int(time.time())}{abs(hash(str(user_id))) % 997:03d}"


def record(
    *,
    user_id: int | str,
    status: str = "created",
    product_id: int | str | None = None,
    edit_url: str = "",
    mode: str = "new",
    title: str = "",
    variations: int = 0,
    price: int = 0,
    price_groups: dict[str, int] | None = None,
    sku_prefix: str = "",
    images: int = 0,
    categories: Iterable[str] = (),
    warnings: Iterable[str] = (),
    error: str = "",
    report: str = "",
    key: str | None = None,
) -> dict[str, Any]:
    """Append one result card and return it (also used directly as the message).

    ``report`` is the preview text at the moment of publishing: the numbers a
    card shows must be the ones the owner approved, not what the parser thinks
    today — so it is stored, not recomputed.
    """
    entry: dict[str, Any] = {
        "key": key or new_key(user_id),
        "user_id": int(user_id) if str(user_id).lstrip("-").isdigit() else str(user_id),
        "status": status,
        "product_id": product_id,
        "edit_url": edit_url,
        "mode": mode,
        "title": (title or "").strip()[:120],
        "variations": max(0, int(variations)),
        "price": int(price or 0),
        "price_groups": {str(k): int(v) for k, v in (price_groups or {}).items()},
        "sku_prefix": sku_prefix,
        "images": int(images or 0),
        "categories": [str(x) for x in categories][:6],
        "warnings": [str(x) for x in warnings][:8],
        "error": (error or "")[:400],
        "report": (report or "")[:REPORT_LIMIT],
        "ts": time.time(),
    }
    with _lock:
        entries = _load()
        entries.insert(0, entry)
        write_json(FILE, {"version": 1, "entries": entries[:MAX_ENTRIES]})
    return entry


def recent(limit: int = 10) -> list[dict[str, Any]]:
    """The newest cards first (the stored order is already newest-first)."""
    return _load()[: max(1, limit)]


def find(key: str) -> dict[str, Any] | None:
    for entry in _load():
        if str(entry.get("key")) == str(key):
            return entry
    return None


def clear() -> int:
    """Forget the history (sudo panel). Returns how many cards were dropped."""
    with _lock:
        count = len(_load())
        write_json(FILE, {"version": 1, "entries": []})
    return count


def summary(entry: dict[str, Any]) -> str:
    """One line for the list: what happened, to which product, when."""
    moment = time.strftime("%Y/%m/%d %H:%M", time.localtime(float(entry.get("ts") or 0)))
    mark = {"created": "✅", "zip": "📦", "failed": "❌"}.get(str(entry.get("status")), "•")
    title = str(entry.get("title") or "(بدون عنوان)")
    bits = [f"{mark} {title[:38]}"]
    if entry.get("product_id"):
        bits.append(f"#{entry['product_id']}")
    if entry.get("variations"):
        bits.append(f"{entry['variations']} واریژن")
    if entry.get("mode") == "update":
        bits.append("شارژ")
    if entry.get("error"):
        bits.append(str(entry["error"])[:40])
    return " · ".join(bits) + f"\n    🕒 {moment}"


def price_range(entry: dict[str, Any]) -> str:
    """The price(s) a card was built with, as one honest string."""
    groups = {str(k): int(v) for k, v in (entry.get("price_groups") or {}).items() if int(v or 0) > 0}
    if groups:
        return " | ".join(f"{name}: {value:,}" for name, value in groups.items())
    value = int(entry.get("price") or 0)
    return f"{value:,} تومان" if value else "—"


__all__ = [
    "FILE",
    "MAX_ENTRIES",
    "clear",
    "find",
    "new_key",
    "price_range",
    "recent",
    "record",
    "summary",
]
