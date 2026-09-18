"""تست قرارداد با یک فروشگاه واقعی — همان مسیری که `.env` واقعی می‌رود، روی WP+Woo در Docker.

چرا جدا از بقیهٔ سوئیت؟ همهٔ تست‌های نوشتن `MockTransport` دارند (سریع، آفلاین، قطعی) و
ثابت می‌کنند ربات *چه می‌فرستد*. این فایل ثابت می‌کند ووکامرس همان را *چطور می‌خواند*:
محصول draft می‌شود، تعداد واریژن‌ها با `plan` می‌خواند، ویژگی‌ها سرِ جایشان می‌نشینند،
`batch_id` تکراری محصول دوم نمی‌سازد، و فایلِ افزونهٔ ZIP بدون fatal error لود می‌شود.

اجرا (توضیح کامل: docs/CONTRACT-TESTS.md):

    docker compose up -d wordpress && bash deploy/bootstrap-wordpress.sh
    docker compose run --rm contract

بیرون از آن محیط این فایل **خاموش** است: بدون `TISA_CONTRACT=1` هر کلاسی skip می‌خورد،
پس در CI عمومی و روی سرورِ بدون Docker سوئیت اصلی را نمی‌شکند. هیچ داده‌ای از سایت
واقعی به اینجا نمی‌آید و هرچه ساخته می‌شود نشانگر «TISA CONTRACT» دارد: در پایانِ هر
تست پاک می‌شود، و اگر تستی میانه‌اش بخورد، کلاس بعدی اول جارو می‌کند.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

ENABLED = os.getenv("TISA_CONTRACT", "").strip() == "1"
SITE = os.getenv("TISA_TEST_WOO_URL", "").strip().rstrip("/")
KEY = os.getenv("TISA_TEST_WOO_KEY", "").strip()
SECRET = os.getenv("TISA_TEST_WOO_SECRET", "").strip()
MEDIA_URL = os.getenv("TISA_TEST_WP_URL", "").strip() or SITE
MEDIA_USER = os.getenv("TISA_TEST_WP_USER", "").strip()
MEDIA_PASSWORD = os.getenv("TISA_TEST_WP_APP_PASSWORD", "").strip()

MARKER = "TISA CONTRACT"
SKIP_REASON = "تست قرارداد با TISA_CONTRACT=1 و سایتِ تستِ docker-compose فعال می‌شود (docs/CONTRACT-TESTS.md)"

try:
    import httpx  # (لایهٔ شبکهٔ خودِ تست؛ بدون آن هیچ درخواستی زده نمی‌شود)

    HAS_HTTPX = True
except Exception:  # pragma: no cover
    HAS_HTTPX = False

READY = ENABLED and bool(SITE and KEY and SECRET) and HAS_HTTPX
_swept = False


def _settings():
    from bot.config import Settings

    return Settings(
        bot_token=os.environ["BOT_TOKEN"],
        sudo_ids=os.environ["SUDO_IDS"],
        woocommerce_url=SITE,
        woocommerce_key=KEY,
        woocommerce_secret=SECRET,
        wordpress_url=MEDIA_URL if (MEDIA_USER and MEDIA_PASSWORD) else "",
        wordpress_username=MEDIA_USER,
        wordpress_app_password=MEDIA_PASSWORD,
        woo_dry_run=False,
    )


def _draft_data(title: str) -> dict[str, Any]:
    """یک محصولِ متغیرِ دورَ‌محوره با یک محدودیتِ رنگ — همان شکلی که فاز ۲/۴ می‌سازد."""
    from bot.services import plan
    from bot.services.postmodel import ProductData

    data = ProductData(
        title=title,
        sku_prefix="TC",
        price=250_000,
        models=["iPhone 15", "iPhone 15 Pro"],
        attributes={"رنگ": ["مشکی", "سفید", "قرمز"]},
        model_colors={"iPhone 15": ["مشکی", "سفید"]},
        categories=["قاب گوشی"],
    )
    data.variation_count = plan.plan_from_dict(data.to_dict()).count
    return data.to_dict()


async def _request(method: str, path: str, **kwargs: Any) -> Any:
    import httpx

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.request(
            method, f"{SITE}{path}", auth=(KEY, SECRET), **kwargs
        )
    if method != "DELETE":
        response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"_status": response.status_code, "_text": response.text}


def _call(method: str, path: str, **kwargs: Any) -> Any:
    return asyncio.run(_request(method, path, **kwargs))


def _sweep() -> None:
    """پاک‌کردن همهٔ محصولاتی که نشانگر دارند (یادگارِ یک اجرای نیمه‌کاره)."""
    found = _call("GET", "/wp-json/wc/v3/products", params={"per_page": 50, "search": MARKER, "status": "any"})
    for product in found or []:
        _call("DELETE", f"/wp-json/wc/v3/products/{int(product['id'])}", params={"force": True})


class _ContractCase(unittest.TestCase):
    """سربارِ مشترک: تنظیماتِ سایت تست، دفتر و صفِ موقت، و جارو یک‌بار در ابتدا."""

    @classmethod
    def setUpClass(cls) -> None:
        """یک‌بار: سایت بالاست؟ بعد leftovers را جارو کن.

        پیامِ «سایت پایین است» باید خوانا باشد، نه یک traceback از httpx — کسی که
        این را اجرا می‌کند معمولاً داخل ترمینالِ docker نشسته و چیزی را جا انداخته.
        """
        global _swept
        if _swept:
            return
        _swept = True
        try:
            _call("GET", "/wp-json/wc/v3/ping")
        except Exception as exc:  # pragma: no cover - فقط وقتی سایت بالا نباشد
            raise AssertionError(
                f"سایت تست در {SITE!r} جواب نداد ({exc.__class__.__name__}). "
                "اول: docker compose up -d wordpress && bash deploy/bootstrap-wordpress.sh"
            ) from exc
        _sweep()

    def setUp(self) -> None:
        import _flow_harness as h

        self.h = h
        self.ledger = h.temp_ledger()
        self.ledger.__enter__()
        self.addCleanup(self.ledger.__exit__, None, None, None)
        self.product_ids: list[int] = []

    def note(self, product_id: int) -> int:
        self.product_ids.append(product_id)
        return product_id

    def tearDown(self) -> None:
        for product_id in self.product_ids:
            try:
                _call("DELETE", f"/wp-json/wc/v3/products/{product_id}", params={"force": True})
            except Exception:  # pragma: no cover - سایت پایین بیاید، خودِ تست می‌گوید
                pass


@unittest.skipUnless(READY, SKIP_REASON)
class TestContractAgainstRealStore(_ContractCase):
    def test_ping_answers(self) -> None:
        """درِ همان مسیری که ربات می‌زند؛ اگر این نگیرد، بقیه بی‌معنی است."""
        import httpx

        async def go() -> httpx.Response:
            async with httpx.AsyncClient(timeout=30.0) as client:
                return await client.get(f"{SITE}/wp-json/wc/v3/ping", auth=(KEY, SECRET))

        response = asyncio.run(go())
        self.assertEqual(200, response.status_code, f"ping کد {response.status_code} داد: {response.text[:200]}")

    def test_draft_variable_product_matches_the_plan(self) -> None:
        """شمارشِ پیش‌نمایش باید با شمارشِ واقعیِ روی سایت برابر بماند (P0-6)."""
        from bot.services import plan
        from bot.services.woocommerce_direct import create_draft

        data = _draft_data(f"{MARKER} — قاب تست")
        built = plan.plan_from_dict(data)
        self.assertEqual(5, built.count, "انتظار: ۲ مدل × ۳ رنگ، ناقصِ محدودیتِ رنگ = ۵")

        with self.h.patched_settings(_settings()):
            product_id, edit_url = asyncio.run(create_draft(data, [], report=[]))
        self.note(product_id)
        self.assertGreater(product_id, 0)
        self.assertIn("post.php", edit_url, "روی سایت واقعی باید لینک ویرایش داده شود")

        product = _call("GET", f"/wp-json/wc/v3/products/{product_id}")
        variations = _call("GET", f"/wp-json/wc/v3/products/{product_id}/variations",
                           params={"per_page": 100})
        self.assertEqual("draft", product["status"])
        self.assertEqual(built.count, len(variations), "تعداد واریژنِ سایت با پیش‌نمایش نمی‌خواند")
        self.assertEqual(
            sorted(str(a["name"]) for a in product["attributes"]),
            sorted(name for name, _values in built.axes),
            "ویژگی‌هایی که ربات فرستاد روی سایت نیستند",
        )
        # SKU فقط روی والد؛ واریژن‌ها هرگز SKU نمی‌گیرند (تصمیمِ فاز ۴).
        self.assertTrue(str(product["sku"]).startswith("TC"), f"SKU والد: {product['sku']!r}")
        self.assertEqual([], [v.get("sku") for v in variations if v.get("sku")])
        self.assertEqual(250_000.0, float(product["price"]), "قیمت والد سرِ خودش نرفت")

    def test_sale_price_and_stock_reach_the_site(self) -> None:
        """فیلدهایی که فاز ۰ برای «قیمت/موجودیِ صریح» ساخت، روی سایت هم باید بخواند."""
        from bot.services.woocommerce_direct import create_draft

        data = _draft_data(f"{MARKER} — قیمت ویژه")
        data["sale_price"] = 210_000
        data["stock"] = 7
        with self.h.patched_settings(_settings()):
            product_id, _ = asyncio.run(create_draft(data, [], report=[]))
        self.note(product_id)
        product = _call("GET", f"/wp-json/wc/v3/products/{product_id}")
        self.assertEqual(210_000.0, float(product["sale_price"]))
        self.assertEqual(7, int(product["stock_quantity"]))
        self.assertTrue(product["manage_stock"], "موجودی گفته شد ولی manage_stock روشن نشد")

    def test_same_batch_id_does_not_publish_twice(self) -> None:
        """`batch_id` ضدِ تکرار است: صفحهٔ بازمانده از کرش، محصول دوم نمی‌سازد."""
        from bot.services.woocommerce_direct import create_draft

        data = _draft_data(f"{MARKER} — ضدِ تکرار")
        with self.h.patched_settings(_settings()):
            first, _ = asyncio.run(create_draft(data, [], report=[], batch_id="contract-batch-1"))
            second, _ = asyncio.run(create_draft(data, [], report=[], batch_id="contract-batch-1"))
        self.note(first)
        self.assertEqual(first, second, "دوبارِ همان batch محصول تازه ساخت")
        found = _call("GET", "/wp-json/wc/v3/products",
                      params={"search": "ضدِ تکرار", "status": "any", "per_page": 50})
        self.assertEqual(1, len(found), "دو محصول با یک batch_id روی سایت است")

    def test_media_upload_roundtrip(self) -> None:
        """تصویر باید روی Media API بنشیند و به محصول بچسبد (فاز ۳/۴)."""
        if not (MEDIA_USER and MEDIA_PASSWORD):
            self.skipTest("TISA_TEST_WP_USER / TISA_TEST_WP_APP_PASSWORD تنظیم نشده")
        try:
            from PIL import Image
        except Exception:  # pragma: no cover
            self.skipTest("Pillow نصب نیست")
        from bot.services.woocommerce_direct import create_draft

        tmp = Path(tempfile.mkdtemp(prefix="tisa-contract-img-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        image = tmp / "01_black.jpg"
        Image.new("RGB", (40, 40), (10, 20, 30)).save(image, format="JPEG")

        data = _draft_data(f"{MARKER} — با تصویر")
        with self.h.patched_settings(_settings()):
            product_id, _ = asyncio.run(create_draft(data, [image], report=[]))
        self.note(product_id)
        product = _call("GET", f"/wp-json/wc/v3/products/{product_id}")
        self.assertTrue(product["images"], "تصویر آپلود شد ولی به محصول نچسبید")


@unittest.skipUnless(READY, SKIP_REASON)
class TestPluginIsLoaded(_ContractCase):
    def test_plugin_file_does_not_execute_or_leak_when_fetched(self) -> None:
        """دسترسیِ مستقیم به فایل PHP نباید کد را اجرا یا منبع را لو بدهد.

        نگهبان `ABSPATH` در رأس فایل دقیقاً همین است. اگر روزی کسی‌اش بردارد، این تست
        روی همین سایتِ محلی می‌گیردش — پیش از اینکه روی سایتِ مالک معلوم شود.
        """
        import httpx

        url = f"{SITE}/wp-content/plugins/tisa-product-importer/tisa-product-importer.php"

        async def go() -> tuple[int, str]:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
            return response.status_code, response.text

        status, body = asyncio.run(go())
        self.assertIn(status, (200, 400, 403, 404), f"کد غیرمنتظره {status}")
        self.assertNotIn("<?php", body, "منبعِ افزونه به‌صورت متن بیرون داد")
        self.assertNotIn("Fatal error", body, "افزونه موقع لود خطای PHP می‌دهد")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
