"""TisaCase management bot — entrypoint.

Usage:
    cp .env.example .env   # fill in BOT_TOKEN
    pip install -r requirements.txt
    python main.py
"""

from bot.app import build_application
from bot.config import settings
from bot.utils.logging import setup_logging


def main() -> None:
    setup_logging(settings.log_level)
    app = build_application()
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
