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

## Configuration (`.env`)

| Variable    | Required | Description                                                        |
|-------------|----------|--------------------------------------------------------------------|
| `BOT_TOKEN` | yes      | Token from [@BotFather](https://t.me/BotFather)                    |
| `ADMIN_IDS` | no       | Comma-separated Telegram user IDs allowed to use the bot. Empty = everyone. |
| `LOG_LEVEL` | no       | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`)            |

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

### 🏓 Ping

Diagnostics button — measures API round-trip and confirms the bot is alive.

---

## Architecture

```
main.py                      # entrypoint (polling)
bot/
├── config.py                # Settings loaded from .env
├── app.py                   # Application factory, /commands list
├── constants.py             # CB.* — callback-data namespace for all buttons
├── keyboards/               # keyboard builders, one module per screen
│   └── main_menu.py
├── services/                # pure business logic — no Telegram imports
│   └── processor.py         # order file → tracking.csv + problem report
├── modules/                 # features — each exposes register(app)
│   ├── __init__.py          # ALL_MODULES registry (order matters)
│   ├── start.py             # /start, /menu, back-to-menu navigation
│   ├── tracking_converter.py# 📦 تبدیل فایل کد رهگیری (conversation flow)
│   ├── ping.py              # 🏓 Ping button
│   └── fallback.py          # unknown buttons/text/files + global error handler
└── utils/
    └── logging.py
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
