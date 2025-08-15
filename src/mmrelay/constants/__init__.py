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
from .commands import (
    get_command,
    get_deprecation_warning,
    suggest_command,
    require_command,
    retry_command,
    validate_command,
    cmd_generate_config,
    cmd_check_config,
    cmd_auth_login,
    cmd_auth_status,
    cmd_service_install,
    msg_suggest_generate_config,
    msg_suggest_check_config,
    msg_require_auth_login,
    msg_retry_auth_login,
)
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
    # Command constants and utilities
    "get_command",
    "get_deprecation_warning",
    "suggest_command",
    "require_command",
    "retry_command",
    "validate_command",
    "cmd_generate_config",
    "cmd_check_config",
    "cmd_auth_login",
    "cmd_auth_status",
    "cmd_service_install",
    "msg_suggest_generate_config",
    "msg_suggest_check_config",
    "msg_require_auth_login",
    "msg_retry_auth_login",
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
