"""Safe WordPress Media REST API connectivity test."""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from bot.config import settings


# A tiny valid 1x1 PNG; no real product data is sent during the test.
_TEST_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDAT"
    b"\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass(frozen=True)
class MediaTestResult:
    ok: bool
    message: str
    status_code: int | None = None
    media_id: int | None = None
    elapsed_ms: float = 0.0
    deleted: bool = False


async def test_wordpress_media(timeout: float = 15.0) -> MediaTestResult:
    if not all((settings.wordpress_url, settings.wordpress_username, settings.wordpress_app_password)):
        return MediaTestResult(False, "اطلاعات WordPress Application Password در .env کامل نیست.")

    endpoint = f"{settings.wordpress_url.rstrip('/')}/wp-json/wp/v2/media"
    import time
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.post(
                endpoint,
                content=_TEST_PNG,
                auth=(settings.wordpress_username, settings.wordpress_app_password),
                headers={
                    "Content-Type": "image/png",
                    "Content-Disposition": 'attachment; filename="tisa-api-test.png"',
                    "User-Agent": "TisaPostToWP/1.0",
                },
            )
            elapsed = (time.perf_counter() - started) * 1000
            if response.status_code not in (200, 201):
                if response.status_code == 401:
                    msg = "احراز هویت WordPress ناموفق است."
                elif response.status_code == 403:
                    msg = "WordPress یا ModSecurity اجازه آپلود نمی‌دهد."
                elif response.status_code == 404:
                    msg = "REST API رسانه پیدا نشد."
                else:
                    msg = f"WordPress returned HTTP {response.status_code}."
                return MediaTestResult(False, msg, response.status_code, elapsed_ms=elapsed)

            body = response.json()
            media_id = int(body.get("id", 0)) or None
            if not media_id:
                return MediaTestResult(False, "پاسخ آپلود معتبر نیست و Media ID ندارد.", response.status_code, elapsed_ms=elapsed)

            delete_response = await client.delete(
                f"{endpoint}/{media_id}",
                params={"force": "true"},
                auth=(settings.wordpress_username, settings.wordpress_app_password),
            )
            deleted = delete_response.status_code in (200, 202)
            if not deleted:
                return MediaTestResult(True, "تصویر تستی آپلود شد، اما حذف خودکار آن ناموفق بود.", response.status_code, media_id, elapsed, False)
            return MediaTestResult(True, "آپلود و حذف تصویر تستی موفق بود.", response.status_code, media_id, elapsed, True)
    except httpx.TimeoutException:
        return MediaTestResult(False, "آپلود Media API timeout شد.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        return MediaTestResult(False, f"خطای HTTP: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        return MediaTestResult(False, f"خطای غیرمنتظره: {exc.__class__.__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
