"""The text model: one classified line, read once, used by every rule.

Before blocks, each rule re-decided «is this line a price?» and they disagreed —
a weight line was a price for the scanner and prose for the title picker. These
tests pin the classification itself, so the disagreement cannot come back.

Stdlib only.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

from bot.services import postmodel as ev

try:
    from bot.services.product_extractor import _fallback
except ImportError:      # the extractor pulls in httpx; stdlib-only runs skip
    _fallback = None
    _NEEDS_HTTPX = False
else:
    _NEEDS_HTTPX = True

needs_httpx = unittest.skipUnless(_NEEDS_HTTPX, "httpx is not installed")


class TestClassify(unittest.TestCase):
    def test_weight_line_is_meta_not_price(self):
        self.assertEqual(ev.classify_line("وزن 250 گرم"), (ev.ROLE_META,))

    def test_tracking_and_sku_lines_are_meta(self):
        for line in ["SKU: BO147", "کد رهگیری 1234567890", "تاریخ 1403/01/01", "ابعاد 10x20"]:
            self.assertEqual(ev.classify_line(line), (ev.ROLE_META,), line)

    def test_model_line_that_also_states_a_price_keeps_both_roles(self):
        # Dropping either role was the bug: as prose the price was lost, as
        # price-only the model was lost.
        roles = ev.classify_line("S24 اولترا 768t")
        self.assertIn(ev.ROLE_PRICE, roles)
        self.assertIn(ev.ROLE_MODEL, roles)

    def test_group_price_line(self):
        self.assertIn(ev.ROLE_PRICE, ev.classify_line("قیمت ایفون 698 اندروید 598"))

    def test_section_header_is_brand(self):
        for line in ["آیفون:", "Samsung", "سامسونگ 📱", "apple"]:
            self.assertEqual(ev.classify_line(line), (ev.ROLE_BRAND,), line)

    def test_persian_variant_words_alone_make_a_model_line(self):
        # Without this «15 اولترا» read as prose and became the product title.
        self.assertIn(ev.ROLE_MODEL, ev.classify_line("15 اولترا"))
        self.assertIn(ev.ROLE_MODEL, ev.classify_line("17pro :"))
        self.assertNotIn(ev.ROLE_MODEL, ev.classify_line("قاب سیلیکونی ضدلک مقاوم"))

    def test_color_list_line(self):
        self.assertIn(ev.ROLE_COLORS, ev.classify_line("رنگ: مشکی | سفید | قرمز"))

    def test_attribute_line(self):
        self.assertIn(ev.ROLE_ATTRIBUTE, ev.classify_line("ویژگی: ضدضربه"))

    def test_plain_prose(self):
        self.assertEqual(ev.classify_line("قاب سیلیکونی ضدلک مقاوم"), (ev.ROLE_PROSE,))

    def test_blank_lines_have_no_roles(self):
        self.assertEqual(ev.classify_line("   "), ())
        self.assertEqual(ev.parse_blocks("\n\n"), [])


class TestParseBlocks(unittest.TestCase):
    def test_line_numbers_and_message_survive(self):
        blocks = ev.parse_blocks("قیمت 698\nرنگ: مشکی | سفید", message="info")
        self.assertEqual([b.line_no for b in blocks], [1, 2])
        self.assertEqual({b.message for b in blocks}, {"info"})
        self.assertTrue(blocks[0].has(ev.ROLE_PRICE))

    def test_sources_keep_their_order_and_labels(self):
        blocks = ev.parse_sources([("info", "قیمت 698"), ("caption", "قیمت 750")])
        self.assertEqual([(b.message, b.text()) for b in blocks],
                         [("info", "قیمت 698"), ("caption", "قیمت 750")])

    def test_describe_shows_the_line_not_the_message(self):
        # The message is already in the evidence source; repeating it here makes
        # the preview read «متن کپشن: متن کپشن خط 2 …».
        block = ev.parse_blocks("قیمت 698 تومان", message="caption")[0]
        self.assertEqual(ev.describe(block), 'خط 1 «قیمت 698 تومان»')
        self.assertEqual(ev.describe(None), "")

    def test_with_role_filters(self):
        blocks = ev.parse_blocks("وزن 250 گرم\nقیمت 698\nرنگ: مشکی | سفید")
        self.assertEqual([b.text() for b in ev.with_role(blocks, ev.ROLE_PRICE)], ["قیمت 698"])
        self.assertEqual([b.text() for b in ev.with_role(blocks, ev.ROLE_META)], ["وزن 250 گرم"])

    def test_block_to_dict_is_json_safe(self):
        block = ev.parse_blocks("قیمت 698", message="info")[0]
        data = block.to_dict()
        self.assertEqual(data["roles"], [ev.ROLE_PRICE])
        self.assertEqual(data["line_no"], 1)


@needs_httpx
class TestProvenanceOfBlocks(unittest.TestCase):
    def test_price_evidence_names_the_message_it_came_from(self):
        blocks = ev.parse_sources([("info", "قیمت 698000 تومان"), ("caption", "قاب سیلیکونی")])
        data = _fallback("قیمت 698000 تومان\nقاب سیلیکونی", [], blocks=blocks)
        self.assertEqual(data.price, 698000)
        # the message is the evidence source; the note adds the exact line
        self.assertEqual(data.evidence["price"].source, ev.INFO)
        self.assertIn("خط 1", data.evidence["price"].note)
        self.assertIn("قیمت 698000", data.evidence["price"].note)

    def test_colors_from_two_messages_are_merged_but_flagged(self):
        # Dropping the second list would delete sellable variations; keeping it
        # silently could merge two products. So: keep, say where each came from,
        # and warn when the second message looks like its own product (P1-11).
        blocks = ev.parse_sources([
            ("info", "قاب سیلیکونی\nرنگ: مشکی | سفید"),
            ("caption", "کیف دوشی\nرنگ: قهوه‌ای | عسلی"),
        ])
        data = _fallback("", [], blocks=blocks)
        self.assertIn("مشکی", data.attributes.get("رنگ", []))
        self.assertIn("عسلی", data.attributes.get("رنگ", []))
        self.assertEqual(data.evidence["colors"].source, ev.INFO)
        self.assertIn("خط 2", data.evidence["colors"].note)
        self.assertTrue(any("از چند پیام" in note for note in data.notes), data.notes)
        self.assertTrue(any("محصول دیگری" in note for note in data.notes), data.notes)

    def test_one_message_with_colors_stays_quiet(self):
        data = _fallback("", [], blocks=ev.parse_sources([("info", "قاب\nرنگ: مشکی | سفید")]))
        self.assertEqual([n for n in data.notes if "چند پیام" in n], [])


if __name__ == "__main__":
    unittest.main()
