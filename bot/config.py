"""Application settings, loaded once from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    # Telegram user ID(s) of the bot owner — the only *sudo*. Full access.
    sudo_ids: frozenset[int] = field(default_factory=frozenset)
    log_level: str = "INFO"
    supervisor_program: str = "tisabot"
    supervisorctl_bin: str = "supervisorctl"
    supervisor_conf: str = ""
    supervisor_url: str = ""
    # Phone-post processor (migrated from the former OPTION bot).
    album_wait_seconds: float = 1.8
    max_download_mb: float = 20.0
    image_quality: int = 88
    ai_base_url: str = ""
    ai_token: str = ""
    ai_model: str = ""
    # Fixed Telegram log group for the product-processing audit trail.
    log_chat_id: int | None = -5061365940
    woocommerce_url: str = ""
    woocommerce_key: str = ""
    woocommerce_secret: str = ""
    woocommerce_version: str = "wc/v3"
    wordpress_url: str = ""
    wordpress_username: str = ""
    wordpress_app_password: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

        raw_sudo = os.getenv("SUDO_IDS", "")
        sudo_ids = frozenset(
            int(part) for part in raw_sudo.replace(";", ",").split(",") if part.strip()
        )

        return cls(
            bot_token=token,
            sudo_ids=sudo_ids,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            supervisor_program=os.getenv("SUPERVISOR_PROGRAM", "tisabot").strip() or "tisabot",
            supervisorctl_bin=os.getenv("SUPERVISORCTL_BIN", "supervisorctl").strip() or "supervisorctl",
            supervisor_conf=os.getenv("SUPERVISOR_CONF", "").strip(),
            supervisor_url=os.getenv("SUPERVISOR_URL", "").strip(),
            album_wait_seconds=float(os.getenv("ALBUM_WAIT_SECONDS", "1.8")),
            max_download_mb=float(os.getenv("MAX_DOWNLOAD_MB", "20")),
            image_quality=int(os.getenv("IMAGE_QUALITY", "88")),
            ai_base_url=os.getenv("AI_BASE_URL", "").strip(),
            ai_token=os.getenv("AI_TOKEN", "").strip(),
            ai_model=os.getenv("AI_MODEL", "").strip(),
            log_chat_id=int(os.getenv("LOG_CHAT_ID", "-5061365940").strip() or "-5061365940"),
            woocommerce_url=os.getenv("WOOCOMMERCE_URL", "").strip(),
            woocommerce_key=os.getenv("WOOCOMMERCE_CONSUMER_KEY", "").strip(),
            woocommerce_secret=os.getenv("WOOCOMMERCE_CONSUMER_SECRET", "").strip(),
            woocommerce_version=os.getenv("WOOCOMMERCE_API_VERSION", "wc/v3").strip() or "wc/v3",
            wordpress_url=os.getenv("WORDPRESS_URL", "").strip(),
            wordpress_username=os.getenv("WORDPRESS_USERNAME", "").strip(),
            wordpress_app_password=os.getenv("WORDPRESS_APP_PASSWORD", "").replace(" ", "").strip(),
        )

    def is_sudo(self, user_id: int | None) -> bool:
        """True if the user is the bot owner (set via SUDO_IDS in .env)."""
        return bool(user_id) and user_id in self.sudo_ids


settings = Settings.from_env()
