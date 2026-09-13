# -*- coding: utf-8 -*-
"""Self-learning memory for the owner's corrections.

When the bot misreads something and the owner fixes it, the fix should not be
lost with the session — and it should not be memorized as a one-off either.
This module turns a correction into the *narrowest rule that still generalizes*:

``price_scale``
    A bare price was off by a power of ten: ``1098`` was read as 1,098 and
    corrected to 1,098,000, so a bare 4-digit amount means "thousands".
    Next time ``1298`` is read as 1,298,000 with no help.

``term``
    One word was replaced by another (a color, a brand spelling, a title word):
    «سلفی» -> «مشکی». Applied to attribute values and title tokens later.

Everything else is still recorded in ``corrections`` (a bounded log) so the
owner can see what the bot was told, even when no safe rule could be inferred.

Rules live in ``data/learned.json`` (git-ignored, like ``roles.json``) and are
managed from the «🧠 یادگیری» screen, because a memory that cannot be edited is
a liability: one wrong generalization would silently corrupt every later
product.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Runtime state lives in the repo-root data/ directory, next to rbac's
# roles.json — both are sudo-managed and git-ignored. (parents[2] because this
# module sits two levels below the root, in bot/services/.)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
LEARNED_FILE = DATA_DIR / "learned.json"

_lock = threading.Lock()

# Guards: a learned rule must never be able to produce an absurd price.
_MAX_SCALED_PRICE = 10 ** 12
_MAX_RULES = 200
_MAX_CORRECTIONS = 300

_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    """One learned, human-readable generalization."""

    kind: str                      # "price_scale" | "term"
    key: str                       # digits (price_scale) or the wrong term
    value: str                     # multiplier (price_scale) or the correct term
    example: str = ""              # the correction it was learned from
    hits: int = 0                  # how often it has been applied since
    created: float = field(default_factory=time.time)

    @property
    def rule_id(self) -> str:
        return f"{self.kind}:{self.key}"

    def describe(self) -> str:
        if self.kind == "price_scale":
            return (
                f"💰 عدد {self.key} رقمیِ بدون پسوند = ×{int(self.value):,} "
                f"(نمونه: {self.example})"
            )
        return f"🔤 «{self.key}» ← «{self.value}» (نمونه: {self.example})"


@dataclass
class Correction:
    """A raw record of one correction, kept even when no rule was inferred."""

    field: str
    old: str
    new: str
    rule: str = ""                 # rule_id when a rule was learned from it
    created: float = field(default_factory=time.time)

    def describe(self) -> str:
        label = {
            "price": "قیمت", "prices": "قیمت گروهی", "title": "عنوان",
            "sku_prefix": "پیشوند SKU", "attributes": "ویژگی",
        }.get(self.field, self.field)
        arrow = f" ← قاعده: {self.rule}" if self.rule else " (بدون قاعدهٔ قابل تعمیم)"
        return f"{label}: «{self.old}» ← «{self.new}»{arrow}"


@dataclass
class Memory:
    rules: dict[str, Rule] = field(default_factory=dict)
    corrections: list[Correction] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "rules": [asdict(rule) for rule in self.rules.values()],
            "corrections": [asdict(item) for item in self.corrections],
        }

    @classmethod
    def from_json(cls, data: Any) -> "Memory":
        memory = cls()
        if not isinstance(data, dict):
            return memory
        for item in data.get("rules") or []:
            if not isinstance(item, dict):
                continue
            try:
                rule = Rule(
                    kind=str(item.get("kind", "")),
                    key=str(item.get("key", "")),
                    value=str(item.get("value", "")),
                    example=str(item.get("example", "")),
                    hits=int(item.get("hits", 0) or 0),
                    created=float(item.get("created", 0) or time.time()),
                )
            except (TypeError, ValueError):
                continue
            if rule.kind and rule.key and rule.value:
                memory.rules[rule.rule_id] = rule
        for item in data.get("corrections") or []:
            if not isinstance(item, dict):
                continue
            memory.corrections.append(
                Correction(
                    field=str(item.get("field", "")),
                    old=str(item.get("old", "")),
                    new=str(item.get("new", "")),
                    rule=str(item.get("rule", "")),
                    created=float(item.get("created", 0) or time.time()),
                )
            )
        return memory


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _read_disk() -> Memory:
    try:
        if LEARNED_FILE.exists():
            return Memory.from_json(json.loads(LEARNED_FILE.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        logger.exception("Could not read learned memory %s", LEARNED_FILE)
    return Memory()


# ``price_multiplier`` and ``apply_terms`` run inside the price parser, i.e. once
# per line of every product — they must not touch the disk each time. The cache
# is keyed on (path, mtime) so an external edit, or a test monkeypatching
# LEARNED_FILE, invalidates it automatically.
_CACHE: Memory | None = None
_CACHE_KEY: tuple[str, float] | None = None


def load() -> Memory:
    """The current memory, shared and cached. Mutate it, then call ``_write``."""
    global _CACHE, _CACHE_KEY
    try:
        mtime = LEARNED_FILE.stat().st_mtime if LEARNED_FILE.exists() else -1.0
    except OSError:
        mtime = -1.0
    key = (str(LEARNED_FILE), mtime)
    if _CACHE is None or _CACHE_KEY != key:
        _CACHE = _read_disk()
        _CACHE_KEY = key
    return _CACHE


def _write(memory: Memory) -> None:
    """Persist and refresh the cache so the next ``load`` sees our own write."""
    global _CACHE, _CACHE_KEY
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LEARNED_FILE.write_text(
            json.dumps(memory.to_json(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logger.exception("Could not write learned memory %s", LEARNED_FILE)
        return
    try:
        mtime = LEARNED_FILE.stat().st_mtime
    except OSError:
        mtime = -1.0
    _CACHE = memory
    _CACHE_KEY = (str(LEARNED_FILE), mtime)


def _put_rule(memory: Memory, rule: Rule) -> bool:
    """Insert/refresh a rule. Returns True when something changed."""
    existing = memory.rules.get(rule.rule_id)
    if existing and existing.value == rule.value and existing.example == rule.example:
        return False
    if existing:
        rule.hits = existing.hits          # keep the usage counter
        rule.created = existing.created
    memory.rules[rule.rule_id] = rule
    # Bound the store: drop the oldest low-usage rules first.
    if len(memory.rules) > _MAX_RULES:
        oldest = sorted(memory.rules.values(), key=lambda r: (r.hits, r.created))
        for stale in oldest[: len(memory.rules) - _MAX_RULES]:
            memory.rules.pop(stale.rule_id, None)
    return True


def _log_correction(memory: Memory, correction: Correction) -> None:
    memory.corrections.append(correction)
    del memory.corrections[:-_MAX_CORRECTIONS]


# ---------------------------------------------------------------------------
# Rule application
# ---------------------------------------------------------------------------

def price_multiplier(raw_digits: int, has_suffix: bool) -> int:
    """The learned thousands-multiplier for a bare amount, else 1.

    Only bare numbers are scaled: an explicit suffix («۱۰۹۸ هزار», «1098t») is
    already unambiguous and must not be scaled twice.
    """
    if has_suffix or raw_digits <= 0:
        return 1
    with _lock:
        rule = load().rules.get(f"price_scale:{raw_digits}")
        if not rule:
            return 1
        try:
            multiplier = int(rule.value)
        except ValueError:
            return 1
        if multiplier <= 1:
            return 1
        # Hits are a usage stat, not state anyone branches on: they stay in the
        # in-memory cache and reach disk with the next rule change. Writing here
        # would mean one file write per parsed price line.
        rule.hits += 1
    return multiplier


def scaled_price_is_sane(value: int) -> bool:
    return 0 < value <= _MAX_SCALED_PRICE


def apply_terms(text: str) -> str:
    """Replace learned wrong terms with their corrections inside ``text``."""
    if not text:
        return text
    with _lock:
        terms = [
            (rule.key, rule.value)
            for rule in load().rules.values()
            if rule.kind == "term"
        ]
    if not terms:
        return text
    out = text
    matched: list[str] = []
    for wrong, right in terms:
        # Word-boundary replacement so a learned term never edits the inside of
        # a longer word. Persian has no \w boundaries for ZWNJ-joined forms, so
        # match on whitespace/punctuation edges explicitly.
        pattern = re.compile(
            r"(?<![\w\u0600-\u06FF])" + re.escape(wrong) + r"(?![\w\u0600-\u06FF])"
        )
        out, count = pattern.subn(right, out)
        if count:
            matched.append(wrong)
    if matched:
        # Same reasoning as price_multiplier: a usage counter is not worth a
        # disk write on the hot path.
        with _lock:
            memory = load()
            for wrong in matched:
                rule = memory.rules.get(f"term:{wrong}")
                if rule:
                    rule.hits += 1
    return out


def apply_terms_to_values(values: list[str]) -> list[str]:
    """Apply learned term rules to a list of attribute values, de-duplicated."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        fixed = apply_terms(str(value))
        if fixed and fixed not in seen:
            seen.add(fixed)
            out.append(fixed)
    return out


