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
    WOO_PING = "ping:woocommerce"
    WP_MEDIA_PING = "ping:wordpress-media"
    WOO_PRODUCT_PING = "ping:woocommerce-product"

    # Restart via supervisor
    RESTART_ASK = "restart:ask"
    RESTART_CONFIRM = "restart:confirm"

    # Tracking-file converter (تبدیل فایل کد رهگیری)
    TRACKING_CONVERT = "tracking:convert"
    TRACKING_CANCEL = "tracking:cancel"

    # Phone/accessories post processor (migrated from OPTION)
    # Product builder modes
    PHONE_POST = "phone:post"
    PHONE_NEW = "phone:new"
    PHONE_RESTOCK = "phone:restock"
    PHONE_POST_CANCEL = "phone:post:cancel"
    PHONE_IMAGE_KEEP = "phone:image:keep"
    PHONE_IMAGE_REPLACE = "phone:image:replace"

    # Image compression-only utility (فشرده‌سازی عکس‌ها)
    COMPRESS = "compress:start"

    # Sudo runtime preferences (تنظیمات نمایش دکمه‌ها برای ادمین‌ها)
    SETTINGS = "settings"
    SETTINGS_TOGGLE_COMPRESS = "settings:toggle:compress"

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

BOT_NAME = "تیساکیس"

# Role badge shown in the main-menu greeting.
ROLE_BADGE = {
    "sudo": "👑 مالک (سودو)",
    "admin": "🛡️ ادمین",
    "user": "👤 کاربر",
}

DENIED_TEXT = "⛔ شما اجازه‌ی استفاده از این ربات را ندارید."
