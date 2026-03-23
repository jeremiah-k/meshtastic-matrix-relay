import asyncio
import contextlib
import json
import logging
import os
import sqlite3
import threading
from collections.abc import Collection
from typing import Any, Callable, Dict, NamedTuple, Tuple, cast

from mmrelay.constants.database import (
    DEFAULT_BUSY_TIMEOUT_MS,
    DEFAULT_ENABLE_WAL,
    DEFAULT_EXTRA_PRAGMAS,
    DEFAULT_NAME_PRUNE_CHUNK_SIZE,
    NAMES_FIELD_LONGNAME,
    NAMES_FIELD_SHORTNAME,
    NAMES_TABLE_LONGNAMES,
    NAMES_TABLE_SHORTNAMES,
    PROTO_NODE_NAME_LONG,
    PROTO_NODE_NAME_SHORT,
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


# Global config variable that will be set from main.py
config = None

# Cache for database path to avoid repeated logging and path resolution
_cached_db_path = None
_db_path_logged = False
_cached_config_hash = None

# Database manager cache
_db_manager: DatabaseManager | None = None
_db_manager_signature: Tuple[str, bool, int, Tuple[Tuple[str, Any], ...]] | None = None
_db_manager_lock = threading.Lock()

logger = get_logger(name="db_utils")


class NodeNameEntry(NamedTuple):
    meshtastic_id: str
    long_name: str | None
    short_name: str | None


NodeNameState = tuple[NodeNameEntry, ...]

_CONFLICT_SENTINEL = object()
_NODE_NAME_DEBUG_ID_SAMPLE_LIMIT = 20

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

_SELECT_STALE_IDS_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: "SELECT meshtastic_id FROM longnames",
    NAMES_TABLE_SHORTNAMES: "SELECT meshtastic_id FROM shortnames",
}

_DELETE_STALE_ID_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: "DELETE FROM longnames WHERE meshtastic_id = ?",
    NAMES_TABLE_SHORTNAMES: "DELETE FROM shortnames WHERE meshtastic_id = ?",
}

# json_each() is used for efficient batched lookups by Meshtastic ID.
# Runtime SQLite compatibility is validated in db_runtime.DatabaseManager.
_SELECT_NAME_VALUES_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: (
        "SELECT meshtastic_id, longname FROM longnames "
        "WHERE meshtastic_id IN (SELECT value FROM json_each(?))"
    ),
    NAMES_TABLE_SHORTNAMES: (
        "SELECT meshtastic_id, shortname FROM shortnames "
        "WHERE meshtastic_id IN (SELECT value FROM json_each(?))"
    ),
}

_UPSERT_NAME_SQL_BY_TABLE = {
    NAMES_TABLE_LONGNAMES: (
        "INSERT INTO longnames (meshtastic_id, longname) VALUES (?, ?) "
        "ON CONFLICT(meshtastic_id) DO UPDATE SET longname=excluded.longname"
    ),
    NAMES_TABLE_SHORTNAMES: (
        "INSERT INTO shortnames (meshtastic_id, shortname) VALUES (?, ?) "
        "ON CONFLICT(meshtastic_id) DO UPDATE SET shortname=excluded.shortname"
    ),
}


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