def describe_rules(limit: int = 0) -> str:
    """Human-readable list of everything the bot currently remembers."""
    with _lock:
        memory = load()
        rules = list(memory.rules.values())
    if not rules:
        return "🧠 هنوز چیزی یاد نگرفته‌ام."
    rules.sort(key=lambda rule: (rule.kind, rule.created))
    lines = [f"🧠 <b>{len(rules)} قاعدهٔ یادگرفته‌شده</b>", ""]
    for rule in (rules[:limit] if limit else rules):
        usage = f" — {rule.hits} بار اعمال شده" if rule.hits else ""
        lines.append(f"• {rule.describe()}{usage}")
    if limit and len(rules) > limit:
        lines.append(f"… و {len(rules) - limit} قاعدهٔ دیگر")
    return "\n".join(lines)


def short_id(rule_id: str) -> str:
    """A short, stable, callback-safe handle for a rule.

    Telegram caps ``callback_data`` at 64 bytes, and a term rule's id embeds an
    arbitrary Persian word (2 bytes per character in UTF-8), so the id itself
    cannot go on the button. A 4-byte digest is 8 ASCII characters and, with at
    most ``_MAX_RULES`` rules in play, a collision is not a practical concern —
    and a miss simply means the button reports "not found" instead of deleting
    the wrong rule.
    """
    return hashlib.blake2b(rule_id.encode("utf-8"), digest_size=4).hexdigest()


