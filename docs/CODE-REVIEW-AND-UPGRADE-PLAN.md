# TisaPostToWP — بررسی کامل منطق ربات + پلن آپگرید

> تاریخ: 2026-09-15 · برنچ: `arena/01a0a4a0-tisaposttowp` · کامیت پایه: `1ed2a6c`
> قلمرو: **ربات پایتون** (`main.py`, `bot/**`, `tests/**`, `requirements.txt`, `.env.example`, `README.md`).
> افزونهٔ WordPress داخل `tisa-product-importer.zip` خارج از قلمرو است؛ فقط جایی که رفتار ربات به *قرارداد*
> آن فایل گره خورده، علامت‌گذاری شده است.
> این سند **فقط پلن است** و هیچ کدی در ریپو تغییر نکرده است.

---

## ۰) روش بررسی

ده پاس مستقل روی ۳۴ فایل پایتون (۷٬۷۱۳ خط) اجرا شد و هر ادعا با اجرای واقعی کد تأیید شده است:

| پاس | تمرکز |
|---|---|
| ۱ | مسیر ورودی: `main.py` → `app.py` → رجیستری ماژول‌ها → ترتیب handlerها |
| ۲ | پیکربندی، `.env`، اسرار، اعتبارسنجی مقادیر |
| ۳ | RBAC / امنیت: sudo / admin / user، `data/roles.json`، پنل ادمین‌ها، «⚙️ تنظیمات» |
| ۴ | فلوهای `ConversationHandler`: tracking / compress / product / admins |
| ۵ | فلو محصول: album collector، دانلود، فشرده‌سازی، استخراج، پیش‌نمایش، confirm، حالت update |
| ۶ | پارسرها: `phone_parser`، `product_extractor`، `color_matrix` |
| ۷ | یادگیری خودکار: `learning` + اتصالش به `_learn_from_diff` و به prompt |
| ۸ | نوشتن در ووکامرس: `woocommerce_direct` (SKU، مدیا، دسته، batch variation، retry) |
| ۹ | عملیات: restart/supervisor، چت لاگ، مدیریت `/tmp`، پایداری/بازگشت |
| ۱۰ | بهداشت ریپو: تست‌ها، CI، مستندات، فایل‌های ترک‌شده، وابستگی‌ها |

ابزارها: `python -m py_compile` (سالم) · `unittest` + `pytest` (۹۹ تست پاس؛ ۴۲‌شان وقتی deps نصب نباشد skip می‌شدند) ·
ساخت `Application` با PTB 22.8 · اسکریپت‌های repro که خروجی‌شان در §۱۲ آمده است.

### کارت سلامت

| حوزه | وضعیت | توضیح کوتاه |
|---|---|---|
| اجرا/کامپایل | 🟢 | بدون خطای import؛ با PTB 22 هم ساخته می‌شود (۴ `PTBUserWarning`) |
| تست | 🟠 | ۹۹ تست، ولی صفر پوشش روی `product_flow`، `processor`، `woocommerce_direct`، `rbac`، `app`، `config` |
| صحت برداشت داده (قیمت/مدل/بارکد) | 🔴 | شش مسیرِ اثبات‌شده که دادهٔ غلط را **بی‌صدا** به مقصد می‌فرستند |
| جریان (flow/UX) | 🔴 | مکالمهٔ بی‌مهلت، اصلاح append-only، بدون idempotency، خروج ناقص از صفحه‌ها |
| عملیات/زیرساخت | 🟠 | نشت `/tmp`، نوشتن غیراتمیکی JSON، بدون CI/lockfile/health-check |
| امنیت/اسرار | 🔴 | شناسهٔ چت لاگ هاردکد در ریپو؛ افزودن ادمین بدون هیچ تأییدیه |
| مستندات | 🟠 | README در چند نقطه با کد نمی‌خواند؛ نسخهٔ ربات `0.1.0` vs افزونه `0.7.0` |

**حکم کلی:** اسکلت معماری خوب است (تفکیک `services/` از `modules/`، `CB.*` متمرکز، `asyncio.to_thread` برای
کارهای سنگین، fail-safe در `color_matrix`) و دو ماژول `color_matrix` و `learning` واقعاً مهندسی‌شده‌اند.
اما پاشنهٔ آشیل، **لایهٔ «برداشت داده» و «حالت جریان»** است: دقیقاً همان‌جا که README ادعای قطعی می‌کند
(«پیش‌نمایش، تعداد واقعی variation را نشان می‌دهد»، «آخرین خط برنده است») هیچ تستی وجود ندارد و ادعا با یک
ورودی واقع‌نما نقض می‌شود. اولویت، بستن مسیر «دادهٔ غلط → سایت زنده» است، نه افزودن قابلیت.

---

## ۱) P0 — دادهٔ غلط را بی‌صدا به خروجی نهایی می‌رساند

نقطهٔ مشترک همهٔ این موارد: **fail-open**. وقتی سیستم مطمئن نیست، حدس را می‌فرستد به‌جای اینکه بایستد و بپرسد.

### P0-1 · بارکدِ خراب‌شده در `tracking.csv` نوشته می‌شود

- محل: `bot/services/processor.py:226` (`build_csv`) در برابر `_analyze:193-198`
- اثبات (خروجی واقعی اجرا):

  ```
  --- SUMMARY ---   ❌ ردیف 1: بارکد به‌صورت عدد ذخیره شده و دقتش از بین رفته
  --- CSV ---       order_id,tracking_code
                    306176,192999999999999989514240     ← این عدد ساختگی است
                    778899,6100015738451234567           ← بارکد ۱۹ رقمی نامعتبر
  ```

- چرا خطرناک: `1.93E+23` یک بارکد واقعی نیست، ولی در CSV کاملاً معقول به‌نظر می‌رسد. اگر فایل به سامانهٔ
  رهگیری بخورد، کد رهگیری غلط روی سفارش می‌نشیند و در چت هیچ نشانه‌ای نیست (فقط در `problems.txt` که کسی
  باز نمی‌کند).
- اصلاح: هر سلولی که `_analyze` برایش `❌` داده، در CSV **خالی** بماند (همان سیاستی که برای «کد سفارش خالی»
  اعمال شده) + ستون `needs_review` + یک خط شمارش در خلاصه: `🚫 ۲ ردیف به‌دلیل بارکد نامعتبر خالی ماند`.
- معیار پذیرش: تست golden-file با «بارکد عددی» و «بارکد ۱۹ رقمی» که assert می‌کند سطر متناظر CSV خالی است.

### P0-2 · هر عدد ≥۳ رقمی می‌تواند «قیمت» شود

- محل: `bot/services/product_extractor.py:189-232` (`_scan_prices`)؛ کلیدها خط ۲۱۹
  (`if not explicit_price and len(raw_digits) < 3`) و خط ۲۲۵ (`elif explicit_price or not price or not price_explicit`)
- خروجی واقعی اجرا:

  | ورودی | برداشت ربات |
  |---|---|
  | `وزن 250 گرم` | قیمت = **۲۵۰٬۰۰۰** تومان |
  | `1403/01/01` | قیمت = ۱٬۴۰۳ |
  | `SKU: BO147` | قیمت = ۱۴۷٬۰۰۰ |
  | `کد ملی 1234567890` | قیمت = ۱٬۲۳۴٬۵۶۷٬۸۹۰ |
  | `کد رهگیری 610001573845123456789012` | قیمت = ۶٫۱e۲۳ |
  | پیام واقعی: `قیمت ایفون 698` + `اندروید 598` + `وزن 250 گرم` | قیمت والد = **۲۵۰٬۰۰۰**، واریژن‌ها ۶۹۸٬۰۰۰/۵۹۸٬۰۰۰ |

- چرا: قاعدهٔ «آخرین خط برنده» (که برای رفع باگ اصلاحات اضافه شده) هیچ گاردی روی **شکل خط** ندارد؛ پس
  «۲۵۰ گرم» که بعد از قیمت آمده، قیمت را بازنویسی می‌کند. نتیجه در سایت: بازهٔ قیمت ۲۵۰٬۰۰۰ تا ۶۹۸٬۰۰۰.
- اصلاح: (۱) رد هر خطی که لیبلِ غیرقیمت دارد (`وزن|تاریخ|کد|شناسه|sku|تعداد|ردیف|سال|گارانتی|ابعاد|کد ملی`)؛
  (۲) عدد برهنه فقط وقتی قیمت است که خط هیچ لیبل دیگری نداشته باشد؛ (۳) دامنهٔ معقول قیمت از تنظیمات
  (`PRICE_MIN`/`PRICE_MAX`، پیش‌فرض ۱۰٬۰۰۰–۵۰٬۰۰۰٬۰۰۰) → خارج از دامنه یعنی «نیازمند بازبینی»، نه پذیرش.

### P0-3 · عددِ اول خط به‌جای عددِ چسبیده به واحد خوانده می‌شود

- محل: `product_extractor.py:130` (`_number_from_line`)
- اثبات: `S24 اولترا 768t` → قیمت = **۲۴** (تومان!) · `A25 مشکی 598k` → ۲۵ · `17promax 800t` → ۸۰۰٬۰۰۰ (شانسی درست)
- چرا: الگوی `(?<!\d)([۰-۹٠-٩\d][...])\s*(تومان|تومن|هزار|ت|t|k)?` اولین عدد خط را می‌گیرد و واحد **اختیاری**
  است؛ روی خط مدل، آن عدد شمارهٔ مدل است. بدتر: در `_scan_prices` تعریف `explicit_price` شامل
  `\d\s*[tTkKت]\b` است، پس وجود `768t` کل «گارد خط مدل» را دور می‌زند.
- اصلاح: اول عدد دارای واحد را پیدا کن و همان برنده است؛ اگر نبود، عدد برهنه. روی `model_line` فقط نسخهٔ
  دارای واحد پذیرفته شود و همان عددِ دارای واحد برداشته شود (نه اولین عدد خط).

### P0-4 · قیمت دو گروه در یک خط: یکی گم می‌شود و قیمتِ اشتباه جایش می‌نشیند

- محل: `product_extractor.py:222-227` (`if iphone → elif android → else`) + `woocommerce_direct.py:128` (`_price_for_model`)
- اثبات: `قیمت ایفون 698 اندروید 598` → `prices={'iphone': 698000}` و **`android` غایب**؛ سپس
  `_price_for_model` برای مدل‌های اندروید به `common` برمی‌گردد که خودش برابر قیمت آیفون است
  (`common_price = data.price or next(iter(prices.values()))`).
- نتیجه: همهٔ واریژن‌های سامسونگ/شیائومی **به قیمت آیفون** منتشر می‌شوند، بدون هیچ هشداری.
- اصلاح: اسکن همهٔ جفت‌های (واحد/گروه، عدد) در خط به‌جای `elif`؛ و اگر گروهی در `prices` نیست، به‌جای
  fallback به `common`، تأییدیه را متوقف کن: «قیمت اندروید مشخص نیست».

### P0-5 · مدل‌های فارسی‌زبان در یک مدل ادغام می‌شوند (واریژن از دست می‌رود)

- محل: `bot/services/phone_parser.py:140` (regex `_parse_single_iphone`) و `:215` (regex تطبیق)
- اثبات:

  ```
  Apple / «13 پرو مکس»  → 'iPhone 13'
  Apple / «13 پرو»      → 'iPhone 13'
  Apple / «13»          → 'iPhone 13'      ← هر سه یکی!
  Apple / «15max»       → 'iPhone 15'      ← alias «max» در IPHONE_VARIANTS هست، در regex نیست
  Apple / «15 Max»      → 'iPhone 15'
  ```

- چرا: `aliases` در `_iphone_variant` «max» را می‌شناسد، ولی الگوی
  `(\d{1,2})(mini|air|promax|pro\s*max|pro|plus|\+)?` هرگز `max` تنها را capture نمی‌کند؛ و معادل‌های فارسی
  (پرو، مکس، پرومکس، پلاس، مینی، اولترا) در هیچ لایه‌ای وجود ندارند.
- اثر: سه مدلِ متمایزِ فروشنده → یک مدل → واریژن‌های سوخت‌نشده + قیمت/SKU روی مدل اشتباه؛ **بدون هیچ خطایی**.
  تست‌های موجود این را نمی‌گیرند چون همهٔ ورودی‌هایشان لاتین است.
- اصلاح: (۱) `VARIANT_LEXICON` (فارسی/عربی/لاتین → توکن کَنونیکال) هم‌ساختِ `COLOR_LEXICON`؛ (۲) افزودن `max`
  به الگو؛ (۳) **قاعدهٔ عدم‌ادغام**: اگر دو خطِ مدل به یک لیبل کَنونیکال برسند در حالی که متن‌شان فرق دارد →
  خطای «ابهام مدل»، نه ادغام.

### P0-6 · قول «پیش‌نمایش = واقعیت» نقض می‌شود

- محل: `color_matrix.py:813` (`variation_count` — بدون dedupe) در برابر `woocommerce_direct.py:158`
  (`_attributes` — dedupe با `_clean_options` و حذف محورِ `<2`)
- اثبات:

  ```
  attrs = {"رنگ": ["سفید", "سفید"]}
  preview variation_count : 4
  what Woo actually gets  : [{'name': 'مدل', ...}]   ← محور رنگ کلاً حذف می‌شود
  ```

- اثر: در تلگرام «۴ واریژن»، در سایت ۲ تا؛ در بدترین حالت محصول `variable` به `simple` فرو می‌ریزد و کاربر
  فکر می‌کند منتشر شده.
- اصلاح: **یک تابع واحد** `resolve_variation_plan(data) -> (attrs, combos)` که `variation_count`،
  `payload["attributes"]` و `product.json` مانیفست هر سه از همان یک نتیجه بخورند. این کار وابستگی معکوس
  فعلی (`woocommerce_direct` → `color_matrix` که خودش `_attributes` را مقایسه می‌کند) را هم درست می‌کند.

### P0-7 · اعتبارسنجی `mode=new` مدل را الزامی نمی‌کند

- محل: `product_flow.py:634` — `required_missing = (not data.price or not data.title or not data.sku_prefix)`
- اثر: پستی که مدلش تشخیص داده نشده (برند پشتیبانی‌نشده، کپشن خالی، AI قطع‌شده و fallback خالی) با موفقیت
  به‌عنوان محصول `simple` ساخته می‌شود. مسیر ZIP برعکس است (آنجا `models` الزامی است) → دو مسیر، دو استاندارد.
- اصلاح: یک `validate(data, mode)` مشترک در `bot/services/validation.py` که هر دو مسیر (REST و ZIP) از همان
  عبور کنند؛ اگر `models` خالی بود → دکمهٔ تأیید غیرفعال با پیام «مدلی پیدا نشد؛ اصلاحش کن».

