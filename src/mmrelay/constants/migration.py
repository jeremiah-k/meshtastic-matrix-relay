"""
Migration-related constants.

Contains constants for the migration system including retry settings,
directory names, and file patterns.
"""

from typing import Final

from mmrelay.constants.cli import TEMP_DEVICE_NAME_LOGOUT

__all__ = [
    "BYTES_PER_MB",
    "DEFAULT_SERVICE_ARGS_SUFFIX",
    "MIGRATION_BACKUP_DIRNAME",
    "MIGRATION_FREE_SPACE_WARNING_FACTOR",
    "MIGRATION_INITIAL_RETRY_DELAY",
    "MIGRATION_LOCK_FILENAME",
    "MIGRATION_MAX_RETRIES",
    "MIGRATION_MAX_RETRY_DELAY",
    "MIGRATION_MIN_FREE_SPACE_BYTES",
    "MIGRATION_STAGING_DIRNAME",
    "TEMP_DEVICE_NAME_LOGOUT",
]

# Migration retry settings
MIGRATION_MAX_RETRIES: Final[int] = 5
MIGRATION_INITIAL_RETRY_DELAY: Final[float] = 0.1
MIGRATION_MAX_RETRY_DELAY: Final[float] = 2.0

# Migration directory and file names
MIGRATION_STAGING_DIRNAME: Final[str] = ".migration_staging"
MIGRATION_BACKUP_DIRNAME: Final[str] = ".migration_backups"
MIGRATION_LOCK_FILENAME: Final[str] = ".migration.lock"

# Migration space requirements
MIGRATION_MIN_FREE_SPACE_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MB
MIGRATION_FREE_SPACE_WARNING_FACTOR: Final[float] = 1.5

# Byte conversion
BYTES_PER_MB: Final[int] = 1024 * 1024

# Service args template
DEFAULT_SERVICE_ARGS_SUFFIX: Final[str] = (
    " --home %h/.mmrelay"  # Leading space included for safe concatenation with base command
)
