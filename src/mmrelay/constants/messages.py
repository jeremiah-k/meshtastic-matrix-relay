"""
User-facing messages and string templates.

Contains error messages, log templates, command responses, and other
strings that are displayed to users or logged.
"""

# Log configuration defaults
DEFAULT_LOG_SIZE_MB = 5
DEFAULT_LOG_BACKUP_COUNT = 1
LOG_SIZE_BYTES_MULTIPLIER = 1024 * 1024  # Convert MB to bytes

# Numeric constants for comparisons
PORTNUM_NUMERIC_VALUE = 1  # Numeric equivalent of TEXT_MESSAGE_APP
DETECTION_SENSOR_NUMERIC_VALUE = 10  # Numeric equivalent of DETECTION_SENSOR_APP
DEFAULT_CHANNEL_VALUE = 0

# Message formatting constants
MAX_TRUNCATION_LENGTH = 20  # Maximum characters for variable truncation
TRUNCATION_LOG_LIMIT = 6  # Only log first N truncations to avoid spam
DEFAULT_MESSAGE_TRUNCATE_BYTES = 227  # Default message truncation size
MESHNET_NAME_ABBREVIATION_LENGTH = 4  # Characters for short meshnet names
SHORTNAME_FALLBACK_LENGTH = 3  # Characters for shortname fallback
MESSAGE_PREVIEW_LENGTH = 40  # Characters for message preview in logs
DISPLAY_NAME_DEFAULT_LENGTH = 5  # Default display name truncation

# Component logger names
COMPONENT_LOGGERS = {
    "matrix_nio": ["nio", "nio.client", "nio.http", "nio.crypto", "nio.responses"],
    "bleak": ["bleak", "bleak.backends"],
    "meshtastic": [
        "meshtastic",
        "meshtastic.serial_interface",
        "meshtastic.tcp_interface",
        "meshtastic.ble_interface",
    ],
}

# Log level styling
LOG_LEVEL_STYLES = {
    "DEBUG": {"color": "cyan"},
    "INFO": {"color": "green"},
    "WARNING": {"color": "yellow"},
    "ERROR": {"color": "red"},
    "CRITICAL": {"color": "red", "bold": True},
}
