"""فاز ۹: خواصی که باید برای **هر** ورودی درست بمانند، نه فقط برای مثال‌های انتخابی.

تستِ نمونه‌محور یک چیز را ثابت نمی‌کند: «ورودیِ کجی که بهش فکر نکردیم چه می‌شود».
فازهای ۲ تا ۷ دقیقاً از همان‌جا ضربه خوردند — «وزن 250 گرم» قیمت شد، سه مدلِ متفاوت به
یک لیبل ادغام شد، محدودیتِ رنگ یک مدل را حذف کرد، قیمتِ دومِ یک خط گم شد. پس این فایل
چهار قاعده را روی ورودی‌های تصادفی می‌چرخاند:

1. ماتریس واریژن هیچ‌وقت بزرگ‌تر از حاصل‌ضرب محورها نیست، ترکیب تکراری ندارد، و هیچ
   مدلی با محدودیتِ رنگ **حذف نمی‌شود**؛
2. مبلغی که واحدِ صریح دارد دوباره مقیاس نمی‌خورد، عددِ پشتِ برچسبِ غیرقیمت (وزن/تاریخ/
   کد/…) قیمت نمی‌شود، و دو قیمتِ دو گروه در یک خط هر دو می‌مانند؛
3. قیمتی که بیرون از `PRICE_MIN..PRICE_MAX` است همیشه گزارش می‌شود — نه بی‌صدا پذیرفته،
   نه بی‌صدا رد؛
4. دو خطِ مدلِ متفاوت هرگز به یک لیبل ادغام نمی‌شوند، و برچسبِ یک مدلِ واقعی هم
   دوباره‌سازی نمی‌شود.

hypothesis در `requirements-dev.txt` است و مثل بقیهٔ تست‌های وابسته، بدونِش **skip**
می‌شود — سوئیت اصلی باید با کتابخانهٔ استانداردِ خالی هم سبز بماند (همان چیزی که CI روی
«سرور اشتَریِ بدون deps» اثبات می‌کند).
"""

from __future__ import annotations

import asyncio
import os
import unittest

os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ.setdefault("SUDO_IDS", "1234567")

try:
    from hypothesis import HealthCheck, assume, given, settings, strategies as st

    HAS_HYPOTHESIS = True
except Exception:  # pragma: no cover - hypothesis not installed
    HAS_HYPOTHESIS = False

    def _install_hypothesis_stub() -> None:
        """دکوریتورهای hypothesis در زمانِ *تعریفِ کلاس* اجرا می‌شوند، پس حتی وقتی
        نصب نیست باید چیزی باشد که `st.text(...)` و `@settings(...)` را بلعَد؛
        خودِ کلاس‌ها skip می‌شوند و این اشیء هیچ‌وقت صدا زده نمی‌شوند.

        با `globals()` نوشته می‌شود تا mypy آن را «بازتعریفِ type» نخوانَد.
        """

        class _Stub:
            def __getattr__(self, _name: str) -> _Stub:
                return _Stub()

            def __call__(self, *_args: object, **_kwargs: object) -> _Stub:
                return _Stub()

        stub = _Stub()
        for _name in ("HealthCheck", "assume", "given", "settings", "st"):
            globals()[_name] = stub

    _install_hypothesis_stub()

try:
    from bot.services import money, plan, phone_parser, validation
    from bot.services.product_extractor import extract_product

    HAS_SERVICES = True
except Exception:  # pragma: no cover - لایهٔ سرویس وابستگی ندارد، ولی صادقانه چک می‌شود
    HAS_SERVICES = False

