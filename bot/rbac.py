"""Role-Based Access Control (RBAC) for the bot.

Two roles:

* **sudo**  — the bot owner, set once in ``.env`` via ``SUDO_IDS``. Full access.
* **admin** — people added by sudo at runtime through the «مدیریت ادمین‌ها»
  button. Their access is limited to the «📦 تبدیل فایل کد رهگیری» feature.

Everyone else is an ordinary ``user`` with no access at all. Sudo and admins
are the only roles allowed to use the bot; everything lives in private chats
(see bot/app.py), so a non-sudo/non-admin who reaches the bot is blocked at
every entry point.

Admins are persisted to ``data/roles.json`` (git-ignored) so they survive
restarts. Sudo is NOT stored there — it always comes from the environment,
so sudo can never be removed by a misclicked button.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from bot.config import settings

logger = logging.getLogger(__name__)

# data/ is git-ignored on purpose — runtime-managed state lives here.
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ROLES_FILE = DATA_DIR / "roles.json"

_lock = threading.Lock()

SUDO = "sudo"
ADMIN = "admin"
USER = "user"


# --- Roles -------------------------------------------------------------------

def is_sudo(user_id: int | None) -> bool:
    """Sudo list is authoritative from .env — it cannot be edited at runtime."""
    return bool(user_id) and user_id in settings.sudo_ids


def sudo_ids() -> frozenset[int]:
    """The sudo (owner) Telegram IDs from .env — never stored in roles.json."""
    return settings.sudo_ids


# --- Admin persistence -------------------------------------------------------

def _load() -> dict:
    """Return the stored ``{"admins": {id: {...}}}`` dict (empty if missing)."""
    try:
        if not ROLES_FILE.exists():
            return {"admins": {}}
        data = json.loads(ROLES_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"admins": {}}
        data.setdefault("admins", {})
        if not isinstance(data["admins"], dict):
            data["admins"] = {}
        return data
    except (ValueError, OSError):
        logger.exception("Could not read roles file %s", ROLES_FILE)
        return {"admins": {}}


def _save(data: dict) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ROLES_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.exception("Could not write roles file %s", ROLES_FILE)


def admins() -> dict:
    """Snapshot of stored admins as ``{str(id): record}``."""
    return dict(_load()["admins"])


def is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    return str(user_id) in _load()["admins"]


def role(user_id: int | None) -> str:
    if is_sudo(user_id):
        return SUDO
    if is_admin(user_id):
        return ADMIN
    return USER


def is_allowed(user_id: int | None) -> bool:
    """Only sudo and admin may use the bot at all."""
    return role(user_id) in (SUDO, ADMIN)


def add_admin(user_id: int, *, name: str = "", added_by: int | None = None) -> None:
    """Persist a new admin. Safe to call repeatedly (idempotent)."""
    with _lock:
        data = _load()
        data["admins"][str(user_id)] = {
            "name": name,
            "added_by": added_by,
            "ts": time.time(),
        }
        _save(data)
    logger.info("Admin added: user %s (%s) by %s", user_id, name, added_by)


def remove_admin(user_id: int) -> bool:
    """Remove an admin. Returns True if something was actually removed."""
    with _lock:
        data = _load()
        removed = data["admins"].pop(str(user_id), None) is not None
        if removed:
            _save(data)
    if removed:
        logger.info("Admin removed: user %s", user_id)
    return removed
