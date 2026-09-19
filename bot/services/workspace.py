"""Temp workspaces for the flows that download files from Telegram.

A download that is being waited on between two messages cannot live in the
process's memory: the bot may restart, the user may answer an hour later, and a
file left in ``/tmp`` by a killed process is never cleaned by anything. So every
flow that holds a file gets one directory per session, under one shared rule —
delete what is older than ``TEMP_TTL_HOURS`` — and each flow schedules the sweep
for its own root.

This module owns the *mechanics*. Which directory belongs to which flow stays
with the flow (``bot/modules/*.py`` patch their own module-level ``TEMP_DIR`` in
tests, and that must keep working).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)


def new_dir(root: Path, owner: object, prefix: str = "") -> Path:
    """A private directory for one session of one user (created on demand)."""
    root.mkdir(parents=True, exist_ok=True)
    tag = f"{prefix}{owner}_{int(time.time() * 1000)}_{os.getpid()}"
    path = root / tag
    path.mkdir(parents=True, exist_ok=True)
    return path


def iter_workspaces(root: Path) -> Iterator[Path]:
    """The session directories of ``root``, oldest first (missing root is fine)."""
    if not root.exists():
        return iter(())
    return iter(sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime))


def sweep(root: Path, max_age_hours: float, *, label: str = "workspace") -> int:
    """Delete session directories older than ``max_age_hours``; return how many."""
    removed = 0
    cutoff = time.time() - max(1.0, max_age_hours) * 3600
    for path in list(iter_workspaces(root)):
        if path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("swept %d stale %s(s) older than %g h", removed, label, max_age_hours)
    return removed


def close_owner(root: Path, owner: object, *, prefix: str = "") -> bool:
    """Drop this owner's leftover directories; True when something was on disk.

    Used when a flow ends or another flow takes over, so a half-finished download
    cannot be picked up by the next session of the same user.
    """
    if not root.exists():
        return False
    removed = False
    needle = f"{prefix}{owner}_"
    for path in root.iterdir():
        if path.is_dir() and path.name.startswith(needle):
            shutil.rmtree(path, ignore_errors=True)
            removed = True
    return removed


__all__ = ["close_owner", "iter_workspaces", "new_dir", "sweep"]
