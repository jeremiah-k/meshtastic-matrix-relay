"""
Migration-related constants.

Contains constants for the migration system including retry settings,
directory names, and file patterns.
"""

from typing import Final

__all__ = [
    "MIGRATION_MAX_RETRIES",
    "MIGRATION_INITIAL_RETRY_DELAY",
    "MIGRATION_MAX_RETRY_DELAY",
    "MIGRATION_STAGING_DIRNAME",
    "MIGRATION_BACKUP_DIRNAME",
    "MIGRATION_LOCK_FILENAME",
    "TEMP_DEVICE_NAME_LOGOUT",
    "MIGRATION_MIN_FREE_SPACE_BYTES",
    "DEFAULT_SERVICE_ARGS_SUFFIX",
]

# Migration retry settings
MIGRATION_MAX_RETRIES: Final[int] = 5
MIGRATION_INITIAL_RETRY_DELAY: Final[float] = 0.1
MIGRATION_MAX_RETRY_DELAY: Final[float] = 2.0

# Migration directory and file names
MIGRATION_STAGING_DIRNAME: Final[str] = ".migration_staging"
MIGRATION_BACKUP_DIRNAME: Final[str] = ".migration_backups"
MIGRATION_LOCK_FILENAME: Final[str] = ".migration.lock"
TEMP_DEVICE_NAME_LOGOUT: Final[str] = "mmrelay-logout-verify"

# Migration space requirements
MIGRATION_MIN_FREE_SPACE_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MB

# Service args template
DEFAULT_SERVICE_ARGS_SUFFIX: Final[str] = " --home %h/.mmrelay"  # %h = user home
