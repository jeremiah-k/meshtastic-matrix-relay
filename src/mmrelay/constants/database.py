"""
Database-related constants.

Contains default values for database configuration, message retention,
and data management settings.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, TypeAlias

PragmaValue: TypeAlias = str | bool | int | float

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
DEFAULT_EXTRA_PRAGMAS: Final[Mapping[str, PragmaValue]] = MappingProxyType(
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

# Debug and sampling
DEBUG_ID_SAMPLE_LIMIT: Final[int] = 20

# SQLite paths and filenames
SQLITE_IN_MEMORY_PATH: Final[str] = ":memory:"
DEFAULT_DB_FILENAME: Final[str] = "meshtastic.sqlite"
LEGACY_DATABASE_SUBDIR: Final[str] = "database"

# Table and column names
PLUGIN_DATA_TABLE: Final[str] = "plugin_data"
PLUGIN_DATA_COLUMNS: Final[tuple[str, ...]] = ("plugin_name", "meshtastic_id", "data")
MESSAGE_MAP_TABLE: Final[str] = "message_map"
MESSAGE_MAP_COLUMNS: Final[tuple[str, ...]] = (
    "meshtastic_id",
    "matrix_event_id",
    "matrix_room_id",
    "meshtastic_text",
    "meshtastic_meshnet",
)

# SQLite pragmas
PRAGMA_JOURNAL_MODE_WAL: Final[str] = "PRAGMA journal_mode=WAL"
PRAGMA_FOREIGN_KEYS_ON: Final[str] = "PRAGMA foreign_keys=ON"

# SQLite sidecar file suffixes
SQLITE_SIDECAR_SUFFIXES: Final[tuple[str, ...]] = (
    ".sqlite-wal",
    ".sqlite-shm",
    "-journal",
)

# Database executor
DB_EXECUTOR_MAX_WORKERS: Final[int] = 1

# Plugin database template
PLUGIN_DB_FILENAME_TEMPLATE: Final[str] = "plugin_data_{plugin_name}.sqlite"
