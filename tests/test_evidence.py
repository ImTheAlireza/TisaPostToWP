"""Provenance: every preview field says where it came from.

The bot reads a post from four sources (caption, filenames, OCR, the AI). When
all four produce the same-looking card, a wrong price and a right one are
indistinguishable — so the user has to re-read the whole post. These tests pin
the evidence trail: the quote that produced a value, and the policy decisions
that used to be silent.

Stdlib only: the AI and Telegram are never contacted.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1")

from bot.services import postmodel as ev

try:
    from bot.services.product_extractor import _fallback, _scan_prices
except ImportError:          # the extractor pulls in httpx; stdlib-only runs skip
    _fallback = _scan_prices = None
    _NEEDS_HTTPX = False
else:
    _NEEDS_HTTPX = True

needs_httpx = unittest.skipUnless(_NEEDS_HTTPX, "httpx is not installed")


@needs_httpx
class TestPriceScanEvidence(unittest.TestCase):
    def test_stated_price_carries_the_line_it_came_from(self):
        scan = _scan_prices(["قیمت 698000 تومان"])
        self.assertEqual(scan.price, 698000)
        self.assertEqual(scan.evidence["price"].source, ev.CAPTION)
        self.assertIn("698000", scan.evidence["price"].note)

    def test_group_prices_are_evidenced(self):
        scan = _scan_prices(["قیمت ایفون 698 اندروید 598"])
        self.assertEqual(scan.prices, {"iphone": 698000, "android": 598000})
        self.assertEqual(scan.evidence["prices"].source, ev.CAPTION)

    def test_weight_line_is_recorded_as_rejected(self):
        scan = _scan_prices(["وزن 250 گرم"])
        self.assertEqual(scan.price, 0)
        self.assertEqual(len(scan.rejected), 1)
        self.assertIn("250", scan.rejected[0])
        # …but it is NOT shown to the user: nobody asked for a price there.
        self.assertEqual(scan.surprising, [])

    def test_a_refused_price_line_is_surfaced(self):
        # «قیمت ۵» is almost certainly a typo or an out-of-range amount; the
        # owner must be told we ignored it instead of wondering.
        scan = _scan_prices(["قیمت 5"])
        self.assertEqual(scan.price, 0)
        self.assertEqual(len(scan.surprising), 1)

    def test_empty_block_has_no_evidence(self):
        self.assertEqual(_scan_prices(["قاب موبایل"]).evidence, {})


@needs_httpx
class TestFallbackProvenance(unittest.TestCase):
    def test_title_and_colors_are_attributed(self):
        data = _fallback("قاب سیلیکونی آیفون 13\nرنگ: مشکی | سفید", ["iPhone 13"])
        self.assertEqual(data.evidence["title"].source, ev.CAPTION)
        self.assertEqual(data.evidence["colors"].source, ev.CAPTION)
        self.assertIn("مشکی", data.evidence["colors"].note)

    def test_uniform_price_note_appears(self):
        data = _fallback("قیمت 698000 تومان", [])
        self.assertTrue(any("یکسان" in note for note in data.notes))

    def test_ordinary_numbered_lines_make_no_notes(self):
        data = _fallback("قاب 13 پرو مکس\nوزن 250 گرم\nتاریخ 1403/01/01", [])
        self.assertEqual(data.notes, [])

    def test_round_trip_through_to_dict_keeps_evidence(self):
        data = _fallback("قیمت 698000 تومان\nرنگ: مشکی | سفید", [])
        restored = ev.from_dict(data.to_dict()["evidence"])
        self.assertEqual(restored["price"].source, ev.CAPTION)
        self.assertIn("698000", restored["price"].note)


class TestTrustOrder(unittest.TestCase):
    def test_user_edit_beats_the_caption(self):
        evidence: dict[str, ev.Evidence] = {}
        ev.merge(evidence, "price", ev.CAPTION, quote="قیمت 698")
        ev.merge(evidence, "price", ev.USER, quote="اصلاح دستی")
        self.assertEqual(evidence["price"].source, ev.USER)

    def test_a_lower_trust_source_cannot_overwrite(self):
        evidence = {"price": ev.note(ev.CAPTION, quote="قیمت 698")}
        ev.merge(evidence, "price", ev.AI, quote="قیمت 700")
        self.assertIn("698", evidence["price"].note)

    def test_the_sellers_text_beats_the_machine_reads(self):
        evidence: dict[str, ev.Evidence] = {}
        ev.merge(evidence, "price", ev.AI, quote="حدس مدل")
        ev.merge(evidence, "price", ev.OCR, quote="تصویر")
        ev.merge(evidence, "price", ev.CAPTION, quote="قیمت 698")
        self.assertEqual(evidence["price"].source, ev.CAPTION)

    def test_unknown_source_is_treated_as_a_policy_note(self):
        evidence: dict[str, ev.Evidence] = {}
        ev.merge(evidence, "price", "who-knows", quote="x")
        self.assertEqual(evidence["price"].source, ev.POLICY)


class TestPreviewBlock(unittest.TestCase):
    def test_renders_sources_and_notes(self):
        html = ev.preview_html({"price": ev.note(ev.CAPTION, quote="قیمت 698")},
                               ["«وزن 250 گرم» قیمت نبود"])
        self.assertIn("از کجا می‌دانم", html)
        self.assertIn("قیمت", html)
        self.assertIn("وزن 250 گرم", html)

    def test_plain_caption_evidence_is_not_noise(self):
        # A field whose only story is «it came from the caption» with no quote
        # must not add a line — the preview would drown in it.
        self.assertEqual(ev.preview_html({"tags": ev.note(ev.CAPTION)}, []), "")

    def test_html_is_escaped(self):
        html = ev.preview_html({"title": ev.note(ev.AI, quote="<script>bad()</script>")}, [])
        self.assertNotIn("<script>", html)


class TestVocabulary(unittest.TestCase):
    def setUp(self) -> None:
        from bot.services import jsonstore, vocabulary

        self.vocabulary = vocabulary
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, vocabulary, "VOCAB_FILE", vocabulary.VOCAB_FILE)
        vocabulary.VOCAB_FILE = self.tmp / "vocabulary.json"
        jsonstore.invalidate()
        vocabulary.invalidate()

    def test_no_rules_means_no_change(self):
        self.assertEqual(self.vocabulary.apply("قاب پلومریا زرد"), "قاب پلومریا زرد")

    def test_rule_is_applied_and_reported(self):
        self.vocabulary.set_rule("پلومریا", "Plumeria")
        changes: list[str] = []
        out = self.vocabulary.apply("قاب پلومریا زرد", changes)
        self.assertEqual(out, "قاب Plumeria زرد")
        self.assertTrue(any("Plumeria" in item for item in changes))

    def test_latin_rule_is_word_bounded(self):
        self.vocabulary.set_rule("cover", "Case")
        self.assertEqual(self.vocabulary.apply("iphone cover"), "iphone Case")
        self.assertEqual(self.vocabulary.apply("coverphone"), "coverphone")

    def test_longest_rule_wins(self):
        self.vocabulary.set_rule("قاب گوشی", "Case")
        self.vocabulary.set_rule("قاب", "Cover")
        self.assertEqual(self.vocabulary.apply("قاب گوشی آیفون"), "Case آیفون")

    def test_remove_rule(self):
        self.vocabulary.set_rule("a", "b")
        self.assertTrue(self.vocabulary.remove_rule("a"))
        self.assertFalse(self.vocabulary.remove_rule("a"))
        self.assertEqual(self.vocabulary.rules(), {})

    def test_self_referential_rule_is_ignored(self):
        self.vocabulary.set_rule("x", "y")
        from bot.services import jsonstore

        jsonstore.write_json(self.vocabulary.VOCAB_FILE,
                             {"version": 1, "rules": {"y": "y", "": "z", "k": ""}})
        jsonstore.invalidate()
        self.vocabulary.invalidate()
        self.assertEqual(self.vocabulary.rules(), {})


if __name__ == "__main__":
    unittest.main()
