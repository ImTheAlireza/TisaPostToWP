"""Direct WooCommerce draft creation through Woo REST + WordPress Media API."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from bot.config import settings

_USER_AGENT = "TisaPostToWP/1.0 (+https://tisacase.com)"
_MAX_SKU_RETRIES = 10


def _is_sku_collision(message: str) -> bool:
    """True when a WooCommerce 400 means "this SKU is already taken".

    Matches both the standard "Invalid or duplicated SKU." and WooCommerce's
    "already present in the lookup table" error, which fires when a deleted
    (trashed) product left a ghost SKU row in ``wc_product_meta_lookup``.
    """
    lowered = (message or "").casefold()
    if "lookup table" in lowered:
        return True
    if "sku" not in lowered:
        return False
    return any(token in lowered for token in ("duplicate", "duplicated", "already", "present", "exists"))


class WooCommerceAPIError(RuntimeError):
    """A WooCommerce REST request failed; carries the parsed error message.

    The message comes from the WooCommerce JSON error body (e.g. "Invalid or
    duplicated SKU."), which is far more actionable than httpx's default
    "Client error '400 Bad Request' for url '...'" string. The URL is
    deliberately NOT stored here: it can contain the consumer secret.
    """

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _auth_params() -> dict[str, str]:
    return {"consumer_key": settings.woocommerce_key, "consumer_secret": settings.woocommerce_secret}


def _error_message(response: httpx.Response) -> str:
    """Pull the human-readable reason out of a WooCommerce/WordPress error body."""
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        for key in ("message", "code", "error"):
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        data = body.get("data")
        if isinstance(data, dict) and data.get("status"):
            return f"HTTP {data['status']}"
    text = (response.text or "").strip()
    return text if text and len(text) <= 400 else f"HTTP {response.status_code}"


def _check(response: httpx.Response) -> httpx.Response:
    """Raise a readable WooCommerceAPIError instead of httpx's URL-leaking one."""
    if response.is_success:
        return response
    raise WooCommerceAPIError(response.status_code, _error_message(response))


def product_description(data: dict[str, Any]) -> str:
    """Return the controlled WooCommerce description for this product."""
    prefix = str(data.get("sku_prefix", "")).strip().upper()
    title = str(data.get("title", "")).casefold()
    if prefix in {"CH", "SB"}:
        return (
            '<p>برای مشاهده محصولات چاپی بیشتر به سایت '
            '<strong><a href="https://TISACHAP.COM">TISACHAP.COM</a></strong> '
            'مراجعه کنید.</p>'
            '<p>آماده سازی و تولید محصولات چاپی 7 تا 18 روزکاری زمان بر خواهد بود؛ '
            'از صبوری شما متشکریم</p>'
        )
    if "قاب" in title:
        return (
            '<p><strong>⚠️ توجه: تصاویر صرفاً برای نمایش رنگ و طرح محصول هستند. '
            'ظاهر نهایی قاب (گرد یا تخت بودن لبه‌ها، میزان برجستگی محافظ دوربین، '
            'محل دکمه‌ها و...) متناسب با مدل گوشی انتخابی شما تولید و ارسال می‌شود</strong></p>'
        )
    return ""


def _price_for_model(model: str, common: int, prices: dict[str, int]) -> int:
    if prices.get("iphone") and re.search(r"\biphone\b", model, re.I):
        return prices["iphone"]
    if prices.get("android"):
        return prices["android"]
    return common


def _clean_options(values: list[Any]) -> list[str]:
    """Deduplicate options, drop empties, and normalize whitespace."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _attributes(data: dict[str, Any]) -> list[dict[str, Any]]:
    attrs: list[dict[str, Any]] = []
    used_names: set[str] = set()
    models = _clean_options(data.get("models") or [])
    if len(models) >= 2:
        attrs.append({"name": "مدل", "visible": True, "variation": True, "options": models})
        used_names.add("مدل".casefold())
    for name, values in (data.get("attributes") or {}).items():
        if not isinstance(values, list):
            continue
        cleaned = _clean_options(values)
        attribute_name = str(name).strip()
        # WooCommerce rejects two attributes with the same name (HTTP 400), so
        # drop duplicates and never let the AI re-add «مدل» as a plain attribute.
        if len(cleaned) < 2 or not attribute_name or attribute_name.casefold() in used_names:
            continue
        attrs.append({"name": attribute_name, "visible": True, "variation": True, "options": cleaned})
        used_names.add(attribute_name.casefold())
    return attrs


def _combinations(attrs: list[dict[str, Any]]) -> list[dict[str, str]]:
    combos = [{}]
    for attr in attrs:
        next_combos = []
        for combo in combos:
            for option in attr["options"]:
                item = dict(combo)
                item[attr["name"]] = option
                next_combos.append(item)
        combos = next_combos
    return combos


async def _upload_media(client: httpx.AsyncClient, path: Path) -> int:
    endpoint = f"{settings.wordpress_url.rstrip('/')}/wp-json/wp/v2/media"
    response = await client.post(
        endpoint,
        content=path.read_bytes(),
        auth=(settings.wordpress_username, settings.wordpress_app_password),
        headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="{path.name}"',
            "User-Agent": _USER_AGENT,
        },
    )
    _check(response)
    return int(response.json()["id"])


async def _sku_from_prefix_plugin(client: httpx.AsyncClient, prefix: str) -> str | None:
    """Ask the SKU-prefix plugin for the next SKU; ``None`` means "not available".

    The plugin is optional — if its route is missing (404) or the Application
    Password can't use it (401/403), we fall back to scanning the catalog
    instead of aborting the whole product-creation flow.
    """
    if not prefix or not all((settings.wordpress_url, settings.wordpress_username, settings.wordpress_app_password)):
        return None
    endpoint = f"{settings.wordpress_url.rstrip('/')}/wp-json/wcspb/v1/next-sku"
    # This is a WordPress REST route, not a wc/v3 route. Use Application
    # Password authentication so current_user_can('edit_products') works;
    # do not put WooCommerce consumer secrets in the URL or error logs.
    response = await client.get(
        endpoint,
        params={"prefix": prefix},
        auth=(settings.wordpress_username, settings.wordpress_app_password),
        headers={"User-Agent": _USER_AGENT},
    )
    if response.status_code in (401, 403, 404):
        return None
    _check(response)
    value = response.json().get("sku")
    return str(value).strip() if value else None


def _sku_number(value: str, prefix: str) -> int | None:
    """Return the numeric suffix of a SKU like ``BO147`` / ``BO-147`` / ``BO 147``."""
    match = re.fullmatch(re.escape(prefix) + r"[\s._-]*(\d+)\s*", value, re.I)
    return int(match.group(1)) if match else None


async def _sku_exists(client: httpx.AsyncClient, base: str, sku: str) -> bool:
    """True if a product with this exact SKU already exists in the store.

    Also checks the Trash: a trashed product keeps its SKU in WooCommerce's
    ``wc_product_meta_lookup`` table and would reject a new product with the
    same SKU even though the normal product list hides it.
    """
    for status in (None, "trash"):
        params = {**_auth_params(), "sku": sku, "per_page": 1}
        if status:
            params["status"] = status
        response = await client.get(
            base,
            params=params,
            headers={"User-Agent": _USER_AGENT},
        )
        if response.status_code == 400:
            # The store does not support this filter scope; assume it is free.
            return False
        _check(response)
        if response.json():
            return True
    return False


async def _scan_max_sku(client: httpx.AsyncClient, base: str, prefix: str) -> int:
    """Find the highest numeric suffix for ``prefix`` among existing SKUs.

    Tries WooCommerce's `search` first (cheap, but many stores only search
    titles), then walks the catalog newest-first — SKU numbers grow over time,
    so the newest products hold the highest suffixes. The exact-SKU collision
    check in ``_next_sku`` covers anything a truncated scan misses.
    """

    def scan(items: list[dict[str, Any]]) -> int:
        top = 0
        for item in items:
            number = _sku_number(str(item.get("sku", "")), prefix)
            if number:
                top = max(top, number)
        return top

    maximum = 0
    for page in range(1, 51):
        response = await client.get(
            base,
            params={**_auth_params(), "search": prefix, "per_page": 100, "page": page},
            headers={"User-Agent": _USER_AGENT},
        )
        if response.status_code == 400:
            break
        _check(response)
        items = response.json()
        maximum = max(maximum, scan(items))
        if len(items) < 100:
            break

    if maximum == 0:
        for page in range(1, 51):
            response = await client.get(
                base,
                params={**_auth_params(), "per_page": 100, "page": page, "orderby": "id", "order": "desc"},
                headers={"User-Agent": _USER_AGENT},
            )
            if response.status_code == 400 or not response.json():
                break
            _check(response)
            items = response.json()
            maximum = max(maximum, scan(items))
            if len(items) < 100:
                break
    return maximum


async def _next_sku(client: httpx.AsyncClient, base: str, prefix: str) -> str:
    """Resolve a free SKU for ``prefix`` (e.g. BO -> BO148).

    The candidate is verified against the store with the exact `sku` filter and
    bumped until it is truly free, so a stale plugin counter or a catalog too
    large to scan completely can never produce a duplicate-SKU 400.
    """
    prefix = (prefix or "").strip().upper()
    if not prefix:
        return ""

    number = 0
    plugin_sku = await _sku_from_prefix_plugin(client, prefix)
    if plugin_sku:
        number = _sku_number(plugin_sku, prefix) or 0
    if not number:
        number = await _scan_max_sku(client, base, prefix)

    for attempt in range(200):
        candidate = f"{prefix}{number + 1 + attempt}"
        if not await _sku_exists(client, base, candidate):
            return candidate
    raise WooCommerceAPIError(500, f"یافتن SKU آزاد برای پیشوند «{prefix}» ممکن نشد.")


async def _create_with_sku_retry(
    client: httpx.AsyncClient, base: str, payload: dict[str, Any], prefix: str, sku: str
) -> httpx.Response:
    """POST the product, bumping the SKU if WooCommerce reports a collision.

    A "free" SKU can still be rejected at insert time: a deleted product may
    have left a ghost row in WooCommerce's ``wc_product_meta_lookup`` table
    that is invisible to both the product list and the Trash. Retrying with
    the next number is the only reliable way past such a ghost entry.
    """
    number = _sku_number(sku, prefix) if (sku and prefix) else None
    for _ in range(_MAX_SKU_RETRIES):
        response = await client.post(base, params=_auth_params(), json=payload, headers={"User-Agent": _USER_AGENT})
        if response.is_success:
            return response
        message = _error_message(response)
        if response.status_code == 400 and prefix and _is_sku_collision(message):
            if number is None:
                number = await _scan_max_sku(client, base, prefix)
            number += 1
            payload["sku"] = f"{prefix}{number}"
            continue
        _check(response)
    raise WooCommerceAPIError(
        400,
        "چندین SKU پشت‌سرهم با رکوردهای قدیمی ووکامرس برخورد کردند. "
        "از مسیر WooCommerce → Status → Tools گزینهٔ Product lookup tables را Regenerate کن "
        "و سطل زبالهٔ محصولات (Trash) را هم خالی کن.",
    )


async def _resolve_categories(client: httpx.AsyncClient, base: str, categories: list[str]) -> list[dict[str, int]]:
    endpoint = f"{base}/categories"
    category_ids: list[dict[str, int]] = []
    for raw_path in categories:
        parts = [part.strip() for part in str(raw_path).replace("&gt;", ">").split(">") if part.strip()]
        parent_id = 0
        for part in parts:
            response = await client.get(
                endpoint,
                params={**_auth_params(), "search": part, "per_page": 100},
                headers={"User-Agent": _USER_AGENT},
            )
            if not response.is_success:
                continue
            matches = [item for item in response.json() if str(item.get("name", "")).casefold() == part.casefold()]
            exact = next((item for item in matches if parent_id and int(item.get("parent", 0)) == parent_id), None)
            exact = exact or (matches[0] if matches else None)
            if exact:
                category_id = int(exact["id"])
                if not any(item["id"] == category_id for item in category_ids):
                    category_ids.append({"id": category_id})
                parent_id = category_id
    return category_ids


async def create_draft(data: dict[str, Any], image_paths: list[Path]) -> tuple[int, str]:
    """Create a WooCommerce draft and its variations; return ID and edit URL."""
    if not all((settings.woocommerce_url, settings.woocommerce_key, settings.woocommerce_secret)):
        raise RuntimeError("اطلاعات WooCommerce API در .env کامل نیست.")
    if image_paths and not all((settings.wordpress_url, settings.wordpress_username, settings.wordpress_app_password)):
        raise RuntimeError("اطلاعات WordPress Media API برای آپلود عکس کامل نیست.")

    base = f"{settings.woocommerce_url.rstrip('/')}/wp-json/{settings.woocommerce_version.strip('/')}/products"
    prices = {str(k): int(v) for k, v in (data.get("prices") or {}).items() if v}
    common_price = int(data.get("price") or (next(iter(prices.values())) if prices else 0))
    attrs = _attributes(data)
    prefix = str(data.get("sku_prefix", "")).strip().upper()

    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        category_ids = await _resolve_categories(client, base, data.get("categories") or [])
        sku = await _next_sku(client, base, prefix)
        media_ids = [await _upload_media(client, path) for path in image_paths]

        # Only send fields we actually have values for. WooCommerce returns
        # HTTP 400 for some empty/zero placeholders (e.g. a "0" regular_price
        # or a null SKU), so omitting them is safer than defaulting them.
        payload: dict[str, Any] = {
            "name": data["title"],
            "type": "variable" if attrs else "simple",
            "status": "draft",
            "description": product_description(data),
            "attributes": attrs,
        }
        if sku:
            payload["sku"] = sku
        if common_price:
            payload["regular_price"] = str(common_price)
        if category_ids:
            payload["categories"] = category_ids
        if media_ids:
            payload["images"] = [{"id": image_id} for image_id in media_ids]

        response = await _create_with_sku_retry(client, base, payload, prefix, sku)
        product = response.json()
        product_id = int(product["id"])

        if attrs:
            for combo in _combinations(attrs):
                variation_attrs = [{"name": name, "option": value} for name, value in combo.items()]
                model = combo.get("مدل", "")
                variation_price = _price_for_model(model, common_price, prices)
                variation = await client.post(
                    f"{base}/{product_id}/variations",
                    params=_auth_params(),
                    json={"regular_price": str(variation_price), "status": "publish", "attributes": variation_attrs},
                    headers={"User-Agent": _USER_AGENT},
                )
                _check(variation)

        return product_id, f"{settings.woocommerce_url.rstrip('/')}/wp-admin/post.php?post={product_id}&action=edit"
