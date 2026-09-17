"""Tests for self-learning from the owner's corrections.

Covers the three layers of the feature:

1. ``bot.services.learning`` — rule inference, persistence, application.
2. ``bot.services.product_extractor`` — the parser honoring learned scales,
   Persian unit words, and corrections (the newest line wins).
3. ``bot.modules.product_flow._learn_from_diff`` — the wiring that turns a
   correction into a rule.

Run with either::

    python3 -m unittest discover -s tests
    pytest tests

Only the standard library is required, except the flow-integration test which
needs python-telegram-bot and skips itself when it is missing.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1")
os.environ.setdefault("WOOCOMMERCE_URL", "https://example.test")
os.environ.setdefault("WOOCOMMERCE_KEY", "ck_test")
os.environ.setdefault("WOOCOMMERCE_SECRET", "cs_test")

from bot.services import learning

try:  # the parser tests need httpx (product_extractor imports it at module level)
    from bot.services.product_extractor import (
        ProductData,
        _fallback,
        _number_from_line,
        _scan_prices,
    )
    HAS_EXTRACTOR = True
except Exception:  # pragma: no cover
    HAS_EXTRACTOR = False

try:  # the flow-integration test needs python-telegram-bot
    from bot.modules.product_flow import _learn_from_diff
    HAS_TELEGRAM = True
except Exception:  # pragma: no cover
    HAS_TELEGRAM = False

needs_extractor = unittest.skipUnless(HAS_EXTRACTOR, "httpx is not installed")
needs_telegram = unittest.skipUnless(
    HAS_TELEGRAM and HAS_EXTRACTOR, "python-telegram-bot or httpx is not installed"
)


class IsolatedMemory(unittest.TestCase):
    """Point the memory at a temp file so tests never touch data/learned.json."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        data_dir = Path(self._tmp.name)
        learning.DATA_DIR = data_dir
        learning.LEARNED_FILE = data_dir / "learned.json"
        # The cache is keyed on (path, mtime); a new path invalidates it.
        learning._CACHE = None
        learning._CACHE_KEY = None

    def tearDown(self) -> None:
        learning._CACHE = None
        learning._CACHE_KEY = None


# ---------------------------------------------------------------------------
# Baseline price parsing (no rules learned yet)
# ---------------------------------------------------------------------------

@needs_extractor
class TestPriceBaseline(IsolatedMemory):
    def test_bare_four_digit_is_not_scaled_by_default(self):
        # The pre-learning behaviour: only 3-digit bare amounts were assumed to
        # be thousands. This is the misunderstanding the owner had to correct.
        self.assertEqual(_number_from_line("1098"), 1098)

    def test_bare_three_digit_is_thousands(self):
        self.assertEqual(_number_from_line("698"), 698_000)
        self.assertEqual(_number_from_line("قیمت: 758"), 758_000)

    def test_full_amount_is_literal(self):
        self.assertEqual(_number_from_line("1098000"), 1_098_000)
        self.assertEqual(_number_from_line("قیمت 1098000 تومان"), 1_098_000)

    def test_thousands_suffix(self):
        self.assertEqual(_number_from_line("قیمت 768t"), 768_000)
        self.assertEqual(_number_from_line("قیمت 768 هزار"), 768_000)

    def test_persian_digits(self):
        self.assertEqual(_number_from_line("قیمت: ۱۰۹۸۰۰۰"), 1_098_000)


@needs_extractor
class TestPersianUnitWords(IsolatedMemory):
    """«۱ میلیون و ۹۸ هزار تومان» used to parse as 1 — the correction itself failed."""

    def test_million_and_thousand_compound(self):
        self.assertEqual(_number_from_line("1 میلیون و 98 هزار تومان"), 1_098_000)
        self.assertEqual(_number_from_line("۱ میلیون و ۹۸ هزار"), 1_098_000)

    def test_million_alone(self):
        self.assertEqual(_number_from_line("قیمت 2 میلیون"), 2_000_000)

    def test_fractional_million(self):
        self.assertEqual(_number_from_line("1.5 میلیون تومان"), 1_500_000)

    def test_thousands_with_tail(self):
        self.assertEqual(_number_from_line("3 هزار و 500"), 3_500)

    def test_no_unit_words_is_untouched(self):
        self.assertEqual(_number_from_line("کد 1098"), 1098)

    def test_repeated_unit_means_two_amounts_not_a_sum(self):
        # «۶۹۸ هزار و ۵۹۸ هزار» on one line is an iPhone price and an Android
        # price — summing them would invent 1,296,000 that nobody wrote. The
        # unit path bows out and the first amount is parsed as before.
        self.assertEqual(_number_from_line("698 هزار و 598 هزار"), 698_000)
        self.assertNotEqual(_number_from_line("698 هزار و 598 هزار"), 1_296_000)


