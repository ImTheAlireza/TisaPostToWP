"""The catalog: what may legitimately exist, and what must be warned about.

The AI used to be trusted to know phone lineups. «iPhone 13 Pro Plus» does not
exist, but it reads like one — and a wrong model label is a wrong SKU, a wrong
variation and an unfound product page. The catalog answers «is this a real
variant of this brand?» in one place, and the same table is given to the model.

Stdlib only.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

from bot.services import jsonstore, model_catalog as mc


class TestBrandLookup(unittest.TestCase):
    def test_persian_and_latin_spellings(self):
        for text in ["آیفون 15 پرو مکس", "iPhone 15", "ایفون 15"]:
            self.assertEqual(mc.brand_of(text), "iphone", text)
        self.assertEqual(mc.brand_of("S24 اولترا سامسونگ"), "samsung")

    def test_unknown_brand_is_none(self):
        self.assertIsNone(mc.brand_of("قاب سیلیکونی مات"))


class TestVariants(unittest.TestCase):
    def test_pairs_are_their_own_variant(self):
        self.assertIn("pro plus", mc.variant_words("13 پرو پلاس"))
        self.assertIn("pro max", mc.variant_words("13 پرو مکس"))

    def test_forbidden_variant_per_brand(self):
        self.assertTrue(mc.unsupported_variant("iphone", "ultra"))
        self.assertFalse(mc.unsupported_variant("samsung", "ultra"))
        self.assertTrue(mc.unsupported_variant("iphone", "pro plus"))
        self.assertFalse(mc.unsupported_variant("iphone", "pro max"))

    def test_suspicious_lines_follow_the_section_header(self):
        found = mc.suspicious_lines("آیفون:\n13 پرو پلاس\n15 اولترا\n17 پرو مکس")
        self.assertEqual({(b, w) for b, _l, w in found},
                         {("iphone", "pro plus"), ("iphone", "ultra")})

    def test_a_brand_never_guesses_the_other(self):
        self.assertEqual(mc.suspicious_lines("سامسونگ:\nS24 اولترا\nA54 5G"), [])

    def test_unknown_brand_words_next_to_a_model_number(self):
        found = mc.unknown_brand_words("قاب برای Nubia Z60 و Pocophone X7")
        self.assertEqual(sorted(found), ["Nubia", "Pocophone"])
        self.assertEqual(mc.unknown_brand_words("کاور ضدضربه مات"), [])


class TestPromptBlock(unittest.TestCase):
    def test_only_detected_brands_are_listed(self):
        text = mc.prompt_block(["iPhone 15 Pro Max"])
        self.assertIn("iPhone (key=iphone)", text)
        self.assertNotIn("Xiaomi", text)

    def test_forbidden_list_is_part_of_the_prompt(self):
        text = mc.prompt_block(["iPhone 15"])
        self.assertIn("NEVER = ultra", text)

    def test_empty_models_means_no_catalog(self):
        self.assertEqual(mc.prompt_block([]), "")


class TestShopOverride(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, mc, "DATA_FILE", mc.DATA_FILE)
        mc.DATA_FILE = self.tmp / "model_catalog.json"
        self.addCleanup(mc.invalidate)
        mc.invalidate()
        jsonstore.invalidate()

    def test_catalog_without_the_file_is_the_base_one(self):
        self.assertIsNone(mc.brand_of("قاب نوبیا Z60"))

    def test_added_brand_is_used(self):
        jsonstore.write_json(mc.DATA_FILE, {
            "version": 1,
            "brands": {"nubia": {"label": "Nubia", "words": ["نوبیا", "nubia"],
                                 "variants": ["pro", "ultra"], "forbidden_variants": ["fe"]}},
        })
        mc.invalidate()
        jsonstore.invalidate()
        self.assertEqual(mc.brand_of("قاب نوبیا Z60"), "nubia")
        self.assertTrue(mc.unsupported_variant("nubia", "fe"))

    def test_added_variant_extends_instead_of_replacing(self):
        jsonstore.write_json(mc.DATA_FILE, {"version": 1, "brands": {"iphone": {"variants": ["se"]}}})
        mc.invalidate()
        jsonstore.invalidate()
        self.assertIn("se", mc.known_variants("iphone"))
        self.assertIn("pro max", mc.known_variants("iphone"))

    def test_broken_file_falls_back_to_the_base_catalog(self):
        mc.DATA_FILE.write_text("{oops", encoding="utf-8")
        jsonstore.invalidate()
        mc.invalidate()
        self.assertEqual(mc.brand_of("آیفون 15"), "iphone")


if __name__ == "__main__":
    unittest.main()