#: حروفِ عمداً محدود: کاراکترهای کنترل و بک‌اسلش، تست را دربارهٔ fuzzingِ لوایح
#: می‌چرخانند، نه دربارهٔ قواعد این ربات.
_LETTERS = "ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی aA123456789-/"
_WORDS = st.text(alphabet=_LETTERS, min_size=1, max_size=8)
_COLOURS = st.sampled_from(["سفید", "مشکی", "قرمز", "نارنجی", "سرمه‌ای", "طلایی", "سفید برفی"])
#: پسوندهایی که برای آیفون معنی دارند (و در کاتالوگ هم‌اند) — هر کدام باید در لیبل
#: بنشیند، وگرنه دو مدلِ متمایز یک واریژن می‌شود.
IPHONE_SUFFIXES = ["", "pro", "max", "pro max", "promax", "plus", "+", "mini", "air", "پرو", "مکس", "پرومکس", "پلاس"]
#: پسوندهای برندهای دیگر؛ روی آیفون «ساخته» نمی‌شوند.
FOREIGN_VARIANTS = ["اولترا", "ultra", "فولد", "وچ", "نوت"]
_PRICE_UNITS = ["تومان", "تومن", "ت", "t", "هزار", "میلیون", "میلیارد"]
_NON_PRICE_LABELS = ["وزن", "تاریخ", "کد ملی", "شناسه", "SKU", "ردیف", "سال", "گارانتی", "ابعاد", "کد رهگیری", "تعداد"]

_settings = settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
_SKIP = "hypothesis نصب نیست (pip install -r requirements-dev.txt)"


@unittest.skipUnless(HAS_HYPOTHESIS and HAS_SERVICES, _SKIP)
class TestVariationPlan(unittest.TestCase):
    """`plan.build_plan` تنها منبعِ «چند واریژن» است — پیش‌نمایش == پیلود (P0-6)."""

    @_settings
    @given(
        models=st.lists(_WORDS, min_size=0, max_size=4),
        colours=st.lists(_COLOURS, min_size=0, max_size=5),
        restrict_for=st.integers(min_value=-1, max_value=3),
    )
    def test_the_matrix_is_never_bigger_than_the_axes(self, models, colours, restrict_for):
        restrictions = {}
        distinct = set(plan.clean_values(models))
        if restrict_for >= 0 and distinct and colours:
            restrictions[sorted(distinct)[restrict_for % len(distinct)]] = colours[:1]
        built = plan.build_plan(models, {"رنگ": colours}, restrictions)

        self.assertLessEqual(built.count, built.naive_count)
        self.assertEqual(built.count, len(built.combos))
        # ترکیب تکراری یعنی دو واریژن با یک مشخصات؛ ووکامرس دومی را رد می‌کند و
        # عددِ پیش‌نمایش دیگر با سایت نمی‌خواند.
        unique = {tuple(sorted(combo.items())) for combo in built.combos}
        self.assertEqual(len(unique), len(built.combos), "ترکیب تکراری در ماتریس")
        for combo in built.combos:
            self.assertEqual(set(combo), set(built.attribute_names()))

    @_settings
    @given(models=st.lists(_WORDS, min_size=2, max_size=4), colours=st.lists(_COLOURS, min_size=1, max_size=5))
    def test_without_colour_restrictions_the_matrix_is_the_full_product(self, models, colours):
        built = plan.build_plan(models, {"رنگ": colours})
        # یک محور فقط وقتی محور است که دو مقدار متمایز داشته باشد؛ اگر هیچ محوری
        # نماند، محصول simple است و دقیقاً یک «ترکیب» تهی دارد.
        lengths = [n for n in (len(plan.clean_values(models)), len(plan.clean_values(colours))) if n >= 2]
        expected = 1
        for length in lengths:
            expected *= length
        self.assertEqual(built.count, expected)
        self.assertEqual(built.count, built.naive_count)
        self.assertFalse(built.restricted)

    @_settings
    @given(
        models=st.lists(_WORDS, min_size=2, max_size=4),
        colours=st.lists(_COLOURS, min_size=2, max_size=5),
        restrict_for=st.integers(min_value=0, max_value=3),
    )
    def test_a_colour_restriction_never_deletes_a_model(self, models, colours, restrict_for):
        """محدودیتِ رنگ یعنی «این مدل این رنگ‌ها را دارد»، نه «آن مدل را حذف کن».

        سه دریچهٔ ایمنیِ `restrict_combinations` همین جمله‌اند: مدلی که ورودیِ محدودیت
        ندارد آزاد می‌ماند، محدودیتی که با رنگ‌های واقعی تقاطع ندارد بی‌اثر است، و اگر
        فیلتر همه را می‌کشت، ماتریسِ فیلترنشده برمی‌گردد.
        """
        # همان پاک‌سازیِ خودِ plan: فاصله‌های داخلی هم جمع می‌شوند، پس مقایسه باید
        # با خروجی clean_values باشد نه با stripِ دستِ خودمان.
        distinct = set(plan.clean_values(models))
        assume(len(distinct) >= 2)
        picked = sorted(distinct)[restrict_for % len(distinct)]
        built = plan.build_plan(models, {"رنگ": colours}, {picked: colours[:1]})
        self.assertTrue(built.combos, "ماتریس خالی شد؛ هیچ واریژنی نمی‌شد ساخت")
        self.assertIn("مدل", built.attribute_names())
        kept = {combo["مدل"] for combo in built.combos}
        self.assertEqual(kept, distinct, "برخی مدل‌ها از ماتریس حذف شدند")

    @_settings
    @given(models=st.lists(_WORDS, min_size=1, max_size=3), colour=_COLOURS)
    def test_an_attribute_with_one_value_is_name_not_axis_and_is_reported(self, models, colour):
        """سه املا از یک مقدار، یک مقدار است: محور نمی‌شود، ولی گم هم نمی‌شود."""
        built = plan.build_plan(models, {"رنگ": [colour, colour, f"  {colour}  "]})
        self.assertNotIn("رنگ", built.attribute_names())
        self.assertIn(("رنگ", 3, 1), built.dropped)
        self.assertIn("حذف شد", built.summary())


