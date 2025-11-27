"""
Configuration section and key constants.

Contains configuration section names, key names, and default values
used throughout the configuration system.
"""

# Configuration section names
CONFIG_SECTION_MATRIX = "matrix"
CONFIG_SECTION_MATRIX_ROOMS = "matrix_rooms"
CONFIG_SECTION_MESHTASTIC = "meshtastic"
CONFIG_SECTION_LOGGING = "logging"
CONFIG_SECTION_DATABASE = "database"
CONFIG_SECTION_PLUGINS = "plugins"
CONFIG_SECTION_COMMUNITY_PLUGINS = "community-plugins"
CONFIG_SECTION_CUSTOM_PLUGINS = "custom-plugins"

# Matrix configuration keys
CONFIG_KEY_HOMESERVER = "homeserver"
CONFIG_KEY_ACCESS_TOKEN = (
    "access_token"  # nosec B105 - This is a config key name, not a hardcoded password
)
CONFIG_KEY_BOT_USER_ID = "bot_user_id"
CONFIG_KEY_PREFIX_ENABLED = "prefix_enabled"
CONFIG_KEY_PREFIX_FORMAT = "prefix_format"

# Matrix rooms configuration keys
CONFIG_KEY_ID = "id"
CONFIG_KEY_MESHTASTIC_CHANNEL = "meshtastic_channel"

# Meshtastic configuration keys (additional to network.py)
CONFIG_KEY_MESHNET_NAME = "meshnet_name"
CONFIG_KEY_MESSAGE_INTERACTIONS = "message_interactions"
CONFIG_KEY_REACTIONS = "reactions"
CONFIG_KEY_REPLIES = "replies"
CONFIG_KEY_BROADCAST_ENABLED = "broadcast_enabled"
CONFIG_KEY_DETECTION_SENSOR = "detection_sensor"
CONFIG_KEY_MESSAGE_DELAY = "message_delay"
CONFIG_KEY_HEALTH_CHECK = "health_check"
CONFIG_KEY_ENABLED = "enabled"
CONFIG_KEY_HEARTBEAT_INTERVAL = "heartbeat_interval"

# Logging configuration keys
CONFIG_KEY_LEVEL = "level"
CONFIG_KEY_LOG_TO_FILE = "log_to_file"
CONFIG_KEY_FILENAME = "filename"
CONFIG_KEY_MAX_LOG_SIZE = "max_log_size"
CONFIG_KEY_BACKUP_COUNT = "backup_count"
CONFIG_KEY_COLOR_ENABLED = "color_enabled"
CONFIG_KEY_DEBUG = "debug"

# Database configuration keys
CONFIG_KEY_PATH = "path"
CONFIG_KEY_MSG_MAP = "msg_map"
CONFIG_KEY_MSGS_TO_KEEP = "msgs_to_keep"
CONFIG_KEY_WIPE_ON_RESTART = "wipe_on_restart"

# Plugin configuration keys
CONFIG_KEY_ACTIVE = "active"
CONFIG_KEY_CHANNELS = "channels"
CONFIG_KEY_UNITS = "units"
CONFIG_KEY_REPOSITORY = "repository"
CONFIG_KEY_TAG = "tag"
CONFIG_KEY_REQUIRE_BOT_MENTION = "require_bot_mention"

# Default configuration values
DEFAULT_LOG_LEVEL = "info"
DEFAULT_WEATHER_UNITS = "metric"
DEFAULT_WEATHER_UNITS_IMPERIAL = "imperial"
DEFAULT_PREFIX_ENABLED = True
DEFAULT_BROADCAST_ENABLED = True
DEFAULT_DETECTION_SENSOR = True
DEFAULT_HEALTH_CHECK_ENABLED = True
DEFAULT_HEARTBEAT_INTERVAL = 60
DEFAULT_COLOR_ENABLED = True
DEFAULT_WIPE_ON_RESTART = False
DEFAULT_REQUIRE_BOT_MENTION = True

# E2EE constants
E2EE_KEY_SHARING_DELAY_SECONDS = 5
