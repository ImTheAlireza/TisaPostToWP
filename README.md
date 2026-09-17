# TisaCase Management Bot

Modular, keyboard-driven Telegram bot for case management on [tisacase.com](https://tisacase.com).

Every interaction is button-based: each inline-keyboard button either triggers a
single action or starts a `ConversationHandler` flow.

## Quick start

```bash
cp .env.example .env        # paste your BOT_TOKEN from @BotFather
pip install -r requirements.txt
python main.py
```

Send `/start` to the bot to open the main menu.

## Behavior

The bot only works in **private chats**. Any update that comes from a group,
supergroup or channel is ignored completely — commands, replies to the bot,
@mentions, documents and inline-button callbacks are all dropped **before any
handler runs**. This applies to *everyone*, including the sudo owner: no
message from a group is ever processed. To also prevent the bot from being
added to groups in the first place, use @BotFather → Bot Settings → Group
Privacy → `/setjoingroups` → Disable.

## Roles (role-based access)

Every user is exactly one of three roles:

| Role    | Who sets it                  | Stored in               | What they can do                                             |
|---------|------------------------------|-------------------------|--------------------------------------------------------------|
| 👑 sudo | You, via `SUDO_IDS` in `.env`| `.env` (not runtime-editable) | Everything: converter, phone-post processor, Ping, 🔄 restart, «👥 مدیریت ادمینها», «⚙️ تنظیمات» and «🧠 یادگیری‌ها» |
| 🛡️ admin| You, at runtime from the menu | `data/roles.json`       | Only the buttons the owner marked *admin-eligible* and left visible in «⚙️ تنظیمات» (today: converter, image compression, product new/restock). Everything else is hidden, and its callback is rejected server-side |
| 👤 user | —                            | —                       | Denied everywhere (no access to any feature)                 |

The main menu is **role-aware**: an admin only ever sees the tracking-file
converter button; the Ping / ری‌استارت / مدیریت ادمینها buttons are shown to
sudo only (and are re-checked server-side, so forging their callback data is
rejected too).

### Managing admins

Sudo opens **👥 مدیریت ادمینها** from the main menu → lists current admins with
their numeric IDs, and lets you:

* **➕ افزودن ادمین** — forward any message from the person to add, **or** type
  their numeric Telegram user ID.
* **حذف ادمین <id>** — per-admin remove button (with a confirmation step).

Admins persist across restarts in `data/roles.json` (git-ignored, written
atomically so a restart in the middle of a save cannot wipe the list). The sudo
owner is never listed or removable from the menu, so you can't lock yourself
out.

## Data files (all optional, all git-ignored)

| File | What it is |
|---|---|
| `data/vocabulary.json` | word substitutions, applied before parsing |
| `data/model_catalog.json` | brands/variants this shop sells, merged over the built-in table |
| `data/learned.json` | corrections the bot has learned from you |
| `data/flow_state.json` | which flows were open when the process died |
| `data/recent_products.json` | the last result cards (id, link, variation count, warnings) |
| `data/admins.json`, `data/preferences.json` | RBAC and per-user settings (written atomically, with a `.bak`) |

Delete any of them and the bot falls back to its defaults — none of them is
required to start.

## Configuration (`.env`)

| Variable    | Required | Description                                                        |
|-------------|----------|--------------------------------------------------------------------|
| `BOT_TOKEN` | yes      | Token from [@BotFather](https://t.me/BotFather)                    |
| `SUDO_IDS`  | yes      | Comma-separated numeric Telegram user ID(s) of the owner (**sudo**). Get your ID from @userinfobot / @getmyid_bot. |
| `LOG_LEVEL` | no       | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`)            |
| `SUPERVISOR_PROGRAM` | no | Supervisor program name for the 🔄 restart button (default `tisabot`) |
| `SUPERVISORCTL_BIN` | no | Path to `supervisorctl` if not on `PATH`                          |
| `SUPERVISOR_CONF` | no | Supervisor config for `supervisorctl -c …` (e.g. `/etc/supervisor/supervisord.conf`). If unset, common locations are auto-tried. |
| `SUPERVISOR_URL` | no | supervisord server URL for `supervisorctl -s …` (e.g. `unix:///var/run/supervisor.sock`) |
| `ALBUM_WAIT_SECONDS` | no | Wait time for collecting Telegram photo albums (default `1.8`). |
| `MAX_DOWNLOAD_MB` | no | Maximum size of each downloaded image (default `20`). |
| `IMAGE_QUALITY` | no | JPEG quality for compressed output (default `88`). |
| `AI_BASE_URL` / `AI_TOKEN` / `AI_MODEL` | no | Optional OpenAI-compatible API for normalizing messy phone captions. |
| `AI_TIMEOUT_SECONDS` | no | Timeout for the AI calls (default `30`). AI failures are logged and the deterministic parser is used. |
| `LOG_CHAT_ID` | no | Telegram chat receiving the product-processing log. **Empty = disabled** — there is no built-in default on purpose. |
| `PRICE_MIN` / `PRICE_MAX` | no | Sanity range for a parsed price in toman (defaults `1000` / `500000000`). Anything outside is reported instead of published. |
| `REQUIRE_MODELS` | no | Refuse to publish a product with zero detected models (default `yes`). |
| `FLOW_TIMEOUT_SECONDS` | no | Idle time before a product flow is closed and its temp files deleted (default `900`). |
| `TEMP_TTL_HOURS` | no | Age after which leftover `/tmp` workspaces are swept (default `12`). |
| `MAX_FILE_MB` / `MAX_ROWS` / `PROCESS_TIMEOUT_SECONDS` | no | Limits for the tracking-file converter. |
| `BARCODE_LENGTHS` | no | Accepted barcode digit counts, comma-separated (default `24`). |
| `WOOCOMMERCE_URL` | no | Store URL used by the WooCommerce REST connection test. |
| `WOOCOMMERCE_CONSUMER_KEY` / `WOOCOMMERCE_CONSUMER_SECRET` | no | WooCommerce REST API credentials used by the Ping diagnostic and the direct product writer. |
| `WOOCOMMERCE_API_VERSION` | no | API path version, default `wc/v3`. |
| `WORDPRESS_URL` / `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD` | no | Application Password used for media uploads (product images) and the upload/delete test.

---

## Features

### 📦 تبدیل فایل کد رهگیری (tracking-file converter)

فایل سفارش (اکسل / CSV / PDF خروجی سامانه تیساکیس و تیسا چاپ) را می‌گیرد و:

1. ستون‌های **بارکد** و **کد سفارش** را پیدا می‌کند — اگر ستون «کد سفارش» جدا
   وجود نداشته باشد، کد ۵-۶ رقمی را از داخل ستون **نام گیرنده** برمی‌دارد
   (مثل «امیرحسین عاشوری ۳۰۶۱۷۶»)
2. **مشکلات** را گزارش می‌دهد (سلول خالی، بارکد/کد تکراری، فرمت اشتباه، بارکد ۲۴ رقمی خراب‌شده توسط اکسل و …) — سطر «جمع کل» نادیده گرفته می‌شود
3. فایل **`tracking.csv`** با دو ستون `order_id,tracking_code` می‌سازد (+ `problems.txt` اگر مشکلی باشد)

> کد سفارش‌های خالی در CSV **خالی** می‌مانند تا خودت تکمیل کنی.

**جریان کار:** دکمه «📦 تبدیل فایل کد رهگیری» → فایل را به‌صورت Document بفرست
(`.xlsx` / `.csv` / `.pdf`) → خروجی‌ها را بگیر → فایل بعدی، یا «⬅️ بازگشت به منو» / `/cancel`.

مشکلاتی که تشخیص داده می‌شود:

| نوع | شدت |
|---|---|
| بارکد خالی / نامعتبر (طول ≠ ۲۴) / تکراری | ❌ خطا |
| بارکد به‌صورت عدد ذخیره‌شده (اکسل دقتش را از بین برده، مثل `1.93E+23`) | ❌ خطا |
| کد سفارش خالی / ۵ رقمی / تکراری | ⚠️ هشدار |
| کد سفارش نامعتبر (طول ≠ ۶ یا غیرعددی) | ❌ خطا |

### 📱 پردازش پست گوشی

دکمهٔ sudo-only «پردازش پست گوشی» یک جریان مکالمه‌ای دارد: عکس‌ها و کپشن مدل‌ها را می‌گیرد، سپس متن آزاد قیمت/عنوان/SKU/ویژگی‌ها را تا زمان تأیید جمع می‌کند، پیش‌نمایش می‌دهد و در نهایت ZIP آمادهٔ محصول متغیر (با توضیحات خالی) می‌سازد. تصاویر فشرده می‌شوند و مدل‌ها و سایر ویژگی‌ها در `product.json` ذخیره می‌شوند. در صورت تنظیم `AI_BASE_URL`، `AI_TOKEN` و `AI_MODEL`، کپشن‌های نامنظم با مدل OpenAI-compatible نیز استاندارد می‌شوند.

این قابلیت از منوی اصلی با دکمهٔ «📱 پردازش پست گوشی» در دسترس است.

#### 🎨 رنگ هر مدل (ماتریس رنگ)

پست‌های واقعی، رنگ‌های موجود را **برای هر گوشی جداگانه** می‌نویسند:

```
Apple
📱17promax :
سفید/مشکی/نارنجی
📱17pro :
مشکی
Samsung
📱S26ultra (صورتی و سفید)
📱A25 (سلفی مشکی)
xiaomi (فقط سفید)
📱Note 14 pro 4g
```

ووکامرس فقط ویژگیِ تخت دارد، پس ربات این دو را از هم جدا می‌کند:

| | نتیجه |
|---|---|
| **ویژگی «رنگ»** | اجتماعِ *همهٔ* رنگ‌های پست (سفید، مشکی، نارنجی، نچرال، دیزرت، صورتی، سیرابلو، بنفش، آبی) |
| **variationها** | فقط جفت‌های مدل↔رنگی که فروشنده نوشته — برای مثال iPhone 17 Pro فقط «مشکی» می‌گیرد، نه هر ۹ رنگ |

برای پست نمونهٔ بالا: **۳۶ مدل × ۹ رنگ = ۳۲۴ ترکیب کامل ← ۷۹ variation معتبر.**

قالب‌هایی که تشخیص داده می‌شوند:

* رنگ در خط بعد از مدل (`📱17promax :` و سپس `سفید/مشکی/نارنجی`)
* رنگ داخل خودِ خط مدل، با پرانتز یا بدون آن (`S26ultra (صورتی و سفید)`)
* گروه‌های اسلشی (`12/12pro` یک مدل می‌ماند و رنگ‌ها به همان یک مدل می‌رسند)
* **رنگِ سراسریِ یک برند**: `xiaomi (فقط سفید)` به همهٔ مدل‌های زیرش ارث می‌رسد
* کلمه‌های غیررنگ نادیده گرفته می‌شوند: `فقط`، `سلفی مشکی` ← فقط «مشکی»
* مترادف‌ها یکسان‌سازی می‌شوند (`سیاه`/`black` ← «مشکی»، `PINK` ← «صورتی»)
* رنگِ ناشناخته ولی لیست‌شکل (`سفید/لاجوردی`) حذف نمی‌شود

**امن بودن:** مدلی که رنگی برایش نوشته نشده **محدود نمی‌شود** (همهٔ رنگ‌ها را
می‌گیرد) تا هیچ variation قابل‌فروشی از بین نرود؛ این مدل‌ها در لاگ با
«⚠️ بدون محدودیت رنگ» گزارش می‌شوند. اگر رنگ‌های یک مدل با گزینه‌های واقعیِ
ویژگی «رنگ» هیچ اشتراکی نداشته باشند (اختلاف املا بین AI و کپشن)، آن مدل هم
محدود نمی‌شود. پیش‌نمایش، تعداد واقعی variation را نشان می‌دهد — همان عددی که
ساخته می‌شود.

مسیر ZIP/شارژ هم همین ماتریس را می‌برد: کلید `model_colors` داخل `product.json`
نوشته می‌شود و افزونهٔ وردپرس (نسخهٔ ۰٫۷٫۰ به بعد) هنگام ساخت variationها
اعمالش می‌کند.

#### ✏️ ویرایش فیلد‌محور (و «منظورت این بود؟»)

قبلاً تنها راه اصلاح، نوشتن دوبارهٔ متن بود؛ حالا روی همان صفحهٔ پیش‌نمایش
دکمهٔ **«✏️ اصلاح فیلد خاص»** هست: فهرست فیلدها با مقدار فعلی، انتخاب، و نوشتن
**فقط همان یک مقدار**. هر فیلد پارسر خودش را دارد (قیمت، رنگ‌ها، مدل‌ها،
پیشوند SKU، دسته‌ها، قیمت جدا برای هر گروه، ویژگی‌های دستی) و اگر چیزی که
نوشته‌ای به آن فیلد نمی‌خورد، پیام فارسی می‌گیری و مقدار قبلی دست‌نخورده
می‌ماند — نه یک «خطا»ی بی‌معنی و نه یک تغییر نیمه‌کاره.

دو قاعدهٔ مهم:

* **ویرایش دستی قفل است**: تا وقتی خودت چیزی را عوض نکرده‌ای، استخراج بعدی
  (عکس تازه، متن تازه، یا درخواست بعدی به AI) مقدار دست‌نویس تو را بازنمی‌نویسد.
* **هر ویرایش در پیش‌نمایش منبع دارد**: برچسب «ویرایش شما» کنار فیلد می‌آید تا
  معلوم باشد چه چیزی را تو گفته‌ای و چه چیزی را ماشین.

اگر رنگ‌ها از **دو پیام** آمده باشند (معمول‌ترین حالت چسبیدن دو محصول)، یادداشت
«این رنگ‌ها مال این محصول نیست» دیگر فقط توصیه نیست: دکمهٔ **«🎨 رنگ‌ها از چند پیام
آمده»** لیست رنگ هر پیام را با شمارهٔ پیام نشان می‌دهد و با یک ضربه رنگ آن پیام
هم از پارسر deterministic و هم از **متنی که به هوش مصنوعی داده می‌شود** بیرون
می‌رود، پس در دور بعدی خودبه‌خود برنمی‌گردد.

برندِ غلط‌تایپ‌شده هم بن‌بست نیست: اگر کلمه‌ای کنار شمارهٔ مدل شبیه یک برندِ
شناخته‌شده باشد، دکمهٔ **«بله، منظورت Nokia بود؟»** می‌آید (فاصلهٔ ویرایشی کم و
فقط یک کاندید؛ دو کاندید یعنی حدس نمی‌زنیم). با تأیید، همان لحظهٔ اصلاح در
**واژه‌نامهٔ فروشگاه** نوشته می‌شود، پس برای همیشه و در هر دو مسیر (متن و AI)
صحیح خوانده می‌شود. «نه» گفتن فقط همین محصول را ساکت می‌کند.

#### 🎯 کارت نتیجه، 🧾 آخرین محصولات، 🔍 تست پارسر

یک ساخت موفق با «✅ ساخته شد» تمام نمی‌شود. ربات یک **کارت نتیجه** می‌فرستد:

```
🎯 پیش‌نویس ساخته شد
🆔 id: 4321 · پیش‌نویس
🔗 https://shop/wp-admin/post.php?post=4321&action=edit
🌐 انتشار نهایی فقط از داخل سایت انجام می‌شود.

عنوان: قاب سیلیکونی آیفون 13 پرو مکس
🎨 79 واریژن · 🖼 12 تصویر · 💰 698,000 تومان
🏷 پیشوند SKU: BO147
📎 1 نکته‌ای که باید بدانی: رنگ برای ۲ مدل محدود نشد
```

با دکمه‌های **🌐 ویرایش در سایت**، **📦 محصول بعدی (همان تنظیمات)** و
**🧾 گزارش همین محصول**. «محصول بعدی» همان حالت (جدید/شارژ) را باز می‌کند و
کارت قبلی را **پاک نمی‌کند** — برای اینکه شمارهٔ محصول زیر دستت نرود.

هر کارت (موفق یا ناموفق) در `data/recent_products.json` می‌ماند، پس
**🧾 آخرین محصولات** در منو همان کارت‌ها را دوباره نشان می‌دهد — حتی بعد از
ری‌استارت. چیزی که در کارت ذخیره می‌شود، همان پیش‌نمایشی است که **تأیید کردی**،
نه بازسازیِ امروزِ پارسر.

**🔍 تست پارسر** هم همین‌جا است: یک متن نمونه می‌فرستی و همان چیزی را می‌بینی که
جریان محصول می‌بیند (مدل‌ها، قیمت‌ها، رنگ‌ها، دسته‌ها، منبع هر فیلد، هشدارها،
پیشنهادها) — بدون ساخت هیچ محصولی. این عمدتاً **همان کدِ جریان** را اجرا
می‌کند: اگر تست یک پارسر دوم و ساده‌تر داشت، جواب سؤالِ «چرا ربات این‌طور
خواند؟» را نمی‌داد.

و بعد از هر ویرایش دستی، به‌جای رندر دوبارهٔ کل پیش‌نمایش، یک خط diff
می‌گیری: «قیمت: 250,000 تومان ← 698,000 تومان · +2 واریژن (4 ← 6)» و دکمهٔ
«👁 پیش‌نمایش کامل» هر وقت خواستی.

#### 🚦 دو صفحه، دو قاعده (COLLECT و REVIEW)

جریان محصول حالا ماشین‌حالت دارد، و حرف اصلی‌اش این است که **یک متن آزاد
کجا information است و کجا فقط یک پیشنهاد**:

| صفحه | متن آزاد یعنی | دکمه‌ها |
|---|---|---|
| **در حال جمع‌آوری** (عکس/متن هنوز کامل نشده) | همان «اطلاعات محصول» است و به متن فعلی اضافه می‌شود | ✅ تصاویر تمام شد · ❌ لغو |
| **بازبینی** (پیش‌نمایش را دیدی) | یک **پیشنهاد** است: «این را اضافه کنم؟» با ✅/⏭️ — تا تأیید نکنی هیچ فیلدی حرکت نمی‌کند | ✏️ اصلاح فیلد خاص · ➕ افزودن عکس یا متن · 🎨 جدا کردن رنگ‌ها · ✅ تأیید |

قبلاً هر دو یکی بودند؛ به همین دلیل جمله‌ای مثل «نه صبر کن، قیمت را عوض نکن»
می‌توانست داخل محصول برود. «✅ تصاویر تمام شد» هم منتظر تایمر آلبوم نمی‌ماند:
صفِ عکس‌های معلق را همان لحظه پردازش می‌کند.

**یک جریان فعال:** با ورود به یک جریان، جریان بازِ دیگر همان کاربر بسته می‌شود
(سشن، فایل‌های موقت و آلبوم نیمه‌کاره) و ربات می‌گوید چه چیزی را بسته:
«↩️ جریان «فشرده‌سازی عکس‌ها» قبلی‌ات بسته شد». اگر هم کلیک قدیمی روی صفحهٔ
بسته‌شده بمانی، پیام «این جریان بسته شده است» می‌گیری — ربات پشت صحنه یک
سشن نصفه برای‌ات نمی‌سازد.

**مسیر پیام‌ها:** هر پیامی که ربات خودش می‌فرستد (پیش‌نمایش، مراحل کار، کارت
نتیجه) به همان چت و همان رشتهٔ گفتگو (thread) برمی‌گردد که جریان از آنجا شروع
شده — نه لزوماً به آیدی کاربر. در چت‌های تاپیک‌دار، همین تفاوت فرقِ «درست تحویل
شد» با «ریخت توی چت عمومی» است.

#### 🧠 یادگیری از اصلاحات (حالت خودیادگیر)

وقتی ربات چیزی را اشتباه برداشت می‌کند و مالک اصلاحش می‌کند، اصلاح **در همان نشست**
اثر می‌کند و قاعدهٔ کلی‌اش هم برای همیشه ذخیره می‌شود. نمونهٔ واقعی:

```
قیمت به این شکل ارسال می‌شود:   1098
ربات قبلاً می‌فهمید:            ۱٬۰۹۸ تومان      ← اشتباه
مالک اصلاح می‌کند:              قیمت 1098000 تومان
ربات یاد می‌گیرد:               💰 عدد ۴ رقمیِ بدون پسوند = ×۱٬۰۰۰
از این پس:                      1198 → ۱٬۱۹۸٬۰۰۰ تومان   (بدون هیچ اصلاحی)
```

قاعده روی **تعداد رقم** کلید می‌خورد نه روی خودِ عدد، پس به محصول بعدی هم تعمیم
می‌یابد. دو نوع قاعده یاد گرفته می‌شود:

| نوع | چه وقتی | نمونه |
|---|---|---|
| `price_scale` | قیمتِ برهنه ضریبی از ۱۰ اشتباه خوانده شده | «1098» ← ۱٬۰۹۸٬۰۰۰ ⇒ هر عدد ۴ رقمی ×۱٬۰۰۰ |
| `term` | یک واژه با واژهٔ دیگری جایگزین شده | «سلفی» ← «مشکی»، «Air skin» ← «Airskin» |

**سه محافظ، تا یک قاعدهٔ اشتباه همهٔ محصولات بعدی را خراب نکند:**

* مقدارِ اصلاح‌شده باید واقعاً در همان پیامی که رسیده نوشته شده باشد (تا
  «هوش مصنوعی نظرش عوض شد» به‌جای «مالک اصلاح کرد» چیزی یاد ندهد)؛
* نسبت باید دقیقاً توانی از ۱۰ باشد، پس تغییر معمولی قیمت
  («698000» ← «750000») قاعده نمی‌سازد — فقط ثبت می‌شود؛
* بازنویسی کامل عنوان یا اضافه‌شدن یک رنگ جدید «جایگزینی یک واژه» نیست،
  پس قاعده نمی‌سازد.

قواعد هم به مسیر قطعی اعمال می‌شوند و هم داخل prompt هوش مصنوعی تزریق می‌شوند،
تا این دو مسیر دربارهٔ یک اصلاح اختلاف پیدا نکنند.

دکمهٔ sudo-only **«🧠 یادگیری‌ها»** در منوی اصلی، حافظه را نشان می‌دهد: فهرست
قواعدها با تعداد دفعات اعمال، حذف تکی هر قاعده، «📜 تاریخچهٔ اصلاحات»، و
«🗑️ فراموشی همه» با تأیید دومرحله‌ای. یادگیری **فقط برای سودو** است — یک قاعده
نحوهٔ پارس‌شدنِ *همهٔ* محصولات بعدی را عوض می‌کند، پس نباید از یک حساب ادمینِ
مشترک قابل ساختن باشد. (خودِ اصلاح برای همه در همان نشست اثر می‌کند.)

همراه این قابلیت، سه باگ واقعیِ برداشت قیمت هم اصلاح شد:

* **اصلاح اصلاً اثر نمی‌کرد**: اولین قیمتِ یافت‌شده برنده بود، پس
  `1098` و سپس `قیمت 1098000 تومان` همان ۱٬۰۹۸ را می‌داد. اکنون در هر بلوکِ
  متن **آخرین** خط برنده است، و بلوکِ PRODUCT INFO همچنان بر کپشن اولویت دارد.
* عدد برهنه دیگر نمی‌تواند قیمتِ صریح را بازنویسی کند (`قیمت: 698000` و بعد
  `کد 1098` ← همان ۶۹۸٬۰۰۰).
* «۱ میلیون و ۹۸ هزار تومان» قبلاً **۱** خوانده می‌شد؛ اکنون واحدهای فارسی
  (میلیارد/میلیون/هزار) جمع می‌شوند ⇒ ۱٬۰۹۸٬۰۰۰.
* یک عددِ قیمت، مدلِ شبح‌وار نمی‌سازد: «1098» داخل بخش Apple پیشوندِ «10» را
  مطابقت می‌داد و یک **iPhone 10** اضافه می‌کرد (یک مدل کامل با variationهای
  خودش). `(?!\d)` این را بست.

### 🏓 Ping

Diagnostics button — measures bot round-trip and includes a WooCommerce REST test. The WooCommerce test reads one product through `wp-json/wc/v3/products` using HTTPS query-string authentication, matching shared-host configurations where Basic Auth is blocked.

### 🔄 Restart (via supervisor)

Sudo-only button → confirmation screen → runs
`supervisorctl restart $SUPERVISOR_PROGRAM` in a detached session (survives the
bot being stopped mid-restart). If plain `supervisorctl` can't reach
supervisord («refused connection»), the bot automatically retries with the
common Debian/Ubuntu config (`-c /etc/supervisor/supervisord.conf`) and unix
sockets — or pin it explicitly with `SUPERVISOR_CONF` / `SUPERVISOR_URL`.
On the next startup the bot edits the
«♻️ در حال ری‌استارت…» message to «✅ ربات با موفقیت ری‌استارت شد» — real proof
the cycle completed. If `supervisorctl` fails (bad program name, supervisor
down), the error output is shown in the chat instead.

Example supervisor config:

```ini
[program:tisabot]
command=python3 main.py
directory=/path/to/TisaPostToWP
autostart=true
autorestart=true
stopasgroup=false          ; keep false so the detached restart completes
```

---

## Safety rails (what the bot refuses to do)

These exist because every one of them used to be a real bug that silently
corrupted a product or an import file:

- **Nothing unverified is published.** A barcode Excel turned into a float, a
  19-digit code or a broken order code never reaches `tracking.csv`; those rows
  go to `needs-fix.csv` with the reason, and the chat summary counts them.
- **A number is only a price when nothing else explains it.** Weight, date,
  tracking-code, SKU and dimensions lines can no longer overwrite a price, an
  amount is read from the number written *next to its unit* («S24 اولترا 768t»
  is 768 000, not 24), and both groups of a line like
  «قیمت ایفون 698 اندروید 598» are kept. Prices outside `PRICE_MIN..PRICE_MAX`
  are refused instead of published.
- **The preview is the payload.** Variation axes, dedupe and per-model colour
  restriction are computed once (`bot/services/plan.py`) and consumed by the
  Telegram preview, the WooCommerce REST payload and `product.json`. The number
  you approve is the number that exists.
- **A model is never merged away.** Persian variant words are understood
  («۱۳ پرو مکس» ≠ «۱۳ پرو» ≠ «۱۳»), the brand may be written in Persian
  («آیفون 13 پرو مکس» is enough on its own — it used to yield no iPhone at all),
  and if a model line holds a word the parser could not apply, you get a warning
  instead of a plausible wrong model.
- **The preview says where every value came from.** «قیمت ← متن کپشن: قیمت 698»
  or «عنوان ← هوش مصنوعی» — plus a line for anything the bot refused to read as a
  price. A field that was guessed is visibly a guess, so you check the two
  suspicious lines instead of re-reading the whole post
  (`bot/services/postmodel.py`).
- **Your words are yours.** `data/vocabulary.json` is the shop's own dictionary;
  it is applied to the text *before* parsing, so the deterministic reader and
  the AI always see the same words.
- **A line is classified once, then trusted.** Every rule reads
  `bot.services.postmodel.Block` (price / model / colors / meta / brand /
  attribute / prose, with the message and line number it came from), so a
  `meta` line — weight, date, tracking code — cannot be a price *by
  construction* instead of by one more tuned regex.
- **Invented models are refused, not sold.** `bot/services/model_catalog.py`
  knows which variants a brand actually makes: «iPhone 15 اولترا» or
  «13 پرو پلاس» produce a warning in the preview (and the same table is handed
  to the AI, so it proposes inside it). A brand missing from the catalog is
  announced, never guessed. Extend it per shop with `data/model_catalog.json`.
- **No second implementation for a tool to disagree with.** «🔍 تست پارسر» calls
  `bot.modules.product_flow.analyze`, which is the flow's own extraction path, and the
  result card is rendered from `data/recent_products.json` — the same record the history
  screen reads, so a card cannot say one thing and the history another.
- **A missing dependency never bricks a deploy.** `python-dotenv` is optional:
  on a shared host without pip the bot still boots from the real environment.
- **One publish per product.** While a product is being written the keyboard is
  replaced by a «در حال ساخت…» message and a second tap is refused; a failed
  variation build rolls the half-built product back instead of leaving it live.
- **A flow cannot be left hanging.** Every screen has a real back button, an
  idle flow closes after `FLOW_TIMEOUT_SECONDS`, its temp workspace is deleted,
  and stale workspaces are swept hourly.

## Architecture

```
main.py                      # entrypoint (polling)
bot/
├── config.py                # Settings loaded from .env (BOT_TOKEN, SUDO_IDS, …)
├── rbac.py                  # role logic: sudo/admin/user + admin persistence
├── app.py                   # Application factory, /commands list, group-blocking
├── constants.py             # CB.* — callback-data namespace for all buttons
├── keyboards/               # keyboard builders, one module per screen
│   └── main_menu.py         # role-aware main menu
├── services/                # pure business logic — no Telegram imports
│   ├── money.py             # 💰 the ONLY amount parser (units, groups, range policy)
│   ├── plan.py              # 🧮 one variation plan → preview == payload == product.json
│   ├── validation.py        # ⚖️ shared ok/warn/block gate for both output paths
│   ├── barcodes.py          # barcode validity (Excel-float detection, configured lengths)
│   ├── jsonstore.py         # atomic + cached data/*.json (writes survive a restart)
│   ├── postmodel.py         # 🧭 provenance: which field came from caption/AI/filename
│   ├── vocabulary.py        # 📖 the shop's own dictionary, applied before parsing
│   ├── model_catalog.py     # 📚 what variants may exist; warns instead of inventing
│   ├── flow_state.py        # ♻️ ledger of active flows → an honest restart notice
│   ├── processor.py           # order file → tracking.csv (+ needs-fix.csv) + report
│   ├── phone_parser.py        # caption → canonical phone-model labels (fa + latin variants)
│   ├── color_matrix.py        # 🎨 per-model colors → full رنگ axis, restricted variations
│   ├── learning.py            # 🧠 owner corrections → generalizable rules (data/learned.json)
│   ├── product_extractor.py   # caption + PRODUCT INFO → ProductData (AI proposes, we decide)
│   └── woocommerce_direct.py  # draft product + variations through the Woo REST API
├── modules/                 # features — each exposes register(app)
│   ├── __init__.py          # ALL_MODULES registry (order matters)
│   ├── start.py             # /start, /menu, back-to-menu navigation
│   ├── tracking_converter.py# 📦 تبدیل فایل کد رهگیری (conversation flow)
│   ├── product_flow.py     # 📦 گفت‌وگوی ساخت ZIP محصول (+ تشخیص اصلاحات)
│   ├── admins.py            # 👥 مدیریت ادمینها (sudo) — add/remove admins
│   ├── learning_panel.py    # 🧠 یادگیری‌ها (sudo) — list/delete learned rules
│   ├── settings.py          # ⚙️ تنظیمات (sudo) — button visibility for admins
│   ├── ping.py              # 🏓 Ping button (sudo)
│   ├── restart.py           # 🔄 restart via supervisor (sudo) + startup confirmation
│   └── fallback.py          # unknown buttons/text/files + global error handler
└── utils/
    └── logging.py           # rotating logs/bot.log + secret redaction + [user <id>] tags
```

**Rules of the house**

- Handlers all live in group 0; within a group PTB runs only the *first* match
  in registration order. Conversation modules are registered **first** (so their
  `/start` / `/cancel` fallbacks win while a flow is active), `fallback` stays **last**.
- Every button's `callback_data` is a constant in `bot/constants.py` (`CB.*`),
  namespaced per feature (`ping`, `nav:main`, `tracking:convert`, ...).
- Modules never import each other — they share only `constants`, `keyboards`,
  `services`, `config`, and `utils`.
- Heavy processing runs via `asyncio.to_thread` so the bot never blocks.

## Adding a new feature

1. **Constants** — add the button's callback data to `CB` in `bot/constants.py`
   (e.g. `CASE_NEW = "case:new"`).
2. **Logic** — pure processing goes into `bot/services/<feature>.py`.
3. **Module** — create `bot/modules/<feature>.py` with a
   `register(app: Application)` function. For a flow, build a
   `ConversationHandler` whose entry point is
   `CallbackQueryHandler(entry, pattern=f"^{CB.CASE_NEW}$")`.
4. **Button** — add it to `bot/keyboards/main_menu.py` (or a submenu keyboard).
5. **Registry** — append the module to `ALL_MODULES` in
   `bot/modules/__init__.py` (conversations before `start`, always before `fallback`).

## Tests

```bash
python3 -m unittest discover -s tests     # no extra dependencies needed
pytest tests                              # runs everything available
ruff check bot tests main.py && mypy      # what CI also enforces
python main.py --check-config             # validate .env without starting polling
```

CI (`.github/workflows/ci.yml`) runs the suite on 3.11/3.12/3.13, refuses new ruff
or mypy findings in the `services/` layer, builds the `Application` with
`DeprecationWarning` as an error (so a python-telegram-bot major can't arrive
silently), and enforces a coverage floor.

`tests/test_color_matrix.py` covers the per-model color matrix: the color
lexicon and its guards (SKU/prose/material words are never colors), model
signature matching across caption/AI spellings, section-scoped colors, and the
safety valves that keep an unmatched model unrestricted instead of deleting its
variations.

`tests/test_learning.py` covers self-learning: rule inference and its three
guards, persistence/round-trip through `data/learned.json`, term application and
its word-boundary safety, the parser honoring a learned scale (and never scaling
an explicit «تومان»), Persian unit words, corrections taking effect (newest line
wins, PRODUCT INFO still beats the caption), and the `_learn_from_diff` wiring —
including the acceptance case where correcting «1098» once makes an unseen
«1198» parse as ۱٬۱۹۸٬۰۰۰. Every test points the memory at a temp directory, so
the real `data/learned.json` is never touched.

`tests/test_phone_parser.py` covers the bare-amount regression: a price line must
never become a phone model, while genuine model lines (`17`, `17promax`, `7/8`,
`XSMax`) keep working.

Both runners stay green with no third-party dependencies installed — the tests
that need `httpx` or `python-telegram-bot` skip themselves.
