# CHANGELOG

## 0.11.0 — فاز ۴ (بخش چهارم): 📦 موجودی، 🏷 قیمت ویژه، 🖼 تصویر هر رنگ، 🐇 صفِ خروجی

Added
: - **موجودی و قیمت ویژه از متن تا فروشگاه** (طرح 4.5/4.6). `scan_stock_and_sale()` در
  `bot/services/product_extractor.py` «موجودی ۲۰» و «قیمت ویژه ۴۹۸» را می‌خواند و
  «ناموجود/تمام شده/پیش‌فروش» را به `stock_status` تبدیل می‌کند. قاعده‌اش عمداً سخت‌گیرانه است: عددی
  موجودی‌ست که *صدا* زده باشد (برچسب یا پسوند «عدد»)، «۰ عدد» باور نمی‌شود، و
  «ناموجود» هرگز صفر نمی‌سازد. مسیر AI هم از همان `_merge_stock_and_sale()` می‌گذرد:
  متنِ صریح برنده است و عددی که فقط هوش مصنوعی دیده، روی کارت برچسب می‌گیرد.
: - `sale_price` واریژن‌ها، `menu_order` (همان ترتیب پیام فروشنده) و `visible: true`؛
  و **تصویر هر رنگ** وقتی نام فایل هم‌نام رنگ باشد (`01_مشکی.jpg`). ترتیب هرگز حدس
  زده نمی‌شود — چسباندن عکسِ رنگِ دیگر از بی‌عکس بودن بدتر است.
: - `bot/services/outbox.py` + `bot/modules/outbox_flow.py` (طرح 4.8): صفِ بادوامِ SQLite در
  `data/outbox.sqlite3`. یک خطای موقتی (429/5xx/خطای ترنسپورت) انتشار را نمی‌سوزاند:
  با backoff (۶۰ ثانیه و هر بار دو برابر، تا سقف ۳ ساعت)، ۸ تلاش در مجموع (۷ بار دیگر بعد از همان خطا) و سقف ۲۴ ساعت، دوباره
  امتحان می‌شود؛ همان `create_draft` واقعی را صدا می‌زند
  (پس همان `batch_id` و همان دروازهٔ idempotency —
  صف، مسیرِ دومِ انتشار نیست) و تصویرهایش را هنگام queue کپی می‌کند، چون پوشهٔ موقت
  سشن همان لحظه پاک می‌شود. هر دقیقه یک‌بار و یک بار در لحظهٔ استارت؛ در `TISA_DRY_RUN`
  صف نه نوشته می‌شود نه تخلیه.
: - دو فیلد تازه در `✏️ اصلاح اطلاعات`: «قیمت ویژه» و «موجودی» («حذف» یعنی ارسال نشود،
  که با صفر یکی نیست) — و ویرایش دستی از استخراج بعدی رد نمی‌شود.
: - وضعیت کارت/تاریخچه `🐇 queued`: یک تلاش هنوز یک کارت دارد، فقط آن کارت تا نتیجهٔ
  نهایی باز می‌ماند، و جلوی انتشار تازه را هم نمی‌گیرد.

Changed
: - `validate_draft()`: موجودی منفی و `stock_status` ناشناخته ⛔، موجودی غیرعادی ⚠️،
  و «قیمت ویژه ≥ هیچ‌کدام از قیمت‌ها (شامل هر گروه)» ⛔ — چون ووکامرس هر دو عدد را
  نگه می‌دارد و بزرگ‌تر را نشان می‌دهد، یعنی تخفیفی که دیده نمی‌شود.
: - محصول متغیر دیگر موجودیِ خودش را نمی‌گیرد: عدد روی واریژن‌ها می‌نویسد و روی والد فقط
  `stock_status` می‌رود؛ پیش‌نمایش و لاگ هم دامنه را با عدد می‌گویند («روی هر ۴ واریژن»).
: - `product_flow._safe()` حروف غیرلاتین را پاک نمی‌کند (نامِ فارسیِ فایل، همان چیزی است
  که رنگ را به عکسش وصل می‌کند) و هدر `Content-Disposition` با RFC 5987 فرستاده می‌شود؛
  قبلاً یک فایل «قاب‌مشکی.jpg» وسط انتشار `UnicodeEncodeError` می‌داد.
