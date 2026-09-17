"""Direct WooCommerce draft creation through Woo REST + WordPress Media API."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any
from collections.abc import Sequence

import httpx

from bot.config import settings
from bot.services import publish_batch
from bot.services.color_matrix import build_combinations
from bot.services.plan import plan_from_dict
from bot.services.sku import (
    MAX_GHOST_SPAN,
    MAX_SKU_RETRIES,
    exists as sku_exists,
    is_collision as is_sku_collision,
    next_free as next_sku,
    number as sku_number,
)
from bot.services.woo_client import (
    Audit,
    Sink,
    WooClient,
    WooCommerceAPIError,
    body_snippet,
    check,
    error_message,
    media_base,
    products_base,
)

logger = logging.getLogger(__name__)

# WooCommerceAPIError is raised here and caught by bot.modules.product_flow, which imports it
# from this module; it lives in :mod:`bot.services.woo_client` because every HTTP layer failure
# — not just this one — is reported with it.
__all__ = [
    "WooCommerceAPIError",
    "create_draft",
    "product_description",
]

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


def _clean_options(values: Sequence[Any]) -> list[str]:
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
    """The attribute axes for WooCommerce — delegated to :mod:`bot.services.plan`.

    The preview, the REST payload and the ZIP manifest all come from
    ``plan.build_plan`` now, so the number shown in Telegram is the number of
    variations that will exist. Keeping this function as a thin wrapper means the
    existing tests (and any other caller) keep working.
    """
    return plan_from_dict(data).woo_attributes()


def _combinations(attrs: list[dict[str, Any]], restrictions: dict[str, list[str]] | None = None) -> list[dict[str, str]]:
    """Every attribute combination that is actually sellable.

    The full cartesian product minus the model↔color pairs the seller did not
    list: the رنگ attribute still shows every color, but iPhone 17 Pro only
    gets a variation for the colors that exist for it.
    """
    pairs = [(str(attr["name"]), list(attr["options"])) for attr in attrs]
    return build_combinations(pairs, restrictions or {})


def _model_color_restrictions(data: dict[str, Any]) -> dict[str, list[str]]:
    """Read ``model_colors`` from the product data, defensively cleaned."""
    raw = data.get("model_colors") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[str]] = {}
    for model, colors in raw.items():
        if not isinstance(colors, (list, tuple)):
            continue
        values = _clean_options(colors)
        if values:
            out[str(model)] = values
    return out


async def _upload_media(client: WooClient, path: Path, audit: Sink) -> int:
    response = await client.post(
        media_base(),
        content=path.read_bytes(),
        basic=True,
        headers={
            "Content-Type": "image/jpeg",
            "Content-Disposition": f'attachment; filename="{path.name}"',
        },
    )
    if not response.is_success:
        audit.log(f"[media] آپلود {path.name} ناموفق: HTTP {response.status_code}: {error_message(response)} | body={body_snippet(response)}")
    check(response)
    media_id = int(response.json()["id"])
    audit.log(f"[media] آپلود شد: {path.name} → media id {media_id}")
    return media_id


async def _upload_media_many(client: WooClient, paths: list[Path], audit: Sink) -> list[int]:
    """Upload all product images concurrently, preserving their order."""
    if not paths:
        return []
    semaphore = asyncio.Semaphore(4)

    async def upload(path: Path) -> int:
        async with semaphore:
            return await _upload_media(client, path, audit)

    return list(await asyncio.gather(*(upload(path) for path in paths)))


async def _create_without_sku_then_set(
    client: WooClient, base: str, payload: dict[str, Any], sku: str, audit: Sink
) -> httpx.Response | None:
    """Bypass the broken WooCommerce SKU lock: create without SKU, then update.

    WooCommerce only takes the ``wc_product_meta_lookup`` SKU lock inside the
    product data store's ``create()`` path, and only for REST requests that
    carry a non-empty SKU. When that lock INSERT fails (a known WooCommerce bug,
    see issue #57312), every REST create with a SKU is rejected with "already
    present in the lookup table" even though the SKU is genuinely free.

    Creating without a SKU skips the lock entirely, and the follow-up PUT runs
    through ``update()`` + ``set_sku()`` — the ordinary uniqueness check, which
    works. Returns the update response on success, or ``None`` after logging
    (and deleting the temporary no-SKU draft) when it fails, so the caller can
    fall back to the normal bump/jump loop.
    """
    no_sku_payload = {key: value for key, value in payload.items() if key != "sku"}
    response = await client.post(base, json=no_sku_payload)
    if not response.is_success:
        audit.log(
            f"[sku] دور زدن قفل SKU: ساخت بدون SKU ناموفق بود (HTTP {response.status_code}: "
            f"{error_message(response)} | body={body_snippet(response)})."
        )
        return None
    product_id = int(response.json()["id"])
    audit.log(f"[sku] دور زدن قفل SKU: محصول بدون SKU ساخته شد (id={product_id})؛ اکنون SKU را ثبت می‌کنیم.")
    response = await client.put(f"{base}/{product_id}", json=payload)
    if not response.is_success:
        audit.log(
            f"[sku] ثبت SKU با به‌روزرسانی ناموفق بود (HTTP {response.status_code}: "
            f"{error_message(response)} | body={body_snippet(response)})."
        )
        try:
            await client.delete(
                f"{base}/{product_id}",
                params={"force": "true"}
            )
            audit.log(f"[sku] پیش‌نویس موقت بدون SKU حذف شد (id={product_id}).")
        except Exception:
            audit.log(f"[sku] حذف پیش‌نویس موقت بدون SKU ناموفق بود (id={product_id}).")
        return None
    audit.log(f"[sku] SKU «{sku}» با به‌روزرسانی روی محصول {product_id} ثبت شد.")
    return response


async def _create_with_sku_retry(
    client: WooClient, base: str, payload: dict[str, Any], prefix: str, sku: str, audit: Sink
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
    number = sku_number(sku, prefix) if (sku and prefix) else None
    candidate_num = number if number is not None else 1
    start_num = candidate_num
    ceiling = start_num + MAX_GHOST_SPAN
    last_sku = sku or ""
    jump = 1
    linear_attempts = 5
    hit_ceiling = False
    tried_lock_fallback = False

    for attempt in range(1, MAX_SKU_RETRIES + 1):
        if prefix and candidate_num > ceiling:
            hit_ceiling = True
            audit.log(
                f"[sku] توقف: کاندید از سقف معقول «{prefix}{ceiling}» گذشت؛ "
                f"این دیگر بلوک شبح عادی نیست و پرش بیشتر بی‌فایده است."
            )
            break
        candidate = f"{prefix}{candidate_num}" if prefix else None
        if candidate:
            payload["sku"] = candidate
            last_sku = candidate
        response = await client.post(base, json=payload)
        if response.is_success:
            audit.log(f"[attempt {attempt}] POST موفق با SKU «{candidate}» → HTTP {response.status_code}")
            return response
        message = error_message(response)
        audit.log(
            f"[attempt {attempt}] POST با SKU «{candidate}» → HTTP {response.status_code}: {message} "
            f"| body={body_snippet(response)}"
        )
        if response.status_code == 400 and prefix and is_sku_collision(message):
            # The SKU lock (obtain_lock_on_sku_for_concurrent_requests) can fail
            # spuriously and reject every SKU. Try the no-SKU bypass exactly once
            # on the first "lookup table" collision; if it works we are done.
            if not tried_lock_fallback and "lookup table" in (message or "").casefold():
                tried_lock_fallback = True
                fallback_response = await _create_without_sku_then_set(
                    client, base, payload, candidate or last_sku, audit
                )
                if fallback_response is not None:
                    return fallback_response
            if attempt <= linear_attempts:
                # Probe the API (including Trash) to distinguish a real product
                # from a ghost row for the diagnostic log.
                visible = await sku_exists(client, base, candidate or last_sku, include_trash=True)
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
        check(response)

    if hit_ceiling:
        raise WooCommerceAPIError(
            400,
            f"ووکامرس همهٔ SKUها از «{prefix}{start_num}» تا «{last_sku}» را اشغال می‌داند "
            f"(بیش از {MAX_GHOST_SPAN:,} عدد پشت‌سرهم). این «رکورد شبح» عادی نیست و پاک‌سازی جدول lookup حلش نمی‌کند.\n\n"
            "این علامتِ باگِ شناخته‌شدهٔ «قفل SKU» ووکامرس است (obtain_lock_on_sku_for_concurrent_requests، از WC 9.3): "
            "INSERT قفل برای هر SKU شکست می‌خورد و همهٔ ساخت‌های REST را با «already present in the lookup table» رد می‌کند "
            "هرچند SKU واقعاً آزاد است.\n\n"
            "راه‌حل (در سرور وردپرس، نه ربات): یک فایل mu-plugin بساز تا این قفل معیوب را دور بزند:\n\n"
            "فایل wp-content/mu-plugins/disable-sku-lock.php:\n"
            "<?php\n/**\n * Plugin Name: Disable WC SKU lock\n */\nadd_filter( 'wc_product_pre_lock_on_sku', '__return_true', 10 );\n\n"
            "یا اگر ووکامرس 9.7.x / 9.8.x است، آن را به آخرین نسخه به‌روزرسانی کن (باگ در نسخه‌های بعدی رفع شده است).\n\n"
            "برای دیدن خطای دقیق MySQL، در لاگ‌های ووکامرس (WooCommerce → Status → Logs یا پوشهٔ wp-content/uploads/wc-logs) دنبال "
            "عبارت «Failed to obtain SKU lock» بگرد؛ فیلد error همان پیام واقعی دیتابیس است.",
        )

    raise WooCommerceAPIError(
        400,
        f"ربات {MAX_SKU_RETRIES} تلاش برای یافتن SKU آزاد انجام داد اما همه در جدول lookup ووکامرس اشغال بودند "
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


async def _create_variations_individually(
    client: WooClient, base: str, product_id: int, payloads: list[dict[str, Any]], audit: Sink
) -> None:
    """Fallback: create variations with concurrent individual POSTs."""
    semaphore = asyncio.Semaphore(5)

    async def one(payload: dict[str, Any]) -> None:
        async with semaphore:
            response = await client.post(
                f"{base}/{product_id}/variations",
                json=payload
            )
            if not response.is_success:
                audit.log(
                    f"[variation] ساخت variation ناموفق: HTTP {response.status_code}: "
                    f"{error_message(response)} | body={body_snippet(response)}"
                )
            check(response)

    await asyncio.gather(*(one(payload) for payload in payloads))
    audit.log(f"[variation] {len(payloads)} variation ساخته شد (تکی موازی).")


async def _find_resumable(
    client: WooClient, base: str, title: str, batch_id: str, audit: Sink
) -> dict[str, Any] | None:
    """Did an earlier attempt already create this product? Find it instead of doubling it.

    The store cannot filter products by meta, so the hunt is: search the title (ours, and
    WooCommerce does search titles), then read *our own* ``tisa_batch_id`` back off each
    hit. A hit is therefore this exact publish — not merely a similar product — which is
    the only property that makes resuming safe instead of lucky.
    """
    if not batch_id or not (title or "").strip():
        return None
    params: dict[str, Any] = {
        "search": title, "status": "any",
        "per_page": 20, "orderby": "date", "order": "desc",
    }
    response = await client.get(base, params=params)
    if response.status_code == 400:
        # Some stores reject status=any on products. Losing the hunt is acceptable
        # (we publish normally); failing the whole publish for it is not.
        params.pop("status", None)
        response = await client.get(base, params=params)
    if not response.is_success:
        audit.log(
            f"[resume] جستجوی تلاش‌های قبلی ممکن نشد (HTTP {response.status_code})؛ "
            "مسیر عادی ادامه می‌یابد."
        )
        return None
    try:
        items = response.json() or []
    except ValueError:
        return None
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and publish_batch.meta_batch_of(item) == batch_id:
            return item
    audit.log(
        f"[resume] {len(items) if isinstance(items, list) else 0} محصول هم‌عنوان پیدا شد "
        f"ولی هیچ‌کدام برچسب تلاش {batch_id} را نداشت."
    )
    return None


async def _existing_combos(
    client: WooClient, base: str, product_id: int, audit: Sink
) -> list[dict[str, str]] | None:
    """Every attribute combination the product already has (``None`` = unreadable).

    ``None`` means “we cannot know”, and the caller must then create everything — that is
    the old behaviour. Raising on a real error is deliberate: double variations are not a
    cosmetic problem, they are a product whose price/stock is now ambiguous.
    """
    found: list[dict[str, str]] = []
    page = 1
    while True:
        params: dict[str, Any] = {"per_page": 100, "page": page, "status": "any"}
        response = await client.get(
            f"{base}/{product_id}/variations", params=params
        )
        if response.status_code == 400:
            params.pop("status", None)
            response = await client.get(
                f"{base}/{product_id}/variations", params=params
            )
        if response.status_code in (404, 405, 501):
            audit.log("[resume] endpoint واریژن‌ها در این فروشگاه در دسترس نیست؛ همه ترکیب‌ها ساخته می‌شوند.")
            return None
        if not response.is_success:
            raise WooCommerceAPIError(
                response.status_code,
                "خواندن واریژن‌های موجود ناموفق بود؛ ساخت دوبارهٔ آن‌ها قیمت/موجودی محصول را "
                "دوپاره می‌کند، پس کار متوقف شد (محصول پاک نشد).",
            )
        try:
            items = response.json() or []
        except ValueError:
            items = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            found.append(
                {
                    str(attr.get("name")): str(attr.get("option"))
                    for attr in (item.get("attributes") or [])
                    if isinstance(attr, dict)
                }
            )
        if not isinstance(items, list) or len(items) < 100:
            break
        page += 1
    return found


async def _create_variations(
    client: WooClient,
    base: str,
    product_id: int,
    attrs: list[dict[str, Any]],
    common_price: int,
    prices: dict[str, int],
    audit: Sink,
    restrictions: dict[str, list[str]] | None = None,
    combos: list[dict[str, str]] | None = None,
    existing_combos: list[dict[str, str]] | None = None,
) -> None:
    """Create every variation in bulk via the batch endpoint, with a fallback.

    The WooCommerce ``variations/batch`` route builds all variations in a few
    requests (chunked by 100). If the store blocks or lacks that route, we fall
    back to concurrent individual POSTs so behaviour is preserved.

    ``restrictions`` maps a model to the colors that are actually in stock for
    it; combinations outside that list are never created.
    """
    # The plan's combos are authoritative: they are exactly what the preview
    # counted. Recomputing here is only a fallback for direct callers.
    if combos is None:
        combos = _combinations(attrs, restrictions)
    if not combos:
        return
    if restrictions:
        full = _combinations(attrs)
        audit.log(
            f"[variation] ماتریس رنگ هر مدل اعمال شد: {len(combos)} ترکیب معتبر "
            f"از {len(full)} ترکیب کامل ({len(restrictions)} مدل محدود شد)."
        )
    if existing_combos:
        # A resumed publish must top up, not duplicate: the store keeps every POST.
        had = len(combos)
        combos = [combo for combo in combos if combo not in existing_combos]
        if had != len(combos):
            audit.log(
                f"[resume] {had - len(combos)} واریژن از تلاش قبلی موجود بود؛ ساخته نشد "
                f"(باقی‌مانده: {len(combos)})."
            )
        if not combos:
            audit.log("[resume] همه واریژن‌ها از قبل ساخته شده بودند؛ چیزی اضافه نشد.")
            return
    payloads: list[dict[str, Any]] = []
    for combo in combos:
        model = combo.get("مدل", "")
        payloads.append(
            {
                "regular_price": str(_price_for_model(model, common_price, prices)),
                "status": "publish",
                "attributes": [{"name": name, "option": value} for name, value in combo.items()],
            }
        )

    endpoint = f"{base}/{product_id}/variations/batch"
    created = 0
    failed = 0
    for start in range(0, len(payloads), 100):
        chunk = payloads[start:start + 100]
        response = await client.post(
            endpoint,
            json={"create": chunk}
        )
        if response.status_code in (404, 405, 501) or not response.is_success:
            audit.log(
                f"[variation] بچ در دسترس نیست یا ناموفق بود (HTTP {response.status_code})؛ "
                f"بازگشت به ساخت تکی موازی."
            )
            await _create_variations_individually(client, base, product_id, payloads[start:], audit)
            return
        body = response.json()
        items = body.get("create", []) if isinstance(body, dict) else []
        for item in items:
            if isinstance(item, dict) and "id" in item:
                created += 1
            else:
                failed += 1
                audit.log(f"[variation] بچ: یک variation ساخته نشد: {item}")
    audit.log(f"[variation] {created} variation ساخته شد (بچ)؛ ناموفق: {failed}")
    if failed:
        raise WooCommerceAPIError(400, f"ساخت {failed} variation از طریق بچ ناموفق بود.")


async def _rollback(
    client: WooClient, base: str, product_id: int, media_ids: list[int], audit: Sink
) -> None:
    """Best-effort delete of a product we failed to finish, plus its uploads."""
    try:
        await client.delete(f"{base}/{product_id}", params={"force": "true"})
        audit.log(f"[rollback] محصول {product_id} حذف شد.")
    except Exception as exc:
        audit.log(f"[rollback] حذف محصول {product_id} ناموفق بود: {exc}")
    for media_id in media_ids or []:
        try:
            await client.delete(f"{media_base()}/{media_id}", params={"force": "true"}, basic=True)
        except Exception as exc:
            audit.log(f"[rollback] حذف media {media_id} ناموفق بود: {exc}")
    if media_ids:
        audit.log(f"[rollback] {len(media_ids)} تصویر آپلودشده پاک‌سازی شد.")


async def _resolve_categories(client: WooClient, base: str, categories: list[str], audit: Sink) -> list[dict[str, int]]:
    endpoint = f"{base}/categories"
    category_ids: list[dict[str, int]] = []
    for raw_path in categories:
        parts = [part.strip() for part in str(raw_path).replace("&gt;", ">").split(">") if part.strip()]
        parent_id = 0
        for part in parts:
            response = await client.get(
                endpoint,
                params={"search": part, "per_page": 100}
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


async def create_draft(
    data: dict[str, Any],
    image_paths: list[Path],
    *,
    dry_run: bool = False,
    report: list[str] | None = None,
    batch_id: str = "",
    meta: Sequence[dict[str, str]] = (),
    transport: httpx.BaseTransport | None = None,
) -> tuple[int, str]:
    """Create a WooCommerce draft and its variations; return ID and edit URL.

    ``dry_run`` keeps every step — media payloads, SKU scan, category lookup, the
    variation batch — and only replaces the network (see :func:`_dry_run_transport`).
    The ID it returns is then a fake one and the edit URL is empty, on purpose: a
    link to a product that does not exist would be worse than no link.
    ``report`` receives the audit lines so the caller can show them.

    ``batch_id`` (see :mod:`bot.services.publish_batch`) makes the call idempotent: before
    creating anything we look for a product carrying the same id — which is what a crash
    between «product POSTed» and «response received» leaves behind — and top it up instead
    of publishing a second copy. ``meta`` is written verbatim into the product.
    """
    if not all((settings.woocommerce_url, settings.woocommerce_key, settings.woocommerce_secret)):
        raise RuntimeError("اطلاعات WooCommerce API در .env کامل نیست.")
    if image_paths and not all((settings.wordpress_url, settings.wordpress_username, settings.wordpress_app_password)):
        raise RuntimeError("اطلاعات WordPress Media API برای آپلود عکس کامل نیست.")

    base = products_base()
    prices = {str(k): int(v) for k, v in (data.get("prices") or {}).items() if v}
    common_price = int(data.get("price") or (next(iter(prices.values())) if prices else 0))
    plan = plan_from_dict(data)
    attrs = plan.woo_attributes()
    restrictions = plan.restrictions
    if plan.dropped:
        audit_note = "؛ ".join(
            f"«{name}» {had}→{left}" for name, had, left in plan.dropped
        )
    else:
        audit_note = ""
    prefix = str(data.get("sku_prefix", "")).strip().upper()

    audit = Audit()
    if audit_note:
        audit.log(f"[plan] محورهای حذف‌شده: {audit_note}")
    audit.log(f"[plan] {plan.summary()}")
    audit.log(f"[config] WooCommerce: {settings.woocommerce_url or '(تنظیم نشده)'} (نسخه API: {settings.woocommerce_version})")
    audit.log(f"[config] WordPress media: {settings.wordpress_url or '(تنظیم نشده)'}")
    audit.log(f"[config] عنوان: {data.get('title', '(خالی)')} | پیشوند SKU: {prefix or '(خالی)'} | قیمت پایه: {common_price} | قیمت‌های گروهی: {prices or '(هیچ)'}")
    audit.log(f"[config] ویژگی‌ها: {[a['name'] for a in attrs] or '(هیچ)'} | تعداد تصاویر: {len(image_paths)}")
    if restrictions:
        audit.log(
            f"[config] ماتریس رنگ هر مدل: {len(restrictions)} مدل محدود شد "
            f"(نمونه: {next(iter(restrictions.items()))})"
        )

    try:
        # One client, one policy (auth, timeout, redirect, retry, redaction): see
        # :mod:`bot.services.woo_client`. ``transport`` stays a seam for the suite — a store
        # that answers with 500s, missing endpoints or a half-created product — and dry_run
        # wins over it inside the client: a rehearsal must never reach the real shop.
        async with WooClient(audit=audit, dry_run=dry_run, transport=transport) as client:
            resumed: dict[str, Any] | None = None
            if batch_id and not dry_run:
                resumed = await _find_resumable(client, base, str(data.get("title") or ""), batch_id, audit)
            if resumed is not None:
                # Nothing is re-created on purpose: the previous attempt may have attached
                # images and categories already, and re-uploading would leave the old
                # media orphaned in the library rather than fix anything.
                product_id = int(resumed["id"])
                media_ids: list[int] = []
                category_ids: list[dict[str, int]] = []
                sku = str(resumed.get("sku") or "")
                audit.log(
                    f"[resume] محصول {product_id} از تلاش قبلی (batch {batch_id}) پیدا شد؛ "
                    "عنوان، تصویر و دسته‌ها دست‌نخورده می‌مانند و فقط واریژن‌های جاافتاده ساخته می‌شوند."
                )
            else:
                category_ids = await _resolve_categories(client, base, data.get("categories") or [], audit)
                sku = await next_sku(client, base, prefix, audit)
                media_ids = await _upload_media_many(client, image_paths, audit)

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
                meta_rows = list(data.get("meta") or []) + list(meta)
                if batch_id:
                    meta_rows.append({"key": publish_batch.META_BATCH, "value": batch_id})
                if meta_rows:
                    payload["meta_data"] = meta_rows
                audit.log(f"[payload] {payload}")

                response = await _create_with_sku_retry(client, base, payload, prefix, sku, audit)
                product = response.json()
                product_id = int(product["id"])
                audit.log(f"[product] محصول ساخته شد: id={product_id}, sku={product.get('sku')}")

            try:
                if attrs:
                    existing = await _existing_combos(client, base, product_id, audit) if resumed else None
                    await _create_variations(
                        client, base, product_id, attrs, common_price, prices, audit,
                        restrictions, plan.combos, existing_combos=existing,
                    )
            except Exception as exc:
                # Half-built is worse than not built: a product with a missing
                # colour cannot be ordered, and nobody knows which ones are
                # missing. Remove it (and its media) and report the real error —
                # but never a product we did not create in this attempt.
                if resumed is not None:
                    audit.log(
                        f"[rollback] انجام نشد: محصول {product_id} از تلاش قبلی است و "
                        "ممکن است کسی رویش کار کرده باشد. "
                        "پیش‌نویسِ نیمه‌کاره در وردپرس باقی می‌ماند."
                    )
                    raise
                audit.log(f"[rollback] ساخت واریژن ناموفق بود ({type(exc).__name__}: {exc})؛ محصول در حال حذف است.")
                await _rollback(client, base, product_id, media_ids, audit)
                raise
            if plan.dropped:
                audit.log(
                    "[plan] هشدار: بعضی ویژگی‌ها بعد از حذف مقادیر تکراری از بین رفتند؛ "
                    "تعداد واریژن با پیش‌نمایش یکی است چون هر دو همین نقشه را می‌خوانند."
                )

        if dry_run:
            audit.log(f"[dry-run] جمع‌بندی: محصول ساختگی id={product_id}، {plan.count} واریژن، "
                      f"{len(image_paths)} تصویر (آپلود ساختگی). هیچ داده‌ای در سایت نوشته نشد.")
        elif resumed is not None:
            audit.log(
                f"[resume] جمع‌بندی: محصول {product_id} از تلاش قبلی بود و تکمیل شد؛ "
                "محصول دومی ساخته نشد. اگر واریژنی کم بود، فقط همان‌ها اضافه شدند."
            )
        # The caller always gets the trace when it asks for one — the resume note above
        # is exactly the kind of thing the result card has to admit to.
        if report is not None:
            report.extend(audit.lines)
        if dry_run:
            return product_id, ""
        return product_id, f"{settings.woocommerce_url.rstrip('/')}/wp-admin/post.php?post={product_id}&action=edit"
    except WooCommerceAPIError as exc:
        if not exc.diagnostics:
            exc.diagnostics = audit.lines
        raise
    except Exception:
        logger.exception("create_draft failed unexpectedly")
        raise