### P0-8 · double-submit: دو بار تأیید = دو محصول و دو SKU

- محل: `product_flow.py:622` (`confirm`) — هیچ قفل/توکن/وضعیت «در حال ارسال» نیست و `create_draft` چند ثانیه
  طول می‌کشد (آپلود مدیا + اسکن SKU).
- اثر: دو ضربهٔ متوالی (عادت موبایل) → دو پیش‌نویس تکراری با SKUهای پشت‌سرهم.
- اصلاح: `asyncio.Lock` به‌ازای کاربر + فلگ `session.submitting` + `confirm_token` کوتاه داخل `callback_data`
  که یک‌مصرفی باطل می‌شود؛ هم‌چنین **idempotency key** در `meta_data` ووکامرس
  (`tisa_batch_id = sha1(user_id + manifest_hash)`) تا اگر پاسخ HTTP گم شد، همان کلید جستجو و *به‌روزرسانی*
  شود نه محصول تازه ساخته شود.

### P0-9 · شناسهٔ چت لاگ هاردکد شده و حتی با `LOG_CHAT_ID=` خالی هم فعال است

- محل: `bot/config.py:31` (`log_chat_id: int | None = -5061365940`) و `bot/config.py:67`
  (`os.getenv("LOG_CHAT_ID", "-5061365940").strip() or "-5061365940"`)
- اثبات: با `LOG_CHAT_ID=""` (دقیقاً همان چیزی که `.env.example` تحویل می‌دهد) مقدار نهایی `-5061365940` است.
- چرا: (۱) یک شناسهٔ واقعی چت در ریپوی عمومی افشا شده؛ (۲) هر دیپلوی جدید — از جمله تست‌های دیگران —
  «کامل‌ترین لاگ پردازش محصول» (شامل متن کپشن‌ها، قیمت‌ها و SKUها) را به چتِ مالکِ این ریپو می‌فرستد؛
  (۳) برخلاف README که «اختیاری» می‌گوید، هیچ راهی برای خاموش‌کردنش نیست جز مقدار غیرخالیِ متفاوت.
- اصلاح: پیش‌فرض `None`؛ اگر مقدار داده شد ولی ربات عضو چت نیست، در `post_init` یک تست `get_chat` انجام بده
  و نتیجه را در پیام استارتарт/`📊 وضعیت` نشان بده (نه `except Exception: return` بی‌صدا در
  `product_flow.py:79-88`).

### P0-10 · `confirm` هیچ بازبینی نهایی روی «چیزی که واقعاً ارسال می‌شود» ندارد

- محل: `product_flow.py:640-660` (مسیر `new` → `create_draft`) و `:662-686` (مسیر `update` → ZIP)
- مشکل: دو مسیرِ خروجی، دو مجموعهٔ قواعد دارند. `new` از `data.to_dict()` + `_attributes()` می‌آید، `update` از
  یک دیکشنری `manifest` دستی که فیلدهایش را دستی ساخته‌ایم. همین باعث شده:
  * `description` و `product_type` داخل مانیفست نوشته می‌شوند ولی مصرف‌کنندهٔ ZIP آن‌ها را نمی‌خواند (فیلد مرده)؛
  * `categories` در حالت `update` در مانیفست می‌نشیند در حالی که اصلاً اعمال نمی‌شود — ولی پیش‌نمایش تلگرام
    آن را به‌عنوان «اعمال‌شده» نشان می‌دهد؛
  * محدودیت‌های `new` و `update` یکی نیست (P0-7).
- اصلاح: یک قرارداد دادهٔ واحد (`ProductDraft` v2 در §۶) که هر دو خروجی از `draft.to_manifest()` /
  `draft.to_woo_payload()` تولید شوند؛ فیلد مرده حذف یا مستند شود.

---

## ۲) P1 — صحت/پایداری (کار می‌کند تا اولین حالت واقعیِ کج)

### P1-1 · بعد از لغو، پردازش آلبوم با `KeyError` می‌ترکد و به چشم کاربر می‌آید

- `product_flow.py:530-541` (`_flush_album`) → `:478` (`session = sessions[user_id]`)
- `_cleanup()` در `cancel`/`exit_command` سشن را `pop` می‌کند، اما هیچ‌کدام `album_tasks` را لغو نمی‌کنند.
  تسک معلق بیدار می‌شود، `KeyError` می‌گیرد و کاربر پیام
  «❌ خطا در پردازش عکس‌ها: KeyError: 123456» را **بعد از** زدن «❌ لغو» می‌بیند.
- اصلاح: در `cancel`/`exit_command`/`confirm` → `for k, t in album_tasks.items(): if k[0] == user.id: t.cancel()`
  و `album_buffers` را هم پاک کن؛ `_prepare_files` با `sessions.get(user_id)` شروع کند و اگر نبود silently خارج شود.

### P1-2 · آلبوم دیرهنگام = پردازش دوباره و دور ریختن عکس‌های اول

- `product_flow.py:561-575`: `_flush_album` ابتدا `album_tasks.pop(key)` می‌کند؛ اگر عکس با همان
  `media_group_id` بعد از مهلت ۱.۸s برسد، تسک **تازه**ای ساخته می‌شود و `_prepare_files` اجرا می‌شود، در حالی
  که `session.files.clear()` (خط ۴۸۰) لیست قبلی را می‌ریزد دور → محصول فقط با آن یک عکس باقی‌مانده ساخته می‌شود.
  روی شبکهٔ موبایل ایران کاملاً محتمل است.
- اصلاح: جمع‌آوری بر پایهٔ «دکمهٔ پایان» (کاربر «✅ ارسال تصاویر تمام شد» را می‌زند) + fallback زمان‌سنج؛
  حالت پیش‌فرض **additive** باشد (ضمیمه کردن، نه جایگزینی) و `session.images_epoch` از تکرار جلوگیری کند.

### P1-3 · نشت `/tmp` (نشت دیسک روی هاست اشتراکی)

- `product_flow.py:689-693` (`_cleanup`) فقط `path.parent` را پاک می‌کند، یعنی `root/compressed/`؛
  **عکس‌های اصلِ دانلودشده در `root/` می‌مانند.** اثبات:

  ```
  after _cleanup: compressed still there: False | ORIGINAL still there: True
  leftover tree : ['7_1234', '7_1234/01_photo.jpg']
  ```

- به‌علاوه: اگر کاربر جریان را نیمه‌کاره رها کند (بدون تأیید/لغو) هیچ جارویی نیست؛ `entry` هم وقتی سشن تازه
  می‌سازد، دایرکتوری سشن قبلی را پاک نمی‌کند. با ~۱۰ عکس × هر محصول، `/tmp` پر می‌شود.
- اصلاح: `_cleanup` باید `shutil.rmtree(root, ignore_errors=True)` کند (ریشهٔ سشن، نه والد هر فایل)؛
  یک `JobQueue` روزانه `TEMP_DIR/*` با mtime قدیمی‌تر از N ساعت را بتراشد؛ `product_flow` هم در
  `post_init` یک sweep اولیه بزند.

### P1-4 · مکالمهٔ «چسبنده»: هیچ مهلتی ندارد و حالت را رها نمی‌کند

- `product_flow.py:720-736`: `ConversationHandler` بدون `conversation_timeout`، بدون
  `allow_reentry`، بدون دکمهٔ بازگشت در حالت `WAITING`.
- پیامد: هر متنِ بعدیِ کاربر — حتی «سلام» یا یک سوال بی‌ربط — در `on_text` (خط ۵۸۴) به `info_text` **الحاق**
  می‌شود و دوباره کل خطِ استخراج (۲ درخواست AI) را اجرا می‌کند؛ هر عکسی هم دوباره `_prepare_files` را
  فعال می‌کند و می‌تواند جایگزین فشرده‌سازیِ مستقل (`image_compress`) شود، چون `product_flow` زودتر ثبت شده
  و `WAITING` آن ابتدا match می‌شود.
- اصلاح: `conversation_timeout` (مثلاً ۱۵ دقیقه) با پایان‌بندیِ پاک‌کننده (`_cleanup`)؛ افزودن
  `⬅️ بازگشت به منو` به صفحهٔ پیش‌نمایش که واقعاً `END` کند؛ و یک قاعدهٔ «یک جریان فعال به‌ازای هر کاربر»
  (`app.bot.data['active_flow']`) که پیش از ورود، جریان قبلی را ببندد.

### P1-5 · اصلاحات فقط append می‌شوند؛ «اصلاح فیلد» وجود ندارد

- `product_flow.py:584`. کاربر می‌نویسد «عنوان: قاب سیلیکونی» تا عنوان غلط را عوض کند، ولی خط قبلی هم در متن
  می‌ماند. برای قیمت، شانس می‌آوریم (آخرین خط برنده)؛ برای عنوان/SKU/ویژگی، `_fallback` همچنان ممکن است خط
  اول را بردارد. یادگیری‌هم فقط «جایگزینی یک واژه» یا «ضریب توانِ ۱۰» را می‌فهمد.
- اصلاح: گفت‌وگوی **فیلد‌محور** (هر فیلد در پیش‌نمایش یک دکمهٔ ✏️ داشته باشد → کاربر فقط مقدار جدید را
  بنویسد → `session.overrides[field] = value`) و `overrides` بالاترین اولویت را در ساخت `ProductDraft` داشته
  باشد. متن آزاد فقط برای «پیشنهاد» بماند، نه منبع حقیقت.

### P1-6 · هر پیام = دو درخواست AI؛ در عکس‌ها هم AI اجرا می‌شود برخلاف کامنت

- `product_flow.py:_extract` → هم `ai_normalize` (خط ~۳۴۵) و هم `extract_product` (خط ~۳۵۰) را صدا می‌زند؛
  هر کدام `httpx` با timeout 90s. کامنت «Do not make an unnecessary second AI request while only the photos
  are being processed» با کد نمی‌خواند: در `_prepare_files:515` هم `_extract` اجرا می‌شود و `combined_text`
  به‌خاطر کپشن خالی نیست → درخواست AI می‌رود.
- اثر: تأخیر، هزینه، و **غیرقطعی‌بودن** (دو بار extraction روی یک متن می‌تواند دو نتیجهٔ متفاوت بدهد →
  «اصلاح من اثر نکرد»).
- اصلاح: یک AI call در هر «دور» با debounce (۲–۳ ثانیه بعد از آخرین پیام)، کش روی `sha1(text + rules_version)`،
  و merge: خروجی AI فقط *پیشنهاد* است و با deterministic reconcile می‌شود (مدل‌ها از deterministic،
  عنوان/ویژگی‌ها از AI با اعتبارسنجی، قیمت هرگز از AI نه — P0-2/P0-3).

### P1-7 · کد مرده و خاموش‌کنندهٔ خطا در مسیر AI

- `bot/services/ai_normalizer.py:99-103`:
  `if _deterministic_is_safe(deterministic): return deterministic` و سپس دوباره `return deterministic`
  — هر دو شاخه یکی است؛ یعنی «خروجیِ ناامنِ deterministic» هم بی‌_check استفاده می‌شود و `_deterministic_is_safe`
  کلن مرده است (احتمالاً باید `""` برمی‌گرداند یا هشدار می‌داد).
- `product_flow.py:70-77`: `_ExtractionLog.add()` همه‌چیز را `return None` می‌کند → هیچ‌یک از پیام‌های
  «AI is not configured» و «AI normalization failed» به لاگ نمی‌رسد. خرابی ۱۰۰٪ AI کاملاً نامرئی است.
- اصلاح: لاگ‌ها را به `logging` واقعی وصل کن (`logger.warning`)، `_ExtractionLog` را حذف کن (پارامتر `job_log`
  از امضای `ai_normalize` هم حذف شود)، و `_deterministic_is_safe` یا حذف شود یا واقعاً استفاده شود.

### P1-8 · دسته‌بندی‌ها: تطبیق شلِ سلسله‌مراتب

- `woocommerce_direct.py:_resolve_categories` (خط ~۶۶۰): اگر سطح والد پیدا نشود، حلقه با `continue` رد می‌شود و
  `parent_id` کهنه می‌ماند؛ و در نهایت `exact = exact or (matches[0] if matches else None)` یعنی «هر چیزی که
  شبیه آن نام بود، فارغ از والد». خطر: اتصال محصول به دستهٔ اشتباه با نام یکسان در شاخهٔ دیگر.
- اصلاح: جستجوی سطح‌به‌سطح با `parent=<id>` الزامی؛ اگر والد پیدا نشد، بقیهٔ مسیر رد شود و در پیش‌نمایش
  «⚠️ دسته پیدا نشد» نمایش داده شود (نه حذف بی‌صدا در `product_flow` / نه `continue`).

### P1-9 · ساخت product با حالت ناسازگار: والد `draft`، واریژن‌ها `publish`

- `woocommerce_direct.py:_create_variations` → `"status": "publish"` برای هر واریژن زیر والدی که
  `"status": "draft"` است؛ ووکامرس برای واریژن `publish` در فهرست/ساختار نامتعارف تولید می‌کند. هم‌چنین
  batch اگر در میانهٔ یک chunk بترکد، `raise WooCommerceAPIError` می‌کند و محصولِ نیمه‌ساخته روی سایت
  می‌ماند (بدون rollback) و تصویرِ آپلودشده بی‌صاحب می‌ماند.
- اصلاح: `status: "private"` یا همان `draft` برای واریژن‌ها؛ wrap کردن کل create در یک `try` که در صورت
  شکست، `DELETE /products/{id}?force=true` بزند (rollback) و در `finally` تصویرهای بی‌صاحب را پاک کند؛
  حالت resume: قبل از batch، واریژن‌های موجود را فهرست کن و فقط کمبودها را بساز (idempotent).

### P1-10 · تخصیص SKU: کند، مسابقه‌ای، و دو پیاده‌سازی رقیب

- `woocommerce_direct.py:346-380` (`_next_sku`): تا ۵۰ صفحه جستجو + ۵۰ صفحه اسکن کامل + تا ۲۰۰ probe،
  همه سریال و در مسیرِ کاربر → در هاست اشتراکی ۱۰–۶۰ ثانیه.
- هم‌زمان افزونهٔ PHP هم `self::next_sku($prefix)` خودش را دارد → دو الگوریتم، یک جدول؛ اگر روزی هر دو
  استفاده شوند، برخورد SKU قطعی است.
- اصلاح: یک مسیر واحدِ «دریافت SKU از سرور» (endpoint افزونه با شمارندهٔ اتمیک) + fallback اسکن؛
  کش `last_seen_sku` در `data/sku_state.json` برای پرش اولّیه؛ و `_sku_exists` فقط وقتی اجرا شود که
  `candidate >= cached_max + 1` اعتبارسنجی لازم باشد.

