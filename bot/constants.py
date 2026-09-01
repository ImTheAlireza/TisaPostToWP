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


# --- Misc --------------------------------------------------------------------

BOT_NAME = "TisaCase Manager"
