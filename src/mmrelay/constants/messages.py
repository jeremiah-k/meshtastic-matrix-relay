"""
User-facing messages and string templates.

Contains error messages, log templates, command responses, and other
strings that are displayed to users or logged.
"""

from typing import Final

# Log configuration defaults
DEFAULT_LOG_SIZE_MB = 5
DEFAULT_LOG_BACKUP_COUNT = 1
LOG_SIZE_BYTES_MULTIPLIER = 1024 * 1024  # Convert MB to bytes

# Numeric portnum constants for comparisons
PORTNUM_TEXT_MESSAGE_APP = 1  # Numeric portnum for TEXT_MESSAGE_APP
PORTNUM_DETECTION_SENSOR_APP = 10  # Numeric portnum for DETECTION_SENSOR_APP
DEFAULT_CHANNEL_VALUE = 0

# Message formatting constants
MAX_TRUNCATION_LENGTH = 20  # Maximum characters for variable truncation
TRUNCATION_LOG_LIMIT = 6  # Only log first N truncations to avoid spam
DEFAULT_MESSAGE_TRUNCATE_BYTES = 227  # Default message truncation size
MESHNET_NAME_ABBREVIATION_LENGTH = 4  # Characters for short meshnet names
SHORTNAME_FALLBACK_LENGTH = 3  # Characters for shortname fallback
MESSAGE_PREVIEW_LENGTH = 40  # Characters for message preview in logs
DISPLAY_NAME_DEFAULT_LENGTH = 5  # Default display name truncation

# Time constants
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 3600
RELATIVE_TIME_DAYS_THRESHOLD: Final[int] = 7

# Node display values
UNKNOWN_NODE_VALUE: Final[str] = "Unknown"

# Ping plugin messages
PING_FALLBACK_RESPONSE: Final[str] = "Pong..."
PING_MATRIX_RESPONSE: Final[str] = "pong!"

# Help plugin messages
MSG_NO_SUCH_COMMAND: Final[str] = "No such command: {command}"
MSG_AVAILABLE_COMMANDS_PREFIX: Final[str] = "Available commands: "

# Graph/telemetry messages
MSG_GRAPH_UPLOAD_FAILED: Final[str] = "Failed to generate graph: Image upload failed."

# E2EE messages
MSG_E2EE_WINDOWS_UNSUPPORTED: Final[str] = "E2EE is not supported on Windows"
MSG_E2EE_WINDOWS_UNSUPPORTED_DETAIL: Final[str] = (
    "   Use Linux or macOS for E2EE support"
)
MSG_E2EE_DISABLED: Final[str] = "E2EE is disabled in configuration"
MSG_E2EE_NO_AUTH: Final[str] = "Matrix authentication not configured"

# Legacy credentials warning messages
LEGACY_CREDENTIALS_WARNING_MSG: Final[str] = (
    "Credentials found in legacy location: {}. "
    "Please run 'mmrelay migrate' to move to new unified structure. "
    "Support for legacy credentials will be removed in v1.4."
)
LEGACY_CREDENTIALS_NO_VERSION_MSG: Final[str] = (
    "Credentials found in legacy location: {}. "
    "Please run 'mmrelay migrate' to move to new unified structure."
)

# Error messages
MSG_MISSING_MATRIX_ROOMS: Final[str] = "Missing required matrix_rooms configuration"
MSG_MATRIX_SYNC_TIMEOUT: Final[str] = "Matrix sync timed out"
MSG_MATRIX_SYNC_FAILED: Final[str] = "Matrix sync failed"

# Metadata output limits
METADATA_OUTPUT_MAX_LENGTH: Final[int] = 4096

# Matrix event types
MATRIX_EVENT_TYPE_ROOM_MESSAGE: Final[str] = "m.room.message"