### P1-11 · ماتریس رنگ بین دو پیام «نشت» می‌کند

- `product_flow.py:_apply_color_matrix(session, model_source)` یک رشتهٔ به‌هم‌چسبانده
  (`caption + "\n" + info_text`) می‌گیرد، پس خطِ رنگِ عمومیِ پیام دوم به «مدل در انتظارِ کپشن» چسبیده
  می‌شود. اثبات:

  ```
  caption: Apple / 17pro max / 16 pro    +   info: «سفید/مشکی/نارنجی»
  → iPhone 16 Pro محدود به همان ۳ رنگ شد؛ iPhone 17 Pro Max بدون محدودیت ماند
  ```

- اثر: بسته به ترتیب، یا یک مدل واریژن از دست می‌دهد یا برعکس بی‌‌دلیل آزاد می‌شود.
- اصلاح: `parse_color_matrix` را **به‌ازای هر پیام** با `scope` (message id، نوع پیام) صدا بزن و فقط
  پیام‌هایی که `kind == MEDIA_CAPTION` دارند اجازهٔ «انتساب به مدل قبلی» داشته باشند.

### P1-12 · تنظیمات: کرش در import و بارگذاری وابسته به cwd

- `bot/config.py:41-75`: `float(os.getenv("ALBUM_WAIT_SECONDS", "1.8"))` و `int(...)` برای `IMAGE_QUALITY` و
  `LOG_CHAT_ID` با مقدار خراب → `ValueError` در **زمان import**، یعنی ربات بالا نمی‌آید و supervisor فقط
  crash-loop می‌کند. اثبات: `ALBUM_WAIT_SECONDS=abc → ValueError: could not convert string to float: 'abc'`
- `bot/config.py:10` `load_dotenv()` بدون مسیر → اگر `command=python3 /path/main.py` بدون `directory=`
  اجرا شود، `.env` اصلاً خوانده نمی‌شود (درحالی‌که `restart.py:59` عمداً `dotenv_values(<repo>/.env)` را با
  مسیر مطلق می‌خواند — دو رفتار متفاوت در یک پروژه).
- `settings = Settings.from_env()` در سطح ماژول → هر تستی (و هر اسکریپتی) باید `BOT_TOKEN` جعلی بدهد؛
  دلیل ۴۲ skip در تست‌ها همین است.
- اصلاح: `Settings.load()` با `@lru_cache`، اعتبارسنجی typecoercion با پیام خوانا و fallback به مقدار پیش‌فرض
  + یک لاگ `WARNING`؛ `load_dotenv(Path(__file__).resolve().parents[1] / ".env")`.

### P1-13 · RBAC/تنظیمات: IO همزمان داخل event loop، نوشتن غیراتمیکی

- `bot/rbac.py:56-80` و `bot/services/preferences.py:24-59`: هر `role()` / `is_allowed()` /
  `button_visible()` کل فایل JSON را از دیسک می‌خوانند؛ در یک آپدیت سادهٔ منو چند بار اتفاق می‌افتد و همه
  **sync** داخل coroutine هستند (بلوکرِ ریز اما روی هاست IO-کُند محسوس).
- `ROLES_FILE.write_text(...)` اتمیک نیست (بدون temp+rename، بدون fsync)؛ اگر ربات وسط نوشتن کشته شود
  (دکمهٔ ری‌استارت دقیقاً همین کار را می‌کند!) فایل نیمه‌کاره می‌ماند و `_load()` به `except ValueError`
  برمی‌خورد → **همهٔ ادمین‌ها پاک می‌شوند** و ربات با منوی خالی بالا می‌آید.
- اصلاح: یک `JsonStore` مشترک (کش در حافظه + mtime-check + `os.replace` اتمیک + `fcntl.flock`)؛
  نوشتن در `asyncio.to_thread`؛ در `post_init` اعتبارسنجی schema و در صورت خرابی، بازنشانی از
  `roles.json.bak` (نگهداشتِ یک نسخهٔ قبل از هر نوشتن).

### P1-14 · افزودن ادمین با یک عدد، بدون هیچ تأییدیه

- `bot/modules/admins.py:272-291`: هر عدد ≥۵ رقمی (`\d{5,}`) به `rbac.add_admin` می‌رسد؛ `get_chat` اگر
  شکست بخورد `pass` می‌شود و کاربر در لیست می‌ماند. یعنی: تایپ یک عدد → ساخت دسترسی. هیچ راهی برای
  اطمینان از اینکه آن شخص ربات را می‌شناسد نیست، و اشتباه تایپی = یک ردیف زباله در `roles.json`.
- اصلاح: الگوی «penned-invite»: (۱) sudo آیدی/یوزرنیم را می‌دهد → ربات یک **کد تأیید** می‌سازد و
  وقتی خودِ آن کاربر `/start TISA-INVITE-XXXX` زد، ادمین فعال شود (مهلت ۲۴ ساعت)؛ (۲) اعتبارسنجی
  `1_000_000 <= uid <= 9_999_999_999`؛ (۳) اگر `get_chat` خطا داد، پیش‌نمایش «این کاربر ربات را استارت
  نزده» و تأیید صریح بخواهد.

### P1-15 · وابستگی‌ها بدون pin، بدون CI، بدون lockfile

- `requirements.txt` هیچ upper-bound ندارد؛ `>=21.0` یعنی PTB 22.8 هم نصب می‌شود (اینجا تست شد: می‌سازد،
  اما ۴ × `PTBUserWarning` دربارهٔ `per_message=False` با `CallbackQueryHandler` در stateهای
  `ConversationHandler` — یعنی اگر آپدیتی رفتار match را عوض کند، ربات بی‌صدا می‌شکند).
- هیچ `.github/workflows`، `pyproject.toml`، `ruff`/`mypy`، `Dockerfile`، `CHANGELOG` وجود ندارد؛
  `requirements.txt` تنها راهنمای نصب است.
- اصلاح در فاز ۱: `pyproject.toml` + `uv lock` (یا `requirements-dev.txt` pinned)، `ruff` + `mypy`، CI که
  `pytest -q --cov` و `ruff check` و `python -W error::DeprecationWarning -c "from bot.app import build_application; build_application()"`
  را اجرا کند (warning-as-error تا PTB 23 ما را غافلگیر نکند).

### P1-16 · رفتارهای سطح-اپلیکیشن که کاربر را اذیت می‌کنند

- `main.py:17` `drop_pending_update(pending_updates=True)` → هر چیزی که کاربر موقع ری‌استارت فرستاده
  (عکس محصول، فایل اکسل) **بی‌صدا دور ریخته می‌شود**؛ با دکمهٔ «🔄 ری‌استارت» این حالت عمداً رخ می‌دهد.
- هیچ `Persistence` نیست → جریان نیمه‌کاره با هر ری‌استارت گم می‌شود (و `/tmp` فایل‌هایش می‌ماند).
- `app.py:57-63` فقط `set_my_commands` سراسری دارد؛ ادمین‌ها دکمه‌های sudo را در لیست `/commands` می‌بینند.
- `bot/utils/logging.py:8-14`: بدون file handler / rotation؛ `getattr(logging, level, ...)` با مقدار
  کوچک‌نویس بی‌صدا به INFO می‌افتد.
- اصلاح: `drop_pending_updates` فقط با فلگ محیطی؛ `PicklePersistence(filepath='data/ptb.pkl')`؛
  `set_my_commands(..., scope=BotCommandScopeChat(id))` برای نقش‌ها؛ `RotatingFileHandler`.

---

## ۳) P2 — کیفیت، نگهداشت، UX

| # | ایراد | محل |
|---|---|---|
| P2-1 | `CB.PHONE_POST` هیچ دکمه‌ای ندارد (مرده)؛ `PHONE_IMAGE_*` فقط در `mode=update` دیده می‌شود | `constants.py:32-37`, `buttons.py:25-35` |
| P2-2 | رشته‌های کاربری دوزبانه و بی‌قاعده: `fallback.py` و `on_error` انگلیسی‌اند بقیهٔ ربات فارسی | `fallback.py:28,34,51` |
| P2-3 | متن خطا مستقیماً `type(exc).__name__: exc` را به کاربر نشان می‌دهد (اطلاعات داخلی، بدون ارزش عملی برای ادمین فروشگاه) | `product_flow.py:656-659, 686` |
| P2-4 | README: (الف) خط ۷۰ جدول متغیرها با `\n` literal شکسته شده؛ (ب) می‌گوید ادمین «فقط» مبدل ردیابی را دارد، درحالی‌که `buttons.py` چهار دکمهٔ `admin_eligible` دارد؛ (ج) متن ثابت `admins.py:85` هم همان ادعای کهنه را در UI نشان می‌دهد؛ (د) `LOG_CHAT_ID` «اختیاری» معرفی شده درحالی‌که پیش‌فرض هاردکد دارد (P0-9) | `README.md:70`, `admins.py:85` |
| P2-5 | نسخهٔ ربات `0.1.0` (`bot/__init__.py:3`) vs افزونهٔ `0.7.0`؛ هیچ `CHANGELOG` و هیچ release فرآیندی نیست | `bot/__init__.py` |
| P2-6 | چهار فایل `.pyc` از ربات قبلی در گیت ترک شده: `OPTION/tests/__pycache__/*.pyc` — کل پوشهٔ `OPTION/` فقط همین است | `git ls-files` |
| P2-7 | `tisa-product-importer.zip` (باینری ساخته‌شده) در ریپو ترک شده؛ منبع افزونه جای دیگری است → نسخه‌ها واگرا می‌شوند | ریشهٔ ریپو |
| P2-8 | `product_flow.py` ۷۳۸ خط / ۵۶ خط >۱۱۰ کاراکتر؛ `manifest = {...}` و `_preview` هر کدام یک‌خطی‌های چندصدکاراکتری‌اند (غیرقابل‌ریویو، غیرقابل‌تست) | `product_flow.py:169-186, 674` |
| P2-9 | شکست لایه‌بندی: `product_flow` (UI) از `product_extractor` اسم خصوصی `_number_from_line` را import می‌کند؛ `learning` مستقیماً داخل `product_extractor` فراخوانی می‌شود؛ `services/` (که باید «بدون Telegram» باشد) در `product_flow` با `Message`/`Update` درامدی دارد | `product_flow.py:39-45` |
| P2-10 | ۲۱ × `except Exception` که ۹ تایشان `pass`/`return` خالی‌اند → خرابی‌ها خاموش می‌شوند | کل `bot/` |
| P2-11 | `image_compressor.IMAGE_QUALITY = settings.image_quality` در import گرفته می‌شود (با restart قابل‌تغییر نیست، برخلاف `restart.py:52-65` که عمداً runtime می‌خواند) | `image_compressor.py:7` |
| P2-12 | `MAX_DOWNLOAD_MB` فقط در جریان محصول چک می‌شود؛ جریان تبدیل فایل ردیابی سقف حجم/ردیف/زمان ندارد (یک اکسل ۲۰۰هزار ردیفی در `to_thread` می‌تواند ربات را قفل/OOM کند) | `tracking_converter.py:100-108` |
| P2-13 | پوشهٔ موقت هر کاربر با `time.time()*1000` نام‌گذاری می‌شود؛ اگر دو ورودی در همان میلی‌ثانیه بیایند (آلبوم دوتایی) برخورد نام ممکن است | `product_flow.py:479` |
| P2-14 | عکس‌ها از `message.photo[-1]` گرفته می‌شوند: تلگرام عکس را بازکدگذاری و محدود ارتفاع می‌کند → برای «قاب چاپی» کیفیت منبع از دست می‌رود؛ ربات راهنمایی نمی‌کند که فایل اصلی را به‌صورت Document بفرستند | `product_flow.py:_media` |
| P2-15 | `_analyze` بارکد را «دقیقاً ۲۴ رقم» الزام می‌کند؛ EAN-13/UPC یا بارکد ۲۶ رقمی تیساچاپ خطا می‌خورد درحالی‌که معتبر است | `processor.py:68` |
| P2-16 | `_find_header` فقط ۲۰ سطر اول را می‌بیند و اگر هدر جابه‌جا باشد، خطای عامیانه «ستون بارکد پیدا نشد» می‌دهد (بدون گفتن اینکه چه ستون‌هایی دید) | `processor.py:92-103` |
| P2-17 | PDF فقط با مختصات ثابت رندرِ مشخص کار می‌کند (`x0>535`, `420..535`, `235..420`) — بدون تست fixture در ریپو؛ هر تغییر قالب خروجی سامانه = خاموشی کامل | `processor.py:138-174` |
| P2-18 | تست‌ها: ۹۹ تست در ۳ فایل، همگی روی `color_matrix`/`learning`/`phone_parser`؛ `processor` (پول‌سازترین منطق)، `woocommerce_direct`، `product_flow`، `rbac`، `config` هیچ تستی ندارند — و هر سه ادعای نقض‌شدهٔ README (§۱) داخل همین ناحیهٔ بدون‌تست است | `tests/` |
| P2-19 | `data/` در `.gitignore` است ولی نه نمونه‌ای از schema، نه ابزار backup/restore، نه مهاجرت نسخه (فایل‌ها `version: 1` دارند فقط در `learned.json`) | `data/*` |

---

## ۴) پیشنهادهای بهبود فلو (UX و معماری جریان)

### ۴.۱ فلو فعلیِ محصول در یک نگاه

```
🆕 محصول جدید ─▶ WAITING(1 state) ─┬─ عکس‌ها ─▶ دانلود+فشرده‌سازی+AI(×۲) ─▶ …
                                    ├─ هر متن ─▶ append به info_text ─▶ AI(×۲) ─▶ پیش‌نمایش
                                    ├─ ✏️ اصلاح اطلاعات ─▶ (فقط یک پیام راهنما؛ هیچ state تغییر نمی‌کند)
                                    ├─ ✅ تأیید ─▶ create_draft (REST) ─▶ END   [بدون قفل دابل‌کلیک]
                                    └─ ❌ لغو ─▶ END (تسک‌های معلق لغو نمی‌شوند → P1-1)
🔄 شارژ محصول موجود ─▶ همان WAITING ─▶ ✅ تأیید ─▶ ZIP ─▶ END  (مسیر کاملاً متفاوت، قواعد متفاوت)
```

مشکل ریشه‌ای: **یک state برای همه‌چیز** (`WAITING = 0`)؛ هیچ جای قابل‌اتکایی در جریان وجود ندارد که
«حالتِ اعتبارسنجی‌شده» را نگه دارد، و همه‌چیز از روی «متنِ روی هم تلنبارشده» هر بار از صفر بازسازی می‌شود.

### ۴.۲ فلو پیشنهادی: ماشین‌حالت با گذارهای صریح

