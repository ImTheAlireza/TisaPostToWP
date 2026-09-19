"""End-to-end WooCommerce product + WordPress media connectivity test."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from bot.config import settings
from bot.services.woo_client import WooClient, media_base, products_base
from bot.services.wordpress_media import _TEST_PNG


@dataclass(frozen=True)
class ProductTestResult:
    ok: bool
    message: str
    media_status: int | None = None
    product_status: int | None = None
    product_id: int | None = None
    elapsed_ms: float = 0.0
    cleaned: bool = False


async def test_product_with_image(timeout: float = 20.0) -> ProductTestResult:
    required = (
        settings.woocommerce_url,
        settings.woocommerce_key,
        settings.woocommerce_secret,
        settings.wordpress_url,
        settings.wordpress_username,
        settings.wordpress_app_password,
    )
    if not all(required):
        return ProductTestResult(False, "اطلاعات WooCommerce یا WordPress در .env کامل نیست.")

    started = time.perf_counter()
    media_url = media_base()
    products_url = products_base()
    media_id = None
    product_id = None
    try:
        # Auth, User-Agent and the redirect policy come from the shared client; attempts=1
        # because this tool reports what the shop answered *now*, in a chat.
        async with WooClient(timeout=timeout, attempts=1) as client:
            media_response = await client.post(
                media_url,
                content=_TEST_PNG,
                basic=True,
                headers={
                    "Content-Type": "image/png",
                    "Content-Disposition": 'attachment; filename="tisa-product-test.png"',
                },
            )
            if media_response.status_code not in (200, 201):
                return ProductTestResult(False, f"آپلود تصویر تستی ناموفق بود: HTTP {media_response.status_code}.", media_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)
            media_id = int(media_response.json().get("id", 0)) or None
            if not media_id:
                return ProductTestResult(False, "پاسخ Media API فاقد شناسه تصویر است.", media_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)

            product_response = await client.post(
                products_url,
                json={
                    "name": "Tisa API Test - DELETE ME",
                    "type": "simple",
                    "status": "draft",
                    "regular_price": "1",
                    "description": "Temporary connectivity test; should be deleted automatically.",
                    "images": [{"id": media_id}],
                },
            )
            if product_response.status_code not in (200, 201):
                await client.delete(f"{media_url}/{media_id}", params={"force": "true"}, basic=True)
                return ProductTestResult(False, f"ساخت محصول تستی ناموفق بود: HTTP {product_response.status_code}.", media_response.status_code, product_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)
            product_id = int(product_response.json().get("id", 0)) or None
            if not product_id:
                return ProductTestResult(False, "پاسخ Products API فاقد شناسه محصول است.", media_response.status_code, product_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)

            deleted_product = await client.delete(f"{products_url}/{product_id}", params={"force": "true"})
            deleted_media = await client.delete(f"{media_url}/{media_id}", params={"force": "true"}, basic=True)
            cleaned = deleted_product.status_code in (200, 202) and deleted_media.status_code in (200, 202)
            message = "ساخت محصول با تصویر، اتصال آن و پاک‌سازی تست موفق بود." if cleaned else "محصول تستی ساخته شد اما پاک‌سازی کامل نبود؛ شناسه‌ها را بررسی کن."
            return ProductTestResult(True, message, media_response.status_code, product_response.status_code, product_id, (time.perf_counter() - started) * 1000, cleaned)
    except httpx.TimeoutException:
        return ProductTestResult(False, "تست ساخت محصول timeout شد.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        return ProductTestResult(False, f"خطای HTTP: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        return ProductTestResult(False, f"خطای غیرمنتظره: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
