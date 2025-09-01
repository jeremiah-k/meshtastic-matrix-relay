"""
Constants package for MMRelay.

This package organizes all application constants by functional area:
- app: Application metadata and version information
- queue: Message queue configuration constants
- network: Network connection and timeout constants
- formats: Message format templates and prefixes
- messages: User-facing strings and templates
- database: Database-related constants
- config: Configuration section and key constants

Usage:
    from mmrelay.constants import queue
    from mmrelay.constants.app import APP_NAME
    from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY
"""

# Re-export commonly used constants for convenience
from .app import APP_AUTHOR, APP_NAME
from .config import (
    CONFIG_KEY_LEVEL,
    CONFIG_SECTION_LOGGING,
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_LOG_LEVEL,
)
from .formats import DEFAULT_MATRIX_PREFIX, DEFAULT_MESHTASTIC_PREFIX
from .queue import (
    DEFAULT_MESSAGE_DELAY,
    MAX_QUEUE_SIZE,
    QUEUE_HIGH_WATER_MARK,
    QUEUE_MEDIUM_WATER_MARK,
)

__all__ = [
    # App constants
    "APP_NAME",
    "APP_AUTHOR",
    # Config constants
    "CONFIG_SECTION_MATRIX",
    "CONFIG_SECTION_MESHTASTIC",
    "CONFIG_SECTION_LOGGING",
    "CONFIG_KEY_LEVEL",
    "DEFAULT_LOG_LEVEL",
    # Queue constants
    "DEFAULT_MESSAGE_DELAY",
    "MAX_QUEUE_SIZE",
    "QUEUE_HIGH_WATER_MARK",
    "QUEUE_MEDIUM_WATER_MARK",
    # Format constants
    "DEFAULT_MESHTASTIC_PREFIX",
    "DEFAULT_MATRIX_PREFIX",
]
