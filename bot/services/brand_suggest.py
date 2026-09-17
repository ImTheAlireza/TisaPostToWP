"""«Did you mean …?» for brands — typos are data, not errors.

A seller types fast, on a phone, in two scripts. «Nubia Z60» in a caption is
almost always a mistyped «Nokia» (nubia phones do not come in cases this shop
sells), and the honest answer is not «این برند را نمی‌شناسم» — it is
«منظورت Nokia بود؟» with one tap to fix it *and remember the fix*.

Matching is deliberately narrow:

* only words that sit next to a model number are considered (see
  :func:`bot.services.model_catalog.unknown_brand_words`), so no English word in
  a description becomes a «brand»;
* a known brand is never corrected (it is not a typo, it is the truth);
* distance ≤ 2 on words of length ≥ 4, and only if EXACTLY ONE catalogued brand
  is that close — two candidates means we do not guess.
"""
from __future__ import annotations

from bot.services import model_catalog


def _distance(a: str, b: str, limit: int = 2) -> int:
    """Levenshtein with a cut-off (we never care about distances > limit)."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        best = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current.append(value)
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous = current
    return previous[-1]


def candidates(word: str) -> list[tuple[int, str]]:
    """``(distance, brand_key)`` for every catalogued brand within cutoff, sorted.

    Exposed because «what did you consider» is the interesting question when a
    guess is refused; it is also what makes the ambiguity rule testable.
    """
    cleaned = (word or "").strip().casefold()
    if len(cleaned) < 4 or not cleaned.isalpha():
        return []
    if model_catalog.brand_of(cleaned):
        return []            # a real brand: nothing to correct
    found: list[tuple[int, str]] = []
    for key, entry in model_catalog.brands().items():
        names = [key, *[str(x) for x in entry.get("words", [])]]
        best = min((_distance(cleaned, name.casefold()) for name in names if name), default=99)
        if best <= 2:
            found.append((best, key))
    found.sort()
    return found


def suggest_brand(word: str) -> tuple[str, str] | None:
    """Return ``(brand_key, Brand label)`` when ``word`` is a near miss, else None.

    Ambiguity is a hard stop: «Nokis» is close to Nokia alone and gets a
    suggestion; a typo that is equally close to two brands gets silence, which
    is what the preview's warning is for.
    """
    found = candidates(word)
    if not found:
        return None
    if len(found) > 1 and found[0][0] == found[1][0]:
        return None          # two brands are equally plausible — do not guess
    key = found[0][1]
    label = str(model_catalog.brands()[key].get("label") or key.title())
    return key, label


def correction_rule(word: str, target: str) -> tuple[str, str]:
    """The vocabulary rule that makes this typo correct forever.

    It is written as a substitution (not a catalog edit) on purpose: the shop
    dictionary runs before every parser, so the deterministic path and the AI
    both stop seeing the typo — one edit, both paths fixed.
    """
    return word.strip(), target.strip()


__all__ = ["candidates", "correction_rule", "suggest_brand"]
