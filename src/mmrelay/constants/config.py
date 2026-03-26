"""
Configuration section and key constants.

Contains configuration section names, key names, and default values
used throughout the configuration system.
"""

from typing import Final

# Configuration file names
DEFAULT_CONFIG_FILENAME: Final[str] = "config.yaml"

# Configuration section names
CONFIG_SECTION_MATRIX: Final[str] = "matrix"
CONFIG_SECTION_MATRIX_ROOMS: Final[str] = "matrix_rooms"
CONFIG_SECTION_MESHTASTIC: Final[str] = "meshtastic"
CONFIG_SECTION_LOGGING: Final[str] = "logging"
CONFIG_SECTION_DATABASE: Final[str] = "database"
CONFIG_SECTION_DATABASE_LEGACY: Final[str] = "db"
CONFIG_SECTION_PLUGINS: Final[str] = "plugins"
CONFIG_SECTION_COMMUNITY_PLUGINS: Final[str] = "community-plugins"
CONFIG_SECTION_CUSTOM_PLUGINS: Final[str] = "custom-plugins"

# Matrix configuration keys
CONFIG_KEY_HOMESERVER: Final[str] = "homeserver"
CONFIG_KEY_ACCESS_TOKEN: Final[str] = (
    "access_token"  # nosec B105 - This is a config key name, not a hardcoded password
)
CONFIG_KEY_BOT_USER_ID: Final[str] = "bot_user_id"
CONFIG_KEY_USER_ID: Final[str] = "user_id"
CONFIG_KEY_PREFIX_ENABLED: Final[str] = "prefix_enabled"
CONFIG_KEY_PREFIX_FORMAT: Final[str] = "prefix_format"

# Matrix rooms configuration keys
CONFIG_KEY_ID: Final[str] = "id"
CONFIG_KEY_MESHTASTIC_CHANNEL: Final[str] = "meshtastic_channel"

# Meshtastic configuration keys (additional to network.py)
CONFIG_KEY_MESHNET_NAME: Final[str] = "meshnet_name"
CONFIG_KEY_MESSAGE_INTERACTIONS: Final[str] = "message_interactions"
CONFIG_KEY_REACTIONS: Final[str] = "reactions"
CONFIG_KEY_REPLIES: Final[str] = "replies"
CONFIG_KEY_BROADCAST_ENABLED: Final[str] = "broadcast_enabled"
CONFIG_KEY_DETECTION_SENSOR: Final[str] = "detection_sensor"
CONFIG_KEY_MESSAGE_DELAY: Final[str] = "message_delay"
CONFIG_KEY_NODEDB_REFRESH_INTERVAL: Final[str] = "nodedb_refresh_interval"
CONFIG_KEY_HEALTH_CHECK: Final[str] = "health_check"
CONFIG_KEY_ENABLED: Final[str] = "enabled"
CONFIG_KEY_HEARTBEAT_INTERVAL: Final[str] = "heartbeat_interval"

# Logging configuration keys
CONFIG_KEY_LEVEL: Final[str] = "level"
CONFIG_KEY_LOG_TO_FILE: Final[str] = "log_to_file"
CONFIG_KEY_FILENAME: Final[str] = "filename"
CONFIG_KEY_MAX_LOG_SIZE: Final[str] = "max_log_size"
CONFIG_KEY_BACKUP_COUNT: Final[str] = "backup_count"
CONFIG_KEY_COLOR_ENABLED: Final[str] = "color_enabled"
CONFIG_KEY_DEBUG: Final[str] = "debug"

# Database configuration keys
CONFIG_KEY_PATH: Final[str] = "path"
CONFIG_KEY_MSG_MAP: Final[str] = "msg_map"
CONFIG_KEY_MSGS_TO_KEEP: Final[str] = "msgs_to_keep"
CONFIG_KEY_WIPE_ON_RESTART: Final[str] = "wipe_on_restart"

# Plugin configuration keys
CONFIG_KEY_ACTIVE: Final[str] = "active"
CONFIG_KEY_CHANNELS: Final[str] = "channels"
CONFIG_KEY_UNITS: Final[str] = "units"
CONFIG_KEY_REPOSITORY: Final[str] = "repository"
CONFIG_KEY_TAG: Final[str] = "tag"
CONFIG_KEY_REQUIRE_BOT_MENTION: Final[str] = "require_bot_mention"

