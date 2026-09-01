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

Send `/start` to the bot — you'll get the main menu. Right now it has a single
**🏓 Ping** button that measures the API round-trip and confirms the bot is alive.

## Configuration (`.env`)

| Variable    | Required | Description                                                        |
|-------------|----------|--------------------------------------------------------------------|
| `BOT_TOKEN` | yes      | Token from [@BotFather](https://t.me/BotFather)                    |
| `ADMIN_IDS` | no       | Comma-separated Telegram user IDs allowed to use the bot. Empty = everyone. |
| `LOG_LEVEL` | no       | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default `INFO`)            |

## Architecture

```
main.py                  # entrypoint (polling)
bot/
├── config.py            # Settings loaded from .env
├── app.py               # Application factory, /commands list
├── constants.py         # CB.* — callback-data namespace for all buttons
├── keyboards/           # keyboard builders, one module per screen
│   └── main_menu.py
├── modules/             # features — each exposes register(app)
│   ├── __init__.py      # ALL_MODULES registry (order matters)
│   ├── start.py         # /start, /menu, back-to-menu navigation
│   ├── ping.py          # 🏓 Ping button
│   └── fallback.py      # unknown buttons/text + global error handler
└── utils/
    └── logging.py
```

**Rules of the house**

- Handlers all live in group 0; within a group PTB runs only the *first* match
  in registration order, so `fallback` stays **last** in `ALL_MODULES`.
- Every button's `callback_data` is a constant in `bot/constants.py` (`CB.*`),
  namespaced per feature (`ping`, `nav:main`, later e.g. `case:new`).
- Modules never import each other — they share only `constants`, `keyboards`,
  `config`, and `utils`.

## Adding a new feature

1. **Constants** — add the button's callback data to `CB` in `bot/constants.py`
   (e.g. `CASE_NEW = "case:new"`).
2. **Module** — create `bot/modules/<feature>.py` with a
   `register(app: Application)` function. For a flow, build a
   `ConversationHandler` whose entry point is
   `CallbackQueryHandler(entry, pattern=f"^{CB.CASE_NEW}$")`.
3. **Button** — add it to `bot/keyboards/main_menu.py` (or a submenu keyboard).
4. **Registry** — append the module to `ALL_MODULES` in
   `bot/modules/__init__.py`, *before* `fallback`.