# ---------------------------------------------------------------------------
# Corrections must actually take effect
# ---------------------------------------------------------------------------

@needs_extractor
class TestCorrectionTakesEffect(IsolatedMemory):
    """Before the recency fix the FIRST price won, so a correction did nothing."""

    def test_latest_line_wins_inside_a_block(self):
        lines = ["1098", "قیمت 1098000 تومان"]
        price = _scan_prices(lines).price
        self.assertEqual(price, 1_098_000)

    def test_accumulated_info_text_yields_the_corrected_price(self):
        info = "1098\nقیمت 1098000 تومان"
        self.assertEqual(_fallback(info, [], price_blocks=[info, ""]).price, 1_098_000)

    def test_bare_correction_alone_works(self):
        info = "قیمت: 1098000\n1098000"
        self.assertEqual(_fallback(info, [], price_blocks=[info, ""]).price, 1_098_000)

    def test_info_block_beats_caption_block(self):
        # PRODUCT INFO stays authoritative: a caption amount must never win.
        price = _fallback(
            "", [], price_blocks=["قیمت: 500000", "قیمت: 900000"]
        ).price
        self.assertEqual(price, 500_000)

    def test_group_prices_keep_info_priority(self):
        data = _fallback(
            "",
            [],
            price_blocks=["698000 ایفون", "750000 ایفون"],
        )
        self.assertEqual(data.prices.get("iphone"), 698_000)

    def test_explicit_price_not_overridden_by_later_bare_number(self):
        # A trailing SKU/code line must not repaint a stated price.
        price = _scan_prices(["قیمت: 698000", "کد 1098"]).price
        self.assertEqual(price, 698_000)


# ---------------------------------------------------------------------------
# Rule inference
# ---------------------------------------------------------------------------

@needs_extractor
class TestInferPriceScale(IsolatedMemory):
    SOURCE = "1098\nقیمت 1098000 تومان"

    def test_learns_digit_count_rule(self):
        rule = learning.infer_price_scale(1098, 1_098_000, self.SOURCE)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.kind, "price_scale")
        self.assertEqual(rule.key, "4")       # keyed on digits, not on the value
        self.assertEqual(rule.value, "1000")

    def test_generalizes_to_another_number(self):
        # The point of the feature: 1198 was never mentioned by anyone.
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, self.SOURCE),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        self.assertEqual(_number_from_line("1198"), 1_198_000)
        self.assertEqual(_number_from_line("قیمت: 1298"), 1_298_000)

    def test_rule_does_not_touch_other_digit_counts(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, self.SOURCE),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        self.assertEqual(_number_from_line("1098000"), 1_098_000)   # 7 digits
        self.assertEqual(_number_from_line("698"), 698_000)         # built-in

    def test_explicit_toman_suffix_is_never_scaled(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, self.SOURCE),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        self.assertEqual(_number_from_line("1098 تومان"), 1098)

    def test_rejects_plain_price_change(self):
        # 698000 -> 750000 is a new price, not a misunderstanding of scale.
        self.assertIsNone(
            learning.infer_price_scale(698_000, 750_000, "قیمت: 750000 تومان")
        )

    def test_rejects_when_correct_value_is_absent_from_source(self):
        # Guards against learning from an AI that merely changed its mind.
        self.assertIsNone(learning.infer_price_scale(1098, 1_098_000, "1098"))

    def test_rejects_zero_and_equal(self):
        self.assertIsNone(learning.infer_price_scale(0, 1_098_000, "1098"))
        self.assertIsNone(learning.infer_price_scale(1098, 1098, "1098"))

    def test_rejects_scale_down(self):
        self.assertIsNone(learning.infer_price_scale(1_098_000, 1098, "1098"))


