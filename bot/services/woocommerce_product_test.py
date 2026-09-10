"""End-to-end WooCommerce product + WordPress media connectivity test."""
from __future__ import annotations

from dataclasses import dataclass
import time

import httpx

from bot.config import settings
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
    media_url = f"{settings.wordpress_url.rstrip('/')}/wp-json/wp/v2/media"
    products_url = f"{settings.woocommerce_url.rstrip('/')}/wp-json/{settings.woocommerce_version.strip('/')}/products"
    media_id = None
    product_id = None
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            media_response = await client.post(
                media_url,
                content=_TEST_PNG,
                auth=(settings.wordpress_username, settings.wordpress_app_password),
                headers={
                    "Content-Type": "image/png",
                    "Content-Disposition": 'attachment; filename="tisa-product-test.png"',
                    "User-Agent": "TisaPostToWP/1.0",
                },
            )
            if media_response.status_code not in (200, 201):
                return ProductTestResult(False, f"آپلود تصویر تستی ناموفق بود: HTTP {media_response.status_code}.", media_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)
            media_id = int(media_response.json().get("id", 0)) or None
            if not media_id:
                return ProductTestResult(False, "پاسخ Media API فاقد شناسه تصویر است.", media_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)

            params = {"consumer_key": settings.woocommerce_key, "consumer_secret": settings.woocommerce_secret}
            product_response = await client.post(
                products_url,
                params=params,
                json={
                    "name": "Tisa API Test - DELETE ME",
                    "type": "simple",
                    "status": "draft",
                    "regular_price": "1",
                    "description": "Temporary connectivity test; should be deleted automatically.",
                    "images": [{"id": media_id}],
                },
                headers={"User-Agent": "TisaPostToWP/1.0"},
            )
            if product_response.status_code not in (200, 201):
                await client.delete(f"{media_url}/{media_id}", params={"force": "true"}, auth=(settings.wordpress_username, settings.wordpress_app_password))
                return ProductTestResult(False, f"ساخت محصول تستی ناموفق بود: HTTP {product_response.status_code}.", media_response.status_code, product_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)
            product_id = int(product_response.json().get("id", 0)) or None
            if not product_id:
                return ProductTestResult(False, "پاسخ Products API فاقد شناسه محصول است.", media_response.status_code, product_response.status_code, elapsed_ms=(time.perf_counter() - started) * 1000)

            deleted_product = await client.delete(f"{products_url}/{product_id}", params={**params, "force": "true"}, headers={"User-Agent": "TisaPostToWP/1.0"})
            deleted_media = await client.delete(f"{media_url}/{media_id}", params={"force": "true"}, auth=(settings.wordpress_username, settings.wordpress_app_password))
            cleaned = deleted_product.status_code in (200, 202) and deleted_media.status_code in (200, 202)
            message = "ساخت محصول با تصویر، اتصال آن و پاک‌سازی تست موفق بود." if cleaned else "محصول تستی ساخته شد اما پاک‌سازی کامل نبود؛ شناسه‌ها را بررسی کن."
            return ProductTestResult(True, message, media_response.status_code, product_response.status_code, product_id, (time.perf_counter() - started) * 1000, cleaned)
    except httpx.TimeoutException:
        return ProductTestResult(False, "تست ساخت محصول timeout شد.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        return ProductTestResult(False, f"خطای HTTP: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        return ProductTestResult(False, f"خطای غیرمنتظره: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
