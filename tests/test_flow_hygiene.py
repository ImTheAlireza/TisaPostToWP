"""Flow-level safety: temp workspaces, cancelled album tasks, double publishing.

These were the operational half of the phase-0 review:

* ``_cleanup`` deleted only ``<root>/compressed`` and left every original
  download on disk — a few abandoned flows filled /tmp on the shared host;
* an album that flushed after the user pressed «❌ لغو» raised ``KeyError`` and
  reported it to the user as «خطا در پردازش عکس‌ها»;
* nothing marked a session as busy while a product was being published, so a
  second tap created a second draft with a second SKU.

Run with ``python3 -m unittest discover -s tests`` — the whole module skips
itself when python-telegram-bot is not installed, and needs a writable temp dir.
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1")

try:
    from bot.modules import product_flow as PF

    HAS_FLOW = True
except Exception:  # pragma: no cover - python-telegram-bot missing
    HAS_FLOW = False

needs_flow = unittest.skipUnless(HAS_FLOW, "python-telegram-bot is not installed")


def _make_workspace(root: Path) -> tuple[Path, Path]:
    """Recreate what ``_prepare_files`` leaves behind: originals + compressed."""
    root.mkdir(parents=True, exist_ok=True)
    original = root / "01_photo.jpg"
    original.write_bytes(b"x" * 4096)
    compressed = root / "compressed"
    compressed.mkdir(exist_ok=True)
    out = compressed / "01_photo_compressed.jpg"
    out.write_bytes(b"y" * 512)
    return original, out


@needs_flow
class TestWorkspaceCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        PF.TEMP_DIR = self.tmp
        PF.sessions.clear()
        PF.album_buffers.clear()
        PF.album_tasks.clear()

    def test_cleanup_removes_originals_and_compressed_files(self):
        root = self.tmp / "7_1"
        original, compressed = _make_workspace(root)
        PF.sessions[7] = PF.ProductSession(workspace=root, files=[compressed])

        PF._cleanup(7)

        self.assertFalse(compressed.exists())
        self.assertFalse(original.exists(), "original downloads must not survive")
        self.assertFalse(root.exists())

    def test_cleanup_cancels_pending_album_work(self):
        async def scenario():
            root = self.tmp / "7_2"
            _make_workspace(root)
            PF.sessions[7] = PF.ProductSession(workspace=root)

            async def pending():
                await asyncio.sleep(30)

            task = asyncio.create_task(pending())
            PF.album_buffers[(7, "media-group")] = [object()]
            PF.album_tasks[(7, "media-group")] = task
            PF._cleanup(7)
            await asyncio.sleep(0)
            return task

        task = asyncio.run(scenario())
        self.assertTrue(task.cancelled() or task.done())
        self.assertEqual(PF.album_buffers, {})
        self.assertEqual(PF.album_tasks, {})

    def test_album_flush_after_cancel_is_ignored_not_raised(self):
        # old: KeyError, shown to the user as «خطا در پردازش عکس‌ها»
        asyncio.run(PF._prepare_files(4242, [], SimpleNamespace(bot=None)))

    def test_sweep_removes_only_stale_workspaces(self):
        stale = self.tmp / "1_stale"
        fresh = self.tmp / "2_fresh"
        for path in (stale, fresh):
            _make_workspace(path)
            os.utime(path, (time.time() - 90 * 60, time.time() - 90 * 60))
        os.utime(fresh, (time.time(), time.time()))

        removed = PF.sweep_temp_dir(max_age_hours=1)

        self.assertEqual(removed, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())


@needs_flow
class TestPublishingIsSingleShot(unittest.IsolatedAsyncioTestCase):
    """A second «✅ تأیید» while the first is still running must do nothing."""

    def setUp(self):
        import tempfile
        from bot.services.product_extractor import ProductData

        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        image = self.tmp / "01_one.jpg"
        image.write_bytes(b"z" * 64)
        self.user_id = 99
        session = PF.ProductSession(
            mode="new",
            workspace=self.tmp,
            files=[image],
            data=ProductData(
                title="قاب سیلیکونی",
                price=698_000,
                sku_prefix="BO",
                models=["iPhone 17 Pro Max", "iPhone 17 Pro"],
                attributes={"رنگ": ["سفید", "مشکی"]},
            ),
        )
        PF.sessions[self.user_id] = session

        def query_stub():
            answered = []

            async def answer(text=None, **kwargs):
                answered.append(text)

            async def edit_message_text(text=None, **kwargs):
                return None

            message = SimpleNamespace(chat_id=self.user_id, reply_text=answer)
            cq = SimpleNamespace(
                data="product:confirm",
                answer=answer,
                edit_message_text=edit_message_text,
                message=message,
            )
            update = SimpleNamespace(
                callback_query=cq,
                effective_user=SimpleNamespace(id=self.user_id, username="t", first_name="t"),
                effective_message=message,
            )
            return update, answered

        self.query_stub = query_stub
        self.calls: list = []

        async def fake_create_draft(data, files):
            self.calls.append(data)
            await asyncio.sleep(0.05)     # the window a double tap used to hit
            return 1234, "https://example.test/edit"

        self._real_create_draft = PF.create_draft
        PF.create_draft = fake_create_draft
        self.addCleanup(lambda: setattr(PF, "create_draft", self._real_create_draft))
        self.addCleanup(PF.sessions.pop, self.user_id, None)

    async def _confirm(self):
        update, answered = self.query_stub()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=lambda *a, **k: asyncio.sleep(0)))
        return await PF.confirm(update, context), answered

    async def test_second_tap_is_rejected_while_publishing(self):
        first = asyncio.create_task(self._confirm())
        await asyncio.sleep(0.01)          # let it get inside the slow section
        second, answered = self.query_stub()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=lambda *a, **k: asyncio.sleep(0)))
        await PF.confirm(second, context)  # must be refused
        await first
        self.assertEqual(len(self.calls), 1, "the product was published twice")
        self.assertTrue(any("در جریان است" in (text or "") for text in answered))


@needs_flow
class TestConfirmGate(unittest.IsolatedAsyncioTestCase):
    """The gate is wired into the flow, not only into the service."""

    async def test_missing_models_block_publishing(self):
        import tempfile
        from bot.services.product_extractor import ProductData

        data = ProductData(title="قاب", price=698_000, sku_prefix="BO", models=[], attributes={})
        session = PF.ProductSession(mode="new", files=[Path(tempfile.mktemp())], data=data)
        PF.sessions[101] = session
        self.addCleanup(PF.sessions.pop, 101, None)

        answered = []

        async def answer(text=None, **kwargs):
            answered.append(text)

        async def edit_message_text(text=None, **kwargs):
            return None

        update = SimpleNamespace(
            callback_query=SimpleNamespace(
                data="product:confirm", answer=answer, edit_message_text=edit_message_text
            ),
            effective_user=SimpleNamespace(id=101, username="t", first_name="t"),
            effective_message=None,
        )
        calls = []

        async def must_not_run(*args, **kwargs):  # pragma: no cover
            calls.append(args)

        real = PF.create_draft
        PF.create_draft = must_not_run
        self.addCleanup(setattr, PF, "create_draft", real)

        state = await PF.confirm(update, SimpleNamespace(bot=None))
        self.assertEqual(state, PF.REVIEW, "a blocked publish returns to the review screen")
        self.assertEqual(calls, [], "publishing must not have started")
        self.assertTrue(any("مدل" in (text or "") for text in answered), answered)


if __name__ == "__main__":
    unittest.main()