: - `product.json` در ZIP هم `sale_price/stock/stock_status` را می‌برد و از تابع مشترک
  `_zip_manifest()` ساخته می‌شود (قبلاً وسط `confirm()` به‌صورت یک خط ۴۰۰ کاراکتری بود).
  افزونهٔ فعلی این سه را نمی‌خواند — `docs/IMPORTER-CONTRACT.md` با صراحت می‌گوید کدام
  کلید در کدام مسیر اثر دارد، و چرا SKU واریژن و تصویر واریژن عمداً فقط یک‌طرفه
  نیستند.
: - `main.py --check-config` یک خط اضافه کرد: `outbox : <path> (N در صف)`؛ صفی که نتواند
  بنویسد افت کیفیت نیست، قولِ شکسته است.

Tests: 391 → 468. `tests/test_stock_and_sale.py` (۴۸ تست) برداشتِ عدد، دروازه، ویرایش
دستی، و **بدنهٔ واقعیِ درخواست‌ها** را می‌آزماید؛ `tests/test_outbox.py` (۲۹+۱۲ subtest)
رسیدگی به backoff، کپی تصویر، «۴۰۰ هیچ‌وقت در صف»، خطای موقتیِ بازیافته، رهاکردنِ
نهایی با کارتِ ❌، و دو تست اسکن‌محور: هیچ‌کس جز `outbox_flow` صف را نمی‌نویسد و
`bot/services/outbox.py` هرگز `bot/modules` را import نمی‌کند. `FakeStore` از
`test_idempotency.py` به `tests/_flow_harness.py` منتقل شد (و آپلود رسانه را هم یاد گرفت)
تا دو سوئیت یک درایور داشته باشند، و `temp_ledger()` حالا `outbox.DB_PATH/FILES_DIR` را هم
به دایرکتوری temp می‌برد — سوئیت، `data/outbox.sqlite3` واقعی مخزن را ساخته بود.

## 0.10.0 — phase 4 (part 3): 🔁 one HTTP layer, one retry policy, a SKU that does not scan

Added
: - `bot/services/woo_client.py` (plan 4.1) — auth (query-string for `wc/v3`, application
  password for WP routes), the `User-Agent`, timeouts, `follow_redirects`, the fake
  transport, the audit trace and the retry policy now live in one place. Four modules used
  to re-implement that: the writer, the diagnostics ping and the two 🔧 tools, with two
  spellings of the UA, four timeouts, and a retry loop only in the writer — so three of
  them read a busy host's 502 as "your credentials are broken". `WooClient` is what they
  all call now; a test scans the tree so nobody opens a second socket to the shop.
: - **The retry policy is the one deliberate behaviour change** (and it is the duplicate
  bug, not a cosmetic one): `POST /products` used to be retried on 500/502/504, and a 502
  from the proxy in front of PHP usually means *applied, answer lost* — so the retry
  created a second product. Now a write is resent only when the failure proves the shop
  never saw it (connect error) or refused it without side effects (429). A timed-out write
  is handed to the resume hunt in 0.9.0 instead of being doubled. A 500 on a read is not
  retried either: on WooCommerce it is a PHP fatal, and retrying triples the wait.
