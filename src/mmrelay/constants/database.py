"""
Database-related constants.

Contains default values for database configuration, message retention,
and data management settings.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

# Message retention defaults
DEFAULT_MSGS_TO_KEEP: Final[int] = 500
DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE: Final[int] = 100  # Base plugin default
DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY: Final[int] = (
    50  # Reduced for mesh relay performance
)
# Keep below SQLite's host-parameter limit (999 by default) to leave headroom.
DEFAULT_NAME_PRUNE_CHUNK_SIZE: Final[int] = 900

# Progress tracking
PROGRESS_TOTAL_STEPS: Final[int] = 100
PROGRESS_COMPLETE: Final[int] = 100

# Text truncation
DEFAULT_TEXT_TRUNCATION_LENGTH: Final[int] = 50

# Distance calculations
DEFAULT_DISTANCE_KM_FALLBACK: Final[int] = (
    1000  # Fallback distance when calculation fails
)
DEFAULT_RADIUS_KM: Final[int] = 5  # Default radius for location-based filtering

# SQLite configuration defaults
DEFAULT_ENABLE_WAL: Final[bool] = True
DEFAULT_BUSY_TIMEOUT_MS: Final[int] = 5000
DEFAULT_EXTRA_PRAGMAS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "synchronous": "NORMAL",
        "temp_store": "MEMORY",
    }
)
SQLITE_JSON_EACH_PROBE_SQL: Final[str] = "SELECT value FROM json_each(?)"
SQLITE_JSON_EACH_PROBE_PAYLOAD: Final[str] = '["probe"]'

# Node name storage tables (SQLite schema)
NAMES_TABLE_LONGNAMES: Final[str] = "longnames"
NAMES_TABLE_SHORTNAMES: Final[str] = "shortnames"
NAMES_FIELD_LONGNAME: Final[str] = "longname"
NAMES_FIELD_SHORTNAME: Final[str] = "shortname"

# Meshtastic node payload keys (protocol-level fields, not DB columns)
PROTO_NODE_NAME_LONG: Final[str] = "longName"
PROTO_NODE_NAME_SHORT: Final[str] = "shortName"