@unittest.skipUnless(HAS_HYPOTHESIS and HAS_SERVICES, _SKIP)
class TestMoney(unittest.TestCase):
    """P0-2/P0-3/P0-4: واحدِ صریح یک بار اعمال می‌شود، برچسبِ غیرقیمت قیمت نمی‌سازد."""

    @_settings
    @given(count=st.integers(min_value=1, max_value=99_999), unit=st.sampled_from(_PRICE_UNITS))
    def test_an_explicit_unit_is_applied_once_and_never_rescaled(self, count, unit):
        factor = money._factor_of(unit)
        amounts = money.amounts_in_line(f"{count}{unit}")
        self.assertEqual(len(amounts), 1)
        amount = amounts[0]
        self.assertEqual(amount.value, count * factor)
        self.assertFalse(amount.is_bare)
        # سیاست «سه رقم = هزار» فقط روی عددِ بی‌واحد اعمال می‌شود؛ اگر روی این خط هم
        # می‌نشست، «768t» دو برابرِ قولِ فروشنده می‌شد.
        self.assertEqual(money.apply_bare_policy(amount).value, amount.value)

    @_settings
    @given(count=st.integers(min_value=1, max_value=10**9))
    def test_a_bare_number_is_only_ever_multiplied_by_a_thousand(self, count):
        amount = money.amounts_in_line(str(count))[0]
        self.assertTrue(amount.is_bare)
        self.assertIn(money.apply_bare_policy(amount).value, {count, count * 1000})

    @_settings
    @given(
        count=st.integers(min_value=100, max_value=10**9),
        label=st.sampled_from(_NON_PRICE_LABELS),
        tail=st.sampled_from(["", " گرم", " میلی‌گرم", " سانتی‌متر", " 1403/01/01"]),
    )
    def test_a_number_behind_a_non_price_label_never_becomes_a_price(self, count, label, tail):
        """گارد در دو لایه است: رشته «قیمت» نیست، و خروجیِ استخراج هم صفر می‌ماند."""
        line = f"{label} {count}{tail}"
        self.assertFalse(money.looks_like_price_line(line), f"{line!r} قیمت نیست")
        self.assertFalse(money.states_price_explicitly(line))
        # caption= همان کاری که product_flow می‌کند؛ بدون آن متن به پارسر نمی‌رسید و
        # ادعا بی‌صدا درست می‌شد (خطای خودِ همین تست در first run).
        data = asyncio.run(extract_product(line, [], "", caption=line))
        self.assertEqual(data.price, 0, f"«{line}» قیمتِ {data.price} ساخت")

    @_settings
    @given(
        first=st.integers(min_value=1_000, max_value=5_000),
        second=st.integers(min_value=1_000, max_value=5_000),
        unit=st.sampled_from(["", "t", " هزار", " تومان"]),
    )
    def test_two_group_prices_on_one_line_are_both_kept(self, first, second, unit):
        """P0-4: دو قیمت در یک خط — هیچ‌کدام قیمتِ دیگری (یا fallback) نمی‌شود."""
        line = f"قیمت ایفون {first}{unit} سامسونگ {second}{unit}"
        groups = money.group_amounts(line)
        self.assertEqual(set(groups), {"iphone", "android"}, f"از دو گروه فقط یکی خوانده شد: {line!r}")
        self.assertEqual(groups["iphone"], money.parse_line_amount(f"قیمت ایفون {first}{unit}"))
        self.assertEqual(groups["android"], money.parse_line_amount(f"قیمت سامسونگ {second}{unit}"))
        if first != second:
            self.assertNotEqual(groups["iphone"], groups["android"])