: - `bot/services/sku.py` (plan 4.4, and P1-10's latency half) — the SKU rule was moved out
  of the writer, and the high-water mark per prefix is now remembered in
  `data/sku_state.json` for ten minutes. A publish used to cost up to a hundred serial list
  requests (search pages, then a catalog walk) in front of the waiting admin; with a warm
  cache it costs one. Every candidate is still verified with an exact-SKU request, so the
  cache is a starting hint and can never hand out a taken SKU.

Changed
: - `bot/services/woocommerce_direct.py`: 1077 → 731 lines, and it no longer builds a
  request, an auth dict or an error parser. `_post_transient` is gone (the client's policy
  replaces it); `WooCommerceAPIError`, `Audit` and the fake transport are imported from
  `woo_client`, with `WooCommerceAPIError` still re-exported for `bot.modules.product_flow`.
: - `bot/services/sku.py: from_plugin()` keeps the plugin authoritative for the starting
  number (a stale local cache must not outrank the shop's own counter), while
  `remember()` is monotonic — a lower number is dropped rather than trusted.
: - `bot/services/woocommerce.py` logs the failing response body through the shared
  `body_snippet()` (redacted, single line) instead of hand-slicing `response.text`.

Fixed
: - `python main.py --version` no longer needs a `.env`. `bot.config` was imported at module
  level, so asking "which build is this?" on a host that is still being configured answered
  `RuntimeError: BOT_TOKEN is not set` — exactly the moment it is most useful. The config tests
  that used to swap `main.settings` now go through `patched_settings` on the real source: once
  the import is late, patching the copy would pass silently and prove nothing.

Tests: 360 → 391. `tests/test_woo_client.py` covers the injected policy, all six retry
cases, credential redaction in every user-facing string, and the three 🔧 tools (including
that the product probe deletes its uploaded media when the product POST is refused).
`tests/test_sku.py` covers scan-once/cache-after, stepping over a taken number, the
200-probe ceiling, expiry, and a corrupt state file. `TransportScript` (the scripted fake
transport) and `no_sleep()` moved into `tests/_flow_harness.py` so the two drivers cannot
drift; `temp_ledger()` now isolates `sku_state.json` as well — the suite must not leave a
warm cache behind, or the "dry run scans the catalog" assertion depends on test order.

## 0.9.0 — phase 4 (part 2): ♻️ یک محتوا، یک محصول (idempotency + provenance)

Added
: - `bot/services/publish_batch.py` — the identity of one publish attempt:
  `batch_id = sha1(content + images + chat)[:12]`, written to the shop as the
  `tisa_batch_id` product meta plus `tisa_source` (chat, thread, image/variation counts,
  bot version, capture time) — plan items 4.3 and 4.7. Content-addressing is on purpose:
  a uuid has to survive the crash, and a crash between «product POSTed» and «response
  received» is exactly what this guards. The same draft after a restart recomputes the
  same id, so there is nothing to persist and nothing to lose.
: - **Resume instead of duplicate** (`create_draft(..., batch_id=)`): before creating
  anything we search the store for a product carrying our own label; if it exists, its id
  is returned, only the *missing* variations are POSTed, its images/categories are left
  alone, and it is never rolled back — deleting a product we did not create in this
  attempt is not cleanup, it is vandalism. If the store refuses to answer «what
  variations exist», the publish stops with a Persian error instead of doubling every
  combination.
: - **`⏳ pending` intent card** (`products_ledger.record/update/find_batch`): the intent
  is written before the first request and the *same* card is finished with `created` /
  `failed` / `dry`, so one attempt is one card and a crash leaves something to find.
: - «♻️ این محتوا پیش‌تر منتشر شده است» — the duplicate tap answers with the existing product's
  id, its edit link, and what a second publish would really do (a second SKU, a second product),
  plus «🔁 با این حال دوباره بساز» (`product:force`) as the one-shot override. Two identical products
  is a legitimate thing to want; getting there by accident after a crash is not.
: - The ZIP path carries the same id in `product.json` (`batch_id`), and
  `docs/IMPORTER-CONTRACT.md` documents the plugin side of that contract (with the paste-ready
  PHP) instead of editing the tracked `tisa-product-importer.zip` — patching a build artifact
  whose source lives elsewhere is the divergence P2-7 warns about.
: - **`TISA_DATA_DIR`** moves the JSON stores out of the repo. `data/` holds the shop's durable
  state (roles, publish history, learned rules) and the test suite was writing into it; once the
  bot started *reading* that ledger before publishing, a test card could have blocked a real
  publish, so isolation became part of the feature rather than housekeeping.

Changed
: - `data/recent_products.json` gains `batch_id` / `done_ts`; unknown keys are ignored by older
  readers, so nothing needs migrating (and deleting the file still only costs history).
: - `bot/services/woocommerce_direct.py: create_draft(..., transport=)` — a seam for the suite to
  hand the writer a store that answers with 500s, missing endpoints or a half-created product.
  `dry_run` remains the switch the operator uses; the fake-transport test asserts the seam is
  ignored when a rehearsal is on.

Added (also)
: - `python main.py --check-config` now prints the resolved state directory and **fails** when it cannot be
  written: every JSON writer in `jsonstore` swallows its own `OSError` on purpose (a save that
  fails must not break a product flow), which used to mean an unwritable `TISA_DATA_DIR` simply
  stopped remembering roles and history — silently, forever. The one place that can say it out
  loud is the command an operator runs when something feels off.

Tests: 327 → 360 (`tests/test_idempotency.py` — id determinism, meta shape, intent lifecycle,
five store-behaviour scenarios, the gate/override flow, ZIP manifest; `tests/_flow_harness.py`
shared by the flow tests; `tests/conftest.py` keeps the suite out of the repo's `data/`).

## 0.8.0 — phase 4 (part 1): 🧪 dry-run, the same code path with the socket replaced

Added
: - **`TISA_DRY_RUN`** (`bot/config.py`, `bot/services/woocommerce_direct.py`). With it on,
  «✅ تأیید و ساخت» runs the *identical* publish path — payload builder, SKU scan,
  category lookup, media packaging, variation batch — and only swaps the network for
  an `httpx.MockTransport` that answers like WooCommerce/WordPress. A parallel
  “preview publisher” was deliberately not written: it drifts from production inside
  a release and then proves nothing, which is the failure this file keeps recording.
: - The owner gets the trace: a 🧪 result card (**no product id, no edit link** — a link
  to a product that does not exist would be worse than none) plus «🧪 درخواست‌هایی که
  ساخته شدند و ارسال نشدند» listing every request with its real JSON body. The report goes
  to the chat that started the flow, because `LOG_CHAT_ID` is optional and a rehearsal whose
  log is lost is not a rehearsal.
: - `🧪` status in `data/recent_products.json` (`products_ledger.summary`, `cards.result_card`),
  so a rehearsal is visible in the history and never mistaken for a publish.
: - `--check-config` prints the dry-run state, and the review screen warns before approval.

Changed
: - Credential gates are not skipped in a dry run: missing `WORDPRESS_*` still stops an
  image upload, because that refusal is part of what is being rehearsed.
: - The write-tests on the Ping screen (`🖼️ تست آپلود تصویر`, `📦 تست ساخت محصول با تصویر`) are
  refused while the flag is on — they really do write to the shop and delete afterwards, so
  leaving them live would have made «nothing is written» true only for the product path.

Fixed
: - The ZIP path sent its result card and the `product.zip` document to `user.id`, so in a
  forum topic the file landed in the admin's private chat while the preview stayed in the
  topic. Both now use the flow's `_target` — and a test drives that whole path, which none
  of the previous suites had ever executed.
: - `_body_for_log`: a media POST is multipart, and quoting its first 400 bytes dumped raw
  JPEG control characters into the log group. Binary bodies now log as
  `<N بایت دادهٔ دودویی (فایل ارسالی)>`.

Tests: 305 → 327 (`tests/test_dry_run.py`: every request shape the writer makes must be
answered, fake transport off when the flag is off, no credentials bypass, card and ledger
wording, config parsing, `--check-config` output, the Ping write-tests being refused, and the ZIP result routing).

## 0.7.0 — phase 3c: a real state machine, one active flow, and replies that go home

Changed
: - **The product flow has states** (`COLLECT` → `REVIEW`, with `EDITING_FIELD`
  hanging off the review screen). This is not cosmetic: while collecting, typed
  text *is* the product information; on the review screen the same text is only a
  proposal and needs a «✅ بله، اضافه کن» before any field moves. The class of bug
  where a sentence addressed to a teammate ended up as a price or a SKU prefix is
  closed at the routing level, not by asking people to type carefully.
: - **«✅ تصاویر تمام شد»** flushes the pending album immediately instead of hoping
  the `ALBUM_WAIT_SECONDS` timer was long enough, and **«➕ افزودن عکس یا متن»**
  is the explicit way back to collecting.
: - **One active flow per user** (`bot/services/flow_guard.py`): entering any flow
  closes the others of that user — session, temp files, half-built draft — and says
  what it closed. PTB 21.11 exposes no public way to end another conversation, so
  the guard closes the *work*; a stale button from an abandoned flow now answers «این
  جریان بسته شده است» instead of inventing a session (handlers no longer `setdefault`
  one into existence).
: - **Replies go to the chat and thread that started the flow** (`chat_id` +
  `message_thread_id` on the session). Proactive messages — progress, preview, result
  card — were sent to `user.id`, a private-chat assumption that dumps a topic chat's
  product into the wrong place.

Added
: - `bot/services/flow_guard.py` (registry of closers), `image_compress.close_for`
  (drops that user's leftover workspaces).

Tests: 284 → 305 (`tests/test_flow_states.py` — state transitions, the proposal
round-trip, the no-phantom-session contract, the guard, and thread routing).

## 0.6.0 — phase 3b: the publish loop closes with a card, a history, and a sandbox

Added
: - **Result card** (`bot/keyboards/cards.py`): after a publish the bot answers with
  the product id, the edit link, the variation count, the price(s), the SKU prefix,
  the warnings that remain, and three buttons — «🌐 ویرایش در سایت», «📦 محصول بعدی
  (همان تنظیمات)», «🧾 گزارش همین محصول». Failures are announced the same way (id,
  error), because a silent crash is what makes a shop owner ask «سایت که خالی است؟».
: - **`data/recent_products.json`** (`bot/services/products_ledger.py`): the last
  results — including the preview text as it was approved — so «🧾 آخرین محصولات»
  replays a card after a restart, and «محصول بعدی» can keep the mode of the product
  you just shipped.
: - **🔍 تست پارسر**: paste a post, see exactly what the flow reads (models, prices,
  colors, categories, provenance, warnings, offers). It calls the flow's own
  `analyze()`; nothing is created, nothing is written. Read-only, so admins may use it.
: - **Diff after a manual edit**: one field now answers with «قیمت: 250,000 ← 698,000 ·
  +2 واریژن (4 ← 6)» and a «👁 پیش‌نمایش کامل» button, instead of re-rendering the
  whole preview and asking you to spot the change.

Changed
: - `product:next:<mode>` is a ConversationHandler **entry point**, not a plain
  handler: a tap on the result card re-enters the flow (permissions re-checked), and
  it replies instead of editing, so the card you are reading is not destroyed.
: - `product_flow.analyze(text)` is shared with the flow, so the sandbox cannot drift
  from production.

Tests: 252 → 283 (`tests/test_result_card.py`; the stdlib-only CI run now also
covers `draft_edits` through a dependency-free draft stand-in).

## 0.5.0 — phase 3: per-field editing, and a typo that teaches the shop

Added
: - **Field editor** (`bot/services/draft_edits.py`): «✏️ اصلاح فیلد خاص» on the
  preview lists every editable field with its current value; one field, one
  typed value, one new preview. Each field parses on its own terms (price,
  colors, models, SKU prefix, categories, per-group prices, attribute axes)
  and answers in Persian when the input does not fit — leaving the previous
  value intact instead of half-applying a change.
: - **Manual edits are locks**: `ProductData.user_edits` is re-applied after every
  extraction, so a later photo or an extra text message cannot overwrite what
  the owner typed on purpose. The preview marks the field «ویرایش دستی».
: - **Separating two products' colors** is a button now (`product:colorsrc`): the
  message whose color list belongs to another product is removed from the
  deterministic read *and* from the text handed to the AI, so the decision
  survives the next round. Its title and price stay in scope.
: - **«Did you mean Nokia?» for brands** (`bot/services/brand_suggest.py`): an
  unknown brand-like word next to a model number gets a one-tap offer when
  exactly one catalogued brand is within edit distance 2. Accepting also
  writes the fix into `data/vocabulary.json`, so the typo is corrected
  forever in both paths; ambiguity stays silent (a warning, not a guess).

Changed
: - `extract_product()` takes `color_suppressed={…}`; after a suppression the AI
  may no longer supply the color axis — only what the kept text still says.
: - `product_flow` has a second conversation state (`EDITING_FIELD`) with a
  visible «↩️ انصراف» button, so an edit step is escapable without knowing magic
  words.

Tests: 216 → 252 (new `tests/test_field_edit.py`: parsers, locks, suppression
end-to-end through the extractor, the picker/step routing, and the brand offer).

## 0.4.0 — phase 2b: a text model, and a catalog the AI is held to

Added
: - **Blocks** (`bot.services.postmodel.Block`): each line is classified once —
  `price | model | colors | meta | brand | attribute | prose` — and carries the
  message and line number it came from. Price rules now only see blocks with the
  `price` role, so a weight/date/SKU/tracking line cannot become a price by
  construction; title selection skips bare model lines (a «15 اولترا» under an
  «آیفون:» header used to become the product title).
: - **Model catalog** (`bot/services/model_catalog.py`): which variants each
  brand actually makes, with per-brand forbidden words. `suspicious_lines()`
  warns about «iPhone 15 اولترا» and «13 پرو پلاس» (which folds to a variant
  that does not exist) instead of shipping a variation nobody can sell;
  `unknown_brand_words()` says so when a brand is not in the table. The same
  table is injected into the AI prompt, and the model's own `warnings` field is
  surfaced in the preview. Extend with `data/model_catalog.json`.
: - Evidence notes quote the line («خط 4 …»), and colors that arrive from two
  messages are merged *and* announced — with a warning when each message also
  carries its own title, the case where two products were pasted together.

Fixed
: - **`python-dotenv` is optional.** A host where pip never ran used to die at
  `import bot.config` — a supervisor crash-loop with no Persian hint anywhere.
  It now boots from the real environment and says nothing, because nothing is
  wrong.

## 0.3.0 — phases 1–2: a bot that explains itself and survives its own restarts

Added
: - **Provenance for every preview field** (`bot/services/postmodel.py`). The
  product preview now carries a «🧭 از کجا می‌دانم» block: which value came from
  the caption, which from a filename, which from the AI, and which from your own
  earlier correction. Amounts that were refused as prices are named in the
  preview only when you actually wrote «قیمت» there — nothing else is silent
  noise.
: - **Shop dictionary** (`bot/services/vocabulary.py`, `data/vocabulary.json`):
  word substitutions applied before any parsing, so the deterministic reader and
  the AI never disagree about a supplier name or a preferred spelling.
: - **Rotating, secret-free logs** (`bot/utils/logging.py`): `logs/bot.log`
  (5 MB × 3), every line tagged `[user <id>]`, and `consumer_key`,
  `consumer_secret`, bot tokens and bearer tokens redacted at the filter level.
: - **Honest restarts** (`bot/services/flow_state.py`): a flow that was cut short
  by a restart is announced once in the owner's chat instead of the bot quietly
  forgetting a half-built product.
: - `main.py --check-config` prints what the `.env` actually resolved to,
  including every coercion the loader had to make.

Fixed
: - **A Persian-only caption lost every iPhone.** `phone_parser` recognised only
  the Latin words `iphone`/`apple`, so «آیفون 13 پرو مکس» produced no model —
  and with it no colours, no variations, and no warning. Persian spellings
  (آیفون/ایفون/آيفون/اپل) are brand words now, including as a section header
  («آیفون:» followed by bare model lines).
: - `data/` and `logs/` are git-ignored (state must never end up in a commit).
: - The AI call is bounded by `AI_TIMEOUT_SECONDS`, and an AI failure is logged
  and shown as «هوش مصنوعی در دسترس نبود» instead of being swallowed into a bare
  fallback.

Changed
: - `extract_product` is now: deterministic read of your text → optional AI
  proposal → reconciliation by a written trust ladder
  (`ai < ocr < filename < policy < vocabulary < caption < learned < update < user`).
  The AI can no longer overwrite an amount you wrote yourself.

All notable changes. Versions follow SemVer and are shared with the WordPress
importer only by contract (`product.json`), not by number.

## 0.2.0 — phase 0: stop silent corruption

Fixed
: - **tracking.csv no longer contains broken barcodes.** Excel-destroyed numbers
  (`1.93E+23`) and wrong-length codes were reported as errors *and* written to
  the import file; they are now excluded and collected in `needs-fix.csv`
  (`bot/services/processor.py`, `bot/services/barcodes.py`).
  A duplicated-but-valid barcode is now a warning, not a dropped row.
: - **Prices can no longer come from a weight/date/SKU/tracking line**, an
  amount is read from the number written next to its unit («S24 اولترا 768t» is
  768 000, was 24), every group on a line is kept
  («قیمت ایفون 698 اندروید 598» kept both, Android used to inherit the iPhone
  price), and `PRICE_MIN..PRICE_MAX` rejects absurd values
  (new `bot/services/money.py`; `product_extractor` delegates to it).
: - **Persian model variants**: «۱۳ پرو مکس» / «۱۳ پرو» / «۱۳» are three models
  again (they collapsed into one «iPhone 13», deleting variations), `15max`
  reads as iPhone 15 Pro Max, and «XS Max» is no longer downgraded to XS. A
  model line whose words could not be applied now produces a warning
  (`phone_parser.unmatched_model_words`).
: - **The preview is the payload.** Variation axes/dedupe/per-model colours are
  computed once in `bot/services/plan.py` and reused by the Telegram preview, the
  WooCommerce payload and `product.json`; a colour axis collapsing to one value
  is announced instead of silently turning a variable product into a simple one.
: - **One publish per product**: a second tap on «✅ تأیید» while the first is
  running is refused, and a failed variation build rolls the half-built product
  back (with its media) instead of leaving it live on the store.
: - **No hardcoded log chat**: `LOG_CHAT_ID` defaults to disabled — setting it
  to empty (as `.env.example` did) no longer sends every product's prices, SKUs
  and captions to a chat group baked into the repository.
: - **Config can't crash the bot**: bad numbers in `.env` fall back to the
  documented default with a Persian warning (`--check-config` lists them all).
: - **Temp files are actually deleted**: cleanup removes the whole session
  workspace (it deleted only the compressed copies, leaving originals forever),
  workspaces are namespaced per process, stale ones are swept hourly, and an
  album that arrives after the flow was cancelled is ignored instead of raising
  `KeyError` at the user.
: - **Flow can be left**: «⬅️ بازگشت به منو» now ends the product conversation and
  an idle flow times out (`FLOW_TIMEOUT_SECONDS`) — previously every later text
  message was appended to the abandoned product's PRODUCT INFO.
: - **AI failures are visible again**: the normalizer logged into a sink that
  discarded everything; it now logs through `logging`, and a repeated extraction
  of unchanged text is skipped (it used to re-ask the model and could silently
  change an accepted value).
: - A second batch of photos now adds to the product instead of replacing the
  first batch.

Added
: - `bot/services/{money,plan,validation,barcodes}.py`, `main.py --check-config`
    / `--version`, `CHANGELOG.md`, 30 new regression tests (`131` total).
: - Settings: `PRICE_MIN`, `PRICE_MAX`, `REQUIRE_MODELS`, `FLOW_TIMEOUT_SECONDS`,
  `TEMP_TTL_HOURS`, `BARCODE_LENGTHS`, `MAX_FILE_MB`, `MAX_ROWS`,
  `PROCESS_TIMEOUT_SECONDS`, `AI_TIMEOUT_SECONDS`.

Added
: - `bot/services/jsonstore.py`: every `data/*.json` write is atomic with a `.bak`
  copy and is recovered from it when the main file is truncated (a restart during
  a save used to mean «no admins left»). Reads are cached instead of hitting the
  disk on every permission check.
: - Admin invites: an id typed by sudo no longer grants access by itself — it
  creates a 24 h pending invite, and the person becomes an admin only after they
  send `/start <code>` themselves. The panel lists pending invites with a revoke
  button, and `@username` is accepted as well as a numeric id.
: - `image_compressor` reads the quality per call and never returns a "compressed"
  file that is bigger than the original.
: - `pyproject.toml` (ruff + mypy + pytest config), `.github/workflows/ci.yml`,
  `.pre-commit-config.yaml`, `CHANGELOG.md`; the `services/` layer is mypy-clean
  and `ruff` is silent across the repo.
: - `bot/__init__.py` version → `0.2.0` (it said 0.1.0 while the importer said 0.7.0).

Changed
: - `requirements.txt` pins `python-telegram-bot[job-queue]>=21,<22` (PTB 22 is
  a breaking major and only worked by luck) and runs polling without
  `drop_pending_updates`.
