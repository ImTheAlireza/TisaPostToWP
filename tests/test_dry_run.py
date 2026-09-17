"""فاز ۴ (۱): حالت آزمایشی (dry-run) — همان مسیر، بدون شبکه.

ایدهٔ dry-run این است که *همان کد* اجرا شود، نه یک پیاده‌سازی دومِ ساده‌شده: payload
را سازندهٔ واقعی می‌سازد، client واقعی امضا و ارسال می‌کند، و فقط سوکت با یک
transport جعلی عوض می‌شود. یک «پیش‌نمایش‌ساز» موازی داخل یک ریلیز از production
واپا می‌افتد — و آنگاه دقیقاً همان چیزی را که قرار بود تضمین شود، تضمین نمی‌کند.

پس این فایل هم هر شکلِ درخواستی که بات می‌زند جدا می‌آزماید (اگر کسی درخواست تازه‌ای
اضافه کند و fake را به‌روز نکند، تستِ شمارش ردپا می‌ترکد) و هم جریان کامل
«تأیید و ساخت» را با سوکت جعلی تا آخر اجرا می‌کند.

اجرا: ``python3 -m unittest discover -s tests``
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

from bot.config import Settings
from bot.services import jsonstore, products_ledger

try:
    import httpx

    from bot.keyboards import result_card
    from bot.modules import product_flow as PF
    from bot.services.plan import plan_from_dict
    from bot.services.product_extractor import ProductData
    from bot.services.woocommerce_direct import _dry_run_transport, create_draft

    HAS_FLOW = True
except Exception:                                      # pragma: no cover - PTB missing
    HAS_FLOW = False

needs_flow = unittest.skipUnless(HAS_FLOW, "python-telegram-bot is not installed")


class _Recorder:
    """جای _Audit، تا ردپا را همان‌طور که ثبت می‌شود بخوانیم."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, line: str) -> None:
        self.lines.append(line)


