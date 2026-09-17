"""Phase 3: edit one field on purpose, and have it stick.

Free-text corrections were the only way to fix a draft, which meant two things:
the seller had to speak the parser's language, and the next extraction could
undo what they typed. These tests pin the rules of the field editor (what is
accepted, what is refused and why) plus the two one-tap answers that came out of
the earlier phases: separating colors that came from two different messages, and
the brand typo «Nubia → Nokia».

Run with ``python3 -m unittest discover -s tests``; the flow-level classes skip
themselves when python-telegram-bot is not installed.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

from bot.services import draft_edits as de

try:
    from bot.modules import product_flow as PF
    HAS_FLOW = True
except Exception:                       # pragma: no cover - PTB missing
    PF = None
    HAS_FLOW = False

needs_flow = unittest.skipUnless(HAS_FLOW, "python-telegram-bot is not installed")


def _draft(**kwargs):
    from bot.services.product_extractor import ProductData

    return ProductData(**kwargs)


class TestFieldParsers(unittest.TestCase):
    def test_price_accepts_the_shapes_people_type(self):
        for raw in ("698000", "698", "698t", "۶۹۸ هزار", "698,000 تومان"):
            self.assertEqual(de.parse_price(raw), 698000, raw)

    def test_price_out_of_range_is_refused_with_the_reason(self):
        with self.assertRaises(ValueError) as ctx:
            de.parse_price("5")
        self.assertIn("بازهٔ مجاز", str(ctx.exception))

    def test_title_rejects_a_number_line(self):
        with self.assertRaises(ValueError):
            de.parse_title("1098000")
        self.assertEqual(de.parse_title("قاب سیلیکونی آیفون"), "قاب سیلیکونی آیفون")

    def test_colors_split_dedupe_and_demand_two(self):
        self.assertEqual(de.parse_colors("مشکی | سفید، مشکی"), ["مشکی", "سفید"])
        with self.assertRaises(ValueError):
            de.parse_colors("مشکی")
        self.assertEqual(de.parse_colors("حذف"), [])

    def test_models_are_canonicalised_even_without_a_brand_word(self):
        # «13 پرو مکس» alone still has to become the label the rest of the
        # pipeline keys on, or the edit looks applied and the variation is not.
        self.assertEqual(de.parse_models("13 پرو مکس"), ["iPhone 13 Pro Max"])
        self.assertEqual(de.parse_models("S24 اولترا"), ["S24 Ultra"])

    def test_models_reject_duplicates(self):
        with self.assertRaises(ValueError):
            de.parse_models("13 پرو مکس | iPhone 13 Pro Max")

    def test_sku_prefix_is_sanitised(self):
        self.assertEqual(de.parse_sku_prefix(" b o—147/ "), "BO147")
        with self.assertRaises(ValueError):
            de.parse_sku_prefix("قاب گوشی")

    def test_category_accepts_a_leaf_name_and_refuses_unknown(self):
        self.assertEqual(
            de.parse_categories("آیفون iphone"),
            ["قاب و کاور گوشی و تبلت > آیفون iphone"],
        )
        with self.assertRaises(ValueError):
            de.parse_categories("لوازم فضایی")
        with self.assertRaises(ValueError):
            de.parse_categories("فروش ویژه")

    def test_group_prices_refuse_half_a_table(self):
        self.assertEqual(de.parse_group_prices("ایفون 698 اندروید 598"),
                         {"iphone": 698000, "android": 598000})
        self.assertEqual(de.parse_group_prices("حذف"), {})
        with self.assertRaises(ValueError):
            de.parse_group_prices("iphone 698000")


class TestApplyEdit(unittest.TestCase):
    def test_edit_records_the_owner_as_the_source_and_locks_the_value(self):
        data = _draft(title="قبلی", price=500000)
        self.assertIsNone(de.apply_edit(data, "price", "698000"))
        self.assertEqual(data.price, 698000)
        self.assertEqual(data.evidence["price"].source, "user")
        self.assertEqual(data.user_edits["price"], 698000)
        self.assertTrue(any("ویرایش دستی" in note for note in data.notes))

    def test_a_refused_edit_changes_nothing(self):
        data = _draft(price=500000)
        error = de.apply_edit(data, "price", "5")
        self.assertTrue(error)
        self.assertEqual(data.price, 500000)
        self.assertNotIn("price", data.user_edits)

    def test_colors_edit_prunes_the_per_model_matrix(self):
        data = _draft(attributes={"رنگ": ["مشکی", "سفید", "قرمز"]},
                      model_colors={"iPhone 13": ["مشکی", "قرمز"]})
        self.assertIsNone(de.apply_edit(data, "colors", "مشکی | سفید"))
        self.assertEqual(data.attributes["رنگ"], ["مشکی", "سفید"])
        self.assertEqual(data.model_colors, {"iPhone 13": ["مشکی"]})

    def test_removing_colors_drops_the_axis_entirely(self):
        data = _draft(attributes={"رنگ": ["مشکی", "سفید"]})
        self.assertIsNone(de.apply_edit(data, "colors", "حذف"))
        self.assertNotIn("رنگ", data.attributes)

    def test_attribute_axes_are_editable(self):
        data = _draft(attributes={"طرح": ["پلومریا", "برگی"]})
        keys = [key for key, _l, _c in de.editable_fields(data)]
        self.assertIn("attr:طرح", keys)
        self.assertIsNone(de.apply_edit(data, "attr:طرح", "پلومریا، گلی"))
        self.assertEqual(data.attributes["طرح"], ["پلومریا", "گلی"])

    def test_prompt_shows_the_current_value(self):
        data = _draft(title="قاب سیلیکونی")
        prompt = de.prompt_for("title", data)
        self.assertIn("قاب سیلیکونی", prompt)
        self.assertIn("✏️", prompt)


class TestLocksSurviveReExtraction(unittest.TestCase):
    def test_apply_locks_restores_every_handedit(self):
        fresh = _draft(title="متفاوت", price=100000)
        fresh.user_edits = {"title": "قاب من", "price": 698000}
        moved = de.apply_locks(fresh)
        self.assertEqual(fresh.title, "قاب من")
        self.assertEqual(fresh.price, 698000)
        self.assertEqual(set(moved), {"title", "price"})
        self.assertEqual(fresh.evidence["title"].source, "user")

    def test_a_stale_lock_does_not_break_the_draft(self):
        fresh = _draft(price=100000)
        fresh.user_edits = {"colors": "این لیست نیست"}     # a hand-corrupted store
        de.apply_locks(fresh)                              # must not raise
        self.assertEqual(fresh.price, 100000)


class TestColorSources(unittest.TestCase):
    def test_colors_are_grouped_per_message(self):
        blocks = __import__("bot.services.postmodel", fromlist=["x"]).parse_sources([
            ("info", "قاب\nرنگ: مشکی | سفید"),
            ("caption", "کیف\nرنگ: قهوه‌ای | عسلی"),
        ])
        grouped = de.colors_by_message(blocks)
        self.assertEqual(sorted(grouped), ["caption", "info"])
        self.assertIn("مشکی", grouped["info"])

    def test_suppressing_a_message_keeps_its_title_and_price(self):
        postmodel = __import__("bot.services.postmodel", fromlist=["x"])
        blocks = postmodel.parse_sources([
            ("info", "قیمت 698000\nرنگ: مشکی | سفید"),
            ("caption", "کیف دوشی\nرنگ: قهوه‌ای | عسلی"),
        ])
        kept = de.suppress_colors(blocks, {"caption"})
        roles = {(b.message, b.roles) for b in kept}
        self.assertNotIn(("caption", ("colors",)), roles)
        self.assertTrue(any(b.message == "caption" and b.text() == "کیف دوشی" for b in kept))

    def test_suppression_is_a_noop_with_an_empty_set(self):
        postmodel = __import__("bot.services.postmodel", fromlist=["x"])
        blocks = postmodel.parse_blocks("رنگ: مشکی | سفید", message="caption")
        self.assertEqual(de.suppress_colors(blocks, set()), list(blocks))


class TestBrandTypo(unittest.TestCase):
    def setUp(self):
        from bot.services import jsonstore, vocabulary

        self.vocabulary = vocabulary
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(setattr, vocabulary, "VOCAB_FILE", vocabulary.VOCAB_FILE)
        vocabulary.VOCAB_FILE = self.tmp / "vocabulary.json"
        jsonstore.invalidate()
        vocabulary.invalidate()

    def test_suggestion_offered_for_a_near_miss(self):
        self.assertEqual(
            de.brand_suggestions("قاب برای Nubia Z60"),
            [{"kind": "brand", "word": "Nubia", "target": "Nokia", "brand": "nokia"}],
        )

    def test_known_brands_are_never_corrected(self):
        self.assertEqual(de.brand_suggestions("قاب نوکیا 8.1"), [])
        self.assertEqual(de.brand_suggestions("قاب سامسونگ S24"), [])

    def test_accepting_writes_the_shop_dictionary(self):
        suggestion = de.brand_suggestions("قاب برای Nubia Z60")[0]
        data = _draft(title="قاب Nubia Z60")
        message = de.accept_suggestion(data, suggestion)
        self.assertIn("Nokia", message)
        self.assertEqual(self.vocabulary.rules(), {"Nubia": "Nokia"})
        self.assertIn("Nokia", data.title)
        self.assertEqual(data.evidence["title"].source, "vocabulary")

    def test_ambiguous_typos_get_no_guess(self):
        from bot.services import brand_suggest

        # An unknown-but-far word, a real brand, and a word too short to judge
        # are all cases where the honest answer is «no idea».
        self.assertEqual(brand_suggest.suggest_brand("samsunggalaxy"), None)
        self.assertEqual(brand_suggest.suggest_brand("iphone"), None)
        self.assertEqual(brand_suggest.suggest_brand("xi"), None)
        # Two brands equally close would be a coin flip — check the rule exists
        # by looking at what the matcher considered for a real near-miss.
        self.assertIn((2, "nokia"), brand_suggest.candidates("nubia"))


class TestSuppressionReachesTheDraft(unittest.TestCase):
    """The button has to change the draft, not only the note.

    Two messages about two products is the shape that produced the original
    complaint: removing one message's colors must leave the other product's
    list alone, and must not be undone by a color word hidden in prose.
    """

    CAPTION = chr(10).join([
        "قاب سیلیکونی آیفون 13 پرو مکس",
        "قیمت 698000",
        "رنگ: مشکی | سفید",
    ])
    INFO = chr(10).join([
        "کیف دوشی چرم",
        "رنگ: قهوه‌ای | عسلی",
    ])

    def _extract(self, **kwargs):
        import asyncio

        from bot.services.category_taxonomy import TAXONOMY
        from bot.services.product_extractor import extract_product

        return asyncio.run(extract_product(
            self.CAPTION + chr(10) + self.INFO,
            ["iPhone 13 Pro Max"],
            TAXONOMY,
            caption=self.CAPTION,
            info_text=self.INFO,
            **kwargs,
        ))

    def test_without_suppression_both_lists_are_merged_and_flagged(self):
        data = self._extract()
        self.assertEqual(len(data.attributes.get("رنگ", [])), 4)
        self.assertTrue(any("چند پیام" in note for note in data.notes))

    def test_suppressing_a_message_leaves_the_other_product_alone(self):
        data = self._extract(color_suppressed={"caption"})
        self.assertEqual(data.attributes.get("رنگ"), ["قهوه‌ای", "عسلی"])
        self.assertFalse(any("چند پیام" in note for note in data.notes))

    def test_suppressing_colors_never_touches_the_price(self):
        data = self._extract(color_suppressed={"info"})
        self.assertEqual(data.attributes.get("رنگ"), ["مشکی", "سفید"])
        self.assertEqual(data.price, 698000, "the price line of the other message still counts")

@needs_flow
class TestFieldFlow(unittest.TestCase):
    """The buttons in front of those rules: pick a field, type, get a preview."""

    def setUp(self):
        PF.sessions.clear()
        PF.album_buffers.clear()
        PF.album_tasks.clear()

    def _query(self, data, *, user_id=7):
        """A callback query that records every text (and markup) it is shown."""
        messages: list = []

        async def reply_html(text, **kwargs):
            messages.append((text, kwargs))
            return SimpleNamespace(chat_id=user_id)

        async def reply_text(text, **kwargs):
            messages.append((text, kwargs))
            return SimpleNamespace(chat_id=user_id)

        async def edit_text(text, **kwargs):
            messages.append((text, kwargs))

        async def answer(*a, **k):
            return None

        query = SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=user_id), answer=answer,
            message=SimpleNamespace(reply_html=reply_html, reply_text=reply_text,
                                    edit_text=edit_text, chat_id=user_id),
        )
        return SimpleNamespace(callback_query=query), messages

    def test_edit_without_a_draft_asks_for_input_first(self):
        update, messages = self._query("product:edit")
        result = asyncio.run(PF.edit(update, SimpleNamespace()))
        self.assertEqual(result, PF.WAITING)
        self.assertIn("عکس", messages[0][0])

    def test_picker_lists_the_fields_with_their_current_values(self):
        session = PF.ProductSession(data=_draft(title="قاب سیلیکونی", price=698000))
        PF.sessions[7] = session
        update, messages = self._query("product:edit")
        asyncio.run(PF.edit(update, SimpleNamespace()))
        self.assertIn("title", session.field_keys)
        self.assertIn("price", session.field_keys)
        markup = messages[0][1]["reply_markup"]
        buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn("product:field:0", buttons)
        self.assertIn("product:edit:free", buttons)

    def test_picking_a_field_asks_for_the_value(self):
        session = PF.ProductSession(data=_draft(title="قاب سیلیکونی"))
        session.field_keys = ["title"]
        PF.sessions[7] = session
        update, messages = self._query("product:field:0")
        result = asyncio.run(PF.pick_field(update, SimpleNamespace()))
        self.assertEqual(result, PF.EDITING_FIELD)
        self.assertEqual(session.editing_field, "title")
        self.assertIn("قاب سیلیکونی", messages[0][0])
        cancel = [b.callback_data for row in messages[0][1]["reply_markup"].inline_keyboard for b in row]
        self.assertIn("product:field:cancel", cancel, "an edit step needs a visible way out")

    def test_typed_value_is_applied_and_the_preview_returned(self):
        session = PF.ProductSession(data=_draft(title="قدیمی", price=100000))
        session.field_keys = ["price"]
        session.editing_field = "price"
        PF.sessions[7] = session
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(text="698000"),
        )

        async def reply_html(text, **kwargs):
            self.messages.append(text)

        self.messages = []
        update.effective_message.reply_html = reply_html
        result = asyncio.run(PF.field_value(update, SimpleNamespace()))
        self.assertEqual(result, PF.WAITING)
        self.assertEqual(session.data.price, 698000)
        self.assertEqual(session.editing_field, "")
        self.assertIn("قیمت", self.messages[-1])

    def test_a_bad_value_keeps_the_user_in_the_step(self):
        session = PF.ProductSession(data=_draft(price=100000))
        session.editing_field = "price"
        PF.sessions[7] = session
        replies = []

        async def reply_text(text, **kwargs):
            replies.append(text)

        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_message=SimpleNamespace(text="5", reply_text=reply_text),
        )
        result = asyncio.run(PF.field_value(update, SimpleNamespace()))
        self.assertEqual(result, PF.EDITING_FIELD)
        self.assertEqual(session.data.price, 100000)
        self.assertTrue(replies[0].startswith("⚠️"))

    def test_cancellation_returns_to_the_preview(self):
        session = PF.ProductSession(data=_draft(title="قاب"))
        session.editing_field = "title"
        PF.sessions[7] = session
        update, _messages = self._query("product:field:cancel")
        result = asyncio.run(PF.cancel_field(update, SimpleNamespace()))
        self.assertEqual(result, PF.WAITING)
        self.assertEqual(session.editing_field, "")

    def test_color_source_toggle_is_reversible(self):
        session = PF.ProductSession(
            data=_draft(title="قاب"), info_text="قاب\nرنگ: مشکی | سفید",
            model_text="کیف\nرنگ: قهوه‌ای | عسلی", color_sources=["info", "caption"],
        )
        PF.sessions[7] = session
        calls = []

        async def fake_extract(target):
            calls.append(list(target.suppressed_colors))
            return _draft(title="قاب")

        original = PF._extract
        PF._extract = fake_extract
        self.addCleanup(setattr, PF, "_extract", original)

        update, _messages = self._query("product:colorsrc:1")
        asyncio.run(PF.toggle_color_source(update, SimpleNamespace()))
        self.assertEqual(session.suppressed_colors, ["caption"])
        update, _messages = self._query("product:colorsrc:1")
        asyncio.run(PF.toggle_color_source(update, SimpleNamespace()))
        self.assertEqual(session.suppressed_colors, [])
        self.assertEqual(calls, [["caption"], []])

    def test_keyboard_offers_the_typo_fix_and_accepting_clears_it(self):
        session = PF.ProductSession(data=_draft(title="قاب Nubia Z60"))
        session.data.suggestions = [
            {"kind": "brand", "word": "Nubia", "target": "Nokia", "brand": "nokia"}
        ]
        keys = [button.callback_data
                for row in PF._keyboard(session).inline_keyboard
                for button in row]
        self.assertIn("product:sug:0", keys)
        self.assertIn("product:edit", keys)

        async def fake_extract(target):
            return _draft(title="قاب Nubia Z60")

        original = PF._extract
        PF._extract = fake_extract
        self.addCleanup(setattr, PF, "_extract", original)
        PF.sessions[7] = session
        update, _messages = self._query("product:sug:0")
        asyncio.run(PF.accept_suggestion(update, SimpleNamespace()))
        self.assertEqual(session.data.suggestions, [])
        self.assertIn("brand:Nubia", session.dismissed)
        # the offer must not come back on the next render
        keys = [button.callback_data
                for row in PF._keyboard(session).inline_keyboard
                for button in row]
        self.assertNotIn("product:sug:0", keys)

    def test_dismissing_an_offer_silences_it_only_for_this_product(self):
        session = PF.ProductSession(data=_draft(title="قاب Nubia Z60"))
        session.data.suggestions = [
            {"kind": "brand", "word": "Nubia", "target": "Nokia", "brand": "nokia"}
        ]
        PF.sessions[7] = session

        async def fake_extract(target):
            return target.data

        original = PF._extract
        PF._extract = fake_extract
        self.addCleanup(setattr, PF, "_extract", original)

        update, _messages = self._query("product:sug:no:0")
        asyncio.run(PF.dismiss_suggestion(update, SimpleNamespace()))
        self.assertIn("brand:Nubia", session.dismissed)
        self.assertTrue(session.data.suggestions, "the draft keeps the note, only the nag stops")


if __name__ == "__main__":
    unittest.main()