```
COLLECT ─▶ PARSE ─▶ REVIEW ─▶ CONFIRM ─▶ PUBLISH ─▶ DONE
   ▲                        │            │
   └────── edit(field) ─────┘            └─ fail → RETRY/ROLLBACK + کارت خطا
```

| حالت | چه چیزی نگه می‌دارد | ورودی مجاز | دکمه‌ها |
|---|---|---|---|
| `COLLECT` | `images[]`, `source_messages[]` (هر پیام با id و نقش) | عکس/فایل/متن | `✅ تصاویر تمام شد` · `➕ افزودن عکس` · `❌ لغو` |
| `PARSE` | `draft` + `warnings[]` + `evidence` (هیچ UI) | فقط `/cancel` | پیام پیشرفت تک‌خطی |
| `REVIEW` | `draft` + `overrides{}` | فقط callback (متن آزاد = «پیشنهاد» با دکمهٔ تأیید) | ✏️ هر فیلد · `🧾 جزئیات` · `⬅️ منو` (واقعاً END) |
| `CONFIRM` | تأیید صریح خلاصهٔ خطارساز: N واریژن، قیمت بازه، SKU، دسته‌ها، هشدارها | — | `💾 پیش‌نویس` · `📤 ZIP برای افزونه` · `بازگشت به بازبینی` |
| `PUBLISH` | `batch_id`, `idempotency_key`, `submitting=True` (قفل) | — | «لغو صف» فقط تا قبل از اولین POST |

قاعدهٔ طلایی: **پس از اولین POST، دیگر چیزی ویرایش نمی‌شود** — فقط نتیجهٔ واقعی سایت نمایش داده می‌شود.

### ۴.۳ پانزده بهبود مشخص و کوچک (هر کدام مستقل قابل‌انجام)

1. **پیش‌نمایشِ منبع‌دار**: کنار هر فیلد بنویس از کجا آمده — `قیمت: ۶۹۸٬۰۰۰ ← پیام ۳، خط «قیمت ایفون 698»`.
   هر فیلدی که منبع ندارد `⚠️ حدس` بخورد. (ارزش: تشخیص ۱۰ ثانیه‌ایِ P0-2/P0-3 توسط خود ادمین.)
2. **ویرایش فیلد‌محور با reply-keyboard** به‌جای متن آزاد برای موارد بحرانی:
   `قیمت (تومان)` · `عنوان` · `پیشوند SKU` · `مدل‌ها` · `رنگ‌ها` · `دسته`.
3. **دکمهٔ پایان ارسال رسانه** (`✅ تصاویر تمام شد`) و حذف وابستگی به تایمر ۱.۸s برای آلبوم.
4. **Diff قبل/بعد اصلاح**: «تغییرات: قیمت ۲۵۰٬۰۰۰ → ۶۹۸٬۰۰۰ · +۲ واریژن · −۱ رنگ» به‌جای رندر مجدد کل پیش‌نمایش.
5. **صف «تأیید» مستقل**: کلیک اول فقط خلاصه و هشدارها را نشان می‌دهد؛ کلیک دوم روی
   `🚀 ساخت` (یا با تأخیر ۳ ثانیه‌ای غیرفعال) → دابل‌کلیک بی‌خطر.
6. **یک جریان فعال به‌ازای هر کاربر**: ورود به جریانی دیگر، جریان قبلی را ببندد و فایل‌هایش را پاک کند.
7. **کلید کردن سشن‌ها با `chat_id` به‌جای `user_id`** (نه `user_id`) تا رفتار در چت‌های آینده‌دار درست بماند
   و `status_message_id` هم در همان `chat_id` نگه داشته شود (الان `context.bot.send_message(user_id, …)` فرض می‌کند chat==user).
8. **کارت نتیجه**: پس از ساخت → `🆔 محصول ۱۲۳۴۵` · `🔗 لینک` · `🎨 ۷۹ واریژن` · `💰 بازه: ۵۹۸٬۰۰۰–۶۹۸٬۰۰۰` ·
   `⚠️ ۲ هشدار` · دکمه‌های `ویرایش در سایت` / `حذف پیش‌نویس` / `محصول بعدی`.
9. **«محصول بعدی، همان تنظیمات»** — همان تصاویر ساختار/دسته‌ها clone شود (در پست‌های تیساکیس ۸۰٪ کار همین است).
10. **`🧾 آخرین محصولات`** (۱۰ مورد آخر با id/URL/تاریخ) و `↩️ حذف پیش‌نویس آخر` → برگشت‌پذیری.
11. **حالت «بدون عکس»** برای شارژ: امکان `💰 فقط قیمت` بدون الزام عکس (الان مسیر ZIP اجازه می‌دهد ولی UI
    اول عکس می‌خواهد و اگر نفرستد `confirm` رد می‌شود: `not session.files`).
12. **`🔍 تست پارسر`** برای sudo: یک متن نمونه بدهد، خروجی کاملِ مدل‌ها/رنگ‌ها/قیمت‌ها/هشدارها را ببیند،
    بدون ساخت هیچ محصولی (بهترین ابزار برای «چرا اشتباه فهمید؟» و برای رگرسیون قواعد یادگرفته).
13. **`📊 وضعیت`**: نسخهٔ ربات، آخرین خطا، مصرف `/tmp` و `data/`، عمق صف، دسترسی ووکامرس/چت لاگ،
    تعداد ادمین‌ها، و یک دکمهٔ `🩺 عیب‌یابی کامل` (Ping فعلی را به یک self-check ساختاریافته تبدیل کند).
14. **مهاجرت همهٔ متن‌ها به یک `messages.py`** (فارسی، بدون افشای کلاس استثنا) تا UI یکدست شود.
15. **ضبط «پیش از ارسال»**: `dry_run` — همان مسیر را بدون POST برود و payload نهایی را نشان دهد؛
    برای تست روی سایت واقعی بدون آلوده‌کردن کاتالوگ حیاتی است.

### ۴.۴ چیزهایی که باید **حذف/ادغام** شوند

- `image_compress` و `product_flow` هر دو دانلود+فشرده‌سازی را پیاده کرده‌اند (`_download`, `_media`,
  `_safe_name` کپی‌شده) → یک `bot/services/media_pipeline.py` مشترک با پارامتر `sink={zip|upload|reply}`.
- `woocommerce.py` (ping) و `woocommerce_direct.py` (ساخت) دو کلاینت httpx متفاوت با دو سیاست auth هستند →
  یک `WooClient` مشترک (auth، timeout، retry، redaction، User-Agent).
- `woocommerce_product_test.py` و `wordpress_media.py` منطق تکراری upload/delete دارند → یک
  `bot/services/wp_probe.py`.
- `COLOR_STOPWORDS` و `_NON_COLOR_WORDS` و `explicit_category_words` و `brand_detected` هر چهارجا
  «چیزهایی که رنگ/مدل نیستند» را جدا تعریف می‌کنند → یک `bot/services/vocabulary.py` (تک‌مبدأ).

---

## ۵) پلن آپگرید کامل (فاز ۰ تا ۹)

قاعدهٔ ترتیب: **اول خونریزی را بندآور، بعد تست بساز، بعد بازنویسی کن.** هیچ بازنویسی بزرگ پیش از آنکه
«سوئیت رگرسیون روی ورودی‌های واقعی» وجود داشته باشد شروع نشود — وگرنه همان باگ‌ها با ظاهر نو برمی‌گردند.

برآورد به «نفر-روز» تمام‌وقتی است؛ «✅ قابل تحویل در یک شیفت» یعنی مستقل و بدون وابستگی.

### فاز ۰ — تثبیت و بستن نشتی‌ها (۳–۵ روز، ریسک پایین، ارزش فوری) 🔴

هدف: بدون تغییر معماری، «دادهٔ غلط → سایت زنده» را متوقف کند.

| Task | فایل | کار | حجم |
|---|---|---|---|
| 0.1 | `processor.py:226` | هر سلولِ دارای `❌` در CSV خالی بماند + ستون `needs_review` + شمارش در خلاصه (P0-1) | S |
| 0.2 | `product_extractor.py` | گارد لیبلِ غیرقیمت، اولویت عددِ دارای واحد، دامنهٔ `PRICE_MIN/MAX`، اسکن چندگروهیِ یک خط (P0-2/3/4) | M |
| 0.3 | `phone_parser.py` | `VARIANT_LEXICON` فارسی (`پرو`, `مکس`, `پرومکس`, `پلاس`, `مینی`, `اولترا`) + `max` در regex + قاعدهٔ «عدم ادغام در ابهام» (P0-5) | M |
| 0.4 | `color_matrix` + `woocommerce_direct` | `resolve_variation_plan()` مشترک؛ پیش‌نمایش و payload از یک منبع (P0-6) | M |
| 0.5 | `product_flow.py:634` | `validate(draft, mode)` مشترک؛ مدل الزامی در `new`؛ در `update` هم همان (P0-7) | S |
| 0.6 | `product_flow.py:622` | `asyncio.Lock` + `session.submitting` + `confirm_token` یک‌مصرفی (P0-8) | S |
| 0.7 | `config.py:31,67` | حذف هاردکد `log_chat_id` (پیش‌فرض `None`)؛ `.env.example` و README هم‌راستا (P0-9) | XS ✅ |
| 0.8 | `product_flow.py:478,689` | `_cleanup` پاک‌کردن `root`؛ `KeyError` بعد از لغو؛ لغو `album_tasks` (P1-1/3) | S |
| 0.9 | `ai_normalizer.py:99-103`, `product_flow.py:70` | حذف کد مرده؛ وصل‌کردن لاگ AI به `logging` (P1-7) | XS ✅ |
| 0.10 | ریپو | حذف `OPTION/**`، حذف zip ترک‌شده (یا افزودن source + workflow build)، `README:70` و متن کهنهٔ `admins.py:85` (P2-4/6/7) | XS ✅ |
| 0.11 | همهٔ موارد بالا | برای هرکدام یک تست رگرسیون که **پیش از فیکس قرمز** و بعد از آن سبز شود | M |

**معیار پذیرش فاز ۰:** ورودی‌های §۱۲ (probe2/3/4/5/6/7/8/9) همگی به‌عنوان fixture در `tests/fixtures/` باشند
و همه‌شان پاس شوند؛ `pytest -q` بدون نیاز به `BOT_TOKEN`.

### فاز ۱ — کفِ زیرساخت (۴–۶ روز)

| Task | جزئیات | DoD |
|---|---|---|
| 1.1 ابزار پروژه | `pyproject.toml` (ruff, mypy, pytest config), `requirements-dev.txt` pinned, `.pre-commit-config.yaml`, حذف `__pycache__` ترک‌شده | `ruff check . && mypy bot && pytest -q` محلی = CI |
| 1.2 CI | GitHub Actions: `pytest --cov=bot --cov-fail-under=70`, `ruff`, `mypy`, `py_compile`, ماتریس 3.11/3.12/3.13, و import-smoke با `-W error::DeprecationWarning` | هر PR قرمز/سبز؛ PTB 22 warnings پاک |
| 1.3 Config | `Settings.load()` با `@cache`، type-coercion امن با پیام خوانا + fallback، `load_dotenv(<repo>/.env)`، `--check-config` CLI که مقادیر را بدون شروع polling اعتبارسنجی می‌کند | تست: با ۶ ورودی خراب، ربات بالا می‌آید یا پیام فارسی خوانا می‌دهد |
| 1.4 Pin وابستگی | `python-telegram-bot>=21.0,<22` فعلاً + `httpx`, `pandas`, `Pillow`, `pymupdf` pinned؛ فایل `versions.lock` با خروجی `pip freeze` | build تکرارپذیر |
| 1.5 `JsonStore` مشترک | کش + `os.replace` اتمیک + `.bak` + schema version + `to_thread`؛ مصرف‌کننده‌ها: `rbac`, `preferences`, `learning` | تست: kill وسط نوشتن → بازیابی از `.bak` |
| 1.6 لاگ | `RotatingFileHandler` در `logs/`، `correlation_id` (per update)، `redact()` برای توکن/کلید/URL‌های دارای query auth | grep «consumer_secret» در لاگ‌ها هیچ‌وقت نباید پیدا شود |
| 1.7 PTB runtime | حذف `drop_pending_updates` (یا پشت فلگ)، `PicklePersistence(filepath='data/ptb.pkl')`، `RateLimiter`، `BotCommandScopeChat` برای `/commands` نقش‌محور | بعد از ری‌استارت، جریان نیمه‌کاره حفظ شود |
| 1.8 temp hygiene | `bot/services/workspace.py`: `Workspace.create(user, chat)` با TTL و `sweep(max_age)`؛ اجرا در `post_init` و JobQueue ساعتی | `du -sh /tmp/tisaposttowp-*` بعد از ۲۴ ساعت ≈ ۰ |

**وضعیت اجرا (فاز ۱) — انجام‌شده ✅**

| Task | وضعیت | نکته |
|---|---|---|
| 1.1 ابزار پروژه | ✅ | `pyproject.toml` + ruff/mypy/pytest + pre-commit؛ `OPTION/` از git خارج شد |
| 1.2 CI | ✅ | `.github/workflows/ci.yml` (۳.۱۱/۳.۱۲/۳.۱۳ + import-smoke با `-W error::DeprecationWarning`) |
| 1.3 Config | ✅ | coercion امن با پیام فارسی در `Settings.problems` که در لاگِ استارت چاپ می‌شود |
| 1.4 Pin | ✅ | بازهٔ pinned در `requirements.txt`؛ `versions.lock` هنوز تولید نشده (کار عملیاتی سرور) |
| 1.5 JsonStore | ✅ | کش + اتمیک + `.bak`؛ مصرف‌کننده‌ها: rbac، preferences، learning، flow_state |
| 1.6 لاگ | ✅ | `bot/utils/logging.py` — روتیشن، `redact()`، برچسب `[user <id>]` (correlation) |
| 1.7 PTB runtime | ⚠️ با تغییر طراحی | `PicklePersistence` **عمداً استفاده نشد**: بازیابی `ConversationHandler.state` بدون `session` (مسیرهای temp و file id از دست رفته) بدتر از فراموشی است. به‌جایش `bot/services/flow_state.py` — جریان نیمه‌کاره **ثبت و اعلام** می‌شود. `RateLimiter` و `BotCommandScopeChat` باقی‌مانده. |
| 1.8 temp hygiene | ✅ | `sweep_temp_dir` در post_init + JobQueue ساعتی، و پاک‌شده در `_cleanup` |

### فاز ۲ — هستهٔ استخراج دوباره نوشته شود (۸–۱۲ روز، قلب پروژه)