def rule_by_short_id(sid: str) -> Rule | None:
    with _lock:
        for rule in load().rules.values():
            if short_id(rule.rule_id) == sid:
                return rule
    return None


def rules_sorted() -> list[Rule]:
    with _lock:
        return sorted(load().rules.values(), key=lambda item: (item.kind, item.created))


# ---------------------------------------------------------------------------
# Rule inference
# ---------------------------------------------------------------------------

def _bare_number_equal_to(text: str, target: int) -> str | None:
    """Find the bare numeric token in ``text`` whose literal value is ``target``.

    Returns the token as written, so the rule can be keyed on its digit count
    («1098» -> 4 digits) rather than on the parsed value.
    """
    if target <= 0:
        return None
    for match in re.finditer(r"(?<![\d.])([۰-۹٠-٩\d][۰-۹٠-٩\d,،.]*)(?![\d.])", text or ""):
        token = match.group(1)
        raw = token.translate(_DIGITS).replace(",", "").replace("،", "").replace(".", "")
        if not raw.isdigit():
            continue
        # A suffix right after the number makes it unambiguous — not our case.
        tail = (text or "")[match.end():match.end() + 8]
        if re.match(r"\s*(?:تومان|تومن|هزار|میلیون|ت|t|k)\b", tail, re.I):
            continue
        try:
            if int(raw) == target:
                return raw
        except ValueError:
            continue
    return None


