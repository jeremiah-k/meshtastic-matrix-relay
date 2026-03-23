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
