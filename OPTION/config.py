from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_USER_ID_RAW = os.getenv("OWNER_USER_ID", "").strip()

# Backward-compatible: accepts one ID or multiple IDs separated by comma,
# semicolon, or whitespace. Example: 12345,67890 112233
OWNER_USER_IDS: set[int] = set()
for _part in __import__("re").split(r"[,;\s]+", OWNER_USER_ID_RAW):
    if _part.isdigit():
        OWNER_USER_IDS.add(int(_part))

# Kept for backward compatibility with older imports.
OWNER_USER_ID = next(iter(OWNER_USER_IDS), None)

ALBUM_WAIT_SECONDS = float(os.getenv("ALBUM_WAIT_SECONDS", "1.8"))
MAX_DOWNLOAD_MB = float(os.getenv("MAX_DOWNLOAD_MB", "20"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
IMAGE_FORMAT = "jpg"  # Output is always JPEG/JPG.
IMAGE_QUALITY = int(os.getenv("IMAGE_QUALITY", "88"))
IMAGE_METHOD = int(os.getenv("IMAGE_METHOD", "0"))

# OpenAI-compatible API settings.
# Example: https://api.openai.com/v1
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_TOKEN = os.getenv("AI_TOKEN", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()

TEMP_DIR = BASE_DIR / "tmp"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
