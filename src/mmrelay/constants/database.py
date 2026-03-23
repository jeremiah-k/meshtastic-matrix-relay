"""
Database-related constants.

Contains default values for database configuration, message retention,
and data management settings.
"""

# Message retention defaults
DEFAULT_MSGS_TO_KEEP = 500
DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE = 100  # Base plugin default
DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY = 50  # Reduced for mesh relay performance
DEFAULT_NAME_PRUNE_CHUNK_SIZE = 900

# Progress tracking
PROGRESS_TOTAL_STEPS = 100
PROGRESS_COMPLETE = 100

# Text truncation
DEFAULT_TEXT_TRUNCATION_LENGTH = 50

# Distance calculations
DEFAULT_DISTANCE_KM_FALLBACK = 1000  # Fallback distance when calculation fails
DEFAULT_RADIUS_KM = 5  # Default radius for location-based filtering

# SQLite configuration defaults
DEFAULT_ENABLE_WAL = True
DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_EXTRA_PRAGMAS = {
    "synchronous": "NORMAL",
    "temp_store": "MEMORY",
}
MIN_SQLITE_VERSION_JSON_EACH = (3, 9, 0)
SQLITE_JSON_EACH_PROBE_SQL = "SELECT value FROM json_each(?)"
SQLITE_JSON_EACH_PROBE_PAYLOAD = '["probe"]'

# Node name storage tables (SQLite schema)
NAMES_TABLE_LONGNAMES = "longnames"
NAMES_TABLE_SHORTNAMES = "shortnames"
NAMES_FIELD_LONGNAME = "longname"
NAMES_FIELD_SHORTNAME = "shortname"

# Meshtastic node payload keys (protocol-level fields, not DB columns)
PROTO_NODE_NAME_LONG = "longName"
PROTO_NODE_NAME_SHORT = "shortName"
