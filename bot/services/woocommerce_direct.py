"""Direct WooCommerce draft creation through Woo REST + WordPress Media API."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from bot.config import settings

logger = logging.getLogger(__name__)

_USER_AGENT = "TisaPostToWP/1.0 (+https://tisacase.com)"
_MAX_SKU_RETRIES = 100


class _Audit:
    """Collects a step-by-step trace that is both logged and kept for errors."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, line: str) -> None:
        self.lines.append(line)
        logger.info("%s", line)

    def text(self) -> str:
        return "\n".join(self.lines)


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
    ``diagnostics`` holds the step-by-step audit of the failed request.
    """

    def __init__(self, status_code: int, message: str, diagnostics: list[str] | None = None):
        self.status_code = status_code
        self.diagnostics = diagnostics or []
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


def _body_snippet(response: httpx.Response, limit: int = 400) -> str:
    """A compact, single-line snippet of the raw response body for the audit."""
    text = (response.text or "").replace("\n", " ").replace("\r", " ").strip()
    return text[:limit]


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


async def _upload_media(client: httpx.AsyncClient, path: Path, audit: _Audit) -> int:
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
    if not response.is_success:
        audit.log(f"[media] آپلود {path.name} ناموفق: HTTP {response.status_code}: {_error_message(response)} | body={_body_snippet(response)}")
    _check(response)
    media_id = int(response.json()["id"])
    audit.log(f"[media] آپلود شد: {path.name} → media id {media_id}")
    return media_id


async def _sku_from_prefix_plugin(client: httpx.AsyncClient, prefix: str, audit: _Audit) -> str | None:
    """Ask the SKU-prefix plugin for the next SKU; ``None`` means "not available".

    The plugin is optional — if its route is missing (404) or the Application
    Password can't use it (401/403), we fall back to scanning the catalog
    instead of aborting the whole product-creation flow.
    """
    if not prefix or not all((settings.wordpress_url, settings.wordpress_username, settings.wordpress_app_password)):
        audit.log("[sku] افزونهٔ next-sku بررسی نشد (اطلاعات WordPress ناقص یا پیشوند خالی).")
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
        audit.log(f"[sku] افزونهٔ next-sku در دسترس نیست (HTTP {response.status_code})؛ به اسکن دستی می‌رویم.")
        return None
    _check(response)
    value = response.json().get("sku")
    sku = str(value).strip() if value else None
    audit.log(f"[sku] افزونهٔ next-sku پاسخ داد: «{sku or '(خالی)'}»")
    return sku


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


async def _scan_max_sku(client: httpx.AsyncClient, base: str, prefix: str, audit: _Audit) -> int:
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
    search_pages = 0
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
        search_pages = page
        maximum = max(maximum, scan(items))
        if len(items) < 100:
            break
    audit.log(f"[sku] اسکن با search=«{prefix}»: {search_pages} صفحه، بیشترین شماره={maximum}")

    if maximum == 0:
        full_pages = 0
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
            full_pages = page
            maximum = max(maximum, scan(items))
            if len(items) < 100:
                break
        audit.log(f"[sku] اسکن کامل کاتالوگ (جدید→قدیم): {full_pages} صفحه، بیشترین شماره={maximum}")
    return maximum


async def _next_sku(client: httpx.AsyncClient, base: str, prefix: str, audit: _Audit) -> str:
    """Resolve a free SKU for ``prefix`` (e.g. BO -> BO148).

    The candidate is verified against the store with the exact `sku` filter and
    bumped until it is truly free, so a stale plugin counter or a catalog too
    large to scan completely can never produce a duplicate-SKU 400. Note: this
    can only see what the REST API exposes; ghost rows in WooCommerce's
    ``wc_product_meta_lookup`` table are invisible here and are instead handled
    by the POST retry loop.
    """
    prefix = (prefix or "").strip().upper()
    if not prefix:
        audit.log("[sku] پیشوند SKU خالی است؛ بدون SKU ادامه می‌دهیم.")
        return ""

    # The plugin returns the next SKU it considers free; the catalog scan
    # returns the highest existing number. Start probing from whichever we
    # have (the plugin's suggested SKU wins) and skip anything the API sees.
    start = 0
    plugin_sku = await _sku_from_prefix_plugin(client, prefix, audit)
    if plugin_sku:
        start = _sku_number(plugin_sku, prefix) or 0
        audit.log(f"[sku] افزونهٔ next-sku پاسخ داد: «{plugin_sku}»")
    if not start:
        start = await _scan_max_sku(client, base, prefix, audit) + 1
        audit.log(f"[sku] شروع جستجو از: {prefix}{start}")

    for attempt in range(200):
        candidate = f"{prefix}{start + attempt}"
        if not await _sku_exists(client, base, candidate):
            audit.log(f"[sku] SKU کاندید آزاد است (API): {candidate}")
            return candidate
        audit.log(f"[sku] SKU کاندید اشغال است (API): {candidate}")
    raise WooCommerceAPIError(500, f"یافتن SKU آزاد برای پیشوند «{prefix}» ممکن نشد.")


async def _create_with_sku_retry(
    client: httpx.AsyncClient, base: str, payload: dict[str, Any], prefix: str, sku: str, audit: _Audit
) -> httpx.Response:
    """POST the product, bumping the SKU if WooCommerce reports a collision.

    A "free" SKU can still be rejected at insert time: a deleted product may
    have left a ghost row in WooCommerce's ``wc_product_meta_lookup`` table
    that is invisible to the REST product list, the Trash, and even the
    "Regenerate lookup tables" tool. The retry strategy walks linearly for the
    first few collisions (so single ghost rows cost a single SKU) and then
    switches to doubling jumps, which escapes large contiguous ghost blocks in
    O(log n) attempts instead of failing after a fixed budget.
    """
    number = _sku_number(sku, prefix) if (sku and prefix) else None
    candidate_num = number if number is not None else 1
    last_sku = sku or ""
    jump = 1
    linear_attempts = 5

    for attempt in range(1, _MAX_SKU_RETRIES + 1):
        candidate = f"{prefix}{candidate_num}" if prefix else None
        if candidate:
            payload["sku"] = candidate
            last_sku = candidate
        response = await client.post(base, params=_auth_params(), json=payload, headers={"User-Agent": _USER_AGENT})
        if response.is_success:
            audit.log(f"[attempt {attempt}] POST موفق با SKU «{candidate}» → HTTP {response.status_code}")
            return response
        message = _error_message(response)
        audit.log(
            f"[attempt {attempt}] POST با SKU «{candidate}» → HTTP {response.status_code}: {message} "
            f"| body={_body_snippet(response)}"
        )
        if response.status_code == 400 and prefix and _is_sku_collision(message):
            if attempt <= linear_attempts:
                # Probe the API to distinguish a real product from a ghost row.
                visible = await _sku_exists(client, base, candidate)
                if visible:
                    audit.log(f"[sku] {candidate} محصول واقعی/در زباله‌دان است؛ رد شد.")
                else:
                    audit.log(
                        f"[sku] {candidate} در API و زباله‌دان دیده نمی‌شود اما ووکامرس آن را اشغال می‌داند "
                        f"→ رکورد شبح در wc_product_meta_lookup."
                    )
                candidate_num += 1
            else:
                jump *= 2
                candidate_num += jump
                audit.log(f"[sku] عبور از بلوک رکوردهای شبح: پرش +{jump} → کاندید بعدی {prefix}{candidate_num}")
            continue
        _check(response)

    raise WooCommerceAPIError(
        400,
        f"ربات {_MAX_SKU_RETRIES} تلاش برای یافتن SKU آزاد انجام داد اما همه در جدول lookup ووکامرس اشغال بودند "
        f"(آخرین مورد: «{last_sku}»). این «رکوردهای شبح» متعلق به محصولاتی هستند که حذف شده‌اند ولی ردیف SKU آن‌ها "
        "در جدول wc_product_meta_lookup باقی مانده است. این رکوردها از هیچ API دیده نمی‌شوند و Regenerate یا خالی کردن "
        "زباله‌دان هم طبق باگ شناخته‌شدهٔ ووکامرس آن‌ها را پاک نمی‌کند.\n\n"
        "راه‌حل کم‌خطر: فقط رکوردهای شبح حذف می‌شوند؛ به محصولات واقعی دست زده نمی‌شود "
        "(پیشوند wp_ را با پیشوند واقعی جدول‌هایت جایگزین کن و قبلش بکاپ بگیر):\n\n"
        "۱) اول پیش‌نمایش — این کوئری چیزی حذف نمی‌کند:\n"
        "SELECT l.product_id, l.sku, p.post_type, p.post_status, p.post_parent, pp.post_status AS parent_status\n"
        "FROM wp_wc_product_meta_lookup l\n"
        "LEFT JOIN wp_posts p ON p.ID = l.product_id\n"
        "LEFT JOIN wp_posts pp ON pp.ID = p.post_parent\n"
        "WHERE p.ID IS NULL\n"
        "   OR p.post_type NOT IN ('product','product_variation')\n"
        "   OR p.post_status IN ('trash','auto-draft')\n"
        "   OR (p.post_type = 'product_variation' AND (pp.ID IS NULL OR pp.post_type <> 'product' OR pp.post_status IN ('trash','auto-draft')));\n\n"
        "۲) بعد حذفِ دقیقاً همان ردیف‌ها:\n"
        "DELETE l\n"
        "FROM wp_wc_product_meta_lookup l\n"
        "LEFT JOIN wp_posts p ON p.ID = l.product_id\n"
        "LEFT JOIN wp_posts pp ON pp.ID = p.post_parent\n"
        "WHERE p.ID IS NULL\n"
        "   OR p.post_type NOT IN ('product','product_variation')\n"
        "   OR p.post_status IN ('trash','auto-draft')\n"
        "   OR (p.post_type = 'product_variation' AND (pp.ID IS NULL OR pp.post_type <> 'product' OR pp.post_status IN ('trash','auto-draft')));\n\n"
        "این DELETE فقط ردیف‌هایی را حذف می‌کند که به یک محصول/وارییشن زنده اشاره ندارند؛ "
        "محصولات منتشرشده، پیش‌نویس و خصوصی و وارییشن‌های سالم دست‌نخورده می‌مانند.",
    )


async def _resolve_categories(client: httpx.AsyncClient, base: str, categories: list[str], audit: _Audit) -> list[dict[str, int]]:
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
                audit.log(f"[cat] جستجوی دستهٔ «{part}» ناموفق: HTTP {response.status_code}")
                continue
            matches = [item for item in response.json() if str(item.get("name", "")).casefold() == part.casefold()]
            exact = next((item for item in matches if parent_id and int(item.get("parent", 0)) == parent_id), None)
            exact = exact or (matches[0] if matches else None)
            if exact:
                category_id = int(exact["id"])
                if not any(item["id"] == category_id for item in category_ids):
                    category_ids.append({"id": category_id})
                parent_id = category_id
            else:
                audit.log(f"[cat] دستهٔ «{part}» در فروشگاه پیدا نشد؛ نادیده گرفته شد.")
    audit.log(f"[cat] دسته‌های نهایی: {[c['id'] for c in category_ids] if category_ids else '(هیچ)'}")
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

    audit = _Audit()
    audit.log(f"[config] WooCommerce: {settings.woocommerce_url or '(تنظیم نشده)'} (نسخه API: {settings.woocommerce_version})")
    audit.log(f"[config] WordPress media: {settings.wordpress_url or '(تنظیم نشده)'}")
    audit.log(f"[config] عنوان: {data.get('title', '(خالی)')} | پیشوند SKU: {prefix or '(خالی)'} | قیمت پایه: {common_price} | قیمت‌های گروهی: {prices or '(هیچ)'}")
    audit.log(f"[config] ویژگی‌ها: {[a['name'] for a in attrs] or '(هیچ)'} | تعداد تصاویر: {len(image_paths)}")

    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            category_ids = await _resolve_categories(client, base, data.get("categories") or [], audit)
            sku = await _next_sku(client, base, prefix, audit)
            media_ids = [await _upload_media(client, path, audit) for path in image_paths]

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
            audit.log(f"[payload] {payload}")

            response = await _create_with_sku_retry(client, base, payload, prefix, sku, audit)
            product = response.json()
            product_id = int(product["id"])
            audit.log(f"[product] محصول ساخته شد: id={product_id}, sku={product.get('sku')}")

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
                    if not variation.is_success:
                        audit.log(f"[variation] ساخت variation ناموفق: HTTP {variation.status_code}: {_error_message(variation)} | body={_body_snippet(variation)}")
                    _check(variation)
                audit.log(f"[variation] {len(_combinations(attrs))} variation ساخته شد.")

        return product_id, f"{settings.woocommerce_url.rstrip('/')}/wp-admin/post.php?post={product_id}&action=edit"
    except WooCommerceAPIError as exc:
        if not exc.diagnostics:
            exc.diagnostics = audit.lines
        raise
    except Exception:
        logger.exception("create_draft failed unexpectedly")
        raise