# Default configuration values
DEFAULT_LOG_LEVEL: Final[str] = "info"
DEFAULT_WEATHER_UNITS: Final[str] = "metric"
DEFAULT_WEATHER_UNITS_IMPERIAL: Final[str] = "imperial"
DEFAULT_PREFIX_ENABLED: Final[bool] = True
DEFAULT_BROADCAST_ENABLED: Final[bool] = True
DEFAULT_DETECTION_SENSOR: Final[bool] = True
DEFAULT_HEALTH_CHECK_ENABLED: Final[bool] = False
DEFAULT_HEARTBEAT_INTERVAL: Final[int] = 60
# Default refresh cadence in seconds. Setting this to 0.0 disables periodic
# NodeDB-derived name-cache refresh after the first immediate pass.
DEFAULT_NODEDB_REFRESH_INTERVAL: Final[float] = 15.0
DEFAULT_COLOR_ENABLED: Final[bool] = True
DEFAULT_WIPE_ON_RESTART: Final[bool] = False
DEFAULT_REQUIRE_BOT_MENTION: Final[bool] = True

# E2EE constants
E2EE_KEY_SHARING_DELAY_SECONDS: Final[int] = (
    5  # Default delay after initial sync to allow key sharing
)
E2EE_KEY_REQUEST_MAX_ATTEMPTS: Final[int] = (
    3  # Maximum number of attempts for key requests on decryption failure
)
E2EE_KEY_REQUEST_BASE_DELAY: Final[int] = (
    2  # Base delay in seconds for exponential backoff
)
E2EE_KEY_REQUEST_MAX_DELAY: Final[float] = (
    30.0  # Cap exponential backoff to avoid long waits
)

# Boolean parsing values
ENV_BOOL_TRUE_VALUES: Final[tuple[str, ...]] = ("true", "1", "yes", "on")
ENV_BOOL_FALSE_VALUES: Final[tuple[str, ...]] = ("false", "0", "no", "off")

# Normalizable configuration sections (case-insensitive)
NORMALIZABLE_CONFIG_SECTIONS: Final[tuple[str, ...]] = (
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
    CONFIG_SECTION_LOGGING,
    CONFIG_SECTION_DATABASE,
    CONFIG_SECTION_DATABASE_LEGACY,
    CONFIG_SECTION_PLUGINS,
    CONFIG_SECTION_CUSTOM_PLUGINS,
    CONFIG_SECTION_COMMUNITY_PLUGINS,
)

# Required credential keys
REQUIRED_CREDENTIALS_KEYS: Final[tuple[str, ...]] = (
    CONFIG_KEY_HOMESERVER,
    CONFIG_KEY_ACCESS_TOKEN,
)

# Required config keys
REQUIRED_CONFIG_KEYS_WITH_CREDENTIALS: Final[tuple[str, ...]] = (
    CONFIG_SECTION_MESHTASTIC,
    CONFIG_SECTION_MATRIX_ROOMS,
)
REQUIRED_CONFIG_KEYS_WITHOUT_CREDENTIALS: Final[tuple[str, ...]] = (
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
    CONFIG_SECTION_MATRIX_ROOMS,
)

# Plugin configuration sections
PLUGIN_CONFIG_SECTIONS: Final[tuple[str, ...]] = (
    CONFIG_SECTION_PLUGINS,
    CONFIG_SECTION_COMMUNITY_PLUGINS,
    CONFIG_SECTION_CUSTOM_PLUGINS,
)

PLUGIN_SECTION_TYPES: Final[dict[str, str]] = {
    CONFIG_SECTION_PLUGINS: "core",
    CONFIG_SECTION_COMMUNITY_PLUGINS: "community",
    CONFIG_SECTION_CUSTOM_PLUGINS: "custom",
}

# Versions where deprecation warnings were introduced
DEPRECATION_VERSIONS: Final[tuple[str, ...]] = ("1.3", "1.4")
