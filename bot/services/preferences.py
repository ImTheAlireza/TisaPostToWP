"""Runtime-persisted sudo preferences (stored in data/preferences.json).

These are small on/off flags that the sudo owner flips from inside the bot
(the «⚙️ تنظیمات» screen), so they survive restarts without touching .env.

Only whitelisted keys in ``DEFAULTS`` are ever read/written, which keeps a
malformed or tampered file from leaking arbitrary state.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# data/ is git-ignored on purpose — runtime-managed state lives here.
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FILE = DATA_DIR / "preferences.json"

_lock = threading.Lock()

DEFAULTS = {
    # Whether admins see the «🗜️ فشرده‌سازی عکس‌ها» button in their menu.
    "show_compress_to_admins": True,
}


def _load() -> dict:
    """Return the stored preferences merged over the defaults."""
    out = dict(DEFAULTS)
    try:
        if not FILE.exists():
            return out
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return out
        out.update({key: value for key, value in data.items() if key in DEFAULTS})
    except (ValueError, OSError):
        logger.exception("Could not read preferences file %s", FILE)
    return out


def get(key: str) -> bool:
    """Current value of a whitelisted flag (default if unset)."""
    return bool(_load().get(key, DEFAULTS.get(key)))


def set_flag(key: str, value: bool) -> None:
    """Persist a whitelisted flag. Unknown keys are ignored."""
    if key not in DEFAULTS:
        logger.warning("Ignoring unknown preference key %r", key)
        return
    with _lock:
        data = _load()
        data[key] = bool(value)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Could not write preferences file %s", FILE)