def _dry_settings(**over: object) -> Settings:
    base = {
        "bot_token": "123456:TEST",
        "woo_dry_run": True,
        "woocommerce_url": "https://shop.example",
        "woocommerce_key": "ck_test",
        "woocommerce_secret": "cs_test",
        "wordpress_url": "https://shop.example",
        "wordpress_username": "admin",
        "wordpress_app_password": "aaaa bbbb",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


class _UseSettings:
    """`settings` را در *همهٔ* ماژول‌های bot که آن را import کرده‌اند عوض می‌کند.

    `bot.config.settings` یک singleton است و هر ماژول آن را با `from … import` به نام
    خودش بسته، پس patch کردن `bot.config` چیزی را در آن ماژول عوض نمی‌کند. این تله
    واقعی است: گیتِ صفحهٔ Ping در اولین اجرا بی‌صدا از کار افتاد و تستش همین را گرفت.
    """

    def __init__(self, value: Settings) -> None:
        self.value = value
        self.saved: list[tuple[object, str, object]] = []

    def __enter__(self) -> Settings:
        import sys

        modules: list[object] = [sys.modules["bot.config"], PF]
        modules += [m for name, m in sys.modules.items() if name.startswith("bot.") and m is not None]
        seen: set[int] = set()
        for obj in modules:
            current = getattr(obj, "settings", None)
            if isinstance(current, Settings) and id(obj) not in seen:
                seen.add(id(obj))
                self.saved.append((obj, "settings", current))
                obj.settings = self.value
        assert self.saved, "حداقل bot.config باید patch می‌شد"
        return self.value

    def __exit__(self, *exc: object) -> None:
        for obj, name, old in reversed(self.saved):
            setattr(obj, name, old)


@needs_flow
class TestFakeTransport(unittest.IsolatedAsyncioTestCase):
    """هر شکلی از درخواستی که create_draft می‌زند باید جواب ساختگی داشته باشد."""

    async def _client(self) -> tuple[httpx.AsyncClient, _Recorder]:
        audit = _Recorder()
        client = httpx.AsyncClient(transport=_dry_run_transport(audit), base_url="https://shop.example")
        self.addCleanup(client.aclose)
        return client, audit

    async def test_each_call_shape_is_answered(self) -> None:
        client, audit = await self._client()
        product = await client.post("/wp-json/wc/v3/products", json={"name": "گوشی", "sku": "A1"})
        self.assertEqual(201, product.status_code)
        self.assertGreaterEqual(product.json()["id"], 800_001)
        self.assertEqual("A1", product.json()["sku"], "SKU در جواب لازم است تا تشخیص برخورد انجام شود")

        category = await client.get("/wp-json/wc/v3/products/categories", params={"search": "قاب گوشی"})
        self.assertEqual(200, category.status_code)
        self.assertEqual("قاب گوشی", category.json()[0]["name"], "دسته باید پیدا شود وگرنه payload بی‌دسته می‌شود")
        empty_category = await client.get("/wp-json/wc/v3/products/categories", params={"search": ""})
        self.assertEqual([], empty_category.json())

        scan = await client.get("/wp-json/wc/v3/products", params={"search": "A1"})
        self.assertEqual([], scan.json(), "اسکن SKU باید کاتالوگ خالی ببیند تا مسیر fallback روشن بماند")

        plugin = await client.get("/wp-json/wcspb/v1/next-sku")
        self.assertEqual(404, plugin.status_code, "افزونهٔ SKU نباید صدا زده شود؛ وگرنه dry-run به سایت دست می‌زند")

        batch = await client.post(
            "/wp-json/wc/v3/products/1/variations/batch", json={"create": [{"sku": "A-1"}, {"sku": "A-2"}]}
        )
        created = batch.json()["create"]
        self.assertEqual(2, len(created), "تعداد create باید با chunk برابر بماند، وگرنه شمارش واریژن دروغ می‌شود")
        self.assertNotEqual(created[0]["id"], created[1]["id"], "شناسهٔ تکراری باعث می‌شود واریژن‌ها گم شوند")

        single = await client.post("/wp-json/wc/v3/products/1/variations", json={"sku": "A-3"})
        self.assertEqual(201, single.status_code)
        media = await client.post(
            "/wp-json/wp/v2/media", files={"file": ("1.jpg", b"xx", "image/jpeg")}
        )
        self.assertEqual(201, media.status_code)
        trash = await client.delete("/wp-json/wp/v2/media/5")
        self.assertEqual(200, trash.status_code, "رول‌بک هم باید در dry-run بی‌خطا اجرا شود")
        put = await client.put("/wp-json/wc/v3/products/1", json={"status": "trash"})
        self.assertEqual(200, put.status_code)

        self.assertEqual(len(audit.lines), 10, "هر درخواست دقیقاً یک خط ردپا می‌گذارد")
        self.assertTrue(all("[dry-run]" in line for line in audit.lines))

    async def test_body_is_recorded_for_debugging(self) -> None:
        client, audit = await self._client()
        await client.post("/wp-json/wc/v3/products", json={"name": "قاب مایکروواویو"})
        self.assertIn("قاب مایکروواویو", audit.lines[0], "بدنه باید در گزارش باشد تا ارزش دیباگ داشته باشد")

    async def test_binary_body_is_not_dumped(self) -> None:
        client, audit = await self._client()
        blob = b"\xff\xd8\xff\xe0" + bytes(range(32)) * 4
        await client.post("/wp-json/wp/v2/media", files={"file": ("1.jpg", blob, "image/jpeg")})
        self.assertIn("دادهٔ دودویی", audit.lines[0], "بدنهٔ دودویی نباید کاراکتر کنترلی در لاگ بگذارد")
        self.assertNotIn(chr(0), audit.lines[0])

    def test_report_keeps_every_step_but_not_the_double_dump(self) -> None:
        from bot.modules.product_flow import _dry_run_report

        text = _dry_run_report([
            "[config] عنوان: قاب",
            "[payload] {'name': 'x'}",
            "[dry-run] GET /wp-json/wc/v3/products/categories",
            "[dry-run] POST /wp-json/wc/v3/products",
            "[plan] 4 واریژن",
        ])
        self.assertIn("GET /wp-json/wc/v3/products/categories", text)
        self.assertIn("POST /wp-json/wc/v3/products", text, "برخلاف گزارش خطا، اینجا ترتیب درخواست‌ها مهم است")
        self.assertIn("[config] عنوان: قاب", text)
        self.assertNotIn("{'name': 'x'}", text, "[payload] نباید دو بار بیاید")
        self.assertIn("ارسال نشدند", text)
        self.assertEqual("", _dry_run_report([]))

    def test_report_is_truncated_with_an_honest_note(self) -> None:
        from bot.modules.product_flow import _dry_run_report

        text = _dry_run_report([f"[dry-run] GET /x/{i}" for i in range(400)], budget=600)
        self.assertLess(len(text), 700)
        self.assertIn("خط دیگر — کاملش در لاگ", text)


@needs_flow
class TestCreateDraftDryRun(unittest.IsolatedAsyncioTestCase):
    def _data(self) -> ProductData:
        data = ProductData(
            title="قاب گوشی اپل",
            price=100_000,
            sku_prefix="IP15",
            models=["iPhone 15", "S24 Ultra"],
            attributes={"رنگ": ["مشکی", "سفید"]},
            categories=["قاب گوشی"],
        )
        data.variation_count = plan_from_dict(data.to_dict()).count
        return data

    def _image(self, tmp: Path) -> Path:
        from PIL import Image

        path = tmp / "1.jpg"
        Image.new("RGB", (40, 40), (10, 20, 30)).save(path, format="JPEG")
        return path

    async def test_every_step_runs_without_network(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        report: list[str] = []
        with _UseSettings(_dry_settings()):
            product_id, edit_url = await create_draft(
                self._data().to_dict(), [self._image(tmp)], dry_run=True, report=report
            )
        self.assertGreaterEqual(product_id, 800_001)
        self.assertEqual("", edit_url, "لینک ویرایش به محصولی می‌رسد که وجود ندارد؛ باید خالی بماند")
        joined = "\n".join(report)
        self.assertIn("POST /wp-json/wc/v3/products ", joined)
        self.assertIn("POST /wp-json/wp/v2/media", joined, "آپلود تصویر هم باید در همین مسیر آزمایش شود")
        self.assertIn("variations/batch", joined)
        self.assertIn("4 واریژن", joined, "شمارش باید همان plan.preview باشد، نه یک عدد دستی")
        self.assertIn("هیچ داده‌ای در سایت نوشته نشد", joined)
        for secret in ("cs_test", "ck_test", "aaaa"):
            self.assertNotIn(secret, joined, "گزارش نباید ردپای اعتبارنامه بگذارد")

    async def test_no_media_without_wp_credentials(self) -> None:
        # dry-run نباید دروازهٔ اعتبارنامه را دور بزند: بدون WP، آپلود تصویر یعنی خطا
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        with _UseSettings(_dry_settings(wordpress_url="", wordpress_username="", wordpress_app_password="")), \
                self.assertRaises(RuntimeError) as ctx:
            await create_draft(self._data().to_dict(), [self._image(tmp)], dry_run=True, report=[])
        self.assertIn("WordPress", str(ctx.exception))

    async def test_off_does_not_use_the_fake_transport(self) -> None:
        """با حالت خاموش باید واقعاً سوکت باز شود.

        اگر روزی fake بدون شرط dry_run به client وصل شود، همین تست می‌ترکد: اینجا
        درگاه ۱۲۷.۰.۰.۱:9 هیچ سرویسی ندارد، پس باید خطای اتصال بگیریم — نه یک
        پاسخ ساختگی.
        """
        with _UseSettings(_dry_settings(woo_dry_run=False, woocommerce_url="http://127.0.0.1:9")), \
                self.assertRaises(Exception) as ctx:
            await create_draft(self._data().to_dict(), [], dry_run=False)
        cause: BaseException | None = ctx.exception
        for _ in range(6):
            if cause is None or isinstance(cause, OSError):
                break
            cause = cause.__cause__ or cause.__context__
        self.assertIsInstance(cause, OSError, f"باید خطای شبکه باشد، نه {type(ctx.exception).__name__}")


class LedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._file = products_ledger.FILE
        products_ledger.FILE = self.tmp / "recent_products.json"
        jsonstore.invalidate()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        products_ledger.FILE = self._file
        jsonstore.invalidate()


@needs_flow
class TestDryRunCard(LedgerTestCase):
    def _entry(self, **over: object) -> dict[str, object]:
        entry = {
            "key": "1", "user_id": 7, "status": "dry", "product_id": None, "edit_url": "",
            "mode": "new", "title": "قاب گوشی اپل", "variations": 4, "price": 100_000,
            "price_groups": {}, "sku_prefix": "IP15", "images": 1, "categories": ["قاب گوشی"],
            "warnings": ["🧪 حالت آزمایشی روشن است: هیچ چیزی در سایت ساخته نشد."], "error": "",
            "report": "📦 پیش‌نمایش", "ts": 1_700_000_000,
        }
        entry.update(over)
        return entry

    def test_card_says_nothing_was_created(self) -> None:
        card = result_card(self._entry())
        self.assertIn("🧪", card)
        self.assertIn("هیچ چیزی در سایت ساخته نشد", card)
        self.assertNotIn("ویرایش در سایت", card, "دکمهٔ ویرایش به محصولی می‌رود که نیست")
        self.assertNotIn("#None", card, "شناسهٔ ساختگی نباید روی کارت بیاید")

    def test_ledger_marks_it(self) -> None:
        products_ledger.record(user_id=7, status="dry", title="قاب گوشی اپل", variations=4)
        line = products_ledger.summary(products_ledger.recent(1)[0])
        self.assertIn("🧪", line)
        self.assertIn("قاب گوشی اپل", line)
        self.assertNotIn("#", line.splitlines()[0].replace("🧪", ""), "بدون product_id باید بدون # باشد")

    def test_created_card_still_has_no_dry_text(self) -> None:
        card = result_card(self._entry(status="created", product_id=42, edit_url="https://shop/x", warnings=[]))
        self.assertNotIn("🧪", card)
        self.assertIn("42", card)


@needs_flow
class TestFlowDryRun(unittest.IsolatedAsyncioTestCase):
    """«تأیید و ساخت» با TISA_DRY_RUN=1: هیچ محصولی ساخته نمی‌شود، گزارش می‌آید."""

    def setUp(self) -> None:
        for store in (PF.sessions, PF.album_buffers, PF.album_tasks):
            store.clear()
        self._real_extract = PF._extract
        self._real_sudo = PF.rbac.is_sudo
        PF.rbac.is_sudo = lambda user_id: True
        self._file = products_ledger.FILE
        self.tmp = Path(tempfile.mkdtemp())
        products_ledger.FILE = self.tmp / "recent_products.json"
        jsonstore.invalidate()

        data = ProductData(
            title="قاب گوشی اپل", price=100_000, sku_prefix="IP15",
            models=["iPhone 15", "S24 Ultra"], attributes={"رنگ": ["مشکی", "سفید"]},
            categories=["قاب گوشی"],
        )
        data.variation_count = plan_from_dict(data.to_dict()).count
        self.data = data
        self.tmp_session = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_session, True)
        image = self.tmp_session / "01_one.jpg"
        image.write_bytes(b"z" * 64)
        PF.sessions[7] = PF.ProductSession(mode="new", data=data, model_text="قاب گوشی",
                                           files=[image], chat_id=9, workspace=self.tmp_session)

    def tearDown(self) -> None:
        PF._extract = self._real_extract
        PF.rbac.is_sudo = self._real_sudo
        for store in (PF.sessions, PF.album_buffers, PF.album_tasks):
            store.clear()
        products_ledger.FILE = self._file
        jsonstore.invalidate()
        shutil.rmtree(self.tmp, True)

    def _ctx(self, sent: list[tuple[str, dict[str, object]]]) -> SimpleNamespace:
        class Bot:
            async def edit_message_text(self, **kwargs: object) -> None:
                return None

            async def send_message(self, *args: object, text: str = "", **kwargs: object) -> None:
                sent.append((text, dict(kwargs)))

        return SimpleNamespace(
            bot=Bot(), user=SimpleNamespace(id=7), chat_data={},
            job_queue=SimpleNamespace(run_once=lambda *a, **k: None),
            error=lambda *a, **k: None,
        )

    def _update(self) -> SimpleNamespace:
        async def noop(*args: object, **kwargs: object) -> None:
            return None

        cq = SimpleNamespace(
            data="product:confirm", answer=noop, edit_message_text=noop,
            message=SimpleNamespace(chat_id=9, message_id=1, reply_text=noop),
        )
        return SimpleNamespace(
            callback_query=cq,
            effective_user=SimpleNamespace(id=7, username="t", first_name="t"),
            effective_message=cq.message,
        )

    async def test_dry_publish_writes_the_ledger_and_the_log(self) -> None:
        # همان جریان، ولی با create_draft جعلی: ثابت می‌کند status/گزارش/لاگ درست است
        calls: list[dict[str, object]] = []

        async def fake_create_draft(data, files, *, dry_run=False, report=None):
            calls.append({"dry_run": dry_run, "files": files, "report": report})
            if report is not None:
                report.extend(["[dry-run] POST /wp-json/wc/v3/products", "🧪 جمع‌بندی: هیچ داده‌ای نوشته نشد"])
            return 800_001, ""

        real = PF.create_draft
        PF.create_draft = fake_create_draft
        self.addCleanup(setattr, PF, "create_draft", real)
        sent: list[tuple[str, dict[str, object]]] = []
        with _UseSettings(_dry_settings()):
            result = await PF.confirm(self._update(), self._ctx(sent))
        self.assertEqual(PF.ConversationHandler.END, result)
        self.assertEqual([True], [c["dry_run"] for c in calls], "جریان باید پرچم را به سرویس بدهد")
        entry = products_ledger.recent(1)[0]
        self.assertEqual("dry", entry["status"])
        self.assertIsNone(entry["product_id"], "شناسهٔ ساختگی نباید در دفتر ثبت شود")
        self.assertIn("🧪", entry["warnings"][0])
        cards = [text for text, _ in sent]
        self.assertTrue(any("🧪" in text for text in cards), "کارت نتیجه باید بگوید آزمایشی بوده")
        self.assertTrue(all(item[1].get("chat_id") == 9 for item in sent),
                        "کارت و گزارش هر دو به چتِ شروع‌کننده می‌روند، نه به چت خصوصی")
        trace = [item for item in sent if "درخواست‌هایی که ساخته شدند" in item[0]]
        self.assertEqual(1, len(trace), "ردپای dry-run باید برای خود کاربر هم برود، نه فقط لاگ")
        self.assertEqual(9, trace[0][1].get("chat_id"), "پیام باید به همان چتی برود که جریان در آن شروع شده")
        self.assertIn("POST /wp-json/wc/v3/products", trace[0][0])

    async def test_preview_warns_before_approval(self) -> None:
        with _UseSettings(_dry_settings()):
            text = PF._preview(PF.sessions[7])
        self.assertIn("TISA_DRY_RUN", text)
        self.assertIn("روشن است", text)
        self.assertNotIn("TISA_DRY_RUN", PF._preview(PF.sessions[7]), "با حالت خاموش نباید اخطاری بماند")


@needs_flow
class TestConfig(unittest.TestCase):
    def _env(self, **over: str) -> Settings:
        keys = list(over) or ["TISA_DRY_RUN"]
        saved = {k: os.environ.get(k) for k in keys}
        try:
            for key, value in over.items():
                os.environ[key] = value
            return Settings.from_env()
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_env_parsing(self) -> None:
        for raw, want in [("1", True), ("true", True), ("0", False), ("", False)]:
            self.assertEqual(want, self._env(TISA_DRY_RUN=raw).woo_dry_run, f"برای {raw!r}")

    def test_default_is_off(self) -> None:
        self.assertFalse(self._env(TISA_DRY_RUN="").woo_dry_run)

    def test_check_config_announces_it(self) -> None:
        import main

        saved = main.settings
        out = io.StringIO()
        try:
            main.settings = Settings(bot_token="t", woo_dry_run=True)
            with redirect_stdout(out):
                rc = main.check_config()
        finally:
            main.settings = saved
        self.assertIn("🧪 روشن", out.getvalue())
        self.assertIn("نوشته نمی‌شود", out.getvalue())
        self.assertEqual(0, rc)

    def test_check_config_says_off_when_off(self) -> None:
        import main

        saved = main.settings
        out = io.StringIO()
        try:
            main.settings = Settings(bot_token="t")
            with redirect_stdout(out):
                main.check_config()
        finally:
            main.settings = saved
        self.assertNotIn("🧪", out.getvalue())
        self.assertIn("خاموش", out.getvalue())

    def test_env_example_documents_it(self) -> None:
        text = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(encoding="utf-8")
        self.assertIn("TISA_DRY_RUN", text)


@needs_flow
class TestWriteTestsAreBlocked(unittest.IsolatedAsyncioTestCase):
    """🖼️ و 📦 در صفحهٔ Ping واقعاً می‌نویسند؛ با فلگ روشن باید رد شوند.

    اگر این دو اجازه می‌دادند، «هیچ چیزی نوشته نمی‌شود» دروازه‌ای می‌شد که فقط
    مسیر محصول را می‌پوشاند و خودِ ابزار تست، همان چیزی را می‌ساخت که فلگ قول
    نبودنش را می‌دهد.
    """

    def setUp(self) -> None:
        from bot.modules import ping

        self.ping = ping
        self._real_sudo = ping.rbac.is_sudo
        ping.rbac.is_sudo = lambda user_id: True
        self.calls: list[str] = []

        async def fake_media() -> None:                      # pragma: no cover - must not run
            self.calls.append("media")

        async def fake_product() -> None:                    # pragma: no cover - must not run
            self.calls.append("product")

        self._real_media = ping.test_wordpress_media
        self._real_product = ping.test_product_with_image
        ping.test_wordpress_media = fake_media
        ping.test_product_with_image = fake_product
        self.addCleanup(setattr, ping, "test_wordpress_media", self._real_media)
        self.addCleanup(setattr, ping, "test_product_with_image", self._real_product)
        self.addCleanup(setattr, ping.rbac, "is_sudo", self._real_sudo)

    def _update(self) -> tuple[SimpleNamespace, list[str]]:
        seen: list[str] = []

        async def answer(text=None, **kwargs):
            seen.append(f"answer:{text}")

        async def edit(text=None, **kwargs):
            seen.append(f"edit:{text}")

        cq = SimpleNamespace(answer=answer, edit_message_text=edit,
                            message=SimpleNamespace(chat_id=7, message_id=3))
        return SimpleNamespace(callback_query=cq, effective_user=SimpleNamespace(id=7)), seen

    async def _run(self, handler) -> list[str]:
        update, seen = self._update()
        with _UseSettings(_dry_settings()):
            await handler(update, SimpleNamespace())
        return seen

    async def test_media_test_refused_in_dry_run(self) -> None:
        seen = await self._run(self.ping.cb_media_ping)
        self.assertEqual([], self.calls)
        self.assertIn("TISA_DRY_RUN", seen[-1])
        self.assertIn("واقعاً روی سایت می‌نویسد", seen[-1])

    async def test_product_test_refused_in_dry_run(self) -> None:
        seen = await self._run(self.ping.cb_product_ping)
        self.assertEqual([], self.calls)
        self.assertIn("TISA_DRY_RUN", seen[-1])

    async def test_off_allows_the_real_test(self) -> None:
        from bot.modules import ping

        seen: list[str] = []

        async def answer(text=None, **kwargs):
            seen.append(text)

        async def edit(text=None, **kwargs):
            seen.append(text)

        class _Res:
            ok = True
            message = "ok"
            media_id = 1
            deleted = True
            status_code = 200
            elapsed_ms = 1

        async def fake_media():
            self.calls.append("media")
            return _Res()

        ping.test_wordpress_media = fake_media
        cq = SimpleNamespace(answer=answer, edit_message_text=edit, message=SimpleNamespace(chat_id=7))
        update = SimpleNamespace(callback_query=cq, effective_user=SimpleNamespace(id=7))
        with _UseSettings(_dry_settings(woo_dry_run=False)):
            await ping.cb_media_ping(update, SimpleNamespace())
        self.assertEqual(["media"], self.calls, "با حالت خاموش تست واقعی باید اجرا شود")

if __name__ == "__main__":
    unittest.main()