# Get the database path
def get_db_path() -> str:
    """
    Resolve the absolute filesystem path to the application's SQLite database.

    Selects the path with this precedence: configuration key `database.path` (preferred), legacy `db.path`, then `<database_dir>/meshtastic.sqlite` from the application's resolved paths. The resolved path is cached and the cache is invalidated when relevant database configuration changes. The function will attempt to create missing directories; legacy database migration is handled explicitly by `mmrelay migrate` rather than implicitly here. Directory-creation failures are logged and do not raise exceptions.

    Returns:
        str: Filesystem path to the SQLite database.
    """
    global config, _cached_db_path, _db_path_logged, _cached_config_hash

    # Create a deterministic JSON representation of relevant config sections to detect changes
    current_config_hash = None
    if config is not None:
        # Use only the database-related config sections
        db_config = {
            "database": config.get("database", {}),
            "db": config.get("db", {}),  # Legacy format
        }
        current_config_hash = json.dumps(db_config, sort_keys=True)

    # Check if cache is valid (path exists and config hasn't changed)
    if _cached_db_path is not None and current_config_hash == _cached_config_hash:
        return _cached_db_path

    # Config changed or first call - clear cache and re-resolve
    if current_config_hash != _cached_config_hash:
        _cached_db_path = None
        _db_path_logged = False
        _cached_config_hash = current_config_hash

    # Check if config is available
    if config is not None:
        # Check if database path is specified in config (preferred format)
        if "database" in config and "path" in config["database"]:
            custom_path = config["database"]["path"]
            if custom_path:
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

        # Check legacy format (db section)
        if "db" in config and "path" in config["db"]:
            custom_path = config["db"]["path"]
            if custom_path:
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

    # Use unified path resolution for database
    paths_info = resolve_all_paths()
    database_dir = paths_info["database_dir"]

    # Ensure the database directory exists before using it
    try:
        os.makedirs(database_dir, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning("Could not create database directory %s: %s", database_dir, e)
        # Continue anyway - the database connection will fail later if needed

    default_path = os.path.join(database_dir, "meshtastic.sqlite")

    # If default path doesn't exist, check legacy locations
    if not os.path.exists(default_path) and is_deprecation_window_active():
        legacy_dirs = get_legacy_dirs()
        for legacy_dir in legacy_dirs:
            # Check various possible legacy locations
            candidates = [
                os.path.join(legacy_dir, "meshtastic.sqlite"),
                os.path.join(legacy_dir, "data", "meshtastic.sqlite"),
                os.path.join(legacy_dir, "database", "meshtastic.sqlite"),
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
    Close the given DatabaseManager if provided, suppressing any exceptions raised during close.

    Closes the manager when non-None; any exception raised by the manager's close() is ignored.
    """
    if manager:
        with contextlib.suppress(Exception):
            manager.close()


def _reset_db_manager() -> None:
    """
    Reset the cached global DatabaseManager so a new instance will be created on next access.

    If a manager exists, it is closed while holding the manager lock to avoid race conditions. Intended for testing and when configuration changes require recreating the manager.
    """
    global _db_manager, _db_manager_signature
    manager_to_close = None
    with _db_manager_lock:
        if _db_manager is not None:
            manager_to_close = _db_manager
            _db_manager = None
            _db_manager_signature = None

            # Close old manager inside the lock to prevent race condition
            # where another thread might be using connections from the old manager
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
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
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


def _resolve_database_options() -> Tuple[bool, int, Dict[str, Any]]:
    """
    Resolve database options (WAL, busy timeout, and SQLite pragmas) from the global config, supporting legacy keys and falling back to module defaults.

    Reads values from config["database"] with fallback to legacy config["db"], parses boolean and integer settings, and merges any provided pragmas on top of DEFAULT_EXTRA_PRAGMAS.

    Returns:
        enable_wal (bool): `True` if write-ahead logging should be enabled, `False` otherwise.
        busy_timeout_ms (int): Busy timeout in milliseconds to use for SQLite connections.
        extra_pragmas (dict): Mapping of pragma names to values, starting from DEFAULT_EXTRA_PRAGMAS and overridden by config-provided pragmas.
    """
    database_cfg: dict[str, Any] = (
        config.get("database", {}) if isinstance(config, dict) else {}
    )
    legacy_cfg: dict[str, Any] = (
        config.get("db", {}) if isinstance(config, dict) else {}
    )

    enable_wal = _parse_bool(
        database_cfg.get(
            "enable_wal", legacy_cfg.get("enable_wal", DEFAULT_ENABLE_WAL)
        ),
        DEFAULT_ENABLE_WAL,
    )

    busy_timeout_ms = _parse_int(
        database_cfg.get(
            "busy_timeout_ms",
            legacy_cfg.get("busy_timeout_ms", DEFAULT_BUSY_TIMEOUT_MS),
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
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {NAMES_TABLE_LONGNAMES} "
            f"(meshtastic_id TEXT PRIMARY KEY, {_LONGNAME_DB_FIELD} TEXT)"
        )
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {NAMES_TABLE_SHORTNAMES} "
            f"(meshtastic_id TEXT PRIMARY KEY, {_SHORTNAME_DB_FIELD} TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS plugin_data (plugin_name TEXT, meshtastic_id TEXT, data TEXT, PRIMARY KEY (plugin_name, meshtastic_id))"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS message_map (meshtastic_id TEXT, matrix_event_id TEXT PRIMARY KEY, matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
        )
        # Attempt schema adjustments for upgrades
        try:
            cursor.execute("ALTER TABLE message_map ADD COLUMN meshtastic_meshnet TEXT")
        except sqlite3.OperationalError:
            pass

        # Migrate legacy message_map schema where meshtastic_id used INTEGER affinity.
        cursor.execute("PRAGMA table_info(message_map)")
        columns = cursor.fetchall()
        column_map = {column[1]: column for column in columns}
        meshtastic_column = column_map.get("meshtastic_id")
        meshnet_column = column_map.get("meshtastic_meshnet")
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_map_legacy'"
        )
        legacy_exists = cursor.fetchone() is not None

        if legacy_exists and (
            not meshtastic_column or str(meshtastic_column[2]).upper() == "TEXT"
        ):
            # Recover from a previously interrupted migration by merging legacy rows.
            cursor.execute("PRAGMA table_info(message_map_legacy)")
            legacy_columns = {column[1]: column for column in cursor.fetchall()}
            if "meshtastic_meshnet" in legacy_columns:
                cursor.execute(
                    "INSERT OR IGNORE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
                    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
                    "FROM message_map_legacy"
                )
            else:
                cursor.execute(
                    "INSERT OR IGNORE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
                    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, NULL "
                    "FROM message_map_legacy"
                )
            cursor.execute("DROP TABLE message_map_legacy")
            legacy_exists = False

        if meshtastic_column and str(meshtastic_column[2]).upper() != "TEXT":
            if legacy_exists:
                cursor.execute("DROP TABLE message_map_legacy")
            cursor.execute("ALTER TABLE message_map RENAME TO message_map_legacy")
            cursor.execute(
                "CREATE TABLE message_map (meshtastic_id TEXT, matrix_event_id TEXT PRIMARY KEY, matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
            )
            if meshnet_column:
                cursor.execute(
                    "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
                    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet "
                    "FROM message_map_legacy"
                )
            else:
                cursor.execute(
                    "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) "
                    "SELECT CAST(meshtastic_id AS TEXT), matrix_event_id, matrix_room_id, meshtastic_text, NULL "
                    "FROM message_map_legacy"
                )
            cursor.execute("DROP TABLE message_map_legacy")

        try:
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_message_map_meshtastic_id ON message_map (meshtastic_id)"
            )
        except sqlite3.OperationalError:
            pass

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
        cursor.execute(
            "INSERT INTO plugin_data (plugin_name, meshtastic_id, data) VALUES (?, ?, ?) "
            "ON CONFLICT (plugin_name, meshtastic_id) DO UPDATE SET data = excluded.data",
            (plugin_name, id_key, payload),
        )

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
        cursor.execute(
            "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
            (plugin_name, id_key),
        )

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
        cursor.execute(
            "SELECT data FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
            (plugin_name, id_key),
        )
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
        cursor.execute(
            "SELECT data FROM plugin_data WHERE plugin_name=?", (plugin_name,)
        )
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
        cursor.execute(
            "SELECT longname FROM longnames WHERE meshtastic_id=?",
            (id_key,),
        )
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
            "INSERT INTO longnames (meshtastic_id, longname) VALUES (?, ?) "
            "ON CONFLICT(meshtastic_id) DO UPDATE SET longname=excluded.longname",
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
    """
    if not current_ids:
        return {}

    column_name = _NAME_FIELD_BY_TABLE.get(table)
    select_sql = _SELECT_NAME_VALUES_SQL_BY_TABLE.get(table)
    if column_name is None or select_sql is None:
        raise _InvalidNamesTableError(table)

    manager = _get_db_manager()
    sorted_ids = sorted(current_ids)

    def _fetch(cursor: sqlite3.Cursor) -> dict[str, str | None]:
        rows_by_id: dict[str, str | None] = {}
        for offset in range(0, len(sorted_ids), DEFAULT_NAME_PRUNE_CHUNK_SIZE):
            chunk_ids = sorted_ids[offset : offset + DEFAULT_NAME_PRUNE_CHUNK_SIZE]
            cursor.execute(
                select_sql,
                (json.dumps(chunk_ids),),
            )
            rows_by_id.update(
                {
                    str(row[0]): _normalize_node_name_value(row[1])
                    for row in cursor.fetchall()
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
            if id_key in actual_by_id:
                return False
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
        # Treat empty NodeDB snapshots as incomplete so transient reconnect windows
        # cannot trigger full-table stale-row pruning.
        return (), set(), False

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
        long_name = _normalize_node_name_value(user.get(PROTO_NODE_NAME_LONG))
        short_name = _normalize_node_name_value(user.get(PROTO_NODE_NAME_SHORT))

        existing_entry = state_by_id.get(id_key)
        if existing_entry is None:
            state_by_id[id_key] = (long_name, short_name)
            continue

        # Duplicate IDs can appear in transient snapshots. Merge deterministically
        # so state and DB writes are order-independent.
        merged_long_name = _merge_node_name_values(existing_entry[0], long_name)
        merged_short_name = _merge_node_name_values(existing_entry[1], short_name)

        # If either field has a conflict, skip this ID entirely to avoid
        # writing incorrect data. The conflict will clear in a future snapshot.
        if (
            merged_long_name is _CONFLICT_SENTINEL
            or merged_short_name is _CONFLICT_SENTINEL
        ):
            logger.warning(
                "Skipping node %s due to conflicting duplicate names in snapshot",
                id_key,
            )
            state_by_id.pop(id_key, None)
            # Keep conflicting IDs in current_ids so stale-row pruning does not
            # incorrectly delete existing rows for active nodes.
            skipped_ids.add(id_key)
        else:
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
                cursor.execute(long_delete_sql, (id_key,))
                long_clear_ids.add(id_key)
            else:
                cursor.execute(long_upsert_sql, (id_key, long_name))
                long_upsert_ids.add(id_key)

            if short_name is None:
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

    current_state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
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
            longnames_deleted = _delete_stale_names(
                NAMES_TABLE_LONGNAMES,
                current_ids,
                return_none_on_error=True,
            )
            shortnames_deleted = _delete_stale_names(
                NAMES_TABLE_SHORTNAMES,
                current_ids,
                return_none_on_error=True,
            )
            if longnames_deleted is None or shortnames_deleted is None:
                return previous_state
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

    def _get_long_name(entry: NodeNameEntry) -> str | None:
        return entry.long_name

    def _get_short_name(entry: NodeNameEntry) -> str | None:
        return entry.short_name

    if name_key == PROTO_NODE_NAME_LONG:
        get_name = _get_long_name
    elif name_key == PROTO_NODE_NAME_SHORT:
        get_name = _get_short_name
    else:
        raise ValueError(f"Unsupported node name key: {name_key}")
    state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
    all_saves_ok = True
    for state_row in state:
        id_key = state_row.meshtastic_id
        normalized_name = get_name(state_row)
        if normalized_name is None:
            if not delete_name(id_key):
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
        cursor.execute(
            "SELECT shortname FROM shortnames WHERE meshtastic_id=?",
            (id_key,),
        )
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
            "INSERT INTO shortnames (meshtastic_id, shortname) VALUES (?, ?) "
            "ON CONFLICT(meshtastic_id) DO UPDATE SET shortname=excluded.shortname",
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
        "INSERT INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(matrix_event_id) DO UPDATE SET "
        "meshtastic_id=excluded.meshtastic_id, "
        "matrix_room_id=excluded.matrix_room_id, "
        "meshtastic_text=excluded.meshtastic_text, "
        "meshtastic_meshnet=excluded.meshtastic_meshnet",
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
        cursor.execute(
            "SELECT matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE meshtastic_id=?",
            (id_key,),
        )
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
        cursor.execute(
            "SELECT meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE matrix_event_id=?",
            (matrix_event_id,),
        )
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
        cursor.execute("DELETE FROM message_map")

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
    cursor.execute("SELECT COUNT(*) FROM message_map")
    row = cursor.fetchone()
    total = row[0] if row else 0

    if total > msgs_to_keep:
        to_delete = total - msgs_to_keep
        cursor.execute(
            "DELETE FROM message_map WHERE rowid IN (SELECT rowid FROM message_map ORDER BY rowid ASC LIMIT ?)",
            (to_delete,),
        )
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
    manager = await asyncio.to_thread(_get_db_manager)
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
    manager = await asyncio.to_thread(_get_db_manager)

    try:
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
