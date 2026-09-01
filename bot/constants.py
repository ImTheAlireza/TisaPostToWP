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


# --- Misc --------------------------------------------------------------------

BOT_NAME = "TisaCase Manager"

WELCOME_TEXT = (
    f"👋 Welcome to <b>{BOT_NAME}</b>\n\n"
    "Case management for tisacase.com — pick an action below."
)
