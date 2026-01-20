"""
Database-related constants.

Contains default values for database configuration, message retention,
and data management settings.
"""

# Message retention defaults
DEFAULT_MSGS_TO_KEEP = 500
DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE = 100  # Base plugin default
DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY = 50  # Reduced for mesh relay performance

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
