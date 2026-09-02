"""Application settings, loaded once from environment / .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: frozenset[int] = field(default_factory=frozenset)
    log_level: str = "INFO"
    supervisor_program: str = "tisabot"
    supervisorctl_bin: str = "supervisorctl"
    supervisor_conf: str = ""
    supervisor_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
            )

        raw_admins = os.getenv("ADMIN_IDS", "")
        admin_ids = frozenset(
            int(part) for part in raw_admins.replace(";", ",").split(",") if part.strip()
        )

        return cls(
            bot_token=token,
            admin_ids=admin_ids,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            supervisor_program=os.getenv("SUPERVISOR_PROGRAM", "tisabot").strip() or "tisabot",
            supervisorctl_bin=os.getenv("SUPERVISORCTL_BIN", "supervisorctl").strip() or "supervisorctl",
            supervisor_conf=os.getenv("SUPERVISOR_CONF", "").strip(),
            supervisor_url=os.getenv("SUPERVISOR_URL", "").strip(),
        )

    def is_admin(self, user_id: int | None) -> bool:
        """True if the user may use the bot. Empty ADMIN_IDS = open to all."""
        if not self.admin_ids:
            return True
        return user_id in self.admin_ids


settings = Settings.from_env()
