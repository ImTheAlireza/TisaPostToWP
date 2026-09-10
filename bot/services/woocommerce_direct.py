"""Direct WooCommerce draft creation through Woo REST + WordPress Media API."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx

from bot.config import settings


def _auth_params() -> dict[str, str]:
    return {"consumer_key": settings.woocommerce_key, "consumer_secret": settings.woocommerce_secret}


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


def _attributes(data: dict[str, Any]) -> list[dict[str, Any]]:
    attrs: list[dict[str, Any]] = []
    models = data.get("models") or []
    if len(models) >= 2:
        attrs.append({"name": "مدل", "visible": True, "variation": True, "options": models})
    for name, values in (data.get("attributes") or {}).items():
        if isinstance(values, list) and len(values) >= 2:
            attrs.append({"name": str(name), "visible": True, "variation": True, "options": values})
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
        },
    )
    response.raise_for_status()
    return int(response.json()["id"])


async def _sku_from_prefix_plugin(client: httpx.AsyncClient, prefix: str) -> str | None:
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
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    value = response.json().get("sku")
    return str(value).strip() if value else None


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
    payload: dict[str, Any] = {
        "name": data["title"], "type": "variable" if attrs else "simple", "status": "draft",
        "description": product_description(data), "sku": data.get("sku_prefix", ""), "regular_price": str(common_price),
        "attributes": attrs,
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        categories_endpoint = base + "/categories"
        category_ids = []
        for path in data.get("categories", []) or []:
            parts = [part.strip() for part in str(path).replace("&gt;", ">").split(">") if part.strip()]
            parent_id = 0
            for part in parts:
                found = await client.get(categories_endpoint, params={**_auth_params(), "search": part, "per_page": 100})
                if not found.is_success:
                    continue
                matches = [item for item in found.json() if str(item.get("name", "")).casefold() == part.casefold()]
                exact = next((item for item in matches if parent_id and int(item.get("parent", 0)) == parent_id), None)
                exact = exact or (matches[0] if matches else None)
                if exact:
                    category_id = int(exact["id"])
                    if not any(item["id"] == category_id for item in category_ids):
                        category_ids.append({"id": category_id})
                    parent_id = category_id
        if category_ids: payload["categories"] = category_ids
        prefix = str(data.get("sku_prefix", "")).upper()
        sku = await _sku_from_prefix_plugin(client, prefix) if prefix else None
        if prefix and not sku:
            # WooCommerce's `search` is not guaranteed to search SKU values
            # on every installation. Scan the paginated product collection as
            # a fallback so BO147 correctly produces BO148, not BO1.
            maximum = 0
            candidates = await client.get(base, params={**_auth_params(), "search": prefix, "per_page": 100})
            candidates.raise_for_status()
            pages = [candidates.json()]
            total_pages = int(candidates.headers.get("X-WP-TotalPages", "1"))
            for page in range(2, min(total_pages, 50) + 1):
                response = await client.get(base, params={**_auth_params(), "search": prefix, "per_page": 100, "page": page})
                response.raise_for_status()
                pages.append(response.json())
            for page_items in pages:
                for item in page_items:
                    value = str(item.get("sku", ""))
                    match = re.fullmatch(re.escape(prefix) + r"(\d+)", value, re.I)
                    if match:
                        maximum = max(maximum, int(match.group(1)))
            # If search returned no matching SKU, paginate all products. This
            # is the reliable path on stores where search only checks titles.
            if maximum == 0:
                for page in range(1, 51):
                    response = await client.get(base, params={**_auth_params(), "per_page": 100, "page": page, "orderby": "id", "order": "asc"})
                    if response.status_code == 400 or not response.json():
                        break
                    response.raise_for_status()
                    for item in response.json():
                        value = str(item.get("sku", ""))
                        match = re.fullmatch(re.escape(prefix) + r"(\d+)", value, re.I)
                        if match:
                            maximum = max(maximum, int(match.group(1)))
                    if len(response.json()) < 100:
                        break
            sku = prefix + str(maximum + 1)
        payload["sku"] = sku
        media_ids = []
        for path in image_paths:
            media_ids.append(await _upload_media(client, path))
        if media_ids:
            payload["images"] = [{"id": image_id} for image_id in media_ids]
        response = await client.post(base, params=_auth_params(), json=payload)
        response.raise_for_status()
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
                )
                variation.raise_for_status()
        return product_id, f"{settings.woocommerce_url.rstrip('/')}/wp-admin/post.php?post={product_id}&action=edit"