استراتژی: **«مدل متنی، نه regex روی یک رشتهٔ به‌هم‌چسبانده»**. ورودی = فهرست پیام‌ها، خروجی =
`ProductDraft` با منبع و اطمینان. AI از «تصمیم‌گیر» به «پیشنهاددهنده» تنزل می‌کند.

1. **`bot/services/postmodel.py`** — تبدیل متن به `Block`های تایپ‌شده با provenance:
   `BrandLine | ModelLine | ColorListLine | PriceLine | AttributeLine | MetaLine | ProseLine`، هر بلوک با
   `message_id`, `line_no`, `raw_text`. همهٔ پارسرهای بعدی روی بلوک کار می‌کنند، نه روی `str`.
   → رفع ساختاری P1-11 (نشت رنگ بین پیام‌ها) و نیمی از P0-2 (بلوکِ `MetaLine` هرگز قیمت نمی‌شود).
2. **`bot/services/vocabulary.py`** — تک‌مبدأ: `VARIANT_LEXICON`, `COLOR_LEXICON`, `BRANDS`, `NON_WORDS`,
   `CATEGORY_ALIASES`؛ `color_matrix` و `phone_parser` و `product_extractor` همه از همین می‌خوانند (رفع P2-9).
3. **`bot/services/model_catalog.py`** — کاتالوگ صریح (brand → families → variants) با دادهٔ JSON قابل‌ویرایش:
   `iphone`, `samsung (S/A/M/F + FE/Plus/Ultra/4G/5G + s trailing)`, `xiaomi (NoteNN / 12·13·14 Pro / POCO X-M / Redmi)`,
   `huawei`, `honor`, `google pixel`, `vivo`, `motorola`, `nothing`. هر برندی که در کاتالوگ نیست →
   **هشدار صریح** «برند پشتیبانی نمی‌شود: Huawei P30 Pro» (نه حذف بی‌صدا). (رفع P0-5 + شکاف پوشش.)
4. **`bot/services/money.py`** — یک پارسر واحدِ مبلغ: ارقام فارسی/عربی، جداکننده‌ها، واحدها
   (میلیارد/میلیون/هزار/`t`/`k`/`تومان`/`تومن`)، عبارت مرکب، «برهنگی عدد» و خط‌مشی ضریب از
   `learning`، دامنهٔ اعتبار، و «این مبلغ به چه گروهی می‌چسبد». خروجی:
   `Amount(value, currency="IRT", source=…, confidence=…)`.
   → `product_extractor` دیگر هیچ `re.search` عددی ندارد.
5. **`bot/services/extractor.py`** — دو لایه:
   * `deterministic_extract(blocks)` که تصمیم‌گیر است؛
   * `ai_propose(blocks, rules)` که فقط `Proposal` می‌دهد و هر پیشنهاد با `validate_against_source()`
     سنجیده می‌شود (رنگ/واژه باید واقعاً در متن باشد — همان محافظی که برای `model_colors` هست و باید
     تعمیم یابد). **قیمت هرگز از AI پذیرفته نمی‌شود.**
   یک فراخوانی در هر دور، debounce ۲–۳s، کش `sha1(blocks + rules_hash + model)`، timeout 20s +
   retry ۱ بار، و `AI_STATUS` که در `📊 وضعیت` نمایش داده می‌شود (رفع P1-6/P1-7).
6. **`bot/services/validation.py`** — یک `Rule`-based checker که هر دو مسیر خروجی از آن عبور می‌کنند:
   `E_MISSING_MODEL`, `E_AMBIGUOUS_MODEL_MERGE`, `W_PRICE_OUT_OF_RANGE`, `W_COLOR_PRUNED`,
   `E_ATTR_AXIS_COLLAPSED`, `W_CATEGORY_NOT_FOUND`, `E_NO_IMAGES`, … و سه سطح:
   `ok` / `warn` (نمایش + تأیید صریح) / `block` (تأیید غیرفعال). پیش‌نمایش، payload، ZIP همگی از
   **همین** نتیجه عدد واریژن را می‌گیرند (رفع ریشه‌ای P0-6).
7. **حذف import حلقوی و خصوصی:** `product_flow` نباید `_number_from_line` را import کند؛ `learning` به‌جای
   import در دل `product_extractor`، یک hook تزریق‌شده (`set_price_policy`) دریافت کند.

**وضعیت اجرا (فاز ۲ + ۲b) — انجام شد ✅ (فقط قرارداد `product.json` به فاز ۶ موکول شد)**

| آیتم | وضعیت | نکته |
|---|---|---|
| provenance (`postmodel.py`) | ✅ | `Evidence(source, note)` + نردبان اعتبار ثابت؛ بلوک «🧭 از کجا می‌دانم» در پیش‌نمایش؛ `merge()` طوری است که حدسِ AI هرگز جملهٔ خودِ فروشنده را توضیح نمی‌دهد |
| یادداشت‌های سیاست | ✅ | مبالغ ردشده فقط وقتی به کاربر گفته می‌شود که واقعاً «قیمت» نوشته شده بود (بقیه در لاگ می‌ماند، نه در پیش‌نمایش) |
| `vocabulary.py` | ✅ | `data/vocabulary.json`، اعمال **قبل** از پارس (مسیر deterministic و AI هر دو یکی می‌بینند) |
| تفکیک deterministic / ai_propose / reconcile | ⏳ نیمه | فعلاً `_fallback` (متن) سپس AI سپس `reconcile` در قالب merge موجود با نردبان اعتبار؛ جداسازی کامل به فاز ۲b موکول شد تا با `Block`ها یکجا انجام شود |
| `Block`های تایپ‌شده | ✅ فاز ۲b | `postmodel.Block` با `roles` چندتایی (یک خط هم مدل است هم قیمت)، `line_no` و `message`. **نکتهٔ طراحی:** به‌جای «یک بلوک = یک نقش»، نقش‌ها مجموعه‌اند؛ اولویتِ تک‌نقش باعث می‌شد «۱۵ اولترا» تیتر محصول شود |
| `model_catalog.py` | ✅ فاز ۲b | `suspicious_lines()` (با follow کردن هدر بخش: «آیفون:» و سپس خطوط برهنه) + `unknown_brand_words()` + `prompt_block()` که همان جدول را به AI می‌دهد؛ `data/model_catalog.json` قابل‌ویرایش |
| هم‌راستاسازی قرارداد `product.json` با بلوک‌ها | ⏳ فاز ۶ | محتوای تایپ‌شده (توضیحات/بلوک‌های سئو) نیازمند توافق با پلاگین WP است؛ تا آن موقع `Block` فقط در مسیر استخراج و پیش‌نمایش مصرف می‌شود |
| بوت بدون `python-dotenv` | ✅ | host اشتراکی که pip نداشته باشد دیگر در `import bot.config` نمی‌مرد (تست: `test_config_boots_without_python_dotenv`) |
| رفع باگ برند فارسی | ✅ | `آیفون 13 پرو مکس` بدون کلمهٔ لاتین قبلاً **هیچ** مدل تولید نمی‌کرد؛ حالا رکورد تست دارد |

**معیار پذیرش فاز ۲:** یک corpus از ≥۵۰ پست واقعیِ sanitize‌شده (`tests/fixtures/posts/*.txt` + `expected.json`)؛
`pytest --snapshot` برای سه خروجی: پیش‌نمایش، `product.json`، `payload` ووکامرس. معیار کمّی:
`extracted_price == human_price` در ۱۰۰٪ corpus (هر خطا = بلاکر release)، و «تعداد واریژن پیش‌نمایش ==
تعداد واریژنِ payload» در ۱۰۰٪ (invariant ساختاری).

### فاز ۳ — بازنویسی جریان/UX (۵–۷ روز)

پیاده‌سازی §۴.۲: ماشین‌حالت ✅ (فاز ۳c)، ویرایش فیلد‌محور ✅ (بخش اول)، دکمهٔ پایان، diff ✅، کارت نتیجه ✅، «محصول بعدی» ✅،
`🧾 آخرین محصولات`، `🔍 تست پارسر`، `📊 وضعیت`، خروج کامل از صفحه‌ها، `conversation_timeout=900` با
پیام یادآوری در ۱۰ دقیقه، و «یک جریان فعال» (رفع P1-4، P1-5 و بیشتر §۴.۳).

* زیرکارکرد: `sessions` از `dict[int, ProductSession]` به `FlowState` در `ConversationHandler` منتقل شود
  (`context.chat_data`) تا با Persistence هم‌راستا بماند و `_flush_album` دیگر global نبیند.
* زیرکارکرد: تمام رشته‌های کاربری به `bot/i18n_fa.py` (رفع P2-2/3).

**وضعیت اجرا (فاز ۳ — بخش اول) — انجام شد ✅ (بقیهٔ آیتم‌ها فاز ۳b)**

| آیتم | وضعیت | نکته |
|---|---|---|
| ویرایش فیلد‌محور | ✅ | `bot/services/draft_edits.py` + دکمهٔ «✏️ اصلاح فیلد خاص». هر فیلد پارسر مستقل و پیام خطای فارسی دارد؛ ورودی نامعتبر **هیچ** چیزی را عوض نمی‌کند. فیلدها: عنوان، قیمت، قیمت گروهی، رنگ‌ها، مدل‌ها، پیشوند SKU، دسته‌ها، محورهای ویژگی |
| قفلِ ویرایش دستی | ✅ | `ProductData.user_edits` و `apply_locks()` در انتهای `_extract`؛ استخراج دوباره (عکس جدید/متن جدید) مقدار دست‌نویس را بازنمی‌نویسد. **تصمیم طراحی:** قفل در خودِ `ProductData` است نه در UI، تا هر مسیر استخراج (AI یا متن خالص) یک قانون داشته باشد |
| جداکردن رنگ‌های چند پیام | ✅ | یادداشت فاز ۲b تبدیل شد به `product:colorsrc`؛ حذف، خطوط آن پیام را از **متن ارسالی به AI** هم بیرون می‌اندازد (نه فقط از نتیجه) تا تصمیم کاربر در دور بعدی بی‌صدا برگردد. عنوان و قیمت آن پیام دست‌نخورده می‌ماند |
| پیشنهاد اصلاح برند | ✅ | `bot/services/brand_suggest.py` (فاصلهٔ ویرایشی ≤۲، فقط یک کاندید، کلمهٔ کنار شمارهٔ مدل). تأیید کاربر ⇒ قاعده در `data/vocabulary.json` ⇒ هر دو مسیر (parser و AI) برای همیشه اصلاح می‌شوند؛ «نه» فقط همین محصول را ساکت می‌کند |
| ماشین‌حالت چندوضعیتی | ⏳ ۳b | فعلاً دو state (`WAITING`، `EDITING_FIELD`)؛ جدول کامل MODEL_TEXT → MEDIA → INFO → PREVIEW و دکمهٔ پایان در هر صفحه |
| `sessions` روی `context.chat_data`/`FlowState` | ⏳ ۳b | `flow_state.py` فعلاً ledger برای بازیابی است؛ مهاجرت کامل با «یک جریان فعال» انجام می‌شود |
| کارت نتیجه، «📦 محصول بعدی»، `🧾 آخرین محصولات`، `🔍 تست پارسر` | ✅ ۳b | **تصحیح:** این جدول در نسخهٔ قبل ادعا می‌کرد آن دو ابزار در `bot/modules/devtools.py` هستند؛ چنین ماژولی وجود نداشت. در فاز ۳b همان ابزارها واقعاً ساخته شدند (`bot/modules/product_tools.py`) |
| `bot/i18n_fa.py` | ⏳ ۳b | تک‌مبدأ رشته‌های فارسی |

**وضعیت اجرا (فاز ۳ — بخش دوم / ۳b) — انجام شد ✅**

| آیتم (§۴.۳) | وضعیت | نکته |
|---|---|---|
| بند ۸ — کارت نتیجه | ✅ | `bot/keyboards/cards.py`؛ id + لینک + تعداد واریژن + قیمت + هشدارها. مسیر مستقیم ووکامرس و مسیر ZIP همان کارت را از یک تابع می‌گیرند (دو فراخوانی، یک رندر) |
| بند ۹ — «محصول بعدی» | ✅ | `product:next:<mode>` **entry point**ِ خودِ `ConversationHandler` ثبت شده نه یک هندلر معمولی — وگرنه پیام‌های بعدی به هیچ مکالمه‌ای نمی‌رسیدند. مجوز دوباره کنترل می‌شود و پیام زیر کارت فرستاده می‌شود (ویرایشش نه) تا id و لینک باقی بمانند |
| بند ۱۰ — 🧾 آخرین محصولات | ✅ | `data/recent_products.json` (`products_ledger.py`، ۲۰ کارت آخر). کارتِ بازیابی‌شده همان پیش‌نمایشی است که تأیید شده، نه بازسازیِ امروزِ پارسر. خطا هم رکورد می‌شود: «سایت که خالی است» باید جواب قابل‌دیدن داشته باشد |
| بند ۱۲ — 🔍 تست پارسر | ✅ | `product_tools` + `product_flow.analyze()` که همان `_extract` جریان را صدا می‌زند؛ سندباکسی با پارسر دوم و ساده‌تر، سؤال «چرا این‌طور خواندی؟» را جواب نمی‌داد. هیچ محصول/فایلی ساخته نمی‌شود و پرچمِ ورودی بعد از یک پیام پاک می‌شود |
| بند ۴ — diff قبل/بعد | ✅ | `draft_edits.snapshot()`/`diff()` ⇒ «قیمت: 250,000 تومان ← 698,000 تومان · +2 واریژن (4 ← 6)» + دکمهٔ «👁 پیش‌نمایش کامل» |
| بندهای ۵، ۶، ۷، ۱۱، ۱۳، ۱۴ | ⏳ فاز ۴ و ۳c | صفحهٔ تأیید مستقل، «یک جریان فعال»، کلید `chat_id`، «فقط قیمت» بدون عکس، `📊 وضعیت` ساختاریافته، و `bot/i18n_fa.py` |

**معیار پذیرش ۳b:** کارت از دادهٔ ذخیره‌شده ساخته شود نه از حافظهٔ session؛ شکست هم رکورد شود؛ تست پارسر چیزی نسازد و پرچمش بعد از یک پیام پاک شود؛ «محصول بعدی» مجوز را دوباره ببیند و جریان قبلی را نبازد؛ تاریخچه پس از ری‌استارت خوانده شود. ۲۷ تست در `tests/test_result_card.py` (مجموع ۲۸۳؛ اجرای stdlib-only هم سبز — با یک `_DraftLike` بی‌وابستگی).

