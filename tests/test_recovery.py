"""Restart-safety: the interrupted-flow ledger and log redaction.

A restart used to silently forget a half-built product and leave its temp files
behind; and the WooCommerce credentials travel in query strings, so one logged
exception could paste a consumer secret into a plaintext log file.

Stdlib only.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1")

from bot.services import jsonstore


class TestFlowStateLedger(unittest.TestCase):
    def setUp(self) -> None:
        from bot.services import flow_state

        self.flow_state = flow_state
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for attr in ("DATA_DIR", "STATE_FILE"):
            self.addCleanup(setattr, flow_state, attr, getattr(flow_state, attr))
        flow_state.DATA_DIR = self.tmp
        flow_state.STATE_FILE = self.tmp / "flow_state.json"
        jsonstore.invalidate()

    def test_record_then_clear(self):
        self.flow_state.record(7, chat_id=7, mode="new", images=4, step="منتظر تصاویر")
        self.assertIn("7", self.flow_state.pending())
        self.assertEqual(self.flow_state.pending()["7"]["images"], 4)
        self.flow_state.clear(7)
        self.assertEqual(self.flow_state.pending(), {})

    def test_take_pending_notifies_once(self):
        self.flow_state.record(7, chat_id=7, mode="update")
        first = self.flow_state.take_pending()
        self.assertIn("7", first)
        self.assertEqual(self.flow_state.take_pending(), {})

    def test_stale_notes_are_dropped(self):
        self.flow_state.record(7, chat_id=7)
        jsonstore.write_json(
            self.flow_state.STATE_FILE,
            {"version": 1, "flows": {"7": {"chat_id": 7, "ts": 0, "mode": "new"}}},
        )
        jsonstore.invalidate()
        self.assertEqual(self.flow_state.pending(), {})

    def test_corrupt_ledger_does_not_raise(self):
        self.flow_state.STATE_FILE.write_text("{not json", encoding="utf-8")
        jsonstore.invalidate()
        self.assertEqual(self.flow_state.pending(), {})


class TestLogRedaction(unittest.TestCase):
    def setUp(self) -> None:
        from bot.utils import logging as bot_logging

        self.bot_logging = bot_logging

    def test_consumer_secrets_are_masked(self):
        raw = "GET https://shop.test/wp-json/wc/v3/products?consumer_key=ck_live_ABC&consumer_secret=cs_live_XYZ"
        red = self.bot_logging.redact(raw)
        self.assertNotIn("ck_live_ABC", red)
        self.assertNotIn("cs_live_XYZ", red)
        self.assertIn("products", red)          # the useful part survives

    def test_bot_tokens_are_masked(self):
        red = self.bot_logging.redact("using bot token 123456:AAAAAAAAAAAAAAAAAAAAAAAA-BBBccc")
        self.assertNotIn("AAAAAAAAAAAAAAAAAAAAAAAA-BBBccc", red)

    def test_bearer_tokens_are_masked(self):
        red = self.bot_logging.redact("Authorization: Bearer sk-supersecretvalue123")
        self.assertNotIn("sk-supersecretvalue123", red)

    def test_rotation_files_are_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp) / "logs"
            original = self.bot_logging.LOG_DIR
            self.addCleanup(setattr, self.bot_logging, "LOG_DIR", original)
            self.bot_logging.LOG_DIR = log_dir
            self.bot_logging.setup_logging("INFO")
            logging.getLogger("tisa.test").info("hello user")
            self.assertTrue((log_dir / "bot.log").exists())
            self.bot_logging.setup_logging("INFO", to_files=False)   # restore handlers


if __name__ == "__main__":
    unittest.main()
