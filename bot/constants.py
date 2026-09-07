"""Central namespace for callback data and shared constants.

Every inline button carries a callback-data string. To keep modules from
colliding, each feature gets its own prefix (e.g. "ping", "case:new").
ConversationHandler entry points can pattern-match on these prefixes.
"""

# --- Callback data -----------------------------------------------------------

class CB:
    """Callback-data identifiers. One constant per button action."""

    # Navigation
    MAIN_MENU = "nav:main"

    # Diagnostics
    PING = "ping"

    # Restart via supervisor
    RESTART_ASK = "restart:ask"
    RESTART_CONFIRM = "restart:confirm"

    # Tracking-file converter (تبدیل فایل کد رهگیری)
    TRACKING_CONVERT = "tracking:convert"
    TRACKING_CANCEL = "tracking:cancel"

    # Admin management (sudo only) — see bot/modules/admins.py
    ADMINS_LIST = "admins:list"
    ADMINS_ADD = "admins:add"
    ADMINS_ADD_CANCEL = "admins:add:cancel"
    ADMINS_REMOVE = "admins:remove"          # data = f"{...}:{user_id}"
    ADMINS_REMOVE_CONFIRM = "admins:remove:confirm"
    ADMINS_REMOVE_BACK = "admins:remove:back"


# Prefix for per-admin remove buttons. Full data: f"{ADMINS_REMOVE}:{user_id}".
ADMIN_REMOVE_PREFIX = f"{CB.ADMINS_REMOVE}:"
ADMIN_REMOVE_CONFIRM_PREFIX = f"{CB.ADMINS_REMOVE_CONFIRM}:"
ADMIN_REMOVE_BACK_PREFIX = f"{CB.ADMINS_REMOVE_BACK}:"


# --- Misc --------------------------------------------------------------------

BOT_NAME = "TisaCase Manager"

WELCOME_TEXT = (
    f"👋 Welcome to <b>{BOT_NAME}</b>\n\n"
    "Case management for tisacase.com — pick an action below."
)

DENIED_TEXT = "⛔ شما اجازه‌ی استفاده از این ربات را ندارید."
