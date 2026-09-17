"""Atomic, cached JSON storage for the small runtime state files.

Three modules (``rbac``, ``preferences``, ``learning``) each re-implemented the
same four lines: read the file, parse it, `write_text` the new content. Two
problems:

* the write was not atomic — a restart in the middle of saving (and this bot has
  a «🔄 ری‌استارت» button that does exactly that) left a truncated file, and the
  reader's ``except ValueError`` then returned an *empty* store: every admin and
  every learned rule disappeared with no error anywhere;
* every permission check re-read the file from disk, synchronously, inside an
  event-loop callback.

:func:`read_json` returns a parsed file (or a default) and :func:`write_json`
saves it via a temp file + ``os.replace`` while keeping the previous copy as
``<name>.bak``. The cache is keyed on ``(path, mtime)`` so an external edit or a
test that points a module at a temp directory invalidates it automatically.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

# path → (mtime, payload). Only "successful reads of a file we also wrote" are
# cached, so a corrupt or missing file is always re-checked.
_cache: dict[str, tuple[float, Any]] = {}


def lock_for(path: Path | str) -> threading.Lock:
    key = str(path)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def read_json(path: Path | str, default: Any = None) -> Any:
    """Parse ``path``; return ``default`` when it is missing or unreadable."""
    key = str(path)
    file = Path(path)
    try:
        mtime = file.stat().st_mtime
    except OSError:
        _cache.pop(key, None)
        return default if default is not None else None
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default if default is not None else None
    except (ValueError, OSError) as exc:
        logger.error("could not read %s (%s); trying %s.bak", file, exc, file.name)
        backup = file.with_suffix(file.suffix + ".bak")
        try:
            data = json.loads(backup.read_text(encoding="utf-8"))
            logger.warning("recovered %s from its backup copy", file)
        except (OSError, ValueError):
            return default if default is not None else None
    _cache[key] = (mtime, data)
    return data


def write_json(path: Path | str, data: Any) -> bool:
    """Save atomically (temp file + ``os.replace``) and keep a ``.bak`` copy.

    Never raises: a state file that cannot be written must not take a product
    flow (or an admin action) down with it — the failure is logged instead.
    """
    file = Path(path)
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        temp = file.with_suffix(file.suffix + f".tmp.{os.getpid()}")
        temp.write_text(payload, encoding="utf-8")
        try:
            temp.chmod(0o600)          # roles/preferences are security-relevant
        except OSError:
            pass
        if file.exists():
            try:
                file.replace(file.with_suffix(file.suffix + ".bak"))
            except OSError:  # pragma: no cover — exotic filesystems
                pass
        os.replace(temp, file)
        try:
            _cache[str(file)] = (file.stat().st_mtime, data)
        except OSError:
            _cache.pop(str(file), None)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.exception("could not write %s: %s", file, exc)
        return False


def invalidate(path: Path | str | None = None) -> None:
    """Drop the cache for one path (or all of them — used by tests)."""
    if path is None:
        _cache.clear()
    else:
        _cache.pop(str(path), None)


__all__ = ["invalidate", "lock_for", "read_json", "write_json"]