def _stated_amounts(text: str) -> set[int]:
    """Every amount ``text`` plainly states, in tomans.

    Deliberately small and dependency-free (this module must not import the
    parser, which imports it): it only has to answer "did the owner actually
    write the corrected number?", not reproduce every parsing rule.
    """
    source = text or ""
    out: set[int] = set()
    for match in re.finditer(
        r"(?<![\d.])([۰-۹٠-٩\d][۰-۹٠-٩\d,،.]*)\s*(میلیارد|میلیون|هزار|تومان|تومن|ت|k|t)?",
        source,
        re.I,
    ):
        raw = match.group(1).translate(_DIGITS).replace(",", "").replace("،", "").replace(".", "")
        if not raw.isdigit():
            continue
        value = int(raw)
        suffix = (match.group(2) or "").casefold()
        out.add(value)
        if suffix in {"هزار", "ت", "k", "t"}:
            out.add(value * 1000)
        elif suffix == "میلیون":
            out.add(value * 10 ** 6)
        elif suffix == "میلیارد":
            out.add(value * 10 ** 9)
    # Compound unit expressions: «۱ میلیون و ۹۸ هزار تومان» = 1,098,000.
    total = 0
    for word, factor in (("میلیارد", 10 ** 9), ("میلیون", 10 ** 6), ("هزار", 10 ** 3)):
        for match in re.finditer(r"(\d[\d.]*)\s*" + word, source):
            try:
                total += int(float(match.group(1).rstrip(".")) * factor)
            except ValueError:
                continue
    if total:
        out.add(total)
    return out


def infer_price_scale(old: int, new: int, source_text: str) -> Rule | None:
    """Learn a digit-length -> multiplier rule from a price correction.

    Three guards, because a wrong scale rule silently corrupts every later
    product:

    * the corrected value must be exactly the misread one times a power of ten
      (so «698000» -> «750000», an ordinary price change, never becomes a rule);
    * the misread value must appear in the source as a *bare* number, which is
      what makes it ambiguous and therefore worth a rule keyed on its length;
    * the corrected value must actually be stated in the source, so a parser or
      model that merely changed its mind cannot teach the bot anything.
    """
    if old <= 0 or new <= 0 or new == old or new % old != 0:
        return None
    ratio = new // old
    if ratio <= 1 or re.fullmatch(r"10+", str(ratio)) is None:
        return None
    if new not in _stated_amounts(source_text):
        return None
    raw = _bare_number_equal_to(source_text, old)
    if not raw:
        return None
    digits = len(raw)
    if not 1 <= digits <= 15:
        return None
    if not scaled_price_is_sane(old * ratio):
        return None
    return Rule(
        kind="price_scale",
        key=str(digits),
        value=str(ratio),
        example=f"{raw} ← {new:,}",
    )


def infer_token_substitution(old: str, new: str) -> Rule | None:
    """Learn a word-level replacement when two strings differ in ONE local spot.

    Used for titles and free text: «Air skin» -> «Airskin», «قاب سیلیکن» ->
    «قاب سیلیکونی». Anchoring on a shared prefix/suffix is what keeps this safe:

    * a wholesale rewrite shares no anchor, so it is not a generalizable term;
    * an insertion («قاب آیفون» -> «قاب محافظ آیفون ۱۷») has one side contained
      in the other, i.e. the owner added information rather than renaming a word;
    * a pure number swap is a value change, not a spelling rule.
    """
    old_tokens = (old or "").split()
    new_tokens = (new or "").split()
    if not old_tokens or not new_tokens or old_tokens == new_tokens:
        return None

    limit = min(len(old_tokens), len(new_tokens))
    prefix = 0
    while prefix < limit and old_tokens[prefix] == new_tokens[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old_tokens[len(old_tokens) - 1 - suffix] == new_tokens[len(new_tokens) - 1 - suffix]
    ):
        suffix += 1
    # At least one token of shared context, otherwise it is a different string
    # entirely and no term can be inferred from it.
    if prefix + suffix == 0:
        return None

    wrong = " ".join(old_tokens[prefix: len(old_tokens) - suffix]).strip(".,:;!?،؛")
    right = " ".join(new_tokens[prefix: len(new_tokens) - suffix]).strip(".,:;!?،؛")
    if not wrong or not right or wrong == right:
        return None
    # An expansion/contraction of the same words is added detail, not a rename.
    if wrong in right or right in wrong:
        return None
    if wrong.translate(_DIGITS).isdigit() and right.translate(_DIGITS).isdigit():
        return None
    return Rule(kind="term", key=wrong, value=right, example=f"{old} ← {new}")


