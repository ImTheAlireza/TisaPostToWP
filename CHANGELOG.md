# CHANGELOG

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