**معیار پذیرش بخشی که انجام شد:** برای هر فیلد، یک تست «ورودی نامعتبر ⇒ مقدار قبلی سالم + پیام فارسی»؛ یک تست «ویرایش دستی ⇒ استخراج مجدد ⇒ ویرایش باقی است»؛ یک تست end-to-end برای حذف رنگ یک پیام از مسیر استخراج؛ و تست «دو کاندید ⇒ بدون حدس» در `tests/test_field_edit.py` (۳۶ تست).

**وضعیت اجرا (فاز ۳c — ماشین‌حالت و «یک جریان فعال») — انجام شد ✅**

| آیتم | وضعیت | نکته |
|---|---|---|
| ماشین‌حالت §۴.۲ | ✅ | `COLLECT(0) → REVIEW(2)` + `EDITING_FIELD(1)`. قاعدهٔ اصلی: در COLLECT متن آزاد = اطلاعات محصول؛ در REVIEW همان متن = **پیشنهاد** با دکمهٔ ✅/⏭️. P1-4/P1-5 در سطح routing بسته شد، نه با «مراقب باش تایپ کنی» |
| PARSE/PUBLISH | ✅ (بدون state) | انتشار همان هندلرِ **بلاکینگ** است؛ `ConversationHandler(block=True)` پیش‌فرض، ورودی وسط کار را صف می‌کند، پس state جدا لازم نیست. `session.submitting` هم دابل‌کلیک را رد می‌کند |
| دکمهٔ پایان رسانه (§۴.۳ بند ۳) | ✅ | «✅ تصاویر تمام شد» صفِ آلبوم را همان لحظه flush می‌کند؛ تایمر ۱٫۸ ثانیه‌ای فقط fallback است. فقط task‌هایی که هنوز بافر را برنداشته‌اند cancel می‌شوند، پس `processing_media` هیچ‌وقت روی True گیر نمی‌کند |
| یک جریان فعال (بند ۶) | ✅ با محدودیت مستند | `bot/services/flow_guard.py`: هر flow یک closer ثبت می‌کند. **PTB 21.11 هیچ API عمومی برای «END کردن مکالمهٔ دیگرِ همان کاربر» ندارد** (tracker خصوصي، بدون `exit_conversation`)، پس ما *کار* جریان را می‌بندیم (سشن + temp) و هندلرها دیگر `setdefault` نمی‌کنند: کلیک قدیمی ⇒ «این جریان بسته شده است» + END. تست‌های `TestNoPhantomSession` همین را می‌چسبانند |
| بند ۷ — کلید `chat_id` | ⚠️ نیمه، عمدی | **مسیریابی** با `chat_id` + `message_thread_id` شد (پیش‌نمایش، مراحل، کارت نتیجه به همان جا برمی‌گردند)؛ ولی `sessions` هنوز با `user_id` کلید می‌خورد. دلیل: اگر کلید چت باشد، دو ادمینِ یک چت گروهی **یک پیش‌نویس مشترک** می‌سازند و یکدیگر را خراب می‌کنند — که بدتر از مشکل اصلی است. در §۴.۳ این ریسک ذکر نشده بود |
| بند ۱۴ — `bot/i18n_fa.py` | ⏳ فاز ۵ | عمداً این فاز انجام نشد: مهاجرت رشته‌ها refactor خالص است و آمیختنش با یک تغییر رفتاری، هر دو را در بازبینی و در blame گیت ناخوانا می‌کند. برنامه: یک کامیت مستقل، بدون تغییر هیچ متن |

**معیار پذیرش ۳c:** تبدیل متن→اطلاعات در COLLECT و متن→پیشنهاد در REVIEW (با تست «هیچ فیلدی قبل از ✅ حرکت نمی‌کند»؛ «بله» یک‌بار اعمال می‌شود و دوباره نه)؛ ورود به یک جریان، جریان دیگر را ببندد و به کاربر بگوید کدام؛ هیچ هندلری بدون سشن، سشن نسازد؛ پیام‌های خودکار به chat/threadِ شروع‌کننده بروند. ۳۱ تست در `tests/test_flow_states.py` (مجموع ۳۰۵؛ stdlib-only سبز).

### فاز ۴ — بازنویسی لایهٔ ووکامرس (۵–۸ روز)

| Task | جزئیات |
|---|---|
| 4.1 | `WooClient` واحد: auth (query-string)، timeout، retry با backoff، `redact`، `User-Agent`، و **dry-run** (`TISA_DRY_RUN=1` → validate-only) (رفع P1-15/P2-7 همسویی) |
| 4.2 | `create_draft` اتمیک: build → POST → variations → verify (GET واریژن‌ها) → در خطا: `DELETE …?force=true` + پاک‌کردن مدیاهای بی‌صاحب؛ `created`/`failed` با شمارش دقیق (رفع P1-9) |
| 4.3 | resume/idempotency: `meta_data: tisa_batch_id`؛ پیش از ساخت، جستجوی همان کلید؛ واریژن‌های موجود skip شوند |
| 4.4 | SKU: گرفتن از افزونه (شمارندهٔ اتمیک) + کش `data/sku_state.json` + fallback محدود (≤۱۵ probe، سپس «از سرور بپرس»)؛ **SKU روی هر variation** (`BO148-17PMX-WHT`) (رفع P1-10) |
| 4.5 | موجودی: `manage_stock`, `stock_quantity`, `stock_status` از بلوک «موجودی» پیام/جریان شارژ؛ `status` واریژن = `inherit` |
| 4.6 | ویژگی‌ها: `variation` image (تصویر هر رنگ اگر موجود)، `menu_order`، `visible`، و `sale_price` در صورت `قیمت ویژه` |
| 4.7 | ردیابی: `meta_data: tisa_source = {chat_id, first_message_id, captured_at, bot_version, rules_version, manifest_hash}` |
| 4.8 | صفِ خروجی: `JobQueue` + صف SQLite (`data/outbox.sqlite3`) تا اگر هاست جواب نداد، تلاش خودکار backoff داشته باشیم و کاربر مجبور به دابل‌کلیک نباشد |
**وضعیت اجرا (فاز ۴ — بخش اول: 🧪 حالت آزمایشی و ردپای انتشار) — انجام شد ✅**

| آیتم | وضعیت | نکته |
|---|---|---|
| 4.1 — dry-run (`TISA_DRY_RUN`) | ✅ | `bot/config.py` (فلگ) + `_dry_run_transport` در `woocommerce_direct.py`: یک `httpx.MockTransport` که مثل ووکامرس/وردپرس جواب می‌دهد. **تصمیم مهم:** یک «پیش‌نمایش‌ساز» موازی نوشته نشد — dry-run همان `create_draft` است و فقط سوکت عوض می‌شود؛ پیاده‌سازی دوم داخل یک ریلیز از production واپا می‌افتد و بعد هیچ چیزی را تضمین نمی‌کند |
| 4.1 — گیتِ ابزارهای نوشتنی | ✅ | `🖼️` و `📦` در صفحهٔ Ping واقعاً روی سایت می‌نویسند و بعد پاک می‌کنند؛ با فلگ روشن **رد می‌شوند** و پیامشان راهِ جایگزین (همان rehearsal تا «✅ تأیید و ساخت») را نشان می‌دهد. وگرنه قولِ فلگ فقط نصف مسیر را می‌پوشاند |
| 4.1 — کارت و تاریخچهٔ honest | ✅ | status `dry` در ledger با `product_id=None`: شناسهٔ ساختگی نباید در تاریخچه بماند. کارت هم لینک «ویرایش در سایت» ندارد (نباید داشته باشد) و `--check-config` وضعیت را لو می‌دهد |
| 4.1 — `WooClient` واحد | ⏳ بخش بعد | auth/retry/backoff/redact هنوز داخل `woocommerce_direct.py` است؛ استخراج به `bot/services/woo_client.py` + `sku.py` کار بعدی این فاز است (بدون تغییر رفتار، فقط مرزبندی) |
| 4.2 — اتمیک + رول‌بک | ✅ از قبل، حالا **قابل‌آزمایش** | rollback (DELETE محصول/مدیا) از قبل وجود داشت؛ در dry-run همان DELETEها اجرا می‌شوند، پس مسیر تمیزکاری هم «کد اجراشده» است نه کد خوانده‌شده |
| 4.4 — SKU | ✅ از قبل | در dry-run پاسخ `next-sku` عمداً 404 است تا مسیر fallback (اسکن کاتالوگ + پرش از برخورد) همان چیزی باشد که تمرین می‌بیند |
| ردپای خوانا | ✅ | بدنهٔ دودوییِ آپلود تصویر ۴۰۰ کاراکتر کنترلی در لاگ می‌ریخت؛ `_body_for_log` آن را به `<N بایت دادهٔ دودویی>` تبدیل می‌کند. `[payload]` هم دیگر در گزارش کاربر تکرار نمی‌شود (`_dry_run_report`) |
| 4.3 idempotency، 4.7 `tisa_source` | ✅ بخش دوم | جدول پایین: «فاز ۴ — بخش دوم» |
| 4.5 موجودی، 4.6 تصویر/`menu_order`/`sale_price` واریژن، 4.8 صف خروجی | ⏳ | آیتم‌های بعدی همین فاز |

**معیار پذیرش بخش اول:** هر شکلی از درخواستی که writer می‌زند باید در fake جواب داشته باشد (اگر کسی درخواست تازه‌ای اضافه کند و fake به‌روز نشود، تستِ شمارش ردپا می‌ترکد)؛ با فلگ خاموش باید واقعاً سوکت باز شود؛ دروازهٔ اعتبارنامه‌ها رد نشوند؛ کارت و ledger هیچ‌وقت شناسهٔ ساختگی نشان ندهند. ۱۸ تست در `tests/test_dry_run.py` (مجموع ۳۲۳؛ ruff/mypy سبز).


**وضعیت اجرا (فاز ۴ — بخش دوم: ♻️ idempotency + ۴.۷ ردپای منبع) — انجام شد ✅**

| کار | وضعیت | نکتهٔ اجرایی |
|---|---|---|
| 4.3 — شناسهٔ انتشار | ✅ | `bot/services/publish_batch.py`: `batch_id = sha1(payload + تصاویر + chat_id)[:12]`. **تصمیم: محتوا-آدرس، نه uuid** — یک uuid باید از crash زنده بیرون می‌آمد، یعنی جای دیگری ذخیره‌اش کنیم؛ و اگر آن ذخیره نسوخته باشد، خودِ مشکل حل شده است. محتوا-آدرس یعنی تلاش بعدی از همان draft همان id را می‌سازد، پس چیزی برای گم‌کردن نیست. `chat_id` در امضا است تا دو ادمین با متن یکسان دو محصول مستقل داشته باشند، و نام+حجم فایل‌ها هم آمده تا «همان متن با عکس دیگر» محصول تازه حساب شود |
| 4.3 — متای روی سایت | ✅ | `tisa_batch_id` + `tisa_source` (چت، thread، تعداد تصویر/واریژن، نسخهٔ ربات، زمان ثبت). ووکامرس REST فیلتر روی meta ندارد ⇒ شکار این‌طور است: `search=عنوان` به‌همراه `status=any` (و در HTTP 400 بدون آن دوباره امتحان می‌شود) و بعد مقایسهٔ متای خودمان روی هر نتیجه |
| 4.3 — resume به‌جای دوباره‌سازی | ✅ | محصولِ هم‌شناسه پیدا شد ⇒ id همان برمی‌گردد، تصویر دوباره آپلود نمی‌شود، فقط ترکیب‌های **نبود‌ه** ساخته می‌شوند، و `DELETE` هرگز روی محصولی که این تلاش نساخته اجرا نمی‌شود (پاک‌کردن محصول دیگری پاک‌سازی نیست، خرابکاری است). اگر `GET variations` خطای واقعی داد کار **متوقف** می‌شود — نه «بساز همه‌چیز»، نه «بی‌صدا رد شو» — و پیام می‌گوید محصول دست‌نخورده مانده. 404 روی محصولِ خودِ همان تلاش یعنی «واریژنی نیست، همه را بساز» |
| 4.3 — کارتِ «قصد» | ✅ | `products_ledger.record/update/find_batch`: کارت `⏳ pending` **پیش از** اولین درخواست نوشته می‌شود و همان کارت به `✅/❌/🧪` تبدیل می‌شود (یک `key`)، پس یک تلاش = یک کارت و crash چیزی قابل‌یافتن جا می‌گذارد؛ `done_ts` هم اضافه شد. سقف ۲۰ کارت یعنی یک قصد خیلی قدیمی ممکن است فراموش شود — صادقانه در docstring و README نوشته شده، نه پنهان |
| 4.3 — دروازهٔ UI | ✅ | `confirm` تنها نقطهٔ ورود انتشار است و دروازه همان‌جاست («تلاش دوباره» دکمهٔ جدا ندارد؛ همان تپِ `product:confirm`) پس راهی برای دورزدنش نمی‌ماند. فقط وضعیت `created` بلوکه می‌کند — `failed`/`zip`/`dry` هرگز، وگرنه یک تمرین یا یک آپلود زیپ می‌تواند ساخت واقعی را قفل کند. محتوای `created` → «♻️ این محتوا پیش‌تر منتشر شده است» + id + «🔗 ویرایش همان محصول در سایت» + توضیح اینکه تپ دوباره واقعاً چه می‌کند (SKU تازه و محصول دومی، چون ووکامرس عنوان تکراری را رد نمی‌کند) + دکمهٔ «🔁 با این حال دوباره بساز» → `product:force` که پرچم را **یک‌بارمصرف** می‌کند. هندلر، همان `confirm` را دوباره صدا می‌زند نه یک مسیر انتشار دوم؛ و تست دارد که در `register()` رجیستر شده باشد (رشتهٔ سفت‌وشکل در payload کافی نیست) |
| 4.3 — مسیر ZIP | ✅ (قرارداد) | `batch_id` در manifest نوشته می‌شود. افزونهٔ وردپرس **ویرایش نشد**: `tisa-product-importer.zip` یک باینری ساخته‌شده است و منبعش جای دیگری (P2-7)؛ patch کردنش اینجا فردا با یک build تازه بی‌صدا می‌پرد. به‌جایش `docs/IMPORTER-CONTRACT.md` با قطعهٔ PHP آماده (دِدوپ با `get_posts` روی `_tisa_batch_id` + نوشتن متا) نوشته شد. سازگاری عقب‌رو: افزونهٔ فعلی همه‌چیز را با `??` می‌خواند، پس کلید جدید برایش بی‌ضرر است |
| 4.7 — `TISA_DATA_DIR` | ✅ | شش ماژول `DATA_DIR = <repo>/data` هاردکد داشتند. تا وقتی آن فایل‌ها فقط *نوشته* می‌شدند بی‌ضرر بود؛ از لحظه‌ای که `product_flow` قبل از انتشار `data/recent_products.json` را **می‌خواند**، کارت‌های تستِ همان سوئیت می‌توانست انتشار واقعی را بلوکه کند (و در همین PR هم یک تست را شکست). حالا `bot.config.data_dir()` تنها راه دسترسی است، سوئیت با `tests/conftest.py` به دایرکتوری temp می‌رود، و README هم ردیفش را دارد. `main.py --check-config` هم مسیر نهایی را چاپ می‌کند و اگر دایرکتوری قابل‌نوشتن نباشد با exit کد ۱ می‌گوید — چون هر writer در `jsonstore` عمداً `OSError` را می‌خورد، بی‌اعتباری این مسیر وگرنه بی‌صدا می‌ماند |
| سخت‌سازی تست | ✅ | `tests/_flow_harness.py` (patch کردن `settings` در **همهٔ** ماژول‌هایی که importش کرده‌اند + `temp_ledger` + `FakeBot`) از کپی‌های `test_dry_run.py` گرفته شد و هر دو فایل از آن استفاده می‌کنند. `create_draft(..., transport=)` یک seam تستی است (در docstring نوشته شده که اگر رفتاری به آن اضافه شود از production رد می‌کند) و تست هم دارد که در rehearsal نادیده گرفته می‌شود |
| 4.1 `WooClient`، 4.5 موجودی، 4.6 تصویر/`menu_order`/`sale_price` واریژن، 4.8 صف خروجی | ⏳ | `woocommerce_direct.py` هنوز ~۱۰۰۰ خط است (کاندید بعدی split)؛ rehearsal هنوز فروشگاه سالم را شبیه‌سازی می‌کند نه هانگ/۵۰۰؛ موجودی و تصویر هر رنگ ثبت نمی‌شوند؛ رول‌بک مدیا در مسیر resume عمداً انجام نمی‌شود چون در آن تلاش مدیایی آپلود نشده |

