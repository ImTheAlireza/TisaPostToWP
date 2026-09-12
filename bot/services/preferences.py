"""Runtime-persisted sudo preferences (stored in data/preferences.json).

Currently holds per-button visibility flags that the sudo owner flips from
inside the bot («⚙️ تنظیمات» screen), so they survive restarts without
touching .env. A missing key means the button is visible (default on).
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


def _load() -> dict:
    """Return stored preferences as ``{"button_visibility": {key: bool}}``."""
    visibility: dict[str, bool] = {}
    try:
        if FILE.exists():
            data = json.loads(FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Migrate the older single compress flag (pre-registry layout).
                legacy = data.get("show_compress_to_admins")
                if isinstance(legacy, bool):
                    visibility.setdefault("compress", legacy)
                stored = data.get("button_visibility")
                if isinstance(stored, dict):
                    for key, value in stored.items():
                        if isinstance(key, str) and isinstance(value, bool):
                            visibility[key] = value
    except (ValueError, OSError):
        logger.exception("Could not read preferences file %s", FILE)
    return {"button_visibility": visibility}


def button_visible(key: str) -> bool:
    """Whether the button ``key`` is currently visible (default True)."""
    return _load()["button_visibility"].get(key, True)


def set_button_visible(key: str, value: bool) -> None:
    """Persist the visibility flag for button ``key``."""
    with _lock:
        data = _load()
        data["button_visibility"][key] = bool(value)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            logger.exception("Could not write preferences file %s", FILE)
