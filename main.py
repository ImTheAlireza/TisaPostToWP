"""TisaCase management bot — entrypoint.

Usage:
    cp .env.example .env   # fill in BOT_TOKEN
    pip install -r requirements.txt
    python main.py

    python main.py --check-config   # validate .env and exit (no network)
    python main.py --version
"""

import argparse
import os
import sys

from bot import __version__
from bot.config import data_dir, settings
from bot.utils.logging import setup_logging


def check_config() -> int:
    """Print every configuration problem we found, then exit.

    On a shared host the bot runs under supervisor, where a bad ``.env`` used to
    mean a crash loop and a traceback nobody reads.
    """
    problems = list(settings.problems)
    print(f"BOT_TOKEN: {'set' if settings.bot_token else 'MISSING'}")
    print(f"sudo ids : {sorted(settings.sudo_ids) or '— (هیچ!)'}")
    print(f"log chat : {settings.log_chat_id or '— (غیرفعال)'}")
    print(f"woo      : {settings.woocommerce_url or '—'}")
    print(f"wp media : {settings.wordpress_url or '—'}")
    print(f"ai       : {settings.ai_model or '—'} @ {settings.ai_base_url or '—'}")
    # The JSON stores are where roles and publish history live, and every writer
    # swallows its own OSError (a failed save must not kill a flow) — so an
    # unwritable directory used to mean "nothing is remembered", silently, forever.
    state = data_dir()
    state_problem = ""
    try:
        state.mkdir(parents=True, exist_ok=True)
        if not os.access(state, os.W_OK):
            state_problem = f"دایرکتوری state قابل‌نوشتن نیست: {state}"
    except OSError as exc:
        state_problem = f"دایرکتوری state ساخته/خوانده نشد: {state} ({exc})"
    print(f"state   : {state}{'' if not state_problem else '  ← ⚠️'}")
    if state_problem:
        problems.append(state_problem)
    if settings.woo_dry_run:
        print("dry-run  : 🧪 روشن (TISA_DRY_RUN) — هیچ محصول/تصویری در سایت نوشته نمی‌شود")
    else:
        print("dry-run  : خاموش (منتشر واقعی)")
    if problems:
        print("\n⚠️  مشکلات پیکربندی:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\n✅ پیکربندی سالم است.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="main.py", description="TisaCase management bot")
    parser.add_argument("--check-config", action="store_true", help="validate .env and exit")
    parser.add_argument("--version", action="store_true", help="print the bot version and exit")
    args = parser.parse_args()

    if args.version:
        print(__version__)
        return
    if args.check_config:
        sys.exit(check_config())

    setup_logging(settings.log_level)
    if settings.problems:
        for problem in settings.problems:
            print(f"⚠️  config: {problem}", file=sys.stderr)

    from bot.app import build_application  # imported late so --check-config stays cheap

    app = build_application()
    # `drop_pending_updates=True` used to throw away everything the owner sent
    # while the bot was restarting (photos, order files) — with a restart button
    # in the menu, that was data loss on a schedule.
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
