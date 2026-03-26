"""
Application metadata constants.

Contains version information, application name, and other metadata
used throughout the MMRelay application.
"""

from typing import Final

# Application identification
APP_NAME: Final[str] = "mmrelay"
APP_AUTHOR: Final[str | None] = None  # No author directory for platformdirs

# Application display names
APP_DISPLAY_NAME: Final[str] = "MMRelay"
APP_FULL_NAME: Final[str] = "MMRelay - Meshtastic <=> Matrix Relay"

# Windows installer directory name (used by Inno Setup)
# The installer uses "MM Relay" with a space, not "mmrelay"
WINDOWS_INSTALLER_DIR_NAME: Final[str] = "MM Relay"

# Matrix client identification
MATRIX_DEVICE_NAME: Final[str] = "MMRelay"

# Platform-specific constants
WINDOWS_PLATFORM: Final[str] = "win32"

# Runtime timing defaults
DEFAULT_READY_HEARTBEAT_SECONDS: Final[int] = 60
PLUGIN_SHUTDOWN_TIMEOUT_SECONDS: Final[float] = 5.0
MESSAGE_QUEUE_SHUTDOWN_TIMEOUT_SECONDS: Final[float] = 5.0

# Package and installation constants
PACKAGE_NAME_E2E: Final[str] = "mmrelay[e2e]"
PYTHON_OLM_PACKAGE: Final[str] = "python-olm"

# Configuration file names
CREDENTIALS_FILENAME: Final[str] = "credentials.json"
CONFIG_FILENAME: Final[str] = "config.yaml"
STORE_DIRNAME: Final[str] = "store"
MATRIX_DIRNAME: Final[str] = "matrix"

# Directory and file names
DATABASE_DIRNAME: Final[str] = "database"
DATABASE_FILENAME: Final[str] = "meshtastic.sqlite"
LOGS_DIRNAME: Final[str] = "logs"
LOG_FILENAME: Final[str] = "mmrelay.log"
PLUGINS_DIRNAME: Final[str] = "plugins"
PLUGIN_DATA_DIRNAME: Final[str] = "data"
LEGACY_DATA_SUBDIR: Final[str] = "data"

# File permissions (octal)
SECURE_FILE_PERMISSIONS: Final[int] = 0o600
SECURE_DIR_PERMISSIONS: Final[int] = 0o700

# Windows-specific constants
WINDOWS_VTP_FLAG: Final[int] = 0x0004
WINDOWS_STD_OUTPUT_HANDLE: Final[int] = -11
WINDOWS_STD_ERROR_HANDLE: Final[int] = -12
MIN_PYTHON_VERSION: Final[tuple[int, int]] = (3, 10)
WINDOWS_PATH_LENGTH_WARNING: Final[int] = 200

# Windows error codes
WINERR_ACCESS_DENIED: Final[int] = 5
WINERR_SHARING_VIOLATION: Final[int] = 32
WINERR_LOCK_VIOLATION: Final[int] = 33

# Exit codes
EXIT_CODE_SIGINT: Final[int] = 130

# Service configuration
SERVICE_FILENAME: Final[str] = "mmrelay.service"
SYSTEMD_USER_DIR: Final[str] = ".config/systemd/user"
SERVICE_RESTART_SECONDS: Final[int] = 10

# Process paths
PROC_SELF_STATUS_PATH: Final[str] = "/proc/self/status"
PROC_COMM_PATH_TEMPLATE: Final[str] = "/proc/{ppid}/comm"

# Diagnostics thresholds
DIAGNOSTICS_PARTIAL_ERROR_THRESHOLD: Final[int] = 3
DISK_SPACE_OK_GB: Final[float] = 1.0
DISK_SPACE_WARN_GB: Final[float] = 0.5
DISK_SPACE_CRITICAL_DATABASE_GB: Final[float] = 0.1

# Docker legacy paths (for migration detection)
DOCKER_LEGACY_PATHS: Final[tuple[str, ...]] = (
    "/data",
    "/app/data",
    "/var/lib/mmrelay",
)
