"""
Domain behavior and protocol constants.

Contains non-message constants shared across modules, including timing
thresholds, event types, and metadata output limits.
"""

from typing import Final

# Time constants
SECONDS_PER_MINUTE: Final[int] = 60
SECONDS_PER_HOUR: Final[int] = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY: Final[int] = 24 * SECONDS_PER_HOUR
RELATIVE_TIME_DAYS_THRESHOLD: Final[int] = 7

# Node display values
UNKNOWN_NODE_VALUE: Final[str] = "Unknown"

# Metadata output limits
METADATA_OUTPUT_MAX_LENGTH: Final[int] = 4096

# Matrix event types
MATRIX_EVENT_TYPE_ROOM_MESSAGE: Final[str] = "m.room.message"
