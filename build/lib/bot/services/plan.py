"""One source of truth for «what will actually be created».

The bot used to count variations in three places that disagreed:

* the Telegram preview called ``color_matrix.variation_count`` (no dedupe),
* the REST writer rebuilt the axes itself in ``woocommerce_direct._attributes``
  (dedupe + drop any axis that collapses to one value),
* the ZIP importer rebuilt them a third time from ``product.json``.

So the preview could honestly print «۴ واریژن» while WooCommerce received a
product with one attribute axis — or even a *simple* product. The README promise
«پیش‌نمایش، تعداد واقعی variation را نشان می‌دهد — همان عددی که ساخته می‌شود»
was only true by luck.

Everything now goes through :func:`build_plan`. The preview shows
``plan.count``, the REST payload uses ``plan.woo_attributes()`` and the ZIP
manifest uses ``plan.manifest_attributes()``. They cannot drift, because they
are the same object.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterable, Sequence

from bot.services.color_matrix import (
    build_combinations,
    is_model_attribute,
    restrict_combinations,
)


def clean_values(values: Iterable[Any]) -> list[str]:
    """Strip, drop empties, dedupe (first spelling wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        text = re.sub(r"\s+", " ", str(value)).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


@dataclass
class VariationPlan:
    """The exact matrix that will be created, plus what was dropped on the way."""

    axes: list[tuple[str, list[str]]] = field(default_factory=list)
    combos: list[dict[str, str]] = field(default_factory=list)
    dropped: list[tuple[str, int, int]] = field(default_factory=list)  # (name, in, out)
    models: list[str] = field(default_factory=list)
    restrictions: dict[str, list[str]] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.combos)

    @property
    def is_variable(self) -> bool:
        return bool(self.axes)

    @property
    def naive_count(self) -> int:
        total = 1
        for _name, values in self.axes:
            total *= max(1, len(values))
        return total

    @property
    def restricted(self) -> bool:
        return self.naive_count != self.count

    def attribute_names(self) -> list[str]:
        return [name for name, _values in self.axes]

    def woo_attributes(self) -> list[dict[str, Any]]:
        """The ``attributes`` array for ``POST /wp-json/wc/v3/products``."""
        return [
            {"name": name, "visible": True, "variation": True, "options": values}
            for name, values in self.axes
        ]

    def manifest_attributes(self) -> dict[str, list[str]]:
        """The ``attributes`` object for ``product.json`` (ZIP importer)."""
        return {name: list(values) for name, values in self.axes if name != "مدل"}

    def summary(self) -> str:
        if not self.axes:
            return "هیچ ویژگی قابل‌انتخابی نمانده؛ محصول simple ساخته می‌شود."
        parts = [f"{name} ({len(values)})" for name, values in self.axes]
        text = " | ".join(parts)
        if self.restricted:
            text += f" ← {self.naive_count} ترکیب کامل، {self.count} ترکیب معتبر"
        else:
            text += f" = {self.count} واریژن"
        if self.dropped:
            text += "\n" + "؛ ".join(
                f"«{name}» با {had} مقدار به {left} رسید و حذف شد"
                for name, had, left in self.dropped
            )
        return text


def build_plan(
    models: Sequence[str],
    attributes: dict[str, Sequence[str]] | None,
    restrictions: dict[str, Sequence[str]] | None = None,
    *,
    model_axis_name: str = "مدل",
) -> VariationPlan:
    """Resolve models + attributes + per-model colours into the final matrix.

    Rules kept identical to the previous WooCommerce writer, because that one is
    what actually has to match the store:

    * a model list only becomes an axis when it has ≥ 2 distinct models;
    * every attribute needs ≥ 2 distinct values after dedupe, otherwise it is
      part of the product name, not a variation axis;
    * an attribute named like the model axis never duplicates it;
    * colours are then restricted to the pairs the seller actually listed.
    """
    clean_models = clean_values(models)
    axes: list[tuple[str, list[str]]] = []
    dropped: list[tuple[str, int, int]] = []
    used: set[str] = set()

    if len(clean_models) >= 2:
        axes.append((model_axis_name, clean_models))
        used.add(model_axis_name.casefold())

    for name, values in (attributes or {}).items():
        attribute_name = re.sub(r"\s+", " ", str(name)).strip()
        raw = clean_values(values if isinstance(values, (list, tuple, set)) else [values])
        if not attribute_name or attribute_name.casefold() in used or is_model_attribute(attribute_name):
            if attribute_name and attribute_name.casefold() in used:
                dropped.append((attribute_name, len(raw), 0))
            continue
        used.add(attribute_name.casefold())
        if len(raw) < 2:
            if len(list(values or [])) >= 2:
                dropped.append((attribute_name, len(list(values or [])), len(raw)))
            continue
        axes.append((attribute_name, raw))

    clean_restrictions = {
        str(model): clean_values(colors)
        for model, colors in (restrictions or {}).items()
        if clean_values(colors)
    }
    combos = build_combinations(axes, clean_restrictions)
    if len(axes) > 1 and len(clean_restrictions) > 1:
        # ``build_combinations`` may fall back to the unfiltered matrix when a
        # naming mismatch makes the restriction unusable; keep the real count.
        combos = restrict_combinations(combos, clean_restrictions) or combos

    return VariationPlan(
        axes=axes,
        combos=combos,
        dropped=dropped,
        models=clean_models,
        restrictions=clean_restrictions,
    )


def plan_from_dict(data: dict[str, Any]) -> VariationPlan:
    """Same plan, from ``ProductData.to_dict()`` (used by both output paths)."""
    return build_plan(
        data.get("models") or [],
        data.get("attributes") or {},
        data.get("model_colors") or {},
    )


__all__ = ["VariationPlan", "build_plan", "clean_values", "plan_from_dict"]
