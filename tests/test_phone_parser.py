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

from bot.services.phone_parser import (
    extract_iphone_models,
    extract_phone_models,
    unmatched_model_words,
    normalize_caption,
)

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

    def test_roman_with_space_is_not_silently_downgraded(self):
        # This used to be the documented gap: «XS Max» (with a space) read as
        # «iPhone XS», i.e. a whole model disappeared. The spaced roman form is
        # now folded, so the Max suffix survives.
        self.assertEqual(_labels("iPhone:\nXS Max"), ["iPhone XS Max"])
        self.assertEqual(_labels("iPhone:\nxsmax"), ["iPhone XS Max"])

    def test_persian_variant_words_are_not_merged_into_the_bare_generation(self):
        # «13 پرو مکس» / «13 پرو» / «13» are three sellable models. They used to
        # collapse into one «iPhone 13», deleting variations and mis-pricing the
        # product, and nothing in the bot complained about it.
        self.assertEqual(
            _labels("Apple\n13 پرو مکس\n13 پرو\n13"),
            ["iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13"],
        )
        self.assertEqual(_labels("Apple\n15max"), ["iPhone 15 Pro Max"])
        self.assertEqual(_labels("Apple\n16 پلاس"), ["iPhone 16 Plus"])
        self.assertEqual(_labels("Apple\n15 مینی"), ["iPhone 15 Mini"])

    def test_unapplied_variant_words_are_reported(self):
        # «پرو پلاس» is not an iPhone generation. The parser used to answer with
        # a plausible «iPhone 13 Pro» and drop the rest; now the leftover word is
        # surfaced so a model is never silently replaced by another one.
        flags = unmatched_model_words("Apple\n13 پرو پلاس")
        self.assertTrue(flags, "expected the unread model word to be reported")
        self.assertIn("plus", flags[0][1])

    def test_fully_understood_lines_are_quiet(self):
        self.assertEqual(unmatched_model_words("Apple\n17 Pro Max\n17 promax"), [])
        self.assertEqual(unmatched_model_words("Apple\n13 پرو مکس\n13 پرو"), [])

    def test_persian_variants_do_not_collapse(self):
        self.assertEqual(
            _labels("Apple\n13 پرو مکس\n13 پرو\n13"),
            ["iPhone 13 Pro Max", "iPhone 13 Pro", "iPhone 13"],
        )
        self.assertEqual(_labels("Apple\n15max"), ["iPhone 15 Pro Max"])
        self.assertEqual(_labels("Apple\n16 پلاس"), ["iPhone 16 Plus"])
        self.assertEqual(_labels("Apple\n15 مینی"), ["iPhone 15 Mini"])


class TestPersianBrandWords(unittest.TestCase):
    """«آیفون 13 پرو مکس» alone must be enough — no Latin word required.

    The brand used to be recognized only as ``iphone``/``apple``, so a Persian
    caption yielded no iPhone at all: the model, its colours and every variation
    for it vanished while the post looked handled.
    """

    def test_persian_brand_on_one_line(self):
        self.assertEqual(_labels("آیفون 13 پرو مکس"), ["iPhone 13 Pro Max"])
        self.assertEqual(_labels("ایفون 15 پرو"), ["iPhone 15 Pro"])
        self.assertEqual(_labels("آيفون 16 پرومکس"), ["iPhone 16 Pro Max"])

    def test_persian_brand_inside_prose(self):
        labels = _labels("قاب سیلیکونی آیفون 13 پرو مکس")
        self.assertIn("iPhone 13 Pro Max", labels)

    def test_persian_brand_alone_opens_the_apple_section(self):
        self.assertEqual(_labels("آیفون:\n14 پرو\n14"), ["iPhone 14 Pro", "iPhone 14"])

    def test_persian_apple_word_ends_a_samsung_section(self):
        text = "Samsung\nS24 اولترا\nآیفون 15 پرو مکس"
        labels = [model.label for model in extract_phone_models(text)]
        self.assertIn("iPhone 15 Pro Max", labels)
        self.assertIn("S24 Ultra", labels, "the Samsung model must survive the switch")

    def test_persian_digits_are_still_read(self):
        self.assertEqual(_labels("اپل:\n۱۴ پرو"), ["iPhone 14 Pro"])

    def test_a_number_that_is_not_a_model_is_not_invented(self):
        # «قیمت 1098» inside an Apple section must not become iPhone 10.
        self.assertEqual(_labels("قیمت 1098"), [])


if __name__ == "__main__":
    unittest.main()
