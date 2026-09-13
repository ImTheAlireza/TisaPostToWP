# -*- coding: utf-8 -*-
"""Regression tests for model extraction on lines that are not model lines.

The trigger was a price: inside an Apple section («Apple» / «iPhone:» header),
every following line is treated as a model list, so a bare amount such as
«1098» matched the 2-digit prefix «10» and invented an iPhone 10 — an extra
model with its own variations on a product that never had it.

Run with either::

    python3 -m unittest discover -s tests
    pytest tests
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1")

from bot.services.phone_parser import extract_iphone_models, normalize_caption  # noqa: E402

APPLE_SECTION = """Apple
📱17promax :
مشکی
📱17pro :
مشکی"""


def _labels(text: str) -> list[str]:
    return [model.label for model in extract_iphone_models(text)]


class TestBareAmountsAreNotModels(unittest.TestCase):
    def test_four_digit_amount_does_not_create_a_model(self):
        self.assertEqual(_labels(APPLE_SECTION), ["iPhone 17 Pro Max", "iPhone 17 Pro"])
        self.assertEqual(
            _labels(f"{APPLE_SECTION}\n1098"), ["iPhone 17 Pro Max", "iPhone 17 Pro"]
        )

    def test_amount_with_price_word_does_not_create_a_model(self):
        self.assertEqual(
            _labels(f"{APPLE_SECTION}\nقیمت: 1098"), ["iPhone 17 Pro Max", "iPhone 17 Pro"]
        )
        self.assertEqual(
            _labels(f"{APPLE_SECTION}\n1098000 تومان"),
            ["iPhone 17 Pro Max", "iPhone 17 Pro"],
        )

    def test_sku_prefix_and_title_lines_are_not_models(self):
        self.assertEqual(
            _labels(f"{APPLE_SECTION}\nAS\nقاب سیلیکونی آیفون"),
            ["iPhone 17 Pro Max", "iPhone 17 Pro"],
        )

    def test_normalize_caption_end_to_end(self):
        self.assertEqual(
            normalize_caption(f"{APPLE_SECTION}\nAS\n1098"),
            "iPhone 17 Pro | iPhone 17 Pro Max",
        )


class TestRealModelLinesStillWork(unittest.TestCase):
    """The fix only narrows matching, so genuine model lines must be untouched."""

    def test_bare_generation_in_apple_section(self):
        self.assertEqual(_labels("Apple\n10"), ["iPhone 10"])
        self.assertEqual(_labels("Apple\n17"), ["iPhone 17"])

    def test_variants(self):
        self.assertEqual(
            _labels("Apple\n17promax\n17 pro\n16 Plus\n15 mini\n14 air"),
            [
                "iPhone 17 Pro Max",
                "iPhone 17 Pro",
                "iPhone 16 Plus",
                "iPhone 15 Mini",
                "iPhone 14 Air",
            ],
        )

    def test_slash_groups(self):
        self.assertEqual(_labels("iPhone:\n7/8"), ["iPhone 7/8"])
        self.assertEqual(_labels("iPhone:\n7+/8+"), ["iPhone 7 Plus/8 Plus"])

    def test_roman_generations(self):
        self.assertEqual(_labels("iPhone:\nXSMax"), ["iPhone XS Max"])
        self.assertEqual(_labels("iPhone:\nxr"), ["iPhone XR"])
        self.assertEqual(_labels("iPhone:\nX"), ["iPhone X"])

    def test_known_gap_roman_with_space(self):
        # Pre-existing and unrelated to the bare-amount fix above: the roman
        # alternative only accepts the compact form, so «XS Max» (with a space)
        # is read as «iPhone XS». Documented here so a future fix has to change
        # this test deliberately rather than silently.
        self.assertEqual(_labels("iPhone:\nXS Max"), ["iPhone XS"])

    def test_explicit_iphone_word_with_amount_after(self):
        # «iphone 17» stays a model even when a price follows on the same line.
        self.assertEqual(_labels("iphone 17"), ["iPhone 17"])

    def test_no_iphone_context_means_no_models(self):
        self.assertEqual(_labels("1098\nقیمت 1098000 تومان"), [])


if __name__ == "__main__":
    unittest.main()