@unittest.skipUnless(HAS_HYPOTHESIS and HAS_SERVICES, _SKIP)
class TestPayloadMatchesPreview(unittest.TestCase):
    """P0-6: عددی که در پیش‌نمایش می‌بینیم باید تعداد واریژنِ payload باشد."""

    @_settings
    @given(
        models=st.lists(_WORDS, min_size=2, max_size=4),
        colours=st.lists(_COLOURS, min_size=2, max_size=4),
        restrict_for=st.integers(min_value=-1, max_value=3),
    )
    def test_the_writer_builds_exactly_the_matrix_the_plan_promises(self, models, colours, restrict_for):
        # `woocommerce_direct` دو wrapper نازک روی همین دو توابع است؛ اگر روزی یکی‌شان
        # شمارشِ دیگری برای خود بسازد، این تست می‌گیردش (پیش‌نمایش ≠ سایت = فاجعه).
        from bot.services import woocommerce_direct

        distinct = set(plan.clean_values(models))
        restrictions = {}
        if restrict_for >= 0 and distinct:
            restrictions[sorted(distinct)[restrict_for % len(distinct)]] = colours[:1]
        data = {"models": models, "attributes": {"رنگ": colours}, "model_colors": restrictions}
        built = plan.plan_from_dict(data)
        payload = woocommerce_direct._combinations(
            woocommerce_direct._attributes(data), woocommerce_direct._model_color_restrictions(data)
        )
        self.assertEqual(built.count, len(payload), "تعداد واریژنِ payload با پیش‌نمایش نمی‌خواند")
        self.assertEqual(len(built.woo_attributes()), len(built.axes))
        if built.axes:
            self.assertNotIn("مدل", built.manifest_attributes(), "ZIP manifest محور مدل را تکرار نمی‌کند")