def infer_value_substitution(old_values: list[str], new_values: list[str]) -> Rule | None:
    """Learn a term rule when one attribute value was swapped for another.

    Requires the rest of the list to be unchanged, so a genuinely different
    product (a new color list) is logged but never turned into a rule.
    """
    old_list = [str(v) for v in old_values or []]
    new_list = [str(v) for v in new_values or []]
    if not old_list or old_list == new_list or len(old_list) != len(new_list):
        return None
    removed = [v for v in old_list if v not in new_list]
    added = [v for v in new_list if v not in old_list]
    if len(removed) != 1 or len(added) != 1:
        return None
    wrong, right = removed[0].strip(), added[0].strip()
    if not wrong or not right or wrong == right:
        return None
    return Rule(kind="term", key=wrong, value=right, example=f"{wrong} ← {right}")


# ---------------------------------------------------------------------------
# Recording (called by the product flow)
# ---------------------------------------------------------------------------

def remember(rule: Rule | None, correction: Correction) -> bool:
    """Persist a correction (and its rule when one could be inferred).

    Returns True when a new/changed rule was stored, so the caller only
    announces «یاد گرفتم» when something was actually learned.
    """
    with _lock:
        memory = load()
        changed = False
        if rule is not None:
            correction.rule = rule.rule_id
            changed = _put_rule(memory, rule)
        _log_correction(memory, correction)
        _write(memory)
    if rule is not None:
        logger.info("Learned rule %s from %r -> %r", rule.rule_id, correction.old, correction.new)
    return changed


def delete_rule(rule_id: str) -> bool:
    with _lock:
        memory = load()
        if rule_id not in memory.rules:
            return False
        memory.rules.pop(rule_id, None)
        _write(memory)
    logger.info("Deleted learned rule %s", rule_id)
    return True


def clear_rules() -> int:
    with _lock:
        memory = load()
        count = len(memory.rules)
        memory.rules.clear()
        _write(memory)
    logger.info("Cleared %d learned rules", count)
    return count


def recent_corrections(limit: int = 8) -> list[Correction]:
    with _lock:
        memory = load()
        return list(reversed(memory.corrections[-limit:]))


def rules_for_prompt() -> str:
    """A compact prompt fragment so the AI honors the same learned rules."""
    with _lock:
        memory = load()
        rules = list(memory.rules.values())
    if not rules:
        return ""
    parts = []
    for rule in sorted(rules, key=lambda item: (item.kind, item.key)):
        if rule.kind == "price_scale":
            parts.append(
                f"A bare {rule.key}-digit price number with no suffix means "
                f"x{rule.value} (e.g. {rule.example})."
            )
        else:
            parts.append(f"Always write «{rule.key}» as «{rule.value}».")
    return "LEARNED OWNER RULES (apply them exactly):\n" + "\n".join(parts)


__all__ = [
    "Correction",
    "Memory",
    "Rule",
    "apply_terms",
    "apply_terms_to_values",
    "clear_rules",
    "delete_rule",
    "describe_rules",
    "infer_price_scale",
    "infer_token_substitution",
    "infer_value_substitution",
    "load",
    "price_multiplier",
    "recent_corrections",
    "remember",
    "rule_by_short_id",
    "rules_for_prompt",
    "rules_sorted",
    "scaled_price_is_sane",
    "short_id",
]