class TestInferTerms(IsolatedMemory):
    def test_token_substitution_in_title(self):
        rule = learning.infer_token_substitution(
            "قاب Air skin آیفون 17", "قاب Airskin آیفون 17"
        )
        self.assertIsNotNone(rule)
        self.assertEqual((rule.kind, rule.key, rule.value), ("term", "Air skin", "Airskin"))

    def test_wholesale_rewrite_is_not_a_rule(self):
        self.assertIsNone(
            learning.infer_token_substitution("قاب ژله ای سامسونگ", "کاور محافظ شیائومی")
        )

    def test_different_token_count_is_not_a_rule(self):
        self.assertIsNone(learning.infer_token_substitution("قاب آیفون", "قاب محافظ آیفون ۱۷"))

    def test_pure_number_swap_is_not_a_term_rule(self):
        self.assertIsNone(learning.infer_token_substitution("قاب 1098", "قاب 1298"))

    def test_value_substitution_in_attribute(self):
        rule = learning.infer_value_substitution(
            ["سفید", "سلفی"], ["سفید", "مشکی"]
        )
        self.assertIsNotNone(rule)
        self.assertEqual((rule.key, rule.value), ("سلفی", "مشکی"))

    def test_added_color_is_not_a_rule(self):
        self.assertIsNone(
            learning.infer_value_substitution(["سفید"], ["سفید", "مشکی"])
        )

    def test_two_changes_are_not_a_rule(self):
        self.assertIsNone(
            learning.infer_value_substitution(["سفید", "سلفی"], ["مشکی", "نقره‌ای"])
        )


# ---------------------------------------------------------------------------
# Applying learned term rules
# ---------------------------------------------------------------------------

class TestApplyTerms(IsolatedMemory):
    def _learn(self, wrong: str, right: str) -> None:
        learning.remember(
            learning.Rule(kind="term", key=wrong, value=right, example=f"{wrong} ← {right}"),
            learning.Correction(field="attributes", old=wrong, new=right),
        )

    def test_replaces_attribute_value(self):
        self._learn("سلفی", "مشکی")
        self.assertEqual(learning.apply_terms_to_values(["سفید", "سلفی"]), ["سفید", "مشکی"])

    def test_deduplicates_after_substitution(self):
        self._learn("سلفی", "مشکی")
        self.assertEqual(learning.apply_terms_to_values(["مشکی", "سلفی"]), ["مشکی"])

    def test_word_boundary_is_respected(self):
        # A learned term must never edit the inside of a longer word.
        self._learn("Air skin", "Airskin")
        self.assertEqual(learning.apply_terms("Air skin case"), "Airskin case")
        self.assertEqual(learning.apply_terms("Airskin case"), "Airskin case")

    def test_no_rules_is_identity(self):
        self.assertEqual(learning.apply_terms("قاب سفید"), "قاب سفید")
        self.assertEqual(learning.apply_terms_to_values(["سفید"]), ["سفید"])


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

@needs_extractor
class TestPersistence(IsolatedMemory):
    def test_rule_survives_a_reload(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, "1098\nقیمت 1098000 تومان"),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        # Drop the in-process cache to force a real read from disk.
        learning._CACHE = None
        learning._CACHE_KEY = None
        self.assertEqual(_number_from_line("1198"), 1_198_000)

    def test_file_is_valid_json_and_utf8(self):
        import json

        learning.remember(
            learning.Rule(kind="term", key="سلفی", value="مشکی", example="x"),
            learning.Correction(field="attributes", old="سلفی", new="مشکی"),
        )
        payload = json.loads(learning.LEARNED_FILE.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["rules"][0]["key"], "سلفی")

    def test_delete_rule(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, "1098\nقیمت 1098000 تومان"),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        self.assertTrue(learning.delete_rule("price_scale:4"))
        self.assertEqual(_number_from_line("1098"), 1098)
        self.assertFalse(learning.delete_rule("price_scale:4"))

    def test_clear_rules_keeps_correction_log(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, "1098\nقیمت 1098000 تومان"),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        self.assertEqual(learning.clear_rules(), 1)
        self.assertEqual(learning.rules_sorted(), [])
        self.assertEqual(len(learning.recent_corrections()), 1)

    def test_short_id_round_trip(self):
        learning.remember(
            learning.Rule(kind="term", key="سلفی", value="مشکی", example="x"),
            learning.Correction(field="attributes", old="سلفی", new="مشکی"),
        )
        sid = learning.short_id("term:سلفی")
        self.assertLessEqual(len(f"learning:delete:{sid}".encode()), 64)
        rule = learning.rule_by_short_id(sid)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.rule_id, "term:سلفی")
        self.assertIsNone(learning.rule_by_short_id("deadbeef"))

    def test_correction_log_is_bounded(self):
        for index in range(learning._MAX_CORRECTIONS + 50):
            learning.remember(None, learning.Correction(field="price", old=str(index), new="0"))
        self.assertLessEqual(len(learning.recent_corrections(limit=1000)), learning._MAX_CORRECTIONS)

    def test_prompt_fragment_mentions_the_rule(self):
        learning.remember(
            learning.infer_price_scale(1098, 1_098_000, "1098\nقیمت 1098000 تومان"),
            learning.Correction(field="price", old="1098", new="1098000"),
        )
        prompt = learning.rules_for_prompt()
        self.assertIn("4-digit", prompt)
        self.assertIn("x1000", prompt)
        self.assertEqual(learning.rules_for_prompt(), prompt)

    def test_prompt_fragment_empty_without_rules(self):
        self.assertEqual(learning.rules_for_prompt(), "")


