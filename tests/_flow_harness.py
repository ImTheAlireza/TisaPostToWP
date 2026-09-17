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

import json as _json
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


class FakeStore:
    """ووکامرس ساختگی: هم مسیر موفق، هم حالت‌های بدِ سایت.

    برخلاف transport حالت آزمایشی (که «همه‌چیز خوب پیش رفت» را جواب می‌دهد) این یکی
    محصول نیمه‌ساختهٔ قبلی را یادش می‌آید، endpointی را که سایت ندارد 405 می‌دهد، و
    آپلود تصویر را رد می‌کند. یک نسخه‌اش در `_flow_harness` است چون دو فایل تست همین
    درایور را لازم دارند: `test_idempotency` (تلاش دوباره) و `test_stock_and_sale`
    (بدنهٔ درخواست‌ها) — دو درایور هم‌خانواده یعنی دو رفتار متفاوتِ کدِ یکسان.
    """

    def __init__(
        self,
        *,
        products: list[dict] | None = None,
        variations: list[dict] | None = None,
        search_status: int = 200,
        variations_status: int = 200,
        create_status: int = 201,
        batch_status: int = 201,
        variation_create_status: int = 201,
        media_status: int = 201,
    ) -> None:
        self.products = products or []
        self.variations = variations or []
        self.search_status = search_status
        self.variations_status = variations_status
        self.create_status = create_status
        self.batch_status = batch_status
        self.variation_create_status = variation_create_status
        self.media_status = media_status
        self.requests: list[tuple[str, str, dict, str]] = []
        self.media_id = 900_000

    # — کمکی‌های تست —
    def count(self, method: str, needle: str) -> int:
        return sum(1 for m, path, _params, _body in self.requests if m == method and needle in path)

    def count_created_products(self) -> int:
        """چند «POST /products» واقعاً زده شد (بچِ واریژن هم /products دارد؛ شمرده نمی‌شود)."""
        return sum(1 for m, path, _p, _b in self.requests if m == "POST" and path.endswith("/products"))

    def body(self, method: str, needle: str) -> str:
        for m, path, _params, body in self.requests:
            if m == method and needle in path:
                return body
        return ""

    def last_body(self, method: str, needle: str) -> str:
        for m, path, _params, body in reversed(self.requests):
            if m == method and needle in path:
                return body
        return ""

    @property
    def product_searches(self) -> list[dict]:
        return [params for method, path, params, _b in self.requests
                if method == "GET" and path.endswith("/products")]

    def product_create_body(self) -> dict:
        """بدنهٔ «POST …/products» — مسیرِ دقیق، نه زیررشته.

        ``last_body("POST", "/products")`` بچِ واریژن را هم می‌گیرد (مسیرش هم با
        ``/products`` تمام می‌شود و بعد از محصول است): دو چیز متفاوت به‌عنوان یکی خوانده
        می‌شد. مثل ``count_created_products`` که دقیقاً برای همین هست.
        """
        for m, path, _params, body in reversed(self.requests):
            if m == "POST" and path.endswith("/products"):
                return _json.loads(body) if body else {}
        return {}

    def variation_items(self) -> list[dict]:
        """هر چه در بچِ واریژن فرستاده شد (تعداد و مقادیرش همین‌جا خوانده می‌شود)."""
        try:
            return _json.loads(self.last_body("POST", "variations/batch")).get("create") or []
        except ValueError:
            return []

    def handle(self, request) -> Any:
        import httpx

        path = request.url.path
        method = request.method.upper()
        params = dict(request.url.params)
        body = request.content.decode("utf-8", "ignore") if request.content else ""
        self.requests.append((method, path, params, body))
        if method == "GET" and path.endswith("/variations"):
            if self.variations_status != 200:
                return httpx.Response(self.variations_status, json={"code": "rest_invalid_param"})
            return httpx.Response(200, json=self.variations)
        if method == "GET" and path.endswith("/products"):
            if self.search_status != 200:
                return httpx.Response(self.search_status, json={"code": "rest_invalid_param"})
            return httpx.Response(200, json=self.products)
        if method == "POST" and path.endswith("/media"):
            if self.media_status != 201:
                return httpx.Response(self.media_status, json={"message": "آپلود رسانه رد شد"})
            self.media_id += 1
            return httpx.Response(201, json={"id": self.media_id})
        if method == "POST" and path.endswith("/products"):
            if self.create_status != 201:
                return httpx.Response(self.create_status, json={"message": "خطای ساخت محصول"})
            return httpx.Response(201, json={"id": 4321, "sku": "IP151"})
        if method == "POST" and path.endswith("/variations"):
            if self.variation_create_status != 201:
                return httpx.Response(self.variation_create_status, json={"message": "variation failed"})
            return httpx.Response(201, json={"id": 9500})
        if method == "POST" and path.endswith("/variations/batch"):
            if self.batch_status != 201:
                return httpx.Response(self.batch_status, json={"message": "batch failed"})
            try:
                wanted = _json.loads(body).get("create") or []
            except ValueError:
                wanted = []
            return httpx.Response(201, json={"create": [{"id": 9000 + i} for i in range(len(wanted))]})
        if path.endswith("/categories"):
            return httpx.Response(200, json=[])
        if method in ("DELETE", "PUT", "PATCH"):
            return httpx.Response(200, json={"deleted": True})
        return httpx.Response(200, json={})

    @property
    def transport(self):
        import httpx

        return httpx.MockTransport(self.handle)


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
    """An empty publish history, SKU cache and send-queue, for one reason.

    All three are things the bot *reads before acting* (the duplicate gate, the SKU
    high-water mark, the outbox it drains on boot). A suite that writes into the repo's real
    copies can therefore block a publish, hand out a taken SKU, or publish a queued product on
    the next real start — which the first two did. `unittest discover -s tests` has no
    conftest to redirect ``TISA_DATA_DIR``, so the isolation has to live here.
    """
    from bot.services import outbox, sku

    tmp = Path(tempfile.mkdtemp(prefix="tisa-test-ledger-"))
    old = (products_ledger.FILE, sku.STATE_FILE, outbox.DB_PATH, outbox.FILES_DIR)
    products_ledger.FILE = tmp / "recent_products.json"
    sku.STATE_FILE = tmp / "sku_state.json"
    outbox.DB_PATH = tmp / "outbox.sqlite3"
    outbox.FILES_DIR = tmp / "outbox_files"
    jsonstore.invalidate()
    try:
        yield products_ledger
    finally:
        (products_ledger.FILE, sku.STATE_FILE, outbox.DB_PATH, outbox.FILES_DIR) = old
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