@unittest.skipUnless(HAS_HYPOTHESIS and HAS_SERVICES, _SKIP)
class TestPriceRangeGate(unittest.TestCase):
    """`PRICE_MIN..PRICE_MAX` دروازه است، نه فیلترِ بی‌صدا."""

    @_settings
    @given(price=st.integers(min_value=0, max_value=10**13))
    def test_every_out_of_range_price_is_named(self, price):
        data = {
            "title": "قاب سیلیکونی",
            "price": price,
            "sku_prefix": "BO",
            "models": ["iPhone 15"],
            "variation_count": 1,
        }
        report = validation.validate_draft(data, image_count=1)
        flagged = [issue for issue in report.issues if issue.code == "E_PRICE_RANGE"]
        if price and not 1_000 <= price <= 500_000_000:
            self.assertEqual(len(flagged), 1, f"قیمت {price} بیرون از بازه بود و گفته نشد")
            self.assertIn(f"{price:,}", flagged[0].message)
            self.assertTrue(report.blocking, "بازهٔ قیمت باید تأییدیه را ببندد، نه فقط هشدار بدهد")
        else:
            self.assertEqual(flagged, [])

    @_settings
    @given(value=st.integers(min_value=1, max_value=10**12))
    def test_a_group_price_is_checked_like_the_parent_price(self, value):
        data = {
            "title": "قاب",
            "price": 100_000,
            "prices": {"android": value},
            "sku_prefix": "BO",
            "models": ["iPhone 15", "Galaxy S24"],
            "variation_count": 2,
        }
        report = validation.validate_draft(data, image_count=1)
        flagged = any(issue.code == "E_PRICE_RANGE" and "android" in issue.message for issue in report.issues)
        self.assertEqual(flagged, not 1_000 <= value <= 500_000_000)


@unittest.skipUnless(HAS_HYPOTHESIS and HAS_SERVICES, _SKIP)
class TestModelMerging(unittest.TestCase):
    """P0-5: سه خطِ متفاوتِ آیفون سه مدل‌اند، و دو املا از یک مدل یکی."""

    @_settings
    @given(
        generation=st.integers(min_value=11, max_value=16),
        first=st.sampled_from(IPHONE_SUFFIXES),
        second=st.sampled_from(IPHONE_SUFFIXES),
    )
    def test_distinct_variants_are_never_merged(self, generation, first, second):
        text = f"Apple\n{generation} {first}\n{generation} {second}"
        labels = [model.label for model in phone_parser.extract_phone_models(text)]
        self.assertEqual(len(labels), len(set(labels)), f"لیبل تکراری در {text!r}")
        # هر خطی که تنها مدل می‌ساخت، باید در متنِ ترکیبی هم باشد: «ادغام» یعنی دو خطِ
        # هم‌معنی یکی شوند، نه اینکه مدلِ خطِ دوم خورده شود.
        for line in (first, second):
            for model in phone_parser.extract_phone_models(f"Apple\n{generation} {line}"):
                self.assertIn(model.label, labels, f"{model.label!r} از {text!r} غیب شد")

    @_settings
    @given(generation=st.integers(min_value=11, max_value=16), suffix=st.sampled_from(IPHONE_SUFFIXES))
    def test_every_recognised_variant_survives_into_the_label(self, generation, suffix):
        models = phone_parser.extract_phone_models(f"Apple\n{generation} {suffix}")
        self.assertEqual(len(models), 1)
        label = models[0].label
        self.assertTrue(label.startswith(f"iPhone {generation}"), label)
        if suffix.strip():
            self.assertNotEqual(label, f"iPhone {generation}", f"«{suffix}» در لیبل گم شد")

    @_settings
    @given(
        generation=st.integers(min_value=11, max_value=16),
        foreign=st.sampled_from(FOREIGN_VARIANTS),
    )
    def test_a_variant_of_another_brand_is_not_invented_for_iphone(self, generation, foreign):
        """«اولترا» آیفون ندارد: لیبل همان مدل می‌ماند و کاتالوگ جدای از لیبل هشدار می‌دهد."""
        models = phone_parser.extract_phone_models(f"Apple\n{generation} {foreign}")
        self.assertEqual([m.label for m in models], [f"iPhone {generation}"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
