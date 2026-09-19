"""Keep the test suite out of the repository's real ``data/`` directory.

``data/`` holds the shop's durable state — the publish ledger, roles, learned rules.
Until this file existed, tests wrote cards straight into ``recent_products.json``;
once the bot started *reading* that ledger before publishing (plan 4.3: refuse the
same content twice), a test card could block a real publish. Isolation is therefore
part of the feature being correct, not housekeeping.

pytest loads this module before importing any test, which is the only moment where
``TISA_DATA_DIR`` still works: ``bot.config`` resolves it at import time. Modules
that need per-test isolation still patch ``products_ledger.FILE`` themselves — that
also covers ``python3 -m unittest discover -s tests``, which has no conftest.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_DIR = tempfile.mkdtemp(prefix="tisa-test-data-")
atexit.register(shutil.rmtree, _DIR, True)
os.environ["TISA_DATA_DIR"] = _DIR
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")
