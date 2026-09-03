"""Small WooCommerce REST API client used by the diagnostics button."""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class WooCommerceResult:
    ok: bool
    status_code: int | None
    message: str
    elapsed_ms: float


async def ping_woocommerce(
    url: str,
    consumer_key: str,
    consumer_secret: str,
    version: str = "wc/v3",
    timeout: float = 10.0,
) -> WooCommerceResult:
    """Authenticate against WooCommerce and request its system status.

    Basic authentication is the documented WooCommerce REST API mechanism. The
    request is deliberately made server-side so credentials never reach Telegram.
    """
    import time

    endpoint = f"{url.rstrip('/')}/wp-json/{version.strip('/')}/system_status"
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(
                endpoint,
                auth=(consumer_key, consumer_secret),
                headers={"User-Agent": "TisaPostToWP/1.0 (+https://tisacase.com)"},
            )
        elapsed = (time.perf_counter() - started) * 1000
        if response.is_success:
            return WooCommerceResult(True, response.status_code, "Connected", elapsed)
        # The response body is useful for distinguishing WooCommerce permissions
        # from a hosting/WAF block. It is sent only to the private log chat and
        # never includes the request URL (which contains no credentials here).
        import logging
        logging.getLogger(__name__).warning(
            "WooCommerce response body: %s", response.text[:800].replace("\\n", " ")
        )
        if response.status_code == 401:
            message = "Authentication failed (check the consumer key and secret)."
        elif response.status_code == 403:
            message = "Forbidden (check key permissions, security plugins, or WAF/Cloudflare rules)."
        elif response.status_code == 404:
            message = "Endpoint not found (check the store URL or API version)."
        else:
            message = f"WooCommerce returned HTTP {response.status_code}."
        return WooCommerceResult(False, response.status_code, message, elapsed)
    except httpx.TimeoutException:
        elapsed = (time.perf_counter() - started) * 1000
        return WooCommerceResult(False, None, "Connection timed out.", elapsed)
    except httpx.HTTPError as exc:
        elapsed = (time.perf_counter() - started) * 1000
        return WooCommerceResult(False, None, f"Connection error: {exc.__class__.__name__}.", elapsed)
    except Exception:
        elapsed = (time.perf_counter() - started) * 1000
        return WooCommerceResult(False, None, "Could not connect to WooCommerce.", elapsed)
