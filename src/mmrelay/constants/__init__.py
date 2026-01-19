"""
Constants package for MMRelay.

This package organizes all application constants by functional area:
- app: Application metadata and version information
- cli: CLI command and deprecation constants
- queue: Message queue configuration constants
- network: Network connection and timeout constants
- formats: Message format templates and prefixes
- messages: User-facing strings and templates
- database: Database-related constants
- config: Configuration section and key constants
- plugins: Plugin system security and validation constants

Usage:
    from mmrelay.constants import queue
    from mmrelay.constants.app import APP_NAME
    from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY
    from mmrelay.constants.plugins import DEFAULT_ALLOWED_COMMUNITY_HOSTS
"""

# Re-export commonly used constants for convenience
from .app import APP_AUTHOR, APP_NAME
from .cli import (
    CLI_COMMANDS,
    DEPRECATED_COMMANDS,
)
from .config import (
    CONFIG_KEY_LEVEL,
    CONFIG_SECTION_LOGGING,
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_LOG_LEVEL,
)
from .database import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_ENABLE_WAL,
    DEFAULT_EXTRA_PRAGMAS,
)
from .formats import DEFAULT_MATRIX_PREFIX, DEFAULT_MESHTASTIC_PREFIX
from .network import (
    BLE_FUTURE_WATCHDOG_SECS,
    BLE_SCAN_TIMEOUT_SECS,
    BLE_TIMEOUT_RESET_THRESHOLD,
    BLE_TROUBLESHOOTING_GUIDANCE,
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_SERIAL_PORT,
    CONFIG_KEY_TIMEOUT,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BACKOFF_TIME,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    DEFAULT_MESHTASTIC_TIMEOUT,
    ERRNO_BAD_FILE_DESCRIPTOR,
    INFINITE_RETRIES,
    MAX_TIMEOUT_RETRIES_INFINITE,
)
from .plugins import (
    COMMIT_HASH_PATTERN,
    DEFAULT_ALLOWED_COMMUNITY_HOSTS,
    DEFAULT_BRANCHES,
    MAX_PUNCTUATION_LENGTH,
    PIP_SOURCE_FLAGS,
    PIPX_ENVIRONMENT_KEYS,
    REF_NAME_PATTERN,
    RISKY_REQUIREMENT_PREFIXES,
)
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
    # CLI constants
    "CLI_COMMANDS",
    "DEPRECATED_COMMANDS",
    # Config constants
    "CONFIG_SECTION_MATRIX",
    "CONFIG_SECTION_MESHTASTIC",
    "CONFIG_SECTION_LOGGING",
    "CONFIG_KEY_LEVEL",
    "DEFAULT_LOG_LEVEL",
    # Database constants
    "DEFAULT_BUSY_TIMEOUT_MS",
    "DEFAULT_ENABLE_WAL",
    "DEFAULT_EXTRA_PRAGMAS",
    # Network constants
    "BLE_FUTURE_WATCHDOG_SECS",
    "BLE_SCAN_TIMEOUT_SECS",
    "BLE_TIMEOUT_RESET_THRESHOLD",
    "BLE_TROUBLESHOOTING_GUIDANCE",
    "CONFIG_KEY_BLE_ADDRESS",
    "CONFIG_KEY_CONNECTION_TYPE",
    "CONFIG_KEY_HOST",
    "CONFIG_KEY_SERIAL_PORT",
    "CONFIG_KEY_TIMEOUT",
    "CONNECTION_TYPE_BLE",
    "CONNECTION_TYPE_NETWORK",
    "CONNECTION_TYPE_SERIAL",
    "CONNECTION_TYPE_TCP",
    "DEFAULT_BACKOFF_TIME",
    "DEFAULT_MESHTASTIC_OPERATION_TIMEOUT",
    "DEFAULT_MESHTASTIC_TIMEOUT",
    "ERRNO_BAD_FILE_DESCRIPTOR",
    "INFINITE_RETRIES",
    "MAX_TIMEOUT_RETRIES_INFINITE",
    # Queue constants
    "DEFAULT_MESSAGE_DELAY",
    "MAX_QUEUE_SIZE",
    "QUEUE_HIGH_WATER_MARK",
    "QUEUE_MEDIUM_WATER_MARK",
    # Format constants
    "DEFAULT_MESHTASTIC_PREFIX",
    "DEFAULT_MATRIX_PREFIX",
    # Plugin constants
    "COMMIT_HASH_PATTERN",
    "DEFAULT_ALLOWED_COMMUNITY_HOSTS",
    "DEFAULT_BRANCHES",
    "MAX_PUNCTUATION_LENGTH",
    "PIPX_ENVIRONMENT_KEYS",
    "PIP_SOURCE_FLAGS",
    "REF_NAME_PATTERN",
    "RISKY_REQUIREMENT_PREFIXES",
]