**معیار پذیرش بخش دوم:** اگر همین draft دوباره به `create_draft` برسد و فروشگاه محصولِ برچسب‌خورده را برگرداند، نباید هیچ `POST /products` تازه‌ای ثبت شده باشد؛ رول‌بک فقط روی محصول همان تلاش؛ دروازه فقط با وضعیت `created` و فقط در همان chat؛ `force_publish` بعد از یک بار مصرف خاموش شود؛ و `product:force` واقعاً هندلرِ ConversationHandler باشد. ۳۰ تست در `tests/test_idempotency.py` (مجموع ۳۶۰؛ ruff/mypy سبز؛ هم pytest و هم `python -m unittest discover -s tests`).

### فاز ۵ — «شارژ محصول موجود» واقعی (۴–۶ روز)

امروز `update` فقط قیمت/ویژگی/`post_date` را دست می‌زند و **هیچ موجودی‌ای شارژ نمی‌کند** (P1-9/4.5)، ضمن
اینکه از مسیر REST خارج و به ZIP وابسته است.

1. تطبیق محصول در تلگرام: کاربر `BO148` یا چند کلمه از عنوان را می‌دهد → ربات فهرست کاندیدا (id/title/SKU/
   قیمت فعلی/تعداد واریژن) را در چت نشان می‌دهد → انتخاب با دکمه (حذف گام «برو در پیشخوان وردپرس جستجو کن»).
2. نمایش **diff** پیش از اعمال: «۳ رنگ جدید · ۱ مدل حذف‌شده · قیمت ۵۹۸٬۰۰۰ → ۶۴۸٬۰۰۰ · موجودی ۱۲ واریژن ۰→۳».
3. ورود موجودی با یک خط فشرده: `17promax سفید ۳، مشکی ۱` یا جدول تلگرام؛ `⛔ ناموجود` برای out-of-stock.
4. اعمال از همان `WooClient` (PUT قیمت/stock روی variation)، و ZIP فقط به‌عنوان مسیر جایگزین/پشتیبان.
5. پس از اعمال: `✅ ۱۲ واریژن شارژ شد` + `🧾 گزارش` (چه چیزی تغییر کرد، چه چیزی نه و چرا).

### فاز ۶ — یادگیری خودکار ۲.۰ (۴–۵ روز)

* `Rule` به `data/learned.json` v2: `scope` (کل فروشگاه / دسته / برند)، `status` (`active|pending|disabled`)،
  `applied_to` (نمونه‌های واقعی)، `would_change_preview`.
* **تأیید پیش از فعال‌سازی**: «این قاعده روی ۷ محصول آخر، ۲۳ واریژن را کم می‌کرد — اعمال شود؟» — چون بزرگ‌ترین
  ریسک فعلی یادگیری، قاعده‌ای است که بی‌صدا `variation_count` را می‌سوزاند.
* `hits` روی مسیر داغ روی دیسک نوشته نمی‌شود (رفتار فعلی: شمارش با ری‌استارت می‌سوزد) → شمارش در حافظه +
  flush در پایان هر سشن.
* صف بازبینی: اگر `confidence < threshold` → «از من بپرس» به‌جای حدس؛ جواب کاربر همان‌جا قاعده می‌سازد.
* یک endpoint تست `🔍 تست پارسر` (فاز ۳) که rules فعال را روی متن آزمایش کند و diff بدهد.

### فاز ۷ — مبدل فایل ردیابی ۲.۰ (۲–۴ روز)

* تشخیص schema + **نقشهٔ ستون تعاملی** (اگر دو هدر محتمل بود، از کاربر بپرس نه اینکه اولی را بردارد).
* `❌` هیچ‌وقت به CSV نمی‌رود (P0-1) و `needs_review` دوسو (CSV و XLSX) تولید شود؛ `problems.csv` با لینک سطر.
* سقف حجم/ردیف/زمان (`MAX_FILE_MB`, `MAX_ROWS`, `asyncio.wait_for`) + راهنمای «فایل را به‌صورت متن/PDF بده»
  برای بارکدهای عددی (رفع P1-12/P2-12).
* `BARCODE_LENGTHS = [24]` از تنظیمات (رفع P2-15) و پذیرش EAN-13/UPC با checksum.
* دفترِ پردازش‌شده‌ها (`data/tracking_ledger.json`) تا ارسال دوبارهٔ یک فایل، تکراری/هشدار بدهد.
* fixtures واقعی برای xlsx/csv/pdf (P2-17) — فعلاً هیچ تستی روی `processor` نیست.

### فاز ۸ — مشاهده‌پذیری و عملیات (۳–۴ روز)

* چت لاگ: به‌جای ۱۰ پیام متنی، **یک کارت ساختاریافته به‌ازای هر محصول** (id, hash manifest, N واریژن،
  قیمت، هشدارها، خطا) + حالت `VERBOSE_LOG=1` برای trace کامل.
* متریک‌ها در SQLite (`data/metrics.sqlite3`): `products_created`, `variations_created`, `sku_collisions`,
  `ai_latency_ms`, `ai_failure_rate`, `extract_fallback_used`, `flow_abandoned`, `buttons_denied` →
  نمایش در `📊 وضعیت` + `/export-metrics` برای sudo.
* `🩺 عیب‌یابی`: بررسی همزمان (نه ۴ دکمهٔ جدا) — token, chat id لاگ, WooCommerce, WP media, مصرف دیسک,
  supervisor, نسخهٔ افزونه؛ خروجی یک کارتی «سبز/زرد/قرمز» با راهنمای repair.
* `systemd` unit + `Dockerfile` (برای محلی/CI) + `runbook.md` (restart, restore, ghost-SKU SQL، نحوهٔ
  افزودن برند به کاتالوگ).

### فاز ۹ — تست و انتشار (مستمر)

* `pytest-cov --cov-fail-under` (فاز ۰: ۵۵٪ → فاز ۴: ۷۵٪ → هدف ۸۵٪ روی `bot/services`).
* تست‌های خواصی (hypothesis) برای invariantها: `variation_count == len(payload variations)`؛
  هیچ مبلغی بدون واحدِ صریح > `PRICE_MAX` نشود؛ ادغام مدل رخ ندهد؛ `restrictions` هیچ‌وقت مدلِ
  بی‌رنگ را حذف نکند.
* تست قرارداد با `docker-compose.yml` (WP + WooCommerce + MySQL) با prefix `TISA_TEST_` و `TISA_DRY_RUN`
  در CI محلی (نه در GitHub Actions عمومی؛ ولی با یک workflow `workflow_dispatch` روی self-hosted).
* SemVer + `CHANGELOG.md` + release GitHub؛ افزونه از source build شود، نه zip ترک‌شده.
* «DoD» برای هر PR: تست رگرسیون، به‌روزرسانی README، ثبت تغییر در `CHANGELOG`، و (برای تغییر مسیر استخراج)
  اجرای corpus.

### جدول زمان‌بندی پیشنهادی

| فاز | مدت | وابستگی | ارزش |
|---|---|---|---|
| ۰ تثبیت | ۳–۵ روز | — | 🔴 جلوگیری از دادهٔ غلط در سایت/سامانه |
| ۱ کف زیرساخت | ۴–۶ روز | — | 🔒/⚡ پایداری، CI، نداشتن نشت دیسک |
| ۲ هستهٔ استخراج | ۸–۱۲ روز | ۰،۱ | 🔴 صحت مدل/قیمت/رنگ — بزرگ‌ترین برد |
| ۳ جریان/UX | ۵–۷ روز | ۲ | 🟠 کاهش خطای انسانی و رهاشدگی |
| ۴ ووکامرس | ۵–۸ روز | ۰،۱ | 🟠 اتمیک، idempotent، SKU/stock |
| ۵ شارژ واقعی | ۴–۶ روز | ۴ | 🟠 قابلیت محصولیِ اصلیِ نصفه‌کاره |
| ۶ یادگیری ۲ | ۴–۵ روز | ۲ | 🟡 قابل‌اعتمادکردن حافظه |
| ۷ ردیابی | ۲–۴ روز | ۱ | 🟡 ابزار روزمرهٔ انبار |
| ۸ مشاهده‌پذیری | ۳–۴ روز | ۱ | 🟡 تشخیص خرابی |
| ۹ تست/انتشار | مستمر | همه | 🔵 ضد‌پس‌رفت |

مجموع مسیر بحرانی (فاز ۰→۴): ~۲۵–۳۸ نفر-روز؛ فاز ۰ تنهایی در یک هفته قابل‌تمام‌کردن است و ۸۰٪ ریسک
محصولی را پایین می‌آورد.

---

## ۶) معماری هدف

```
bot/
├── config.py                  # Settings.load() + validators + check_config()      [فاز ۱]
├── app.py                     # PTB wiring, persistence, rate limiter, scoped cmds [فاز ۱]
├── i18n_fa.py                 # تک‌مبدأ رشته‌های کاربری                              [فاز ۳]
├── services/
│   ├── store.py               # JsonStore اتمیک + کش (roles/preferences/learning)   [فاز ۱]
│   ├── workspace.py           # دایرکتوری موقت + TTL sweep                          [فاز ۱]
│   ├── postmodel.py           # متن → Block[] با provenance                         [فاز ۲] ★
│   ├── vocabulary.py          # رنگ/مدل/برند/توقف‌واژه‌ها (تک‌مبدأ)                  [فاز ۲] ★
│   ├── model_catalog.py       # کاتالوگ صریح برند→خانواده→وارینت + canonical       [فاز ۲] ★
│   ├── money.py               # پارسر واحدِ مبلغ (Amount)                          [فاز ۲] ★
│   ├── extractor.py           # deterministic (تصمیم‌گیر) + AI (پیشنهاد) + reconcile [فاز ۲] ★
│   ├── color_matrix.py        # فقط ماتریس/زیرمجموعه؛ lexicon را از vocabulary می‌گیرد
│   ├── validation.py         # Draft → ok/warn/block + شمارهٔ واریژن (تک‌مبدأ)     [فاز ۲] ★
│   ├── plan.py                # resolve_variation_plan() ← پیش‌نمایش + payload + manifest [فاز ۲] ★
│   ├── media_pipeline.py      # دانلود/Retry/compress (مشترک محصول و فشرده‌سازی)   [فاز ۳]
│   ├── woo_client.py          # auth/retry/redact/dry-run                          [فاز ۴]
│   ├── woo_product.py         # create/update/rollback/verify (قبلاً woocommerce_direct) [فاز ۴]
│   ├── sku.py                 # allocation + کش + fallback                         [فاز ۴]
│   ├── learning.py            # rules + confidence + review queue                  [فاز ۶]
│   └── metrics.py             # sqlite counters + export                           [فاز ۸]
├── flows/                     # جای modules/* برای جریان‌های stateful
│   ├── base.py                # FlowState, one-active-flow, timeout, cleanup hook  [فاز ۳]
│   ├── product_new.py         # COLLECT → PARSE → REVIEW → CONFIRM → PUBLISH → DONE
│   ├── product_restock.py     # MATCH → DIFF → APPLY → REPORT                      [فاز ۵]
│   ├── tracking_convert.py
│   ├── compress_images.py
│   └── admin_panel.py         # admins + settings + learning + status
└── keyboards/                 # فقط رندر؛ هیچ متنی اینجا ساخته نشود
```

نشان ★ = ماژول تازه/جابه‌جا‌شده که «منطق کسب‌وکار» را از تلگرام جدا می‌کند. قانون سخت:
**هیچ فایل در `services/` نباید `telegram` import کند** (فعلاً `product_extractor` و `color_matrix` پاکن‌اند،
ولی `product_flow` معکوسِ این را با import خصوصی نقض می‌کند — در فاز ۲ بسته می‌شود).

### قرارداد داده (قلب هر دو مسیر خروجی)

```python
@dataclass(frozen=True)
class Evidence:            # چرا این مقدار؟
    message_id: int
    line_no: int
    snippet: str           # متن دقیقِ منبع، برای نمایش در پیش‌نمایش
    origin: Literal["caption", "info", "ai", "override", "learning"]

@dataclass(frozen=True)
class Field:               # هر فیلد = مقدار + اطمینان + منبع
    value: str | int | None
    confidence: float      # 0..1
    evidence: Evidence | None = None
    needs_review: bool = False

@dataclass
class ProductDraft:
    title: Field
    price: Field
    group_prices: dict[str, Field]      # {"iphone": …, "android": …}
    sku_prefix: Field
    models: list[ModelRef]              # کاننیکال + alias + منبع
    attributes: dict[str, list[str]]
    model_colors: dict[str, list[str]]
    stock: dict[tuple[str, str], int]   # فاز ۵
    categories: list[CategoryPath]
    images: list[ImageRef]
    warnings: list[Warning]             # ساختاریافته، نه رشتهٔ آزاد
    batch_id: str                       # sha1 → کلید idempotency
    def to_manifest(self) -> dict        # product.json (نسخه‌دار)
    def to_woo_payload(self) -> dict     # فقط فیلدهای معتبر
```

