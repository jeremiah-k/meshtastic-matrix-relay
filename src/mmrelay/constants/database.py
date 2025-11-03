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

# Database performance optimization pragmas
OPTIMIZATION_PRAGMAS = {
    "journal_mode": "WAL",  # Better concurrency
    "synchronous": "NORMAL",  # Balanced safety/performance
    "cache_size": -2000,  # 2MB cache
    "temp_store": "MEMORY",  # Temporary tables in memory
    "mmap_size": 268435456,  # 256MB memory mapping
    "wal_autocheckpoint": 1000,  # WAL checkpoint interval
    "busy_timeout": 30000,  # 30 second timeout
}
