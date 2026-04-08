"""
Plugin system constants.

This module contains constants related to plugin security, validation,
and configuration. These constants help ensure safe plugin loading and
execution by defining trusted sources and dangerous patterns.
"""

import re
from types import MappingProxyType
from typing import Final, TypedDict

# Message length limits
MAX_FORECAST_LENGTH: Final[int] = 200
MAX_PUNCTUATION_LENGTH: Final[int] = 5

# Map image size limits
MAX_MAP_IMAGE_SIZE: Final[int] = 1000

# Special node identifiers
SPECIAL_NODE_MESSAGES: Final[str] = "!NODE_MSGS!"

# S2 geometry constants for map functionality
S2_PRECISION_BITS_TO_METERS_CONSTANT: Final[float] = 23905787.925008

# Precompiled regex patterns for validation
COMMIT_HASH_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{7,40}$")
REF_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

# Default branch names to try when ref is not specified
DEFAULT_BRANCHES: Final[tuple[str, ...]] = ("main", "master")

# Environment keys that indicate pipx is being used (for security/testability)
PIPX_ENVIRONMENT_KEYS: Final[tuple[str, ...]] = (
    "PIPX_HOME",
    "PIPX_LOCAL_VENVS",
    "PIPX_BIN_DIR",
)

# Trusted git hosting platforms for community plugins
# These hosts are considered safe for plugin source repositories
DEFAULT_ALLOWED_COMMUNITY_HOSTS: Final[tuple[str, ...]] = (
    "github.com",
    "gitlab.com",
    "codeberg.org",
    "bitbucket.org",
)

# Requirement prefixes that may indicate security risks
# These prefixes allow VCS URLs or direct URLs that could bypass package verification
RISKY_REQUIREMENT_PREFIXES: Final[tuple[str, ...]] = (
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
PIP_SOURCE_FLAGS: Final[tuple[str, ...]] = (
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
        "secret",
        "api_key",
        "apikey",
        "client_secret",
        "bearer",
    }
)

# Weather plugin constants
WEATHER_COMMANDS: Final[tuple[str, ...]] = ("weather", "hourly", "daily")

# Weather mode constants
WEATHER_MODE_CURRENT: Final[str] = "weather"
WEATHER_MODE_HOURLY: Final[str] = "hourly"
WEATHER_MODE_DAILY: Final[str] = "daily"
WEATHER_SLOT_NOW: Final[str] = "now"

# Weather unit constants
WEATHER_UNITS_METRIC: Final[str] = "metric"
WEATHER_UNITS_IMPERIAL: Final[str] = "imperial"

# Forecast configuration
DAILY_FORECAST_DAYS: Final[int] = 5
HOURLY_FORECAST_DAYS: Final[int] = 3
HOURLY_FORECAST_SLOTS: Final[tuple[tuple[int, str], ...]] = (
    (3, "+3h"),
    (6, "+6h"),
    (12, "+12h"),
)
GEOCODING_RESULT_COUNT: Final[int] = 1

# Open-Meteo API URLs
OPEN_METEO_FORECAST_API_URL: Final[str] = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_GEOCODING_API_URL: Final[str] = (
    "https://geocoding-api.open-meteo.com/v1/search"
)

# Open-Meteo API query parameters
OPEN_METEO_HOURLY_FIELDS: Final[tuple[str, ...]] = (
    "temperature_2m",
    "precipitation_probability",
    "weathercode",
    "is_day",
    "relativehumidity_2m",
    "windspeed_10m",
    "winddirection_10m",
)
OPEN_METEO_DAILY_FIELDS: Final[tuple[str, ...]] = (
    "weathercode",
    "temperature_2m_max",
    "temperature_2m_min",
)
OPEN_METEO_TIMEZONE_AUTO: Final[str] = "timezone=auto"
OPEN_METEO_CURRENT_WEATHER_FLAG: Final[str] = "current_weather=true"


class HourlyConfigEntry(TypedDict):
    slots: tuple[str, ...]
    offsets: tuple[int, ...]


# Hourly forecast configuration by mode
HOURLY_CONFIG: Final[MappingProxyType[str, HourlyConfigEntry]] = MappingProxyType(
    {
        WEATHER_MODE_CURRENT: {
            "slots": (WEATHER_SLOT_NOW,),
            "offsets": (),
        },
        WEATHER_MODE_HOURLY: {
            "slots": tuple(label for _, label in HOURLY_FORECAST_SLOTS),
            "offsets": tuple(offset for offset, _ in HOURLY_FORECAST_SLOTS),
        },
    }
)