# ---------------------------------------------------------------------------
# End-to-end: the flow wiring
# ---------------------------------------------------------------------------

@needs_telegram
class TestFlowIntegration(IsolatedMemory):
    """The acceptance scenario the owner described, end to end."""

    def test_correction_is_learned_and_generalizes(self):
        previous = ProductData(title="قاب آیفون ۱۷", price=1098, models=["iPhone 17"])
        current = ProductData(title="قاب آیفون ۱۷", price=1_098_000, models=["iPhone 17"])
        incoming = "قیمت 1098000 تومان"
        source = f"1098\n{incoming}"

        notes = _learn_from_diff(previous, current, incoming, source)

        self.assertEqual(len(notes), 1)
        self.assertIn("یاد گرفتم", notes[0])
        self.assertEqual([rule.rule_id for rule in learning.rules_sorted()], ["price_scale:4"])
        # A brand-new product, never mentioned before, is now read correctly.
        self.assertEqual(_number_from_line("1198"), 1_198_000)

    def test_first_message_is_never_a_correction(self):
        # session.data is None before the first extraction; _learn_from_diff must
        # not blow up on it (it runs on every incoming text message).
        self.assertEqual(
            _learn_from_diff(None, ProductData(price=1098), "1098", "1098"), []
        )

    def test_no_note_when_price_did_not_change(self):
        same = ProductData(price=1_098_000)
        self.assertEqual(_learn_from_diff(same, ProductData(price=1_098_000), "1098000", "1098000"), [])

    def test_no_note_when_value_was_not_stated_in_the_message(self):
        previous = ProductData(price=1098)
        current = ProductData(price=1_098_000)
        # The new price is nowhere in the incoming text -> not attributable to
        # this message, so nothing is learned.
        self.assertEqual(_learn_from_diff(previous, current, "سلام", "1098"), [])

    def test_plain_price_change_is_logged_but_not_generalized(self):
        previous = ProductData(price=698_000)
        current = ProductData(price=750_000)
        notes = _learn_from_diff(previous, current, "قیمت 750000 تومان", "قیمت 750000 تومان")
        self.assertEqual(notes, [])                       # nothing announced
        self.assertEqual(learning.rules_sorted(), [])     # nothing generalized
        self.assertEqual(len(learning.recent_corrections()), 1)   # but recorded

    def test_term_correction_is_learned(self):
        previous = ProductData(title="قاب آیفون", attributes={"رنگ": ["سفید", "سلفی"]})
        current = ProductData(title="قاب آیفون", attributes={"رنگ": ["سفید", "مشکی"]})
        notes = _learn_from_diff(previous, current, "مشکی نه سلفی", "مشکی نه سلفی")
        self.assertEqual(len(notes), 1)
        self.assertEqual([rule.rule_id for rule in learning.rules_sorted()], ["term:سلفی"])

    def test_sku_prefix_change_is_logged_without_a_rule(self):
        previous = ProductData(sku_prefix="AS")
        current = ProductData(sku_prefix="TS")
        notes = _learn_from_diff(previous, current, "TS", "TS")
        self.assertEqual(notes, [])
        self.assertEqual(learning.rules_sorted(), [])
        self.assertEqual(len(learning.recent_corrections()), 1)

    def test_learning_twice_announces_once(self):
        previous = ProductData(price=1098)
        current = ProductData(price=1_098_000)
        args = (previous, current, "قیمت 1098000 تومان", "1098\nقیمت 1098000 تومان")
        self.assertEqual(len(_learn_from_diff(*args)), 1)
        self.assertEqual(_learn_from_diff(*args), [])   # same rule -> no new announcement


if __name__ == "__main__":
    unittest.main()
