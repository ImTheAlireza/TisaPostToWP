"""A tiny ledger of in-progress flows, so a restart can tell the truth.

The bot's product flow keeps its state in memory on purpose: pickling half-built
product sessions (temp paths, Telegram file ids, AI drafts) across a restart
would resurrect a session whose files no longer exist — fragile in exactly the
way we are trying to remove. The one thing worth persisting is the *knowledge*
that a flow was interrupted, so after the restart the owner hears
«جریان ساخت محصول نصفه‌کاره رها شد» instead of the bot silently forgetting.

Written atomically (see :mod:`bot.services.jsonstore`); a failure here is never
allowed to disturb a product flow.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from bot.services.jsonstore import lock_for, read_json, write_json

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
STATE_FILE = DATA_DIR / "flow_state.json"
STALE_AFTER = 6 * 3600          # a day-old note is not worth a message

_lock = lock_for(STATE_FILE)


def _load() -> dict:
    data = read_json(STATE_FILE, None)
    flows = data.get("flows") if isinstance(data, dict) else None
    return flows if isinstance(flows, dict) else {}


def record(user_id: int | str, *, chat_id: int | None = None, mode: str = "new",
           images: int = 0, step: str = "") -> None:
    key = str(user_id)
    with _lock:
        flows = _load()
        flows[key] = {
            "chat_id": chat_id if chat_id is not None else int(user_id),
            "mode": mode,
            "images": images,
            "step": step,
            "ts": time.time(),
        }
        write_json(STATE_FILE, {"version": 1, "flows": flows})


def clear(user_id: int | str) -> None:
    key = str(user_id)
    with _lock:
        flows = _load()
        if key not in flows:
            return
        flows.pop(key, None)
        write_json(STATE_FILE, {"version": 1, "flows": flows})


def pending(max_age: float = STALE_AFTER) -> dict[str, dict]:
    """Flows that ended without a proper finish (crash, restart, kill)."""
    now = time.time()
    return {
        key: dict(value)
        for key, value in _load().items()
        if isinstance(value, dict) and now - float(value.get("ts", 0)) <= max_age
    }


def take_pending(max_age: float = STALE_AFTER) -> dict[str, dict]:
    """Read :func:`pending` and clear it, so a notice is sent exactly once."""
    found = pending(max_age)
    if found:
        with _lock:
            write_json(STATE_FILE, {"version": 1, "flows": {}})
    return found


__all__ = ["STATE_FILE", "clear", "pending", "record", "take_pending"]
