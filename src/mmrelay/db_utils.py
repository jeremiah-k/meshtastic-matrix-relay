import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Collection
from typing import Any, Callable, NamedTuple, cast

from mmrelay.constants.app import DATABASE_FILENAME, LEGACY_DATA_SUBDIR
from mmrelay.constants.config import (
    CONFIG_SECTION_DATABASE,
    CONFIG_SECTION_DATABASE_LEGACY,
    ENV_BOOL_FALSE_VALUES,
    ENV_BOOL_TRUE_VALUES,
)
from mmrelay.constants.database import (
    DEBUG_ID_SAMPLE_LIMIT,
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_ENABLE_WAL,
    DEFAULT_EXTRA_PRAGMAS,
    DEFAULT_NAME_PRUNE_CHUNK_SIZE,
    LEGACY_DATABASE_SUBDIR,
    MESSAGE_MAP_COLUMNS,
    MESSAGE_MAP_TABLE,
    NAMES_FIELD_LONGNAME,
    NAMES_FIELD_SHORTNAME,
    NAMES_TABLE_LONGNAMES,
    NAMES_TABLE_SHORTNAMES,
    PLUGIN_DATA_COLUMNS,
    PLUGIN_DATA_TABLE,
    PROTO_NODE_NAME_LONG,
    PROTO_NODE_NAME_SHORT,
    PragmaValue,
)
from mmrelay.db_runtime import DatabaseManager
from mmrelay.log_utils import get_logger
from mmrelay.paths import (
    get_legacy_dirs,
    is_deprecation_window_active,
    resolve_all_paths,
)


class _InvalidNamesTableError(ValueError):
    """Raised when an invalid table name is provided to stale name deletion functions."""

    def __init__(self, table: str) -> None:
        super().__init__(f"Invalid table name: {table}")


_MESSAGE_MAP_LEGACY_TABLE = f"{MESSAGE_MAP_TABLE}_legacy"
_MESSAGE_MAP_TEMP_TABLE = f"{MESSAGE_MAP_TABLE}_old_temp"
_MESSAGE_MAP_STALE_TEMP_TABLE = f"{MESSAGE_MAP_TABLE}_stale_temp"

_VALID_TABLE_NAMES: frozenset[str] = frozenset(
    {
        "message_map",
        "message_map_legacy",
        "message_map_old_temp",
        "message_map_stale_temp",
        "plugin_data",
        "longnames",
        "shortnames",
    }
)

_VALID_COLUMN_NAMES: frozenset[str] = frozenset(
    {
        "meshtastic_id",
        "matrix_event_id",
        "matrix_room_id",
        "meshtastic_text",
        "meshtastic_meshnet",
        "plugin_name",
        "data",
        "longname",
        "shortname",
    }
)


def _validate_identifier(name: str, allowlist: frozenset[str]) -> str:
    """
    Validate that a SQL identifier matches a known-safe allowlist.

    Raises ValueError if the identifier is not in the allowlist.
    """
    if name not in allowlist:
        raise ValueError(f"Invalid SQL identifier: {name}")
    return name


# Global config variable that will be set from main.py
config = None

# Cache for database path to avoid repeated logging and path resolution
_cached_db_path = None
_db_path_logged = False
_cached_config_hash = None

# Database manager cache
_db_manager: DatabaseManager | None = None
_db_manager_signature: tuple[str, bool, int, tuple[tuple[str, Any], ...]] | None = None
_db_manager_lock = threading.Lock()

logger = get_logger(name="db_utils")


class NodeNameEntry(NamedTuple):
    meshtastic_id: str
    long_name: str | None
    short_name: str | None


NodeNameState = tuple[NodeNameEntry, ...]

_CONFLICT_SENTINEL = object()
_NODE_NAME_DEBUG_ID_SAMPLE_LIMIT = DEBUG_ID_SAMPLE_LIMIT

# Table name to singular field-name mapping used for logging and column lookup.
_NAME_FIELD_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: NAMES_FIELD_LONGNAME,
    NAMES_TABLE_SHORTNAMES: NAMES_FIELD_SHORTNAME,
}

# Explicit protocol-field -> DB-column translation so Meshtastic payload keys
# are not confused with SQLite column names.
_DB_COLUMN_BY_PROTO_NODE_NAME_FIELD = {
    PROTO_NODE_NAME_LONG: _NAME_FIELD_BY_TABLE[NAMES_TABLE_LONGNAMES],
    PROTO_NODE_NAME_SHORT: _NAME_FIELD_BY_TABLE[NAMES_TABLE_SHORTNAMES],
}
_LONGNAME_DB_FIELD = _NAME_FIELD_BY_TABLE[NAMES_TABLE_LONGNAMES]
_SHORTNAME_DB_FIELD = _NAME_FIELD_BY_TABLE[NAMES_TABLE_SHORTNAMES]

# SQL templates are defined as static literals to keep linting deterministic.
_SELECT_STALE_IDS_FROM_LONGNAMES_SQL = "SELECT meshtastic_id FROM longnames"
_SELECT_STALE_IDS_FROM_SHORTNAMES_SQL = "SELECT meshtastic_id FROM shortnames"
_SELECT_STALE_IDS_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: _SELECT_STALE_IDS_FROM_LONGNAMES_SQL,
    NAMES_TABLE_SHORTNAMES: _SELECT_STALE_IDS_FROM_SHORTNAMES_SQL,
}

_DELETE_STALE_ID_FROM_LONGNAMES_SQL = "DELETE FROM longnames WHERE meshtastic_id = ?"
_DELETE_STALE_ID_FROM_SHORTNAMES_SQL = "DELETE FROM shortnames WHERE meshtastic_id = ?"
_DELETE_STALE_ID_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: _DELETE_STALE_ID_FROM_LONGNAMES_SQL,
    NAMES_TABLE_SHORTNAMES: _DELETE_STALE_ID_FROM_SHORTNAMES_SQL,
}

_SELECT_NAME_VALUES_JSON_FROM_LONGNAMES_SQL = (
    "SELECT meshtastic_id, longname FROM longnames "
    "WHERE meshtastic_id IN (SELECT value FROM json_each(?))"
)
_SELECT_NAME_VALUES_JSON_FROM_SHORTNAMES_SQL = (
    "SELECT meshtastic_id, shortname FROM shortnames "
    "WHERE meshtastic_id IN (SELECT value FROM json_each(?))"
)
_SELECT_NAME_VALUES_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: _SELECT_NAME_VALUES_JSON_FROM_LONGNAMES_SQL,
    NAMES_TABLE_SHORTNAMES: _SELECT_NAME_VALUES_JSON_FROM_SHORTNAMES_SQL,
}

_SELECT_NAME_VALUES_IN_PREFIX_FROM_LONGNAMES_SQL = (
    "SELECT meshtastic_id, longname FROM longnames WHERE meshtastic_id IN ("
)
_SELECT_NAME_VALUES_IN_PREFIX_FROM_SHORTNAMES_SQL = (
    "SELECT meshtastic_id, shortname FROM shortnames WHERE meshtastic_id IN ("
)
_SELECT_NAME_VALUES_IN_PREFIX_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: _SELECT_NAME_VALUES_IN_PREFIX_FROM_LONGNAMES_SQL,
    NAMES_TABLE_SHORTNAMES: _SELECT_NAME_VALUES_IN_PREFIX_FROM_SHORTNAMES_SQL,
}

_UPSERT_LONGNAME_SQL = (
    "INSERT INTO longnames (meshtastic_id, longname) VALUES (?, ?) "
    "ON CONFLICT(meshtastic_id) DO UPDATE SET longname=excluded.longname"
)
_UPSERT_SHORTNAME_SQL = (
    "INSERT INTO shortnames (meshtastic_id, shortname) VALUES (?, ?) "
    "ON CONFLICT(meshtastic_id) DO UPDATE SET shortname=excluded.shortname"
)
_UPSERT_NAME_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: _UPSERT_LONGNAME_SQL,
    NAMES_TABLE_SHORTNAMES: _UPSERT_SHORTNAME_SQL,
}

