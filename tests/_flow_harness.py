"""Shared harness for the tests that drive the product flow without Telegram.

Both ``test_dry_run.py`` and ``test_idempotency.py`` need the same three things: a
``Settings`` object swapped into every module that imported it, a stub
``callback_query`` shaped the way PTB hands one to a handler, and an isolated
publish ledger. Keeping that in one place matters for the same reason it matters in
``bot/``: two harnesses drift, and then a test passes because the second harness is
more forgiving than the first.

Import it as ``_flow_harness`` (the tests dir is on ``sys.path`` for both pytest and
``unittest discover -s tests``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot.config import Settings
from bot.services import jsonstore, products_ledger

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")


def settings_with(**over: object) -> Settings:
    """A configured-looking shop: credentials complete, URLs unroutable."""
    base: dict[str, object] = {
        "bot_token": "123456:TEST",
        "woocommerce_url": "https://shop.example",
        "woocommerce_key": "ck_test",
        "woocommerce_secret": "cs_test",
        "wordpress_url": "https://shop.example",
        "wordpress_username": "admin",
        "wordpress_app_password": "aaaa bbbb",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


class TransportScript:
    """transport ساختگی که همان جواب‌های از پیش نوشته را می‌دهد و درخواست‌ها را می‌شمارد.

    وقتی اسکریپت تمام شود عمداً به آخرین پاسخِ داده‌شده برنمی‌گردد مگر `default` را بدهی:
    «تست انتظار داشت client بیشتر تلاش نکند» خودش نتیجهٔ تست است، نه چیزی که بی‌صدا
    تکرار شود. یک نسخه در `test_dry_run` و یکی در `test_woo_client` یعنی دو درایورِ هم‌خانواده
    که می‌واگرا‌ند؛ پس اینجا است.
    """

    def __init__(self, *steps: Any, default: tuple[int, Any] | None = None) -> None:
        self.steps = list(steps)
        self.default = default
        self.requests: list[Any] = []

    @property
    def sends(self) -> int:
        return len(self.requests)

    @property
    def methods(self) -> list[str]:
        return [f"{request.method} {request.url.path}" for request in self.requests]

    def transport(self):
        import httpx

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if not self.steps:
                if self.default is None:
                    raise AssertionError("اسکریپت تمام شد؛ client بیشتر از انتظار تلاش کرد")
                status, body = self.default
                return httpx.Response(status) if body is None else httpx.Response(status, json=body)
            step = self.steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        return httpx.MockTransport(handle)


def respond(status: int, body: Any = None):
    """یک `httpx.Response` برای `TransportScript`."""
    import httpx

    return httpx.Response(status) if body is None else httpx.Response(status, json=body)


@contextmanager
def patched_settings(value: Settings):
    """Swap ``settings`` into every bot module that imported it.

    ``bot.config.settings`` is a singleton and each module bound the name itself, so
    patching only ``bot.config`` changes nothing where it is read. This is not
    hypothetical: the Ping gate silently did nothing until a test caught it.
    """
    import sys

    import bot.config as config

    saved: list[tuple[object, str, object]] = []
    modules = [config, *(m for name, m in list(sys.modules.items()) if name.startswith("bot.") and m)]
    for module in modules:
        current = getattr(module, "settings", None)
        if isinstance(current, Settings):
            saved.append((module, "settings", current))
            module.settings = value
    try:
        assert saved, "patched_settings: هیچ ماژولی عوض نشد"
        yield value
    finally:
        for module, name, old in reversed(saved):
            setattr(module, name, old)


@contextmanager
def temp_ledger():
    """An empty publish history and an empty SKU cache, for one reason.

    Both files are things the bot *reads before acting* (the duplicate gate, the SKU
    high-water mark). A suite that writes into the repo's real copies can therefore block
    a publish or hand out a taken SKU — which it did. `unittest discover -s tests` has no
    conftest to redirect ``TISA_DATA_DIR``, so the isolation has to live here.
    """
    from bot.services import sku

    tmp = Path(tempfile.mkdtemp(prefix="tisa-test-ledger-"))
    old = (products_ledger.FILE, sku.STATE_FILE)
    products_ledger.FILE = tmp / "recent_products.json"
    sku.STATE_FILE = tmp / "sku_state.json"
    jsonstore.invalidate()
    try:
        yield products_ledger
    finally:
        products_ledger.FILE, sku.STATE_FILE = old
        jsonstore.invalidate()
        shutil.rmtree(tmp, ignore_errors=True)


@contextmanager
def no_sleep() -> list[float]:
    """خوابِ backoff را در `woo_client` صفر می‌کند و لیست تأخیرها را برمی‌گرداند.

    سیاست retry باید در تست دیده شود، نه ثانیه‌هایش: بدون این، تستِ «با حالت خاموش
    واقعاً سوکت باز می‌شود» سه بار پشت سر هم یک‌ثانیه و دوثانیه خوابید و سوئیت از
    ۱٫۸s رفت ۵s. همان دلیلی که `WooClient` یک `transport` می‌گیرد: چیزی که فقط برای
    آزمودن بیرون آمده نباید رفتاری داشته باشد.
    """
    from bot.services import woo_client

    delays: list[float] = []

    async def fake(delay: float) -> None:
        delays.append(delay)

    saved = woo_client._sleep
    woo_client._sleep = fake
    try:
        yield delays
    finally:
        woo_client._sleep = saved


def query_update(data: str, *, user_id: int = 7, chat_id: int = 9, thread_id: int | None = None):
    """An update shaped like PTB's, with every reply recorded on the query."""
    seen: list[tuple[str, object]] = []

    async def answer(text=None, **kwargs):
        seen.append(("answer", text))

    async def edit_message_text(text=None, **kwargs):
        seen.append(("edit", text))
        return SimpleNamespace(message_id=5)

    message = SimpleNamespace(
        chat_id=chat_id, message_thread_id=thread_id, message_id=1,
        reply_text=answer, edit_message_text=edit_message_text, text=None,
    )
    query = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=user_id), message=message,
        answer=answer, edit_message_text=edit_message_text,
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=user_id, username="t", first_name="t"),
        effective_message=message,
    )
    return update, seen


class FakeBot:
    """Records every proactive send (message or document) with its routing kwargs."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.documents: list[dict[str, object]] = []

    async def send_message(self, *args: object, text: str = "", **kwargs: object) -> SimpleNamespace:
        self.messages.append({"text": text, **kwargs})
        return SimpleNamespace(message_id=1)

    async def send_document(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.documents.append(dict(kwargs))
        return SimpleNamespace(message_id=2)

    async def edit_message_text(self, *args: object, **kwargs: object) -> None:
        return None


def context(bot: FakeBot | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        bot=bot or FakeBot(),
        user=SimpleNamespace(id=7),
        chat_data={},
        job_queue=SimpleNamespace(run_once=lambda *a, **k: None),
        error=lambda *a, **k: None,
    )


def write_image(directory: Path, name: str = "01_one.jpg") -> Path:
    path = directory / name
    path.write_bytes(b"z" * 64)
    return path