`ProductDraft` تنها منبع حقیقت است؛ `product.json` (ZIP) و `payload` REST هر دو از آن تولید می‌شوند،
پس «پیش‌نمایش = واقعیت» از ادعا به **تضمین ساختاری** تبدیل می‌شود (رفع ریشه‌ای P0-6/P0-7/P0-10).
فیلدهای مردهٔ مانیفست امروزی (`description`، `product_type`، `categories` در حالت update) یا واقعی می‌شوند
یا حذف.

---

## ۷) استراتژی تست

| سطح | چه چیزی | ابزار | درها |
|---|---|---|---|
| واحد | `money`, `vocabulary`, `model_catalog`, `plan`, `validation`, `store`, `sku` | pytest | هر PR |
| contract | corpus پست‌های واقعی → `expected.json` (قیمت، مدل‌ها، تعداد واریژن، هشدارها) | `pytest --snapshot` | هر PR |
| flow | شبیه‌سازی آپدیت‌های تلگرام (callback/عکس/متن) با `PTBTestApplication` — شامل «تأیید دوبار»، «عکس بعد از لغو»، «آلبوم دیرهنگام»، «متن وسط پردازش» | `pytest-asyncio` | هر PR |
| woo | `respx`/`httpx.MockTransport`: 400 lookup-table، 429، ترانزکت، batch partial-fail، timeout → assert rollback | mock | هر PR |
| properties | هیچ‌وقت `variation_count` تغییرناپذیر؛ هیچ مبلغی بدون واحد از دامنه بیرون نزند؛ `restrict` هیچ مدلی را حذف نکند | hypothesis | شبانه |
| e2e | WP+Woo در داکر با `TISA_DRY_RUN=0`، محصولات `TISA_TEST_*`، پاک‌سازی خودکار | workflow_dispatch | قبل از release |
| ops | `check-config`، `--version`، health-check `/status` (اگر webhook شد) | script | قبل از دیپلوی |

Fixtureهای `tests/fixtures/`: پست‌های sanitize‌شده، سه فایل ردیابی (xlsx با بارکد عددی، csv با ۵ رقم،
pdf با layout فعلی)، و `products/expected/` برای ZIP/manifest.

---

## ۸) مهاجرت داده و سازگاری

1. **`.env`:** ورود `PRICE_MIN`, `PRICE_MAX`, `BARCODE_LENGTHS`, `MAX_FILE_MB`, `MAX_ROWS`, `FLOW_TIMEOUT`,
   `TISA_DRY_RUN`, `VERBOSE_LOG`؛ `LOG_CHAT_ID` از «پیش‌فرض هاردکد» به «پیش‌فرض `None`» (رفتار شکسته می‌شود
   — در `README` و `CHANGELOG` به‌عنوان breaking change ثبت شود، و در `post_init` یک پیام «لاگ چت غیر فعال است»).
2. **`data/roles.json`:** بدون تغییر؛ `JsonStore` یک migrate نسخه ۱→۲ اضافه می‌کند (همان payload،
   `schema_version` جدید) و در صورت فایل نیمه‌کاره از `.bak` برمی‌گرداند.
3. **`data/learned.json`:** migrate به v2 (`scope`, `status`, `applied_to`) با fallback: قاعدهٔ فاقد scope
   → `scope: "global", status: "active"` (رفتار فعلی حفظ می‌شود، و در پنل یک بنر «بازبینی» می‌گیرد).
4. **`product.json` مانیفست:** `schema_version: 2` + `min_plugin_version`؛ مصرف‌کنندهٔ قدیمی باید نسخه را
   چک کند (پیش‌نویس ۱.۵: ربات در صورت `TISA_MANIFEST_V1=1` همان ساختار قدیمی را هم تولید کند، تا افزونهٔ
   به‌روزرسانی‌نشده نشکند).
5. **دکمه‌ها/callbackها:** افزودن `CB.*` جدید بدون تغییر مقادیر فعلی؛ استثنای `product:confirm` به
   `product:confirm:<token>` مهاجرت می‌کند → برای سازگاری، الگوی `^product:confirm(:[0-9a-f]{4,12})?$` ثبت شود
   تا کیبوردهای قدیمی (P1-4) نشکنند.
6. **feature flag rollout:** هر فاز پشت یک فلگ (`TISA_ENABLE_PLAN_V2`, `TISA_NEW_EXTRACTOR=…`) و
   «shadow mode»: استخراج قدیمی و جدید همزمان اجرا شوند، خروجی جدید فقط **لاگ** شود، و پس از ۲ هفته تطابق
   در corpus/تولید، جابه‌جا فعال شود. بازگشت = `pip install` نسخهٔ قبلی + فلگ خاموش.

---

## ۹) ریسک‌ها

| ریسک | احتمال×اثر | کاهش |
|---|---|---|
| بازنویسی استخراج، خطاهای جدیدِ جابه‌جا شده تولید کند | زیاد×زیاد | shadow mode + corpus ۵۰ تایی + flip با فلگ؛ هیچ فاز ۲ بدون پاس‌شدن corpus merge نشود |
| افت سرعت روی هاست اشتراکی (AI timeout, SKU scan) | زیاد×متوسط | debounce + کش + dry-run +JobQueue؛ حذف اسکن ۵۰ صفحه‌ای از مسیر کاربر (۴.4) |
| از دست رفتن جریان با ری‌استارت (Persistence) | متوسط×متوسط | `PicklePersistence` + TTL + sweep؛ در `📊 وضعیت` نمایش جریان‌های فعال |
| سازگاری افزونهٔ PHP با مانیفست v2 | متوسط×زیاد | `min_plugin_version` + حالت v1 پشت فلگ (فقط تولید مانیفست، نه منطق) |
| قاعدهٔ یادگرفتهٔ اشتباه در مقیاس | کم×فاجعه | تأیید پیش‌فعال‌سازی + «تأثیر روی ۷ محصول آخر» + سقف `variation_count` کاهش‌نیابد |
| دسترسی‌های تازه از مسیر `/wp-json/wc/v3` (query-string auth) | متوسط×زیاد | کپی‌نبودن راز در لاگ (redact تست‌شدنی)، `SUPERVISOR_*` و کلیدها فقط در `.env` با مجوز ۶۰۰ |

---

## ۱۰) بک‌لاگ آمادهٔ Issue‌سازی

| Title | نوع | اولویت | فاز | بلوکِ |
|---|---|---|---|---|
| `fix(processor): never emit invalid/precision-destroyed barcode into tracking.csv` | bug | P0 | ۰ | — |
| `fix(extractor): reject non-price lines (weight/date/code/sku) as price source` | bug | P0 | ۰ | — |
| `fix(extractor): take the amount adjacent to the unit, not the first number in line` | bug | P0 | ۰ | — |
| `fix(extractor): parse multi-group price lines (iphone + android in one line)` | bug | P0 | ۰ | — |
| `fix(phone_parser): Persian variant words + bare "max"; never merge distinct models` | bug | P0 | ۰ | — |
| `refactor(plan): single resolve_variation_plan for preview + woo payload` | bug | P0 | ۰ | — |
| `fix(flow): require models for mode=new; shared validate(draft, mode)` | bug | P0 | ۰ | — |
| `fix(flow): make confirm idempotent (lock + one-time token + batch_id)` | bug | P0 | ۰ | — |
| `fix(config): drop hardcoded LOG_CHAT_ID default; fail loudly on malformed values` | bug | P0 | ۰ | — |
| `fix(flow): cancel pending album tasks on exit; delete workspace root on cleanup` | bug | P0 | ۰ | — |
| `chore(repo): remove tracked __pycache__/OPTION, stale README claims, version skew` | chore | P1 | ۰ | — |
| `feat(tooling): pyproject + ruff + mypy + CI + pinned requirements` | infra | P1 | ۱ | — |
| `feat(store): atomic cached JsonStore for roles/preferences/learning` | infra | P1 | ۱ | ۶ |
| `feat(flow): state machine (COLLECT→PARSE→REVIEW→CONFIRM→PUBLISH) + timeouts` | feature | P1 | ۳ | ۰ |
| `feat(extractor): block-based post model + typed ProductDraft with evidence` | refactor | P1 | ۲ | ۰,۱ |
| `feat(woo): atomic create with rollback, verify, resume; per-variation SKU` | feature | P1 | ۴ | ۰ |
| `feat(restock): product matching in Telegram + real stock updates + diff preview` | feature | P2 | ۵ | ۴ |
| `feat(learning): rule review queue + impact preview + confidence` | feature | P2 | ۶ | ۲ |
| `feat(tracking): schema mapping UI, ledger, size/row caps, checksums` | feature | P2 | ۷ | ۱ |
| `feat(ops): structured product card + metrics + 📊 status + runbook` | infra | P2 | ۸ | ۱ |
| `test: corpus fixtures + property tests + flow simulation + woo mock` | test | P1 | ۰→۹ | — |

---

## ۱۱) توصیهٔ شخصی برای شروع (اگر فقط یک هفته وقت هست)

1. `git rm -r --cached OPTION` و پاک‌کردن `.pyc`ها (۵ دقیقه، بوی بد ریپو می‌رود).
2. حذف هاردکد `LOG_CHAT_ID` + type-safe config (۲ ساعت، ریسک حریم خصوصی صفر می‌شود).
3. سه فیکس قیمت (کمترین تغییرِ لازم در `money` در `_scan_prices`: رد بلوک‌های غیرقیمت + اولویت عددِ دارای واحد) و تست‌شان
   (۱ روز، بیشترین پولِ نجات‌یافته).
4. `validate(draft, mode)` مشترک + `models` اجباری + `asyncio.Lock` روی confirm (نیم روز، دو فاجعهٔ محتمل).
5. `resolve_variation_plan()` مشترک (۱ روز، قول «پیش‌نمایش = واقعیت» واقعی می‌شود).
6. `_cleanup` درست + sweep روزانهٔ `/tmp` + لغو `album_tasks` (نیم روز، سرور دیگر پر نمی‌شود).

بقیه (فاز ۲ به بعد) بدون عجله و با corpus؛ چون ربات در حال حاضر «کار می‌کند»، اما نه وقتی پست واقعی کمی
کثیف‌تر از سه fixture موجود باشد — و پست‌های تلگرامی تیساکیس همیشه کثیف‌ترند.

---

## ۱۲) پیوست: خروجی واقعی اجراها (مدرک §۱)

همه با `BOT_TOKEN=123:fake` و `LOG_CHAT_ID=` (پیکربندی پیش‌فرض ریپو) روی پایتون ۳.۱۱ + PTB 22.۸ گرفته شد.

```
# P0-9 config
log_chat_id when unset : -5061365940
log_chat_id when empty : -5061365940        ← همان چیزی که .env.example تحویل می‌دهد
ALBUM_WAIT_SECONDS='abc' → ValueError: could not convert string to float: 'abc'
IMAGE_QUALITY='88,'      → ValueError: invalid literal for int() with base 10: '88,'
LOG_CHAT_ID='-100abc'    → ValueError: invalid literal for int() …   (crash در زمان import)

# P0-1 processor (xlsx واقعی با «بارکد عددی» + «جمع کل» + کد فارسی داخل نام گیرنده)
order_id,tracking_code
306176,192999999999999989514240     ← بارکدِ ساختگی، با وجود «❌ ردیف 1: بارکد به‌صورت عدد ذخیره شده»
12345,610001573845123456789012
654321,610001573845123456789012
778899,6100015738451234567           ← بارکد ۱۹ رقمی: ❌ ولی در CSV نوشته شد

# P0-2/3/4 _scan_prices
'وزن 250 گرم'                       → price=250,000
'1403-01-01'                        → price=1,403
'SKU: BO147'                        → price=147,000
'کد ملی 1234567890'                  → price=1,234,567,890
'S24 اولترا 768t'                    → price=24
'A25 مشکی 598k'                      → price=25
'قیمت ایفون 698 اندروید 598'         → price=0, prices={'iphone':698000}     ← اندروید گم شد
«قاب سیلیکونی مگنتی/BO/1403-02-15/SKU: BO147/قیمت ایفون 698/اندروید 598/وزن 250 گرم»
                                     → price=250,000, prices={'iphone':698000,'android':598000}

# P0-5 phone_parser (normalize_caption)
Apple\n13 پرو مکس → 'iPhone 13' | Apple\n13 پرو → 'iPhone 13' | Apple\n13 → 'iPhone 13'   ← ادغام سه مدل
Apple\n15max → 'iPhone 15' | Apple\n15 Max → 'iPhone 15'
xiaomi\n12 pro → ''            (مدل‌های غیر Note کاملاً نامرئی)
Huawei\nP30 pro / Vivo Y33 / Google Pixel 8 / Nothing Phone 2 → ''   (بدون هیچ هشدار)
Samsung\nM33 → نامرئی | 'A03s' → 'A03s' ✅

# P0-6 preview vs payload
attrs={"رنگ":["سفید","سفید"]} → preview variation_count=4 ; woo attrs=[{'name':'مدل',...}]
attrs={"رنگ":[" مشکی ","مشکی"]} → preview=4 ; woo همان محور را حذف می‌کند

# P1-11 color matrix «نشت» بین کپشن و پیام اطلاعات
caption(Apple/17pro max/16 pro) + info("سفید/مشکی/نارنجی") →
  🎨 ماتریس رنگ: 1 مدل محدود شد
  • iPhone 16 Pro: سفید/مشکی/نارنجی        ← رنگ‌بندی عمومیِ پیام دوم به آخرین مدل کپشن چسبید
  ⚠️ بدون محدودیت رنگ (همهٔ رنگ‌ها): iPhone 17 Pro Max

# P1-3 temp leak (بعد از _cleanup با ساختار واقعی root/ + root/compressed/)
session removed: True | compressed still there: False | ORIGINAL still there: True
leftover tree: ['7_1234', '7_1234/01_photo.jpg']

# app build با PTB 22.8 (P1-15)
BUILD OK + 4× PTBUserWarning: "If 'per_message=False', 'CallbackQueryHandler' will not be tracked…"
  (tracking_converter.py:180, product_flow.py:720, image_compress.py:175, admins.py:341)

# تست‌ها
python -m unittest discover -s tests   → Ran 99 tests … OK (skipped=42)  بدون deps
pytest -q (با deps نصب‌شده)             → 99 passed
```

> اسکریپت‌های repro به‌صورت موقت در `/tmp/probe*.py` نوشته و اجرا شدند و **چیزی در ریپو تغییر ندادند**.
> برای بازتولید: هر قطعه را در `tests/fixtures/` تبدیل به تست کنید (فاز ۰، Task 0.11) — انتظار این است که
> پیش از فیکس، قرمز باشند.
