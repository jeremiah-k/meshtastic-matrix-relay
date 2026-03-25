"""
Plugin system constants.

This module contains constants related to plugin security, validation,
and configuration. These constants help ensure safe plugin loading and
execution by defining trusted sources and dangerous patterns.
"""

import re
from typing import Final, Tuple

# Message length limits
MAX_FORECAST_LENGTH = 200
MAX_PUNCTUATION_LENGTH = 5

# Map image size limits
MAX_MAP_IMAGE_SIZE = 1000

# Special node identifiers
SPECIAL_NODE_MESSAGES = "!NODE_MSGS!"

# S2 geometry constants for map functionality
S2_PRECISION_BITS_TO_METERS_CONSTANT = 23905787.925008

# Precompiled regex patterns for validation
COMMIT_HASH_PATTERN = re.compile(r"[0-9a-fA-F]{7,40}")
REF_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")

# Default branch names to try when ref is not specified
DEFAULT_BRANCHES = ["main", "master"]

# Environment keys that indicate pipx is being used (for security/testability)
PIPX_ENVIRONMENT_KEYS = ("PIPX_HOME", "PIPX_LOCAL_VENVS", "PIPX_BIN_DIR")

# Trusted git hosting platforms for community plugins
# These hosts are considered safe for plugin source repositories
DEFAULT_ALLOWED_COMMUNITY_HOSTS: Tuple[str, ...] = (
    "github.com",
    "gitlab.com",
    "codeberg.org",
    "bitbucket.org",
)

# Requirement prefixes that may indicate security risks
# These prefixes allow VCS URLs or direct URLs that could bypass package verification
RISKY_REQUIREMENT_PREFIXES: Tuple[str, ...] = (
    "git+",
    "ssh://",
    "git://",
    "hg+",
    "bzr+",
    "svn+",
    "http://",
    "https://",
)

# Pip source flags that can be followed by URLs
PIP_SOURCE_FLAGS: Tuple[str, ...] = (
    "-e",
    "--editable",
    "-f",
    "--find-links",
    "-i",
    "--index-url",
    "--extra-index-url",
)

# Plugin priorities
DEFAULT_PLUGIN_PRIORITY: Final[int] = 100
DEBUG_PLUGIN_PRIORITY: Final[int] = 1

# Plugin timeouts
PIP_INSTALL_TIMEOUT_SECONDS: Final[int] = 600
PIP_INSTALL_MISSING_DEP_TIMEOUT: Final[int] = 300
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS: Final[int] = 120
GIT_COMMAND_TIMEOUT_SECONDS: Final[int] = 120
GIT_RETRY_ATTEMPTS: Final[int] = 3
GIT_RETRY_DELAY_SECONDS: Final[int] = 2
WEATHER_API_TIMEOUT_SECONDS: Final[int] = 10

# Scheduler timing
SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS: Final[int] = 5
SCHEDULER_LOOP_WAIT_SECONDS: Final[int] = 1

# Sensitive URL parameters to redact
SENSITIVE_URL_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "access_token",
        "auth",
        "key",
        "password",
        "pwd",
        "private_token",
        "oauth_token",
        "x-access-token",
    }
)

# Weather plugin constants
WEATHER_COMMANDS: Final[tuple[str, ...]] = ("weather", "hourly", "daily")
DAILY_FORECAST_DAYS: Final[int] = 5
HOURLY_FORECAST_DAYS: Final[int] = 3
HOURLY_FORECAST_OFFSETS_HOURS: Final[tuple[int, ...]] = (3, 6, 12)
HOURLY_FORECAST_SLOT_LABELS: Final[tuple[str, ...]] = ("+3h", "+6h", "+12h")
GEOCODING_RESULT_COUNT: Final[int] = 1

# Open-Meteo API URLs
OPEN_METEO_FORECAST_API_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_API_URL: Final[str] = (
    "https://geocoding-api.open-meteo.com/v1/search"
)

# Telemetry plugin constants
TELEMETRY_DEFAULT_HOURS: Final[int] = 12
TELEMETRY_MAX_DATA_ROWS: Final[int] = 50

# Health plugin constants
LOW_BATTERY_THRESHOLD_PERCENT: Final[int] = 10

# Regex patterns
PING_COMMAND_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)([!?]*)(ping)([!?]*)(?!\w)"
)
DROP_COMMAND_REGEX: Final[re.Pattern[str]] = re.compile(r"!drop\s+(.+)$")
PROCESSED_PACKET_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^Processed (.+) radio packet$"
)

# Mesh relay constants
MESH_PACKET_DEFAULT_ID: Final[int] = 0
