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
| 👑 sudo | You, via `SUDO_IDS` in `.env`| `.env` (not runtime-editable) | Everything: converter, phone-post processor, Ping, 🔄 restart, and «👥 مدیریت ادمینها» |
| 🛡️ admin| You, at runtime from the menu | `data/roles.json`       | **Only** «📦 تبدیل فایل کد رهگیری» — all other buttons are hidden from them and their callbacks are rejected |
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

Admins persist across restarts in `data/roles.json` (git-ignored). The sudo
owner is never listed or removable from the menu, so you can't lock yourself
out.

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
| `AI_BASE_URL` / `AI_TOKEN` / `AI_MODEL` | no | Optional OpenAI-compatible API for normalizing messy phone captions. |\n| `LOG_CHAT_ID` | no | Telegram group/chat ID receiving the complete product-processing log. |\n| `WOOCOMMERCE_URL` | no | Store URL used by the WooCommerce REST connection test. |\n| `WOOCOMMERCE_CONSUMER_KEY` / `WOOCOMMERCE_CONSUMER_SECRET` | no | WooCommerce REST API credentials used only by the Ping diagnostic. |\n| `WOOCOMMERCE_API_VERSION` | no | API path version, default `wc/v3`. |\n| `WORDPRESS_URL` / `WORDPRESS_USERNAME` / `WORDPRESS_APP_PASSWORD` | no | Credentials for the safe upload/delete test under Ping using `/wp-json/wp/v2/media`. |

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
│   └── processor.py         # order file → tracking.csv + problem report
├── modules/                 # features — each exposes register(app)
│   ├── __init__.py          # ALL_MODULES registry (order matters)
│   ├── start.py             # /start, /menu, back-to-menu navigation
│   ├── tracking_converter.py# 📦 تبدیل فایل کد رهگیری (conversation flow)
│   ├── product_flow.py     # 📦 گفت‌وگوی ساخت ZIP محصول
│   ├── admins.py            # 👥 مدیریت ادمینها (sudo) — add/remove admins
│   ├── ping.py              # 🏓 Ping button (sudo)
│   ├── restart.py           # 🔄 restart via supervisor (sudo) + startup confirmation
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
