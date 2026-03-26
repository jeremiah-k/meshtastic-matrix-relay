"""
Message format constants.

Contains default message prefixes, format templates, and other
formatting-related constants used for message display and relay.
"""

import re
from typing import Final

# Default message prefix formats
DEFAULT_MESHTASTIC_PREFIX = "{display5}[M]: "
DEFAULT_MATRIX_PREFIX = "[{long}/{mesh}]: "

# Port number constants for message types
TEXT_MESSAGE_APP = "TEXT_MESSAGE_APP"
DETECTION_SENSOR_APP = "DETECTION_SENSOR_APP"

# Emoji flag value
EMOJI_FLAG_VALUE = 1

# Default channel
DEFAULT_CHANNEL = 0

# Date/time format strings
DATETIME_FORMAT_STANDARD: Final[str] = "%Y-%m-%d %H:%M:%S"
DATETIME_FORMAT_WITH_TZ: Final[str] = "%Y-%m-%d %H:%M:%S %z"
DATE_FORMAT_LONG: Final[str] = "%b %d, %Y"
BACKUP_TIMESTAMP_FORMAT: Final[str] = "%Y%m%d_%H%M%S"
MIGRATION_TIMESTAMP_FORMAT: Final[str] = "%Y%m%d_%H%M%S_%f"
RICH_LOG_TIME_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

# Log format strings
LOG_FORMAT_CONSOLE: Final[str] = "{message}"
LOG_FORMAT_FILE: Final[str] = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
)

# Unit suffixes
SNR_UNIT_SUFFIX: Final[str] = " dB"

# Conversion factors
KM_TO_MILES_FACTOR: Final[float] = 0.621371

# Regex patterns for message processing
FIRMWARE_VERSION_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?i)\bfirmware[\s_/-]*version\b\s*[:=]\s*['\"]?\s*([^\s\r\n'\"]+)"
)
OBJECT_REPR_REGEX: Final[re.Pattern[str]] = re.compile(
    r"<.+? object at 0x[0-9a-fA-F]+>"
)
HTML_TAG_REGEX: Final[re.Pattern[str]] = re.compile(r"<[a-zA-Z/][^>]*>")
PREFIX_DEFINITION_REGEX: Final[re.Pattern[str]] = re.compile(r"^\[(.+?)\]:(\s*)")
MARKDOWN_ESCAPE_REGEX: Final[re.Pattern[str]] = re.compile(r"([*_`~\\\[\]])")

# Serial port patterns
WINDOWS_SERIAL_PORT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^COM[1-9]\d*$")
UNIX_SERIAL_PORT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^/dev/(tty|cu).+$")

# Network patterns
MAC_ADDRESS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
)

# Matrix-related keys
MATRIX_SUPPRESS_KEY: Final[str] = "mmrelay_suppress"
MATRIX_PACKET_KEY: Final[str] = "meshtastic_packet"

# Format templates
FORMAT_PROCESSED_PACKET: Final[str] = "Processed {packet_type} radio packet"
FORMAT_COMMAND_HELP: Final[str] = "`!{command}`: {description}"

# Image filenames
MAP_IMAGE_FILENAME: Final[str] = "location.png"
TELEMETRY_GRAPH_FILENAME: Final[str] = "graph.png"

# Telemetry
TELEMETRY_APP_PORTNUM: Final[str] = "TELEMETRY_APP"

# Map settings
DEFAULT_MAP_ZOOM: Final[int] = 8
MAP_ZOOM_MIN: Final[int] = 0
MAP_ZOOM_MAX: Final[int] = 30
DEFAULT_MAP_IMAGE_SIZE: Final[tuple[int, int]] = (1000, 1000)
MAP_LABEL_FONT_SIZE: Final[int] = 50

# Label settings
DEFAULT_LABEL_FONT_SIZE: Final[int] = 12
LABEL_MARGIN_PX: Final[int] = 4
LABEL_ARROW_SIZE_PX: Final[int] = 16