# Weather code to text mapping (Open-Meteo codes)
# Keys are weather codes, values are (day_text, night_text) tuples or single text for both
def _make_weather_mapping() -> dict[int, str]:
    def day_night(day: str, night: str) -> str:
        return f"DAY:{day}|NIGHT:{night}"

    def both(text: str) -> str:
        return f"BOTH:{text}"

    raw: dict[int, str] = {
        0: day_night("☀️ Clear sky", "🌙 Clear sky"),
        1: day_night("🌤️ Mainly clear", "🌙🌤️ Mainly clear"),
        2: day_night("⛅️ Partly cloudy", "🌙⛅️ Partly cloudy"),
        3: day_night("☁️ Overcast", "🌙☁️ Overcast"),
        45: day_night("🌫️ Fog", "🌙🌫️ Fog"),
        48: day_night("🌫️ Depositing rime fog", "🌙🌫️ Depositing rime fog"),
        51: both("🌧️ Light drizzle"),
        53: both("🌧️ Moderate drizzle"),
        55: both("🌧️ Dense drizzle"),
        56: both("🌧️ Light freezing drizzle"),
        57: both("🌧️ Dense freezing drizzle"),
        61: both("🌧️ Light rain"),
        63: both("🌧️ Moderate rain"),
        65: both("🌧️ Heavy rain"),
        66: both("🌧️ Light freezing rain"),
        67: both("🌧️ Heavy freezing rain"),
        71: both("❄️ Light snow fall"),
        73: both("❄️ Moderate snow fall"),
        75: both("❄️ Heavy snow fall"),
        77: both("❄️ Snow grains"),
        80: both("🌧️ Light rain showers"),
        81: both("🌧️ Moderate rain showers"),
        82: both("🌧️ Violent rain showers"),
        85: both("❄️ Light snow showers"),
        86: both("❄️ Heavy snow showers"),
        95: both("⛈️ Thunderstorm"),
        96: both("⛈️ Thunderstorm with slight hail"),
        99: both("⛈️ Thunderstorm with heavy hail"),
    }
    return raw


WEATHER_CODE_TEXT_MAPPING: Final[MappingProxyType[int, str]] = MappingProxyType(
    _make_weather_mapping()
)

# Telemetry plugin constants
TELEMETRY_DEFAULT_HOURS: Final[int] = 12
TELEMETRY_MAX_DATA_ROWS: Final[int] = 50

# Health plugin constants
LOW_BATTERY_THRESHOLD_PERCENT: Final[int] = 10

# Regex patterns
PING_EXPLICIT_COMMAND_REGEX: Final[re.Pattern[str]] = re.compile(
    r"!(ping)", re.IGNORECASE
)
PING_COMMAND_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)([!?]*)(ping)([!?]*)(?!\w)", re.IGNORECASE
)
DROP_COMMAND_REGEX: Final[re.Pattern[str]] = re.compile(r"!drop\s+(.+)$")
PROCESSED_PACKET_REGEX: Final[re.Pattern[str]] = re.compile(
    r"^Processed (.+) radio packet$"
)

# Mesh relay constants
MESH_PACKET_DEFAULT_ID: Final[int] = 0

# Git command tokens
GIT_REMOTE_ORIGIN: Final[str] = "origin"
GIT_REF_HEAD: Final[str] = "HEAD"
GIT_COMMIT_DEREF_SUFFIX: Final[str] = "^{commit}"
GIT_CLONE_FILTER_BLOB_NONE: Final[str] = "--filter=blob:none"
GIT_FETCH_DEPTH_ONE: Final[str] = "--depth=1"
GIT_CHECKOUT_CMD: Final[str] = "checkout"
GIT_FETCH_CMD: Final[str] = "fetch"
GIT_PULL_CMD: Final[str] = "pull"
GIT_CLONE_CMD: Final[str] = "clone"
GIT_REV_PARSE_CMD: Final[str] = "rev-parse"
GIT_BRANCH_CMD: Final[str] = "--branch"
GIT_TAGS_FLAG: Final[str] = "--tags"

# Git environment
GIT_TERMINAL_PROMPT_ENV: Final[str] = "GIT_TERMINAL_PROMPT"
GIT_TERMINAL_PROMPT_DISABLED: Final[str] = "0"

# Git default branch sentinel
GIT_DEFAULT_BRANCH_SENTINEL: Final[str] = "default branch"

# Plugin type constants
PLUGIN_TYPE_CORE: Final[str] = "core"
PLUGIN_TYPE_CUSTOM: Final[str] = "custom"
PLUGIN_TYPE_COMMUNITY: Final[str] = "community"