_SELECT_LONGNAME_BY_ID_SQL = "SELECT longname FROM longnames WHERE meshtastic_id=?"
_SELECT_SHORTNAME_BY_ID_SQL = "SELECT shortname FROM shortnames WHERE meshtastic_id=?"
_CREATE_TABLE_NAMES_LONG_SQL = (
    "CREATE TABLE IF NOT EXISTS longnames "
    "(meshtastic_id TEXT PRIMARY KEY, longname TEXT)"
)
_CREATE_TABLE_NAMES_SHORT_SQL = (
    "CREATE TABLE IF NOT EXISTS shortnames "
    "(meshtastic_id TEXT PRIMARY KEY, shortname TEXT)"
)
_CREATE_TABLE_PLUGIN_DATA_SQL = (
    "CREATE TABLE IF NOT EXISTS plugin_data "
    "(plugin_name TEXT, meshtastic_id TEXT, data TEXT, "
    "PRIMARY KEY (plugin_name, meshtastic_id))"
)
_CREATE_TABLE_MESSAGE_MAP_SQL = (
    "CREATE TABLE IF NOT EXISTS message_map "
    "(meshtastic_id TEXT, matrix_event_id TEXT PRIMARY KEY, "
    "matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
)
_UPSERT_PLUGIN_DATA_SQL = (
    "INSERT INTO plugin_data (plugin_name, meshtastic_id, data) VALUES (?, ?, ?) "
    "ON CONFLICT (plugin_name, meshtastic_id) DO UPDATE SET data = excluded.data"
)
_DELETE_PLUGIN_DATA_SQL = (
    "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?"
)
_GET_PLUGIN_DATA_SQL = (
    "SELECT data FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?"
)
_GET_ALL_PLUGIN_DATA_SQL = "SELECT data FROM plugin_data WHERE plugin_name=?"
_UPSERT_MESSAGE_MAP_SQL = (
    "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(matrix_event_id) DO UPDATE SET "
    "meshtastic_id=excluded.meshtastic_id, "
    "matrix_room_id=excluded.matrix_room_id, "
    "meshtastic_text=excluded.meshtastic_text, "
    "meshtastic_meshnet=excluded.meshtastic_meshnet"
)
_GET_MESSAGE_MAP_BY_MESHTASTIC_ID_SQL = (
    "SELECT matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map WHERE meshtastic_id=?"
)
_GET_MESSAGE_MAP_BY_MATRIX_EVENT_ID_SQL = (
    "SELECT meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map WHERE matrix_event_id=?"
)
_ALTER_TABLE_MESSAGE_MAP_ADD_MESH_SQL = (
    "ALTER TABLE message_map ADD COLUMN meshtastic_meshnet TEXT"
)
_PRAGMA_MESSAGE_MAP_INFO_SQL = "PRAGMA table_info(message_map)"
_PRAGMA_MESSAGE_MAP_LEGACY_INFO_SQL = "PRAGMA table_info(message_map_legacy)"
_PRAGMA_MESSAGE_MAP_TEMP_INFO_SQL = "PRAGMA table_info(message_map_old_temp)"
_DROP_TABLE_MESSAGE_MAP_LEGACY_SQL = "DROP TABLE IF EXISTS message_map_legacy"
_DROP_TABLE_MESSAGE_MAP_TEMP_SQL = "DROP TABLE IF EXISTS message_map_old_temp"
_DROP_TABLE_MESSAGE_MAP_STALE_TEMP_SQL = (
    f"DROP TABLE IF EXISTS {_MESSAGE_MAP_STALE_TEMP_TABLE}"
)
_RENAME_MESSAGE_MAP_TO_LEGACY_SQL = (
    "ALTER TABLE message_map RENAME TO message_map_legacy"
)
_RENAME_MESSAGE_MAP_TO_TEMP_SQL = (
    "ALTER TABLE message_map RENAME TO message_map_old_temp"
)
_RENAME_MESSAGE_MAP_TEMP_TO_STALE_TEMP_SQL = (
    f"ALTER TABLE message_map_old_temp RENAME TO {_MESSAGE_MAP_STALE_TEMP_TABLE}"
)
_RENAME_MESSAGE_MAP_STALE_TEMP_TO_TEMP_SQL = (
    f"ALTER TABLE {_MESSAGE_MAP_STALE_TEMP_TABLE} RENAME TO {_MESSAGE_MAP_TEMP_TABLE}"
)
_CREATE_TABLE_MESSAGE_MAP_FROM_SCRATCH_SQL = (
    "CREATE TABLE message_map "
    "(meshtastic_id TEXT, matrix_event_id TEXT PRIMARY KEY, "
    "matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
)
_INSERT_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL = (
    "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map_legacy"
)
_INSERT_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL = (
    "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, NULL "
    "FROM message_map_legacy"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL = (
    "INSERT OR IGNORE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map_legacy"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL = (
    "INSERT OR IGNORE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, NULL "
    "FROM message_map_legacy"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_TEMP_SQL = (
    "INSERT OR IGNORE INTO message_map "
    "(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map_old_temp"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_TEMP_FROM_STALE_TEMP_WITH_MESH_SQL = (
    "INSERT OR IGNORE INTO message_map_old_temp "
    "(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    "SELECT meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    "FROM message_map_stale_temp"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_TEMP_FROM_STALE_TEMP_WITHOUT_MESH_SQL = (
    "INSERT OR IGNORE INTO message_map_old_temp "
    "(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text) "
    "SELECT meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text "
    "FROM message_map_stale_temp"
)
_PRAGMA_MESSAGE_MAP_STALE_TEMP_INFO_SQL = (
    f"PRAGMA table_info({_MESSAGE_MAP_STALE_TEMP_TABLE})"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_STALE_TEMP_WITH_MESH_SQL = (
    f"INSERT OR IGNORE INTO message_map "  # nosec B608
    f"(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    f"SELECT meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
    f"FROM {_MESSAGE_MAP_STALE_TEMP_TABLE}"
)
_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_STALE_TEMP_WITHOUT_MESH_SQL = (
    f"INSERT OR IGNORE INTO message_map "  # nosec B608
    f"(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
    f"SELECT meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, NULL "
    f"FROM {_MESSAGE_MAP_STALE_TEMP_TABLE}"
)
_CREATE_INDEX_MESSAGE_MAP_ID_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_message_map_meshtastic_id "
    "ON message_map (meshtastic_id)"
)
_DELETE_FROM_MESSAGE_MAP_SQL = "DELETE FROM message_map"
_SELECT_COUNT_MESSAGE_MAP_SQL = "SELECT COUNT(*) FROM message_map"
_DELETE_OLDEST_MESSAGE_MAP_SQL = (
    "DELETE FROM message_map WHERE rowid IN "
    "(SELECT rowid FROM message_map ORDER BY rowid ASC LIMIT ?)"
)

if MESSAGE_MAP_TABLE != "message_map":
    raise RuntimeError(
        "Message-map constants changed; update static SQL literals in db_utils."
    )
if _MESSAGE_MAP_TEMP_TABLE != "message_map_old_temp":
    raise RuntimeError(
        "Message-map temp-table constant changed; update static SQL literals in db_utils."
    )
if _MESSAGE_MAP_STALE_TEMP_TABLE != "message_map_stale_temp":
    raise RuntimeError(
        "Message-map stale-temp constant changed; update static SQL literals in db_utils."
    )

if (PLUGIN_DATA_TABLE, *PLUGIN_DATA_COLUMNS) != (
    "plugin_data",
    "plugin_name",
    "meshtastic_id",
    "data",
):
    raise RuntimeError(
        "Plugin-data constants changed; update static SQL literals in db_utils."
    )

if tuple(MESSAGE_MAP_COLUMNS) != (
    "meshtastic_id",
    "matrix_event_id",
    "matrix_room_id",
    "meshtastic_text",
    "meshtastic_meshnet",
):
    raise RuntimeError(
        "Message-map column constants changed; update static SQL literals in db_utils."
    )

if (
    NAMES_TABLE_LONGNAMES,
    NAMES_TABLE_SHORTNAMES,
    NAMES_FIELD_LONGNAME,
    NAMES_FIELD_SHORTNAME,
) != ("longnames", "shortnames", "longname", "shortname"):
    raise RuntimeError(
        "Names-table constants changed; update static SQL literals in db_utils."
    )


def _format_node_id_sample(ids: Collection[str]) -> str:
    """
    Render a deterministic, bounded list of node IDs for debug logging.
    """
    if not ids:
        return "[]"
    ordered_ids = sorted(ids)
    sample = ordered_ids[:_NODE_NAME_DEBUG_ID_SAMPLE_LIMIT]
    remaining = len(ordered_ids) - len(sample)
    if remaining > 0:
        return f"{sample} (+{remaining} more)"
    return str(sample)


def clear_db_path_cache() -> None:
    """Clear the cached database path to force re-resolution on next call.

    This is useful for testing or if the application supports runtime
    configuration changes.
    """
    global _cached_db_path, _db_path_logged, _cached_config_hash
    _cached_db_path = None
    _db_path_logged = False
    _cached_config_hash = None


def _normalize_database_section(section: Any) -> dict[str, Any]:
    """
    Return a dictionary section for database config, defaulting invalid shapes to {}.
    """
    return section if isinstance(section, dict) else {}


def _canonicalize_signature_value(value: Any) -> Any:
    """
    Convert arbitrary config values to deterministic JSON-safe values.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_signature_value(nested_value)
            for key, nested_value in sorted(
                value.items(),
                key=lambda item: str(item[0]),
            )
        }
    if isinstance(value, set):
        return [_canonicalize_signature_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, (list, tuple)):
        return [_canonicalize_signature_value(item) for item in value]
    try:
        value_repr = repr(value)
    except Exception:  # noqa: BLE001 - defensive fallback for arbitrary objects
        value_repr = f"<unrepresentable {type(value).__name__}>"
    return {"__type__": type(value).__name__, "__repr__": value_repr}


def _build_database_config_signature(raw_config: Any) -> str | None:
    """
    Build a stable cache signature from normalized database config sections.
    """
    if not isinstance(raw_config, dict):
        return None
    db_config = {
        CONFIG_SECTION_DATABASE: _normalize_database_section(
            raw_config.get(CONFIG_SECTION_DATABASE)
        ),
        CONFIG_SECTION_DATABASE_LEGACY: _normalize_database_section(
            raw_config.get(CONFIG_SECTION_DATABASE_LEGACY)
        ),
    }
    signature_payload = _canonicalize_signature_value(db_config)
    return json.dumps(
        signature_payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    )


# Get the database path
def get_db_path() -> str:
    """
    Resolve the absolute filesystem path to the application's SQLite database.

    Selects the path with this precedence: configuration key `database.path` (preferred), legacy `db.path`, then `<database_dir>/meshtastic.sqlite` from the application's resolved paths. The resolved path is cached and the cache is invalidated when relevant database configuration changes. The function will attempt to create missing directories; legacy database migration is handled explicitly by `mmrelay migrate` rather than implicitly here. Directory-creation failures are logged and do not raise exceptions.

    Returns:
        str: Filesystem path to the SQLite database.
    """
    global config, _cached_db_path, _db_path_logged, _cached_config_hash

    # Create a deterministic representation of relevant config sections to
    # detect changes without assuming serializable value shapes.
    current_config_hash = _build_database_config_signature(config)

    # Check if cache is valid (path exists and config hasn't changed)
    if _cached_db_path is not None and current_config_hash == _cached_config_hash:
        return _cached_db_path

    # Config changed or first call - clear cache and re-resolve
    if current_config_hash != _cached_config_hash:
        _cached_db_path = None
        _db_path_logged = False
        _cached_config_hash = current_config_hash

    # Check if config is available
    if isinstance(config, dict):
        database_section = _normalize_database_section(
            config.get(CONFIG_SECTION_DATABASE)
        )
        legacy_db_section = _normalize_database_section(
            config.get(CONFIG_SECTION_DATABASE_LEGACY)
        )

        # Check if database path is specified in config (preferred format)
        if "path" in database_section:
            custom_path = database_section["path"]
            if isinstance(custom_path, str) and custom_path:
                # Ensure the directory exists
                db_dir = os.path.dirname(custom_path)
                if db_dir:
                    try:
                        os.makedirs(db_dir, exist_ok=True)
                    except (OSError, PermissionError) as e:
                        logger.warning(
                            "Could not create database directory %s: %s", db_dir, e
                        )
                        # Continue anyway - the database connection will fail later if needed

                # Cache the path and log only once
                _cached_db_path = custom_path
                if not _db_path_logged:
                    logger.info("Using database path from config: %s", custom_path)
                    _db_path_logged = True
                return custom_path
            if custom_path not in (None, ""):
                logger.warning(
                    "Ignoring invalid database.path value of type %s",
                    type(custom_path).__name__,
                )

        # Check legacy format (db section)
        if "path" in legacy_db_section:
            custom_path = legacy_db_section["path"]
            if isinstance(custom_path, str) and custom_path:
                # Ensure the directory exists
                db_dir = os.path.dirname(custom_path)
                if db_dir:
                    try:
                        os.makedirs(db_dir, exist_ok=True)
                    except (OSError, PermissionError) as e:
                        logger.warning(
                            "Could not create database directory %s: %s", db_dir, e
                        )
                        # Continue anyway - the database connection will fail later if needed

                # Cache the path and log only once
                _cached_db_path = custom_path
                if not _db_path_logged:
                    logger.warning(
                        "Using 'db.path' configuration (legacy). 'database.path' is now the preferred format and 'db.path' will be deprecated in a future version."
                    )
                    _db_path_logged = True
                return custom_path
            if custom_path not in (None, ""):
                logger.warning(
                    "Ignoring invalid db.path value of type %s",
                    type(custom_path).__name__,
                )

    # Use unified path resolution for database
    paths_info = resolve_all_paths()
    database_dir = paths_info["database_dir"]

    # Ensure the database directory exists before using it
    try:
        os.makedirs(database_dir, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning("Could not create database directory %s: %s", database_dir, e)
        # Continue anyway - the database connection will fail later if needed

    default_path = os.path.join(database_dir, DATABASE_FILENAME)

    # If default path doesn't exist, check legacy locations
    if not os.path.exists(default_path) and is_deprecation_window_active():
        legacy_dirs = get_legacy_dirs()
        for legacy_dir in legacy_dirs:
            # Check various possible legacy locations
            candidates = [
                os.path.join(legacy_dir, DATABASE_FILENAME),
                os.path.join(legacy_dir, LEGACY_DATA_SUBDIR, DATABASE_FILENAME),
                os.path.join(legacy_dir, LEGACY_DATABASE_SUBDIR, DATABASE_FILENAME),
            ]
            for candidate in candidates:
                if os.path.exists(candidate):
                    if not _db_path_logged:
                        logger.warning(
                            "Database found in legacy location: %s. "
                            "Please run 'mmrelay migrate' to move to new unified structure. "
                            "Support for legacy database locations will be removed in v1.4.",
                            candidate,
                        )
                        _db_path_logged = True
                    _cached_db_path = candidate
                    return candidate

    _cached_db_path = default_path
    return default_path


def _close_manager_safely(manager: DatabaseManager | None) -> None:
    """
    Close the given DatabaseManager if provided.

    If close() fails, run the manager's best-effort finalizer to avoid leaked
    sqlite connections during teardown.
    """
    if manager is None:
        return

    try:
        manager.close()
        return
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        logger.debug("Database manager close failed; forcing finalizer", exc_info=True)

    finalizer = getattr(manager, "_finalize_unclosed_resources", None)
    if callable(finalizer):
        with contextlib.suppress(Exception):
            finalizer()


def _reset_db_manager() -> None:
    """
    Reset the cached global DatabaseManager so a new instance will be created on next access.

    The manager reference is cleared atomically under lock, then closed outside the lock.
    This avoids holding the lock during a potentially blocking close() operation while
    still ensuring no new threads can acquire the old manager after this call returns.
    Intended for testing and when configuration changes require recreating the manager.
    """
    global _db_manager, _db_manager_signature
    manager_to_close = None
    with _db_manager_lock:
        if _db_manager is not None:
            manager_to_close = _db_manager
            _db_manager = None
            _db_manager_signature = None

    if manager_to_close is not None:
        _close_manager_safely(manager_to_close)


def _parse_bool(value: Any, default: bool) -> bool:
    """
    Parse a value into a boolean using common representations.

    Parameters:
        value: The input to interpret; typically a bool or string. Common true strings: "1", "true", "yes", "on" (case-insensitive). Common false strings: "0", "false", "no", "off" (case-insensitive).
        default (bool): Fallback value returned when `value` is not a boolean and does not match any recognized string representations.

    Returns:
        bool: `True` if `value` represents true, `False` if it represents false, otherwise `default`.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ENV_BOOL_TRUE_VALUES:
            return True
        if lowered in ENV_BOOL_FALSE_VALUES:
            return False
    return default


def _parse_int(value: Any, default: int) -> int:
    """
    Parse a value as an integer and return a fallback if parsing fails.

    Parameters:
        value: The value to convert to int (may be any type).
        default (int): The value to return if `value` cannot be parsed as an integer.

    Returns:
        int: The parsed integer from `value`, or `default` if parsing raises TypeError or ValueError.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _resolve_database_options() -> tuple[bool, int, dict[str, PragmaValue]]:
    """
    Resolve database options (WAL, busy timeout, and SQLite pragmas) from the global config, supporting legacy keys and falling back to module defaults.

    Reads values from config["database"] with fallback to legacy config["db"], parses boolean and integer settings, and merges any provided pragmas on top of DEFAULT_EXTRA_PRAGMAS.

    Returns:
        enable_wal (bool): `True` if write-ahead logging should be enabled, `False` otherwise.
        busy_timeout_ms (int): Busy timeout in milliseconds to use for SQLite connections.
        extra_pragmas (dict): Mapping of pragma names to values, starting from DEFAULT_EXTRA_PRAGMAS and overridden by config-provided pragmas.
    """
    raw_database_cfg: Any = (
        config.get(CONFIG_SECTION_DATABASE, {}) if isinstance(config, dict) else {}
    )
    database_cfg: dict[str, Any] = (
        raw_database_cfg if isinstance(raw_database_cfg, dict) else {}
    )
    raw_legacy_cfg: Any = (
        config.get(CONFIG_SECTION_DATABASE_LEGACY, {})
        if isinstance(config, dict)
        else {}
    )
    legacy_cfg: dict[str, Any] = (
        raw_legacy_cfg if isinstance(raw_legacy_cfg, dict) else {}
    )

    enable_wal = _parse_bool(
        database_cfg.get(
            "enable_wal", legacy_cfg.get("enable_wal", DEFAULT_ENABLE_WAL)
        ),
        DEFAULT_ENABLE_WAL,
    )

    raw_busy_timeout_ms = database_cfg.get(
        "busy_timeout_ms",
        legacy_cfg.get("busy_timeout_ms", DEFAULT_BUSY_TIMEOUT_MS),
    )
    busy_timeout_ms = _parse_int(
        (
            DEFAULT_BUSY_TIMEOUT_MS
            if isinstance(raw_busy_timeout_ms, bool)
            else raw_busy_timeout_ms
        ),
        DEFAULT_BUSY_TIMEOUT_MS,
    )

    extra_pragmas = dict(DEFAULT_EXTRA_PRAGMAS)
    pragmas_cfg = database_cfg.get("pragmas", legacy_cfg.get("pragmas"))
    if isinstance(pragmas_cfg, dict):
        for pragma, value in pragmas_cfg.items():
            extra_pragmas[str(pragma)] = value

    return enable_wal, busy_timeout_ms, extra_pragmas


def _get_db_manager() -> DatabaseManager:
    """
    Obtain the global DatabaseManager, creating or replacing it when the resolved database path or options change.

    Returns:
        DatabaseManager: The cached DatabaseManager instance configured for the current database path and options.

    Raises:
        RuntimeError: If the DatabaseManager could not be initialized.
    """
    global _db_manager, _db_manager_signature
    path = get_db_path()
    enable_wal, busy_timeout_ms, extra_pragmas = _resolve_database_options()
    signature = (
        path,
        enable_wal,
        busy_timeout_ms,
        tuple(sorted(extra_pragmas.items())),
    )

    manager_to_close = None
    with _db_manager_lock:
        if _db_manager is None or _db_manager_signature != signature:
            try:
                new_manager = DatabaseManager(
                    path,
                    enable_wal=enable_wal,
                    busy_timeout_ms=busy_timeout_ms,
                    extra_pragmas=extra_pragmas,
                )
                # Successfully created a new manager, now swap it with the old one.
                manager_to_close = _db_manager
                _db_manager = new_manager
                _db_manager_signature = signature
                _close_manager_safely(manager_to_close)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                if _db_manager is None:
                    # First-time initialization failed, so we cannot proceed.
                    raise

                # A configuration change failed. Log the error but continue with the old manager
                # to keep the application alive.
                logger.exception(
                    "Failed to create new DatabaseManager with updated configuration. "
                    "The application will continue using the previous database settings."
                )
                # Leave _db_manager_signature unchanged so a future call will retry once the issue is resolved.

        # Critical: Final check and return must be inside the lock to prevent race condition.
        # Without this, _reset_db_manager() could set _db_manager = None after we release
        # the lock but before we return, causing an unexpected RuntimeError.
        if _db_manager is None:
            raise RuntimeError("Database manager initialization failed")
        return _db_manager


# Initialize SQLite database
def initialize_database() -> None:
    """
    Initializes the SQLite database schema for the relay application.

    Creates required tables (`longnames`, `shortnames`, `plugin_data`, and `message_map`) if they do not exist, and ensures the `meshtastic_meshnet` column is present in `message_map`. Raises an exception if database initialization fails.
    """
    db_path = get_db_path()
    # Check if database exists
    if os.path.exists(db_path):
        logger.info("Loading database from: %s", db_path)
    else:
        logger.info("Creating new database at: %s", db_path)
    manager = _get_db_manager()

    def _initialize(cursor: sqlite3.Cursor) -> None:
        """
        Create required SQLite tables for the application's schema and apply minimal schema migrations.

        Creates tables: `longnames`, `shortnames`, `plugin_data`, and `message_map`. Attempts to add the
        `meshtastic_meshnet` column and to create an index on `message_map(meshtastic_id)`; failures
        from those upgrade attempts are ignored (safe no-op if already applied).

        Parameters:
            cursor: An sqlite3.Cursor positioned on the target database; used to execute DDL statements.
        """
        cursor.execute(_CREATE_TABLE_NAMES_LONG_SQL)
        cursor.execute(_CREATE_TABLE_NAMES_SHORT_SQL)
        cursor.execute(_CREATE_TABLE_PLUGIN_DATA_SQL)
        cursor.execute(_CREATE_TABLE_MESSAGE_MAP_SQL)
        _legacy_table = _MESSAGE_MAP_LEGACY_TABLE
        _validate_identifier(_legacy_table, _VALID_TABLE_NAMES)
        _col_id, _col_evt, _col_room, _col_text, _col_mesh = MESSAGE_MAP_COLUMNS
        for _c in MESSAGE_MAP_COLUMNS:
            _validate_identifier(_c, _VALID_COLUMN_NAMES)

        cursor.execute(_PRAGMA_MESSAGE_MAP_INFO_SQL)
        columns = cursor.fetchall()
        column_map = {column[1]: column for column in columns}
        if _col_mesh not in column_map:
            cursor.execute(_ALTER_TABLE_MESSAGE_MAP_ADD_MESH_SQL)
            cursor.execute(_PRAGMA_MESSAGE_MAP_INFO_SQL)
            columns = cursor.fetchall()
            column_map = {column[1]: column for column in columns}
        meshtastic_column = column_map.get(_col_id)
        meshnet_column = column_map.get(_col_mesh)
        _temp_table = _MESSAGE_MAP_TEMP_TABLE
        _validate_identifier(_temp_table, _VALID_TABLE_NAMES)
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_temp_table,),
        )
        temp_exists = cursor.fetchone() is not None
        stale_temp_exists = False
        _validate_identifier(_MESSAGE_MAP_STALE_TEMP_TABLE, _VALID_TABLE_NAMES)

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_MESSAGE_MAP_STALE_TEMP_TABLE,),
        )
        stale_table_exists = cursor.fetchone() is not None
        if stale_table_exists and not temp_exists:
            cursor.execute(_RENAME_MESSAGE_MAP_STALE_TEMP_TO_TEMP_SQL)
            temp_exists = True
            stale_table_exists = False
            logger.info(
                "Recovered stale temporary table %s as %s",
                _MESSAGE_MAP_STALE_TEMP_TABLE,
                _temp_table,
            )
        elif stale_table_exists and temp_exists:
            cursor.execute(_PRAGMA_MESSAGE_MAP_TEMP_INFO_SQL)
            temp_columns = {column[1] for column in cursor.fetchall()}
            cursor.execute(_PRAGMA_MESSAGE_MAP_STALE_TEMP_INFO_SQL)
            stale_columns = {column[1] for column in cursor.fetchall()}
            merge_target = _temp_table
            if _col_mesh in temp_columns:
                if _col_mesh in stale_columns:
                    cursor.execute(
                        _INSERT_OR_IGNORE_MESSAGE_MAP_TEMP_FROM_STALE_TEMP_WITH_MESH_SQL
                    )
                else:
                    cursor.execute(
                        _INSERT_OR_IGNORE_MESSAGE_MAP_TEMP_FROM_STALE_TEMP_WITHOUT_MESH_SQL
                    )
            else:
                if _col_mesh in stale_columns:
                    # Preserve meshnet values by merging directly into message_map when
                    # the destination temp schema does not yet contain meshtastic_meshnet.
                    cursor.execute(
                        _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_STALE_TEMP_WITH_MESH_SQL
                    )
                    merge_target = MESSAGE_MAP_TABLE
                else:
                    cursor.execute(
                        _INSERT_OR_IGNORE_MESSAGE_MAP_TEMP_FROM_STALE_TEMP_WITHOUT_MESH_SQL
                    )
            cursor.execute(_DROP_TABLE_MESSAGE_MAP_STALE_TEMP_SQL)
            stale_table_exists = False
            logger.info(
                "Merged rows from stale temporary table %s into %s",
                _MESSAGE_MAP_STALE_TEMP_TABLE,
                merge_target,
            )

        if (
            temp_exists
            and meshtastic_column
            and str(meshtastic_column[2]).upper() == "TEXT"
        ):
            cursor.execute(_PRAGMA_MESSAGE_MAP_TEMP_INFO_SQL)
            temp_column_map = {column[1]: column for column in cursor.fetchall()}
            insert_sql = (
                _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL
                if _col_mesh in temp_column_map
                else _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL
            ).replace("message_map_legacy", _temp_table)
            if "message_map_legacy" in insert_sql:
                raise RuntimeError("SQL replacement failed")
            cursor.execute(insert_sql)
            cursor.execute(_DROP_TABLE_MESSAGE_MAP_TEMP_SQL)
            temp_exists = False

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_legacy_table,),
        )
        legacy_exists = cursor.fetchone() is not None

        if legacy_exists and (
            not meshtastic_column or str(meshtastic_column[2]).upper() == "TEXT"
        ):
            cursor.execute(_PRAGMA_MESSAGE_MAP_LEGACY_INFO_SQL)
            legacy_columns = {column[1]: column for column in cursor.fetchall()}
            if _col_mesh in legacy_columns:
                cursor.execute(_INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL)
            else:
                cursor.execute(
                    _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL
                )
            cursor.execute(_DROP_TABLE_MESSAGE_MAP_LEGACY_SQL)
            legacy_exists = False

        if meshtastic_column and str(meshtastic_column[2]).upper() != "TEXT":
            if temp_exists:
                cursor.execute(_DROP_TABLE_MESSAGE_MAP_STALE_TEMP_SQL)
                logger.warning(
                    "Preserving stale temporary table %s with incompatible schema for merge during message_map rebuild",
                    _temp_table,
                )
                cursor.execute(_RENAME_MESSAGE_MAP_TEMP_TO_STALE_TEMP_SQL)
                temp_exists = False
                stale_temp_exists = True
            cursor.execute(_RENAME_MESSAGE_MAP_TO_TEMP_SQL)
            cursor.execute(_CREATE_TABLE_MESSAGE_MAP_FROM_SCRATCH_SQL)
            if meshnet_column:
                insert_sql = _INSERT_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL.replace(
                    "message_map_legacy", _temp_table
                )
                if "message_map_legacy" in insert_sql:
                    raise RuntimeError("SQL replacement failed")
                cursor.execute(insert_sql)
            else:
                insert_sql = _INSERT_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL.replace(
                    "message_map_legacy", _temp_table
                )
                if "message_map_legacy" in insert_sql:
                    raise RuntimeError("SQL replacement failed")
                cursor.execute(insert_sql)
            if legacy_exists:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (_legacy_table,),
                )
                if cursor.fetchone():
                    cursor.execute(_PRAGMA_MESSAGE_MAP_LEGACY_INFO_SQL)
                    legacy_columns_local = {
                        column[1]: column for column in cursor.fetchall()
                    }
                    if _col_mesh in legacy_columns_local:
                        cursor.execute(
                            _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITH_MESH_SQL
                        )
                    else:
                        cursor.execute(
                            _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_LEGACY_WITHOUT_MESH_SQL
                        )
            cursor.execute(_DROP_TABLE_MESSAGE_MAP_TEMP_SQL)
            if stale_temp_exists:
                merged_stale_temp = False
                try:
                    cursor.execute(_PRAGMA_MESSAGE_MAP_STALE_TEMP_INFO_SQL)
                    stale_columns = {col[1] for col in cursor.fetchall()}
                    if _col_mesh in stale_columns:
                        cursor.execute(
                            _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_STALE_TEMP_WITH_MESH_SQL
                        )
                    else:
                        cursor.execute(
                            _INSERT_OR_IGNORE_MESSAGE_MAP_FROM_STALE_TEMP_WITHOUT_MESH_SQL
                        )
                    merged_stale_temp = True
                    logger.info(
                        "Merged rows from preserved stale temporary table into rebuilt message_map"
                    )
                except sqlite3.Error as e:
                    logger.warning(
                        "Failed to merge preserved stale temporary table data: %s", e
                    )
                if merged_stale_temp:
                    cursor.execute(_DROP_TABLE_MESSAGE_MAP_STALE_TEMP_SQL)
            cursor.execute(_DROP_TABLE_MESSAGE_MAP_LEGACY_SQL)

        cursor.execute(_CREATE_INDEX_MESSAGE_MAP_ID_SQL)

    try:
        manager.run_sync(_initialize, write=True)
    except sqlite3.Error:
        logger.exception("Database initialization failed")
        raise


def store_plugin_data(plugin_name: str, meshtastic_id: int | str, data: Any) -> None:
    """
    Store or update JSON-serializable plugin data for a given plugin and Meshtastic node.

    The provided `data` is JSON-serialized and written to the `plugin_data` table keyed by `plugin_name` and the string form of `meshtastic_id`. If `data` is not JSON-serializable the function logs the error and does not write to the database.

    Parameters:
        plugin_name (str): The name of the plugin.
        meshtastic_id (int | str): The Meshtastic node identifier; it is converted to a string for storage.
        data (Any): The plugin data to be serialized and stored.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    # Serialize payload up front to surface JSON errors before opening a write txn
    try:
        payload = json.dumps(data)
    except (TypeError, ValueError):
        logger.exception(
            "Plugin data for %s/%s is not JSON-serializable", plugin_name, meshtastic_id
        )
        return

    def _store(cursor: sqlite3.Cursor) -> None:
        """
        Upserts JSON-serialized plugin data for a plugin and Meshtastic node using the provided DB cursor.

        Uses `plugin_name`, `id_key`, and `payload` from the enclosing scope to insert a new row into `plugin_data` or update the existing row on conflict.

        Parameters:
            cursor (sqlite3.Cursor): Open database cursor used to execute the insert/update statement.
        """
        cursor.execute(_UPSERT_PLUGIN_DATA_SQL, (plugin_name, id_key, payload))

    try:
        manager.run_sync(_store, write=True)
    except sqlite3.Error:
        logger.exception(
            "Database error storing plugin data for %s, %s",
            plugin_name,
            meshtastic_id,
        )


def delete_plugin_data(plugin_name: str, meshtastic_id: int | str) -> None:
    """
    Remove a plugin data entry for a Meshtastic node.

    Parameters:
        plugin_name (str): Name of the plugin whose data should be deleted.
        meshtastic_id (int | str): Meshtastic node ID to delete data for; this value is converted to a string key for the database lookup.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _delete(cursor: sqlite3.Cursor) -> None:
        """
        Delete the plugin_data row for the current plugin and node id using the provided database cursor.

        Parameters:
            cursor (sqlite3.Cursor): Cursor on which the DELETE is executed; must be part of the caller's transaction.
        """
        cursor.execute(_DELETE_PLUGIN_DATA_SQL, (plugin_name, id_key))

    try:
        manager.run_sync(_delete, write=True)
    except sqlite3.Error:
        logger.exception(
            "Database error deleting plugin data for %s, %s",
            plugin_name,
            meshtastic_id,
        )


def get_plugin_data_for_node(plugin_name: str, meshtastic_id: int | str) -> Any:
    """
    Retrieve the JSON-serialized value for a plugin and Meshtastic node.

    If no row exists or a database/decoding error occurs, returns an empty list as a fallback.

    Parameters:
        plugin_name (str): Name of the plugin.
        meshtastic_id (int | str): Identifier of the Meshtastic node; will be normalized to a string.

    Returns:
        Any: The deserialized JSON value (may be dict, list, scalar, etc.) for the given plugin and node, or `[]` if none is stored or on error.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Fetches the `data` column for the current plugin and node using the provided cursor.

        Returns:
            `tuple[Any, ...]` with the `data` column for the matched row, or `None` if no row matches.
        """
        cursor.execute(_GET_PLUGIN_DATA_SQL, (plugin_name, id_key))
        return cast(tuple[Any, ...] | None, cursor.fetchone())

    try:
        result = manager.run_sync(_fetch)
    except (MemoryError, sqlite3.Error):
        logger.exception(
            "Database error retrieving plugin data for %s, node %s",
            plugin_name,
            meshtastic_id,
        )
        return []

    try:
        return json.loads(result[0] if result else "[]")
    except (json.JSONDecodeError, TypeError):
        logger.exception(
            "Failed to decode JSON data for plugin %s, node %s",
            plugin_name,
            meshtastic_id,
        )
        return []


def get_plugin_data(plugin_name: str) -> list[tuple[Any, ...]]:
    """
    Retrieve all stored plugin data rows for a given plugin.

    Parameters:
        plugin_name (str): Name of the plugin to query.

    Returns:
        list[tuple]: Rows matching the plugin; each row is a single-item tuple containing the stored JSON string from the `data` column.
    """
    manager = _get_db_manager()

    def _fetch_all(cursor: sqlite3.Cursor) -> list[tuple[Any, ...]]:
        """
        Fetch all `data` values from the `plugin_data` table for the current `plugin_name` and return them as rows.

        The function executes "SELECT data FROM plugin_data WHERE plugin_name=?" using a `plugin_name` value captured from the enclosing scope and returns the query results.

        Parameters:
            cursor (sqlite3.Cursor): Cursor used to execute the SELECT query.

        Returns:
            list[tuple[Any, ...]]: List of rows; each row is a single-item tuple containing the stored `data` value.
        """
        cursor.execute(_GET_ALL_PLUGIN_DATA_SQL, (plugin_name,))
        return cursor.fetchall()

    try:
        result = manager.run_sync(_fetch_all)
    except (MemoryError, sqlite3.Error):
        logger.exception(
            "Database error retrieving all plugin data for %s", plugin_name
        )
        return []

    return cast(list[tuple[Any, ...]], result)


def get_longname(meshtastic_id: int | str) -> str | None:
    """
    Get the stored long name for a Meshtastic node.

    Parameters:
        meshtastic_id (int | str): Meshtastic node identifier; numeric IDs are accepted and will be stringified.

    Returns:
        str | None: The long name if present, `None` if no entry exists or a database error occurs.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Fetches the first row from the given cursor's result set.

        Parameters:
            cursor (sqlite3.Cursor): A cursor whose query has already been executed and is positioned to read results.

        Returns:
            tuple[Any, ...] | None: The first row as a tuple, or `None` if no row is available.
        """
        cursor.execute(_SELECT_LONGNAME_BY_ID_SQL, (id_key,))
        return cast(tuple[Any, ...] | None, cursor.fetchone())

    try:
        result = manager.run_sync(_fetch)
        return result[0] if result else None
    except sqlite3.Error:
        logger.exception("Database error retrieving longname for %s", meshtastic_id)
        return None


def save_longname(meshtastic_id: int | str, longname: str) -> bool:
    """
    Normalize a node ID and upsert its long display name.

    Parameters:
        meshtastic_id (int | str): Identifier of the Meshtastic node; stored as a
            string key.
        longname (str): Full display name to store for the node.

    Returns:
        bool: True if the save was successful, False if a database error occurred.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _store(cursor: sqlite3.Cursor) -> None:
        """
        Insert or update the longname for a Meshtastic ID using the provided database cursor.

        This executes an upsert into the `longnames` table for `id_key` with `longname` taken from the enclosing scope.
        """
        cursor.execute(
            _UPSERT_NAME_SQL_BY_TABLE[NAMES_TABLE_LONGNAMES],
            (id_key, longname),
        )

    try:
        manager.run_sync(_store, write=True)
    except sqlite3.Error:
        logger.exception("Database error saving longname for %s", meshtastic_id)
        return False
    else:
        return True


def _delete_name_by_id(table: str, meshtastic_id: int | str) -> bool:
    """
    Delete one names-table row by Meshtastic ID.

    Parameters:
        table (str): Names table identifier (`longnames` or `shortnames`).
        meshtastic_id (int | str): Node identifier to delete.

    Returns:
        bool: True when deletion succeeded, False on database errors.
    """
    delete_sql = _DELETE_STALE_ID_SQL_BY_TABLE.get(table)
    if delete_sql is None:
        raise _InvalidNamesTableError(table)

    name_type = _NAME_FIELD_BY_TABLE.get(table, table)
    id_key = str(meshtastic_id)
    manager = _get_db_manager()

    def _delete(cursor: sqlite3.Cursor) -> None:
        cursor.execute(delete_sql, (id_key,))

    try:
        manager.run_sync(_delete, write=True)
    except sqlite3.Error:
        logger.exception("Database error deleting %s for %s", name_type, meshtastic_id)
        return False
    else:
        return True


def delete_longname(meshtastic_id: int | str) -> bool:
    """
    Delete one longname row for the given Meshtastic ID.

    Parameters:
        meshtastic_id (int | str): Node identifier to delete.

    Returns:
        bool: True when deletion succeeded, False on database errors.
    """
    return _delete_name_by_id(NAMES_TABLE_LONGNAMES, meshtastic_id)


def delete_shortname(meshtastic_id: int | str) -> bool:
    """
    Delete one shortname row for the given Meshtastic ID.

    Parameters:
        meshtastic_id (int | str): Node identifier to delete.

    Returns:
        bool: True when deletion succeeded, False on database errors.
    """
    return _delete_name_by_id(NAMES_TABLE_SHORTNAMES, meshtastic_id)


def _normalize_node_name_value(name_value: Any) -> str | None:
    """
    Normalize a node-name value to a string suitable for state comparison.

    Parameters:
        name_value (Any): Raw long/short name value from node data.

    Returns:
        str | None: Normalized string value, or None when value is absent/empty.
    """
    if name_value is None:
        return None
    if not isinstance(name_value, str):
        logger.warning(
            "Ignoring non-string node-name value of type %s",
            type(name_value).__name__,
        )
        return None
    return name_value or None


def _read_name_values_for_ids(
    table: str, current_ids: set[str]
) -> dict[str, str | None] | None:
    """
    Read current name-table values for the supplied Meshtastic IDs.

    Parameters:
        table (str): Names table identifier (`longnames` or `shortnames`).
        current_ids (set[str]): Meshtastic IDs to fetch.

    Returns:
        dict[str, str | None] | None: Mapping of ID -> normalized table value for
        rows that currently exist in the table; `None` on database errors.

    Notes:
        Uses a json_each() batched query when available, and falls back to
        chunked IN-clause parameterized selects on runtimes without JSON1 support.
    """
    if not current_ids:
        return {}

    column_name = _NAME_FIELD_BY_TABLE.get(table)
    select_sql = _SELECT_NAME_VALUES_SQL_BY_TABLE.get(table)
    select_in_prefix_sql = _SELECT_NAME_VALUES_IN_PREFIX_SQL_BY_TABLE.get(table)
    if column_name is None or select_sql is None or select_in_prefix_sql is None:
        raise _InvalidNamesTableError(table)

    manager = _get_db_manager()
    sorted_ids = sorted(current_ids)
    supports_json_each = manager.supports_json_each()

    def _fetch(cursor: sqlite3.Cursor) -> dict[str, str | None]:
        rows_by_id: dict[str, str | None] = {}

        for offset in range(0, len(sorted_ids), DEFAULT_NAME_PRUNE_CHUNK_SIZE):
            chunk_ids = sorted_ids[offset : offset + DEFAULT_NAME_PRUNE_CHUNK_SIZE]
            fetched_rows: list[tuple[Any, Any]]
            if supports_json_each:
                cursor.execute(
                    select_sql,
                    (json.dumps(chunk_ids),),
                )
                fetched_rows = cast(list[tuple[Any, Any]], cursor.fetchall())
            else:
                placeholders = ",".join("?" for _ in chunk_ids)
                select_in_sql = f"{select_in_prefix_sql}{placeholders})"
                cursor.execute(select_in_sql, tuple(chunk_ids))
                fetched_rows = cast(list[tuple[Any, Any]], cursor.fetchall())
            rows_by_id.update(
                {
                    str(row[0]): _normalize_node_name_value(row[1])
                    for row in fetched_rows
                }
            )
        return rows_by_id

    try:
        rows = manager.run_sync(_fetch)
        return cast(dict[str, str | None], rows)
    except sqlite3.Error:
        logger.exception(
            "Database error reading %s values for drift check", column_name
        )
        return None


def _name_table_matches_state(
    state: NodeNameState,
    *,
    table: str,
    get_name: Callable[[NodeNameEntry], str | None],
) -> bool:
    """
    Check whether one names table currently matches the expected node-name state.

    Parameters:
        state (NodeNameState): Expected normalized node-name snapshot.
        table (str): Names table identifier (`longnames` or `shortnames`).
        get_name (Callable[[NodeNameEntry], str | None]): Function to extract
            the relevant name field from a NodeNameEntry.

    Returns:
        bool: True when database rows for current IDs match expected normalized
        values; otherwise False.
    """
    current_ids = {entry.meshtastic_id for entry in state}
    actual_by_id = _read_name_values_for_ids(table, current_ids)
    if actual_by_id is None:
        return False

    for state_row in state:
        id_key = state_row.meshtastic_id
        expected_value = get_name(state_row)
        if expected_value is None:
            # None means "unknown / preserve existing" in partial snapshots
            continue

        if id_key not in actual_by_id:
            return False

        actual_value = actual_by_id[id_key]
        if actual_value is None or actual_value == "":
            return False
        if expected_value != actual_value:
            return False
    return True


def _name_tables_match_state(state: NodeNameState) -> bool:
    """
    Check whether both longname and shortname tables match the expected state.

    Parameters:
        state (NodeNameState): Expected normalized node-name snapshot.

    Returns:
        bool: True when both names tables match state for all current IDs.
    """
    if not state:
        return True
    return _name_table_matches_state(
        state,
        table=NAMES_TABLE_LONGNAMES,
        get_name=lambda entry: entry.long_name,
    ) and _name_table_matches_state(
        state,
        table=NAMES_TABLE_SHORTNAMES,
        get_name=lambda entry: entry.short_name,
    )


def _collect_node_name_snapshot(
    nodes: dict[str, Any] | None,
) -> tuple[NodeNameState, set[str], bool]:
    """
    Build a normalized node-name state snapshot and metadata from node data.

    Parameters:
        nodes (dict[str, Any] | None): Current Meshtastic nodes snapshot.

    Returns:
        tuple[NodeNameState, set[str], bool]:
            - Normalized sorted node-name state
            - Current Meshtastic IDs extracted from the snapshot
            - Snapshot completeness flag used for safe stale-row pruning
    """
    if nodes is None or not isinstance(nodes, dict):
        return (), set(), False
    if not nodes:
        return (), set(), True

    snapshot_complete = True
    current_ids: set[str] = set()
    state_by_id: dict[str, tuple[str | None, str | None]] = {}
    skipped_ids: set[str] = set()

    for node in nodes.values():
        if not isinstance(node, dict):
            snapshot_complete = False
            continue

        user = node.get("user")
        if not isinstance(user, dict):
            snapshot_complete = False
            continue

        meshtastic_id = user.get("id")
        if meshtastic_id is None:
            logger.debug("Skipping node-name snapshot entry because user.id is missing")
            snapshot_complete = False
            continue
        if isinstance(meshtastic_id, bool) or not isinstance(meshtastic_id, (str, int)):
            logger.debug(
                "Skipping node-name snapshot entry because user.id has invalid type %s",
                type(meshtastic_id).__name__,
            )
            snapshot_complete = False
            continue
        if isinstance(meshtastic_id, str) and meshtastic_id == "":
            logger.debug("Skipping node-name snapshot entry because user.id is empty")
            snapshot_complete = False
            continue

        id_key = str(meshtastic_id)
        if id_key in skipped_ids:
            continue
        current_ids.add(id_key)

        raw_long_name = user.get(PROTO_NODE_NAME_LONG)
        raw_short_name = user.get(PROTO_NODE_NAME_SHORT)
        if raw_long_name is not None and not isinstance(raw_long_name, str):
            logger.warning(
                "Skipping %s for %s due to non-string value type %s",
                PROTO_NODE_NAME_LONG,
                id_key,
                type(raw_long_name).__name__,
            )
            snapshot_complete = False
            raw_long_name = None
        if raw_short_name is not None and not isinstance(raw_short_name, str):
            logger.warning(
                "Skipping %s for %s due to non-string value type %s",
                PROTO_NODE_NAME_SHORT,
                id_key,
                type(raw_short_name).__name__,
            )
            snapshot_complete = False
            raw_short_name = None

        long_name = _normalize_node_name_value(raw_long_name)
        short_name = _normalize_node_name_value(raw_short_name)

        existing_entry = state_by_id.get(id_key)
        if existing_entry is None:
            state_by_id[id_key] = (long_name, short_name)
            continue

        # Duplicate IDs can appear in transient snapshots. Merge deterministically
        # so state and DB writes are order-independent.
        merged_long_name = _merge_node_name_values(existing_entry[0], long_name)
        merged_short_name = _merge_node_name_values(existing_entry[1], short_name)

        long_name_conflict = merged_long_name is _CONFLICT_SENTINEL
        short_name_conflict = merged_short_name is _CONFLICT_SENTINEL
        if long_name_conflict and short_name_conflict:
            logger.warning(
                "Skipping node %s due to conflicting duplicate %s/%s values in snapshot",
                id_key,
                PROTO_NODE_NAME_LONG,
                PROTO_NODE_NAME_SHORT,
            )
            snapshot_complete = False
            state_by_id.pop(id_key, None)
            skipped_ids.add(id_key)
            continue
        if long_name_conflict or short_name_conflict:
            snapshot_complete = False
            if long_name_conflict:
                logger.warning(
                    "Ignoring conflicting duplicate %s values for node %s",
                    PROTO_NODE_NAME_LONG,
                    id_key,
                )
            if short_name_conflict:
                logger.warning(
                    "Ignoring conflicting duplicate %s values for node %s",
                    PROTO_NODE_NAME_SHORT,
                    id_key,
                )
            resolved_long_name = (
                None if long_name_conflict else cast(str | None, merged_long_name)
            )
            resolved_short_name = (
                None if short_name_conflict else cast(str | None, merged_short_name)
            )
            # If no unambiguous field remains for this ID, preserve existing DB state
            # by skipping updates for this snapshot cycle.
            if resolved_long_name is None and resolved_short_name is None:
                state_by_id.pop(id_key, None)
                skipped_ids.add(id_key)
                continue
            state_by_id[id_key] = (resolved_long_name, resolved_short_name)
            continue
        state_by_id[id_key] = cast(
            tuple[str | None, str | None],
            (merged_long_name, merged_short_name),
        )

    state_entries = [
        NodeNameEntry(id_key, long_name, short_name)
        for id_key, (long_name, short_name) in state_by_id.items()
    ]
    state_entries.sort(key=lambda entry: entry.meshtastic_id)
    return tuple(state_entries), current_ids, snapshot_complete


def _merge_node_name_values(
    existing_value: str | None, incoming_value: str | None
) -> str | None | object:
    """
    Merge duplicate-ID name values into a deterministic canonical value.

    When both values are non-empty and different, returns _CONFLICT_SENTINEL
    to signal that the conflict cannot be resolved safely and the field
    should be skipped for this ID.
    """
    if existing_value is None:
        return incoming_value
    if incoming_value is None:
        return existing_value
    if existing_value == incoming_value:
        return existing_value
    return _CONFLICT_SENTINEL


def _sync_name_tables_atomic(
    state: NodeNameState,
    current_ids: set[str],
    snapshot_complete: bool,
) -> bool:
    """
    Persist longname/shortname rows for one snapshot in a single transaction.

    This keeps names tables consistent if any write fails mid-sync.

    Parameters:
        state (NodeNameState): Precomputed node-name state snapshot.
        current_ids (set[str]): Precomputed set of current Meshtastic IDs.
        snapshot_complete (bool): Precomputed snapshot completeness flag.
    """
    manager = _get_db_manager()
    long_delete_sql = _DELETE_STALE_ID_SQL_BY_TABLE[NAMES_TABLE_LONGNAMES]
    short_delete_sql = _DELETE_STALE_ID_SQL_BY_TABLE[NAMES_TABLE_SHORTNAMES]
    long_upsert_sql = _UPSERT_NAME_SQL_BY_TABLE[NAMES_TABLE_LONGNAMES]
    short_upsert_sql = _UPSERT_NAME_SQL_BY_TABLE[NAMES_TABLE_SHORTNAMES]
    long_upsert_ids: set[str] = set()
    short_upsert_ids: set[str] = set()
    long_clear_ids: set[str] = set()
    short_clear_ids: set[str] = set()
    stale_long_ids: set[str] = set()
    stale_short_ids: set[str] = set()

    def _sync(cursor: sqlite3.Cursor) -> None:
        for id_key, long_name, short_name in state:
            if long_name is None:
                if snapshot_complete:
                    cursor.execute(long_delete_sql, (id_key,))
                    long_clear_ids.add(id_key)
            else:
                cursor.execute(long_upsert_sql, (id_key, long_name))
                long_upsert_ids.add(id_key)

            if short_name is None:
                if snapshot_complete:
                    cursor.execute(short_delete_sql, (id_key,))
                    short_clear_ids.add(id_key)
            else:
                cursor.execute(short_upsert_sql, (id_key, short_name))
                short_upsert_ids.add(id_key)

        if snapshot_complete:
            _delete_stale_names_core(
                cursor,
                NAMES_TABLE_LONGNAMES,
                current_ids,
                deleted_ids=stale_long_ids,
            )
            _delete_stale_names_core(
                cursor,
                NAMES_TABLE_SHORTNAMES,
                current_ids,
                deleted_ids=stale_short_ids,
            )

    try:
        manager.run_sync(_sync, write=True)
    except sqlite3.Error:
        logger.exception("Database error syncing longname/shortname tables")
        return False
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(
            "Node-name DB sync applied: long_upserts=%d short_upserts=%d "
            "long_clears=%d short_clears=%d stale_long_pruned=%d "
            "stale_short_pruned=%d snapshot_complete=%s current_ids=%d",
            len(long_upsert_ids),
            len(short_upsert_ids),
            len(long_clear_ids),
            len(short_clear_ids),
            len(stale_long_ids),
            len(stale_short_ids),
            snapshot_complete,
            len(current_ids),
        )
        if long_upsert_ids:
            logger.debug(
                "Longname upsert IDs: %s", _format_node_id_sample(long_upsert_ids)
            )
        if short_upsert_ids:
            logger.debug(
                "Shortname upsert IDs: %s",
                _format_node_id_sample(short_upsert_ids),
            )
        if long_clear_ids:
            logger.debug(
                "Longname cleared IDs: %s",
                _format_node_id_sample(long_clear_ids),
            )
        if short_clear_ids:
            logger.debug(
                "Shortname cleared IDs: %s",
                _format_node_id_sample(short_clear_ids),
            )
        if stale_long_ids:
            logger.debug(
                "Stale longname pruned IDs: %s",
                _format_node_id_sample(stale_long_ids),
            )
        if stale_short_ids:
            logger.debug(
                "Stale shortname pruned IDs: %s",
                _format_node_id_sample(stale_short_ids),
            )
    return True


def _prune_stale_name_rows_atomic(current_ids: set[str]) -> bool:
    """
    Prune stale rows from both names tables in a single write transaction.

    Returns:
        bool: True when both table prunes succeed, False on database errors.
    """
    manager = _get_db_manager()

    def _prune(cursor: sqlite3.Cursor) -> None:
        _delete_stale_names_core(cursor, NAMES_TABLE_LONGNAMES, current_ids)
        _delete_stale_names_core(cursor, NAMES_TABLE_SHORTNAMES, current_ids)

    try:
        manager.run_sync(_prune, write=True)
    except sqlite3.Error:
        logger.exception(
            "Database error deleting stale %s/%s entries",
            NAMES_FIELD_LONGNAME,
            NAMES_FIELD_SHORTNAME,
        )
        return False
    return True


def build_node_name_state(nodes: dict[str, Any] | None) -> NodeNameState:
    """
    Build a deterministic node-name state snapshot used for change detection.

    Parameters:
        nodes (dict[str, Any] | None): Current Meshtastic nodes snapshot.

    Returns:
        NodeNameState: Sorted tuples of (meshtastic_id, longName, shortName).
    """
    state, _current_ids, _snapshot_complete = _collect_node_name_snapshot(nodes)
    return state


def sync_name_tables_if_changed(
    nodes: dict[str, Any] | None,
    previous_state: NodeNameState | None = None,
) -> NodeNameState | None:
    """
    Sync longname/shortname tables only when node-name state changes.

    When the state has not changed, this function still performs safe stale-row
    pruning for complete snapshots so periodic refresh keeps the DB aligned with
    current node IDs without rewriting unchanged names.

    Parameters:
        nodes (dict[str, Any] | None): Current Meshtastic nodes snapshot.
        previous_state (NodeNameState | None): Last successful state snapshot.

    Returns:
        NodeNameState | None: Current normalized state for the next iteration
        when writes succeed; otherwise the previous state to force retry on the
        next identical snapshot.
    """
    if nodes is None or not isinstance(nodes, dict):
        return previous_state

    # Empty snapshots should preserve prior state to avoid transient data loss
    # from temporary network disconnects or incomplete refreshes.
    # CRITICAL: When previous_state is None (first run after restart), we cannot
    # distinguish between a genuinely empty NodeDB and a transient empty snapshot.
    # We must NOT prune tables on first run - return None to defer pruning until
    # we have established a baseline state.
    if not nodes:
        if previous_state is not None:
            return previous_state
        return None

    current_state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
    # Empty snapshots are only authoritative if snapshot_complete=True from the collector.
    # Don't force snapshot_complete=True here - that allows transient empty cycles to
    # wipe tables. Let the collector's snapshot_complete value propagate naturally.

    # When snapshot is incomplete, preserve previous values for None fields to avoid
    # deleting existing DB rows due to invalid/incomplete data.
    if not snapshot_complete and previous_state is not None:
        previous_by_id = {e.meshtastic_id: e for e in previous_state}
        merged_entries: list[NodeNameEntry] = []
        for entry in current_state:
            prev = previous_by_id.pop(entry.meshtastic_id, None)
            if prev is not None:
                long_name = (
                    entry.long_name if entry.long_name is not None else prev.long_name
                )
                short_name = (
                    entry.short_name
                    if entry.short_name is not None
                    else prev.short_name
                )
                merged_entries.append(
                    NodeNameEntry(entry.meshtastic_id, long_name, short_name)
                )
            else:
                merged_entries.append(entry)
        merged_entries.extend(previous_by_id.values())
        merged_entries.sort(key=lambda e: e.meshtastic_id)
        current_state = tuple(merged_entries)

    # Non-authoritative empty states (for example conflict-only/invalid snapshots)
    # must not replace previous_state, otherwise a subsequent authoritative empty
    # snapshot could incorrectly prune all rows after a single transient cycle.
    if nodes != {} and not snapshot_complete and not current_state:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "Skipping non-authoritative empty node-name snapshot (nodes=%d)",
                len(nodes),
            )
        return previous_state

    if logger.isEnabledFor(logging.DEBUG):
        current_state_ids = {entry.meshtastic_id for entry in current_state}
        if previous_state is None:
            logger.debug(
                "Node-name snapshot initialized with %d IDs (snapshot_complete=%s): %s",
                len(current_state_ids),
                snapshot_complete,
                _format_node_id_sample(current_state_ids),
            )
        else:
            previous_state_ids = {entry.meshtastic_id for entry in previous_state}
            added_ids = current_state_ids - previous_state_ids
            removed_ids = previous_state_ids - current_state_ids
            if added_ids or removed_ids:
                logger.debug(
                    "Node-name snapshot ID delta: added=%d removed=%d "
                    "(snapshot_complete=%s) added_ids=%s removed_ids=%s",
                    len(added_ids),
                    len(removed_ids),
                    snapshot_complete,
                    _format_node_id_sample(added_ids),
                    _format_node_id_sample(removed_ids),
                )

    if previous_state is not None and current_state == previous_state:
        if snapshot_complete:
            if not _prune_stale_name_rows_atomic(current_ids):
                return previous_state
        # For partial snapshots, equal current/previous state means we intentionally
        # avoid stale-row pruning. A non-authoritative partial view can miss IDs, so
        # pruning here could delete valid rows. Drift repair still runs below.
        if not _name_tables_match_state(current_state):
            if not _sync_name_tables_atomic(
                current_state, current_ids, snapshot_complete
            ):
                return previous_state
        return current_state

    if _sync_name_tables_atomic(current_state, current_ids, snapshot_complete):
        return current_state
    return previous_state


def _update_names_core(
    nodes: dict[str, Any],
    *,
    name_key: str,
    save_name: Callable[[str, str], bool],
    delete_name: Callable[[str], bool],
    delete_stale_names: Callable[[set[str]], int | None],
) -> bool:
    """
    Persist one user name field from a node snapshot and prune stale rows.

    Stale-name pruning runs only when every node in the snapshot has a usable
    `user.id` AND all name saves succeeded. If any node is present without enough
    identity data, or if any save operation fails, existing names are preserved
    rather than risking false deletions from an incomplete snapshot.

    .. note::

        This function performs updates non-atomically. Each call to ``save_name``
        or ``delete_name`` initiates a separate database transaction. If an error
        occurs midway through the loop, the database could be left in a partially
        updated state. For atomic updates, use :func:`_sync_name_tables_atomic`
        instead.

    Parameters:
        nodes (dict[str, Any]): Snapshot of node records containing optional
            `user` dictionaries.
        name_key (str): Protocol user field to read (`"longName"` or
            `"shortName"`).
        save_name (Callable[[str, str], bool]): Function used to persist one name.
            Returns True on success, False on failure.
        delete_name (Callable[[str], bool]): Function used to delete one row for
            a Meshtastic ID when the normalized name is absent/empty.
        delete_stale_names (Callable[[set[str]], int]): Function used to delete
            rows whose Meshtastic IDs are absent from the snapshot.

    Returns:
        bool: True when all save operations succeeded; False if any save failed.
    """
    if not nodes:
        return True

    if name_key not in _DB_COLUMN_BY_PROTO_NODE_NAME_FIELD:
        raise ValueError(f"Unsupported node name key: {name_key}")

    def _collect_single_name_snapshot(
        node_rows: dict[str, Any],
    ) -> tuple[dict[str, str | None], set[str], bool]:
        """
        Collect one name-field snapshot without depending on the sibling field.

        This allows update_longnames()/update_shortnames() to proceed for a valid
        field even when the other field is malformed or conflicting.
        """
        snapshot_complete = True
        current_ids: set[str] = set()
        state_by_id: dict[str, str | None] = {}
        skipped_ids: set[str] = set()

        for node in node_rows.values():
            if not isinstance(node, dict):
                snapshot_complete = False
                continue

            user = node.get("user")
            if not isinstance(user, dict):
                snapshot_complete = False
                continue

            meshtastic_id = user.get("id")
            if meshtastic_id is None:
                logger.debug(
                    "Skipping node-name snapshot entry because user.id is missing"
                )
                snapshot_complete = False
                continue
            if isinstance(meshtastic_id, bool) or not isinstance(
                meshtastic_id,
                (str, int),
            ):
                logger.debug(
                    "Skipping node-name snapshot entry because user.id has invalid type %s",
                    type(meshtastic_id).__name__,
                )
                snapshot_complete = False
                continue
            if isinstance(meshtastic_id, str) and meshtastic_id == "":
                logger.debug(
                    "Skipping node-name snapshot entry because user.id is empty"
                )
                snapshot_complete = False
                continue

            id_key = str(meshtastic_id)
            if id_key in skipped_ids:
                continue
            current_ids.add(id_key)

            raw_name = user.get(name_key)
            if raw_name is not None and not isinstance(raw_name, str):
                logger.warning(
                    "Skipping %s update for %s due to non-string value type %s",
                    name_key,
                    id_key,
                    type(raw_name).__name__,
                )
                snapshot_complete = False
                continue

            normalized_name = _normalize_node_name_value(raw_name)
            existing_name = state_by_id.get(id_key)
            if existing_name is None:
                state_by_id[id_key] = normalized_name
                continue

            merged_name = _merge_node_name_values(existing_name, normalized_name)
            if merged_name is _CONFLICT_SENTINEL:
                logger.warning(
                    "Skipping %s update for %s due to conflicting duplicate values",
                    name_key,
                    id_key,
                )
                snapshot_complete = False
                state_by_id.pop(id_key, None)
                skipped_ids.add(id_key)
                continue

            state_by_id[id_key] = cast(str | None, merged_name)

        return state_by_id, current_ids, snapshot_complete

    state_by_id, current_ids, snapshot_complete = _collect_single_name_snapshot(nodes)
    all_saves_ok = True
    for id_key, normalized_name in state_by_id.items():
        if normalized_name is None:
            if snapshot_complete and not delete_name(id_key):
                all_saves_ok = False
            continue
        if not save_name(id_key, normalized_name):
            all_saves_ok = False

    if current_ids and snapshot_complete and all_saves_ok:
        stale_delete_count = delete_stale_names(current_ids)
        if stale_delete_count is None:
            all_saves_ok = False
    return all_saves_ok


def update_longnames(nodes: dict[str, Any]) -> bool:
    """
    Persist each node's `longName` and prune stale longname rows.

    Pruning runs only when the supplied snapshot has a usable `user.id` for
    every node.

    Parameters:
        nodes (dict[str, Any]): Mapping of node identifiers to node dictionaries;
            each node may expose a `user` dict with `id` and `longName`.

    Returns:
        bool: True when longname writes succeeded for all rows attempted; False
        when any write failed.
    """
    return _update_names_core(
        nodes,
        name_key=PROTO_NODE_NAME_LONG,
        save_name=save_longname,
        delete_name=delete_longname,
        delete_stale_names=lambda current_ids: _delete_stale_names(
            NAMES_TABLE_LONGNAMES,
            current_ids,
            return_none_on_error=True,
        ),
    )


def get_shortname(meshtastic_id: int | str) -> str | None:
    """
    Retrieve the short display name for a Meshtastic node.

    Parameters:
        meshtastic_id (int | str): Meshtastic node identifier used to look up short name.

    Returns:
        str | None: The shortname string if present in the database, `None` if not found or on database error.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Retrieve the first shortname row for the normalized Meshtastic ID captured in the enclosing scope.

        Executes a SELECT to fetch the `shortname` from the `shortnames` table for the normalized ID and returns the first matching row.

        Returns:
            tuple[Any, ...] | None: The first row returned by the query (typically a single-item tuple containing the `shortname`), or `None` if no row is found.
        """
        cursor.execute(_SELECT_SHORTNAME_BY_ID_SQL, (id_key,))
        return cast(tuple[Any, ...] | None, cursor.fetchone())

    try:
        result = manager.run_sync(_fetch)
        return result[0] if result else None
    except sqlite3.Error:
        logger.exception("Database error retrieving shortname for %s", meshtastic_id)
        return None


def save_shortname(meshtastic_id: int | str, shortname: str) -> bool:
    """
    Insert or update the shortname for a Meshtastic node.

    Stores the provided `shortname` in the `shortnames` table keyed by `meshtastic_id`. Database errors are logged (with stacktrace) and suppressed; the function does not raise on sqlite3 errors.

    Parameters:
        meshtastic_id (int | str): Node identifier used as the primary key in the shortnames table.
        shortname (str): Display name to store for the node.

    Returns:
        bool: True if the save was successful, False if a database error occurred.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _store(cursor: sqlite3.Cursor) -> None:
        """
        Upserts the shortname for the captured Meshtastic ID into the shortnames table using the provided database cursor.
        """
        cursor.execute(
            _UPSERT_NAME_SQL_BY_TABLE[NAMES_TABLE_SHORTNAMES],
            (id_key, shortname),
        )

    try:
        manager.run_sync(_store, write=True)
    except sqlite3.Error:
        logger.exception("Database error saving shortname for %s", meshtastic_id)
        return False
    else:
        return True


def _delete_stale_names_core(
    cursor: sqlite3.Cursor,
    table: str,
    current_ids: set[str],
    *,
    deleted_ids: set[str] | None = None,
) -> int:
    """
    Delete rows whose `meshtastic_id` is missing from the current node snapshot.

    Uses fixed per-table SQL statements selected from allow-lists so stale
    pruning remains parameterized without interpolating SQL identifiers at
    runtime.

    Parameters:
        cursor (sqlite3.Cursor): Database cursor used to execute the delete.
        table (str): Table name (`"longnames"` or `"shortnames"`).
        current_ids (set[str]): Set of Meshtastic node IDs that should be kept.

    Returns:
        int: Number of rows deleted.

    Raises:
        _InvalidNamesTableError: If `table` is not a supported names table.
    """
    select_sql = _SELECT_STALE_IDS_SQL_BY_TABLE.get(table)
    delete_sql = _DELETE_STALE_ID_SQL_BY_TABLE.get(table)
    if select_sql is None or delete_sql is None:
        raise _InvalidNamesTableError(table)

    # Fetch all existing IDs from the database
    cursor.execute(select_sql)
    all_db_ids = {row[0] for row in cursor.fetchall()}

    # Compute stale IDs (those in DB but not in current snapshot)
    stale_ids = tuple(all_db_ids - current_ids)

    if not stale_ids:
        return 0

    # Delete stale IDs in batches to avoid SQLite parameter limits.
    # Use a fixed, parameterized delete statement selected from the allow-list.
    total_deleted = 0
    chunk_size = DEFAULT_NAME_PRUNE_CHUNK_SIZE
    for i in range(0, len(stale_ids), chunk_size):
        chunk = stale_ids[i : i + chunk_size]
        cursor.executemany(delete_sql, ((stale_id,) for stale_id in chunk))
        if deleted_ids is not None:
            deleted_ids.update(chunk)
        total_deleted += cursor.rowcount

    return total_deleted


def _delete_stale_names(
    table_name: str,
    current_ids: set[str],
    *,
    return_none_on_error: bool = False,
) -> int | None:
    """
    Remove name entries for nodes no longer in the device's nodedb.

    Parameters:
        table_name (str): The name of the table to prune ('longnames' or 'shortnames').
        current_ids (set[str]): Set of Meshtastic node IDs currently known to the device.

    Returns:
        int | None: Number of stale entries removed, or `None` when
        `return_none_on_error=True` and a database error occurs.
    """
    manager = _get_db_manager()
    name_type = _NAME_FIELD_BY_TABLE.get(table_name, table_name)

    def _delete(cursor: sqlite3.Cursor) -> int:
        """
        Delete stale name rows using the bound table name and current ID set.
        """
        return _delete_stale_names_core(cursor, table_name, current_ids)

    try:
        deleted = manager.run_sync(_delete, write=True)
    except sqlite3.Error:
        logger.exception("Database error deleting stale %s entries", name_type)
        return None if return_none_on_error else 0
    else:
        deleted_count = cast(int, deleted)
        if deleted_count > 0:
            logger.debug("Removed %d stale %s entries", deleted_count, name_type)
        return deleted_count


def delete_stale_longnames(current_ids: set[str]) -> int:
    """
    Delete stored long names for nodes absent from the current snapshot.

    This is a low-level prune helper. Passing an empty set intentionally
    removes all longname rows. Most callers should prefer `update_longnames()`,
    which preserves existing rows when the node snapshot is empty or incomplete.

    Parameters:
        current_ids (set[str]): Set of Meshtastic node IDs currently known to the
            device.

    Returns:
        int: Number of rows removed from the longnames table.
    """
    deleted = _delete_stale_names(NAMES_TABLE_LONGNAMES, current_ids)
    return 0 if deleted is None else deleted


def delete_stale_shortnames(current_ids: set[str]) -> int:
    """
    Remove short name entries for nodes no longer in the device's nodedb.

    This is a low-level prune helper. Passing an empty set intentionally
    removes all shortname rows. Most callers should prefer `update_shortnames()`,
    which preserves existing rows when the node snapshot is empty or incomplete.

    Parameters:
        current_ids (set[str]): Set of Meshtastic node IDs currently known to the device.

    Returns:
        int: Number of stale entries removed.
    """
    deleted = _delete_stale_names(NAMES_TABLE_SHORTNAMES, current_ids)
    return 0 if deleted is None else deleted


def update_shortnames(nodes: dict[str, Any]) -> bool:
    """
    Update persisted short names for nodes that include a user object.

    For each node in the provided mapping, if the node contains `user["id"]`, the
    normalized `user["shortName"]` value is applied to the corresponding shortnames
    row. When `user["shortName"]` normalizes to `None`, the existing shortnames row
    for that Meshtastic ID is deleted.

    After updating, removes stale entries from the database only when every node
    in the snapshot has a usable `user["id"]`.

    Parameters:
        nodes (dict[str, Any]): Mapping of node identifiers to node objects; nodes without a `user` entry are ignored.

    Returns:
        bool: True when shortname writes succeeded for all rows attempted; False
        when any write failed.
    """
    return _update_names_core(
        nodes,
        name_key=PROTO_NODE_NAME_SHORT,
        save_name=save_shortname,
        delete_name=delete_shortname,
        delete_stale_names=lambda current_ids: _delete_stale_names(
            NAMES_TABLE_SHORTNAMES,
            current_ids,
            return_none_on_error=True,
        ),
    )


def _store_message_map_core(
    cursor: sqlite3.Cursor,
    meshtastic_id: str,
    matrix_event_id: str,
    matrix_room_id: str,
    meshtastic_text: str,
    meshtastic_meshnet: str | None = None,
) -> None:
    """
    Insert or update a mapping between a Meshtastic message (or node) and a Matrix event.

    Parameters:
        cursor (sqlite3.Cursor): Active database cursor used to execute the statement.
        meshtastic_id (str): Meshtastic message or node identifier (string-normalized).
        matrix_event_id (str): Matrix event ID to map to.
        matrix_room_id (str): Matrix room ID where the Matrix event resides.
        meshtastic_text (str): Text content of the Meshtastic message.
        meshtastic_meshnet (str | None): Optional meshnet flag or value associated with the Meshtastic message.
    """
    cursor.execute(
        _UPSERT_MESSAGE_MAP_SQL,
        (
            meshtastic_id,
            matrix_event_id,
            matrix_room_id,
            meshtastic_text,
            meshtastic_meshnet,
        ),
    )


def store_message_map(
    meshtastic_id: int | str,
    matrix_event_id: str,
    matrix_room_id: str,
    meshtastic_text: str,
    meshtastic_meshnet: str | None = None,
) -> None:
    """
    Persist a mapping between a Meshtastic message and a Matrix event.

    Parameters:
        meshtastic_id (int|str): Identifier of the Meshtastic message.
        matrix_event_id (str): Matrix event ID to associate with the Meshtastic message.
        matrix_room_id (str): Matrix room ID where the event was posted.
        meshtastic_text (str): Text content of the Meshtastic message.
        meshtastic_meshnet (str|None): Optional meshnet identifier associated with the message; stored when provided.
    """
    manager = _get_db_manager()
    # Normalize IDs to a consistent string form to match other DB helpers.
    id_key = str(meshtastic_id)

    try:
        logger.debug(
            "Storing message map: meshtastic_id=%s, matrix_event_id=%s, matrix_room_id=%s, meshtastic_text=%s, meshtastic_meshnet=%s",
            meshtastic_id,
            matrix_event_id,
            matrix_room_id,
            meshtastic_text,
            meshtastic_meshnet,
        )
        manager.run_sync(
            lambda cursor: _store_message_map_core(
                cursor,
                id_key,
                matrix_event_id,
                matrix_room_id,
                meshtastic_text,
                meshtastic_meshnet,
            ),
            write=True,
        )
    except sqlite3.Error:
        logger.exception("Database error storing message map for %s", matrix_event_id)


def get_message_map_by_meshtastic_id(
    meshtastic_id: int | str,
) -> tuple[str, str, str, str | None] | None:
    """
    Retrieve the Matrix event mapping for the given Meshtastic message ID.

    Returns:
        tuple[str, str, str, str | None] | None: A tuple (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) when a mapping exists, `None` otherwise.
    """
    manager = _get_db_manager()
    # Normalize IDs to a consistent string form to match other DB helpers.
    id_key = str(meshtastic_id)

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Retrieve the row from message_map for the current Meshtastic ID.

        Returns:
            `(matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)` tuple if a row exists, `None` otherwise.
        """
        cursor.execute(_GET_MESSAGE_MAP_BY_MESHTASTIC_ID_SQL, (id_key,))
        return cast(tuple[Any, ...] | None, cursor.fetchone())

    try:
        result = manager.run_sync(_fetch)
        logger.debug(
            "Retrieved message map by meshtastic_id=%s: %s", meshtastic_id, result
        )
        if not result:
            return None
        try:
            return result[0], result[1], result[2], result[3]
        except (IndexError, TypeError):
            logger.exception(
                "Malformed data in message_map for meshtastic_id %s",
                meshtastic_id,
            )
            return None
    except sqlite3.Error:
        logger.exception(
            "Database error retrieving message map for meshtastic_id %s",
            meshtastic_id,
        )
        return None


def get_message_map_by_matrix_event_id(
    matrix_event_id: str,
) -> tuple[str, str, str, str | None] | None:
    """
    Retrieve the mapping row for a given Matrix event ID.

    Parameters:
        matrix_event_id (str): Matrix event ID to look up.

    Returns:
        tuple[str, str, str, str | None] | None: A tuple (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if a matching row exists, `None` otherwise.
    """
    manager = _get_db_manager()

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Fetch a single row from message_map for the Matrix event ID taken from the enclosing scope.

        Parameters:
            cursor (sqlite3.Cursor): SQLite cursor used to execute the query.

        Returns:
            tuple[Any, ...] | None: Tuple (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if a matching row is found, `None` otherwise.
        """
        cursor.execute(_GET_MESSAGE_MAP_BY_MATRIX_EVENT_ID_SQL, (matrix_event_id,))
        return cast(tuple[Any, ...] | None, cursor.fetchone())

    try:
        result = manager.run_sync(_fetch)
        logger.debug(
            "Retrieved message map by matrix_event_id=%s: %s", matrix_event_id, result
        )
        if not result:
            return None
        try:
            return result[0], result[1], result[2], result[3]
        except (IndexError, TypeError):
            logger.exception(
                "Malformed data in message_map for matrix_event_id %s",
                matrix_event_id,
            )
            return None
    except (UnicodeDecodeError, sqlite3.Error):
        logger.exception(
            "Database error retrieving message map for matrix_event_id %s",
            matrix_event_id,
        )
        return None


def wipe_message_map() -> None:
    """
    Delete all rows from the message_map table.
    """
    manager = _get_db_manager()

    def _wipe(cursor: sqlite3.Cursor) -> None:
        """
        Delete all rows from the message_map table.

        Parameters:
            cursor (sqlite3.Cursor): Cursor used to execute the deletion.
        """
        cursor.execute(_DELETE_FROM_MESSAGE_MAP_SQL)

    try:
        manager.run_sync(_wipe, write=True)
        logger.info("message_map table wiped successfully.")
    except sqlite3.Error:
        logger.exception("Failed to wipe message_map")


def _prune_message_map_core(cursor: sqlite3.Cursor, msgs_to_keep: int) -> int:
    """
    Prune the message_map table to retain only the most recent msgs_to_keep rows.

    Returns:
        int: Number of rows deleted (0 if no rows were removed).
    """
    cursor.execute(_SELECT_COUNT_MESSAGE_MAP_SQL)
    row = cursor.fetchone()
    total = row[0] if row else 0

    if total > msgs_to_keep:
        to_delete = total - msgs_to_keep
        cursor.execute(_DELETE_OLDEST_MESSAGE_MAP_SQL, (to_delete,))
        return to_delete
    return 0


def prune_message_map(msgs_to_keep: int) -> None:
    """
    Prune the message_map table so only the most recent msgs_to_keep records remain.

    Parameters:
        msgs_to_keep (int): Maximum number of most-recent message_map rows to retain; older rows will be removed.
    """
    manager = _get_db_manager()

    try:
        pruned = manager.run_sync(
            lambda cursor: _prune_message_map_core(cursor, msgs_to_keep),
            write=True,
        )
        if pruned > 0:
            logger.info(
                "Pruned %s old message_map entries, keeping last %s.",
                pruned,
                msgs_to_keep,
            )
    except sqlite3.Error:
        logger.exception("Database error pruning message_map")


async def async_store_message_map(
    meshtastic_id: int | str,
    matrix_event_id: str,
    matrix_room_id: str,
    meshtastic_text: str,
    meshtastic_meshnet: str | None = None,
) -> None:
    """
    Persist a mapping between a Meshtastic message or node and a Matrix event.

    Parameters:
        meshtastic_id (int | str): Meshtastic message or node identifier.
        matrix_event_id (str): Matrix event ID to associate with the Meshtastic message.
        matrix_room_id (str): Matrix room ID where the event was posted.
        meshtastic_text (str): Text content of the Meshtastic message.
        meshtastic_meshnet (str | None): Optional meshnet identifier associated with the message.
    """
    # Normalize IDs to a consistent string form to match other DB helpers.
    id_key = str(meshtastic_id)

    try:
        logger.debug(
            "Storing message map: meshtastic_id=%s, matrix_event_id=%s, matrix_room_id=%s, meshtastic_text=%s, meshtastic_meshnet=%s",
            meshtastic_id,
            matrix_event_id,
            matrix_room_id,
            meshtastic_text,
            meshtastic_meshnet,
        )
        manager = await asyncio.to_thread(_get_db_manager)
        await manager.run_async(
            lambda cursor: _store_message_map_core(
                cursor,
                id_key,
                matrix_event_id,
                matrix_room_id,
                meshtastic_text,
                meshtastic_meshnet,
            ),
            write=True,
        )
    except sqlite3.Error:
        logger.exception("Database error storing message map for %s", matrix_event_id)


async def async_prune_message_map(msgs_to_keep: int) -> None:
    """
    Prune message_map to retain only the most recent msgs_to_keep rows.

    Parameters:
        msgs_to_keep (int): Number of most recent rows to retain; older rows will be deleted.
    """
    try:
        manager = await asyncio.to_thread(_get_db_manager)
        pruned = await manager.run_async(
            lambda cursor: _prune_message_map_core(cursor, msgs_to_keep),
            write=True,
        )
        if pruned > 0:
            logger.info(
                "Pruned %s old message_map entries, keeping last %s.",
                pruned,
                msgs_to_keep,
            )
    except sqlite3.Error:
        logger.exception("Database error pruning message_map")
