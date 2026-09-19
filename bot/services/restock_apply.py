"""Applying a restock plan: the only place in this feature that writes.

:mod:`bot.services.product_match` reads, :mod:`bot.services.restock_plan` decides, and this
module sends. One rule keeps it honest: **the plan is sent exactly as it was shown**. Nothing
is re-parsed here and no field is improved on the way out — if the seller approved «مشکی ۵»,
the body for that variation id carries ``stock_quantity=5`` and nothing else.

Two shop shapes are handled, because WooCommerce has both:

* ``POST products/<id>/variations/batch`` — one request for the whole plan;
* ``PUT  products/<id>/variations/<vid>`` — per row, used when the host rejects the batch
  (older REST APIs answer 404/405) and for rows a batch accepted without echoing back.

After sending, the write is **verified from the shop's own answer**: a variation counts as
charged only if the response carries the value we asked for. A host that returns something
unusable gets one re-read of the variation list; rows still unconfirmed are reported as
unconfirmed rather than as success.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from bot.services import product_match, restock_plan
from bot.services.woo_client import (
    Audit,
    WooClient,
    WooCommerceAPIError,
    error_message,
    products_base,
)

logger = logging.getLogger(__name__)

#: Statuses where «batch is not supported here» is worth answering with per-row PUTs.
BATCH_REJECTED = (400, 404, 405, 501)
#: How many rows one batch request carries (WooCommerce's own soft limit).
BATCH_SIZE = 50
#: The fields a verification compares — everything this module can write.
WRITTEN = ("regular_price", "sale_price", "manage_stock", "stock_quantity", "stock_status")


@dataclass
class ApplyResult:
    """What the shop confirmed. The card the seller sees is built from these numbers only."""

    product_updated: bool = False
    confirmed: list[int] = field(default_factory=list)
    #: sent, but the shop's answer did not show the new value (neither echo nor re-read)
    unconfirmed: list[int] = field(default_factory=list)
    #: refused by the shop
    failed: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.failed and not self.unconfirmed

    def summary(self, plan: restock_plan.RestockPlan) -> str:
        head = (f"✅ {len(self.confirmed)} واریژن شارژ شد" if self.confirmed
                else ("✅ خود محصول به‌روز شد" if self.product_updated else "❌ چیزی اعمال نشد"))
        if self.dry_run:
            head += " (dry-run)"
        bits: list[str] = []
        if self.unconfirmed:
            bits.append(f"{len(self.unconfirmed)} خط فرستاده شد ولی فروشگاه مقدار جدید را "
                        "برنگرداند؛ در پیش‌نمایشِ فروشگاه چک کن")
        if self.failed:
            bits.append(f"{len(self.failed)} خط رد شد")
        if plan.unmatched:
            bits.append(f"{len(plan.unmatched)} خط به هیچ رنگ/مدلی نچسبید و اعمال نشد")
        return head + (("\n⚠️ " + "؛ ".join(bits) + ".") if bits else "")


def _same_value(sent: dict[str, Any], got: dict[str, Any]) -> bool:
    """Did the shop give back what we asked for? WooCommerce answers prices as strings."""
    for key in WRITTEN:
        if key not in sent:
            continue
        if str(sent[key] if sent[key] is not None else "") != str(got.get(key)
                                                                  if got.get(key) is not None else ""):
            return False
    return True


async def apply_plan(plan: restock_plan.RestockPlan,
                     product: product_match.ShopProduct | None = None,
                     *,
                     audit: Audit | None = None,
                     transport: Any | None = None,
                     dry_run: bool = False) -> ApplyResult:
    """Send ``plan`` to the shop. ``product`` is optional; when given, unconfirmed rows are
    re-read from it rather than reported as a guess."""
    if plan.empty or plan.blocking:
        raise ValueError("این طرح قابل اعمال نیست")
    trace = audit or Audit()
    result = ApplyResult(dry_run=dry_run)
    base = products_base()
    async with WooClient(audit=trace, dry_run=dry_run, transport=transport) as client:
        payload = plan.product_payload()
        if payload:
            response = await client.put(f"{base}/{plan.product_id}", json=payload)
            if response.is_success:
                result.product_updated = True
                trace.log(f"[restock] PUT products/{plan.product_id} "
                          f"{json.dumps(payload, ensure_ascii=False)}")
            else:
                # Without the parent's stock status the variations would still hide behind an
                # «out of stock» catalogue entry, so a refused parent stops the whole apply.
                result.errors.append(_human(response))
                trace.log(f"[restock] product {plan.product_id} FAILED {response.status_code}")
                return result

        payloads = plan.variation_payloads()
        if payloads:
            chunks = [payloads[start:start + BATCH_SIZE] for start in range(0, len(payloads),
                                                                             BATCH_SIZE)]
            for chunk in chunks:
                await _write_chunk(client, base, plan.product_id, chunk, product, result, trace)
            trace.log(f"[restock] variations {plan.product_id}: confirmed={len(result.confirmed)} "
                      f"unconfirmed={len(result.unconfirmed)} failed={len(result.failed)}")
    return result


async def _write_chunk(client: WooClient, base: str, product_id: int,
                       chunk: list[dict[str, Any]], product: product_match.ShopProduct | None,
                       result: ApplyResult, trace: Audit) -> None:
    rows: list[dict[str, Any]] = []
    try:
        response = await client.post(f"{base}/{product_id}/variations/batch",
                                     json={"update": chunk})
        if response.is_success:
            rows = _batch_rows(response.json())
        elif response.status_code in BATCH_REJECTED:
            # `variations/batch` is not universal; per-row PUTs are slower and idempotent, so
            # the plan still gets applied instead of asking the seller to retry.
            trace.log(f"[restock] batch rejected (HTTP {response.status_code})؛ PUT تک‌تک")
            for row in chunk:
                await _single(client, base, product_id, row, result, trace)
            return
        else:
            result.errors.append(_human(response))
            result.failed.extend(int(row["id"]) for row in chunk)
            trace.log(f"[restock] batch FAILED {response.status_code}")
            return
    except WooCommerceAPIError as exc:            # a transport error outlived the retry policy
        result.errors.append(str(exc))
        result.failed.extend(int(row["id"]) for row in chunk)
        trace.log(f"[restock] batch ERROR {exc}")
        return

    by_id = {int(row.get("id") or 0): row for row in rows}
    for row in chunk:
        vid = int(row["id"])
        got = by_id.get(vid)
        if got is None:
            await _single(client, base, product_id, row, result, trace)
        elif _same_value(row, got):
            result.confirmed.append(vid)
        else:
            result.unconfirmed.append(vid)
    if result.unconfirmed:
        await _recheck(client, base, product_id, chunk, product, result, trace)


async def _single(client: WooClient, base: str, product_id: int, row: dict[str, Any],
                  result: ApplyResult, trace: Audit) -> None:
    vid = int(row["id"])
    response = await client.put(f"{base}/{product_id}/variations/{vid}", json=row)
    if not response.is_success:
        result.failed.append(vid)
        result.errors.append(f"واریژن {vid}: {_human(response)}")
        trace.log(f"[restock] variation {vid} FAILED {response.status_code}")
        return
    trace.log(f"[restock] PUT products/{product_id}/variations/{vid}")
    try:
        got = response.json()
    except ValueError:
        got = None
    if isinstance(got, dict) and _same_value(row, got):
        result.confirmed.append(vid)
    else:
        result.unconfirmed.append(vid)


async def _recheck(client: WooClient, base: str, product_id: int,
                   chunk: list[dict[str, Any]], product: product_match.ShopProduct | None,
                   result: ApplyResult, trace: Audit) -> None:
    """One read of the variation list, used only when the shop's echo was unusable."""
    if not result.unconfirmed:
        return
    wanted = {int(row["id"]): row for row in chunk}
    rows: list[dict[str, Any]] = []
    try:
        response = await client.get(f"{base}/{product_id}/variations",
                                    params={"per_page": 100, "status": "any"})
        if response.is_success and isinstance(response.json(), list):
            rows = [row for row in response.json() if isinstance(row, dict)]
    except WooCommerceAPIError as exc:            # pragma: no cover - depends on the host
        trace.log(f"[restock] recheck ناممکن: {exc}")
        return
    if not rows and product is not None:
        rows = [{"id": variation.variation_id, "stock_quantity": variation.stock,
                 "stock_status": variation.stock_status, "regular_price": variation.regular_price,
                 "sale_price": variation.sale_price} for variation in product.variations]
    fixed = {int(row["id"]) for row in rows
             if int(row.get("id") or 0) in wanted
             and int(row.get("id") or 0) in set(result.unconfirmed)
             and _same_value(wanted[int(row["id"])], row)}
    if fixed:
        result.confirmed.extend(sorted(fixed))
        result.unconfirmed = [vid for vid in result.unconfirmed if vid not in fixed]
        trace.log(f"[restock] با یک خواندن مجدد، {len(fixed)} خط تأیید شد")


def _batch_rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):                     # some hosts answer with a bare list
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        rows = raw.get("update") or raw.get("updated") or []
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _human(response: Any) -> str:
    """The shop's own message when it is usable, the status code when it isn't."""
    text = error_message(response).strip() if hasattr(response, "status_code") else ""
    if text.startswith("{"):
        try:
            text = str(json.loads(text).get("message") or "").strip() or text
        except ValueError:
            pass
    return text or f"خطای {getattr(response, 'status_code', '?')}"


__all__ = ["BATCH_SIZE", "ApplyResult", "apply_plan"]