def conversation_patterns() -> set[str]:
    """Patterns the bot's *conversations* answer — the only ones a click can reach.

    A callback button whose handler is not inside a ``ConversationHandler`` looks alive and is
    not: the click lands, the flow never sees the next message. Both phases 3 and 5 were caught
    by exactly this, so the check lives here and every phase re-uses it.
    """
    from telegram.ext import ConversationHandler

    from bot.app import build_application

    app = build_application()
    found: set[str] = set()
    for handlers in app.handlers.values():
        for handler in handlers:
            if not isinstance(handler, ConversationHandler):
                continue
            for group in list(handler.states.values()) + [handler.entry_points, handler.fallbacks]:
                for sub in group:
                    # PTB keeps the compiled regex on the handler, not on .callback (that is ours).
                    pattern = getattr(sub, "pattern", None)
                    if pattern:
                        found.add(str(pattern))
    return found


class FakeChat:
    """Anything the flow can reply to: records the text, and answers like Telegram does.

    A reply is itself a FakeChat, because the flows edit their own «🔎 دنبال می‌گردم…» note
    afterwards — a stub that returned None would hide half the bug surface.
    """

    def __init__(self, sink: list, *, kind: str = "text", chat_id: int = 9,
                 thread_id: int | None = None, text: str | None = None) -> None:
        self.sink = sink
        self.kind = kind
        self.chat_id = chat_id
        self.message_thread_id = thread_id
        self.text = text
        self.message_id = 5
        self.reply_markup = None
        self.edits = 0

    async def reply_text(self, text: str | None = None, **kwargs: object) -> FakeChat:
        self.sink.append((self.kind, text, kwargs))
        return FakeChat(self.sink, chat_id=self.chat_id, thread_id=self.message_thread_id,
                        text=str(text or ""))

    async def edit_text(self, text: str | None = None, **kwargs: object) -> FakeChat:
        self.edits += 1
        self.reply_markup = kwargs.get("reply_markup")
        self.sink.append(("edit", text, kwargs))
        return self

    async def edit_message_text(self, text: str | None = None, **kwargs: object) -> FakeChat:
        return await self.edit_text(text, **kwargs)

    async def delete(self) -> None:
        self.sink.append(("delete", None, {}))

    async def answer(self, text: str | None = None, **kwargs: object) -> None:
        self.sink.append(("answer", text, kwargs))

    def keyboard_rows(self) -> list[list]:
        markup = self.reply_markup
        return list(getattr(markup, "inline_keyboard", []) or [])


def message_update(text: str = "", *, user_id: int = 7, chat_id: int = 9,
                   thread_id: int | None = None):
    """An update shaped like a plain message, with every reply recorded."""
    sent: list = []
    message = FakeChat(sent, chat_id=chat_id, thread_id=thread_id, text=text)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id, username="t", first_name="t"),
        effective_message=message,
        callback_query=None,
    )
    return update, sent


def query_update(data: str, *, user_id: int = 7, chat_id: int = 9, thread_id: int | None = None):
    """An update shaped like PTB's, with every reply recorded on the query."""
    seen: list[tuple[str, object]] = []

    async def answer(text=None, **kwargs):
        seen.append(("answer", text))

    message = FakeChat(seen, chat_id=chat_id, thread_id=thread_id)
    query = SimpleNamespace(
        data=data, from_user=SimpleNamespace(id=user_id), message=message,
        answer=answer, edit_message_text=message.edit_message_text,
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
