import contextlib
import json
import os
import sqlite3
import threading
from typing import Any, Dict, Tuple, cast

from mmrelay.config import get_data_dir
from mmrelay.db_runtime import DatabaseManager
from mmrelay.log_utils import get_logger

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

DEFAULT_ENABLE_WAL = True
DEFAULT_BUSY_TIMEOUT_MS = 5000
DEFAULT_EXTRA_PRAGMAS: Dict[str, Any] = {
    "synchronous": "NORMAL",
    "temp_store": "MEMORY",
}

logger = get_logger(name="db_utils")


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
    Resolve the absolute filesystem path to the SQLite database, preferring configured values and falling back to the application data directory.

    Selects the path in this precedence: `config["database"]["path"]` (preferred), `config["db"]["path"]` (legacy), then `<data_dir>/meshtastic.sqlite`. The chosen path is cached and the cache is invalidated when relevant database configuration changes. Attempts to create parent or data directories and logs warnings on failure but does not raise.

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

    # Use the standard data directory
    data_dir = get_data_dir()
    # Ensure the data directory exists before using it
    try:
        os.makedirs(data_dir, exist_ok=True)
    except (OSError, PermissionError) as e:
        logger.warning("Could not create data directory %s: %s", data_dir, e)
        # Continue anyway - the database connection will fail later if needed
    default_path = os.path.join(data_dir, "meshtastic.sqlite")
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
            "CREATE TABLE IF NOT EXISTS longnames (meshtastic_id TEXT PRIMARY KEY, longname TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS shortnames (meshtastic_id TEXT PRIMARY KEY, shortname TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS plugin_data (plugin_name TEXT, meshtastic_id TEXT, data TEXT, PRIMARY KEY (plugin_name, meshtastic_id))"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS message_map (meshtastic_id INTEGER, matrix_event_id TEXT PRIMARY KEY, matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
        )
        # Attempt schema adjustments for upgrades
        try:
            cursor.execute("ALTER TABLE message_map ADD COLUMN meshtastic_meshnet TEXT")
        except sqlite3.OperationalError:
            pass

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
    Store or update JSON-serialized plugin data for a specific plugin and Meshtastic ID in the database.

    Parameters:
        plugin_name (str): The name of the plugin.
        meshtastic_id (int | str): The Meshtastic node identifier.
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
        Store JSON-serialized plugin data for a specific plugin and Meshtastic node using the provided DB cursor.

        Executes an INSERT (with ON CONFLICT DO UPDATE) into `plugin_data` for captured `plugin_name` and `meshtastic_id`, storing `data` serialized as JSON.

        Parameters:
            cursor (sqlite3.Cursor): Open database cursor used to execute insert/update. The function uses `plugin_name`, `meshtastic_id`, and `payload` from the enclosing scope.
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
        return


def delete_plugin_data(plugin_name: str, meshtastic_id: int | str) -> None:
    """Remove a plugin data entry for a Meshtastic node from the database.

    Parameters:
        plugin_name (str): The name of the plugin whose data should be deleted.
        meshtastic_id (int | str): The Meshtastic node ID associated with the plugin data.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _delete(cursor: sqlite3.Cursor) -> None:
        """
        Delete the plugin_data row for the current `plugin_name` and `meshtastic_id` using the provided DB cursor.

        Parameters:
            cursor (sqlite3.Cursor): Active database cursor; deletion is executed on this cursor and should be part of the caller's transaction.
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
    Return deserialized JSON value stored for a plugin and Meshtastic node, or an empty list on error.

    The stored value may be of any JSON-serializable type. If no row exists or a database or decoding error occurs, returns an empty list as a fallback.

    Parameters:
        plugin_name (str): Name of the plugin.
        meshtastic_id (int | str): Identifier of the Meshtastic node.

    Returns:
        Any: The deserialized JSON value (may be dict, list, scalar, etc.) for the given plugin and node, or an empty list `[]` if none is stored or on error.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Retrieve the single-row `data` column for the current plugin/node pair using the provided database cursor.

        Returns:
            `tuple[Any, ...]` containing the `data` column for the matched row, or `None` if no matching row exists.
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
    Return the long name for a given Meshtastic node ID.

    Parameters:
        meshtastic_id (int | str): Meshtastic node identifier to look up.

    Returns:
        str | None: The stored long name if present, `None` if not found or on database error.
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


def save_longname(meshtastic_id: int | str, longname: str) -> None:
    """
    Store or update the long display name for a Meshtastic node in the longnames table.

    If a row for the given node exists it will be replaced; database errors are logged and suppressed.

    Parameters:
        meshtastic_id (int | str): Identifier of the Meshtastic node.
        longname (str): Full display name to store for the node.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _store(cursor: sqlite3.Cursor) -> None:
        """
        Store the longname using the provided cursor.

        Parameters:
            cursor (sqlite3.Cursor): Open database cursor used to execute the insert/update. The function uses `meshtastic_id` and `longname` from the enclosing scope.
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


def update_longnames(nodes: dict[str, Any]) -> None:
    """
    Persist long names from node user entries into the database.

    For each node in `nodes` that contains a `"user"` mapping, save the user's `"longName"` (use `"N/A"` if missing) under the user's `"id"` by calling `save_longname`.

    Parameters:
        nodes (dict[str, Any]): Mapping of node identifiers to node dictionaries; each node dictionary may include a `"user"` dict with an `"id"` key and an optional `"longName"` key.
    """
    if nodes:
        for node in nodes.values():
            user = node.get("user")
            if user:
                meshtastic_id = user["id"]
                longname = user.get("longName", "N/A")
                save_longname(meshtastic_id, longname)


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
        Fetches the first shortname row for an active Meshtastic ID using the provided database cursor.

        Parameters:
            cursor (sqlite3.Cursor): Cursor on which the SELECT query is executed; the function expects a `meshtastic_id` value to be available from the surrounding scope.

        Returns:
            tuple[Any, ...] | None: The first row returned by query (typically a single-item tuple containing the `shortname`), or `None` if no row is found.
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


def save_shortname(meshtastic_id: int | str, shortname: str) -> None:
    """
    Insert or update the shortname for a Meshtastic node.

    Stores the provided `shortname` in the `shortnames` table keyed by `meshtastic_id`. Database errors are logged (with stacktrace) and suppressed; the function does not raise on sqlite3 errors.

    Parameters:
        meshtastic_id (int | str): Node identifier used as the primary key in the shortnames table.
        shortname (str): Display name to store for the node.
    """
    manager = _get_db_manager()
    id_key = str(meshtastic_id)

    def _store(cursor: sqlite3.Cursor) -> None:
        """
        Insert or update a shortname row for the captured `meshtastic_id` and `shortname` into the `shortnames` table.

        Parameters:
            cursor (sqlite3.Cursor): Active database cursor used to execute the write operation.
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


def update_shortnames(nodes: dict[str, Any]) -> None:
    """
    Update persisted short names for nodes that include a user object.

    For each node in the provided mapping, if the node contains a `user` dictionary, the function
    uses `user["id"]` as the Meshtastic ID and `user.get("shortName", "N/A")` as the short name and
    stores that value in the database.

    Parameters:
        nodes (Mapping): Mapping of node identifiers to node objects; nodes without a `user` entry are ignored.
    """
    if nodes:
        for node in nodes.values():
            user = node.get("user")
            if user:
                meshtastic_id = user["id"]
                shortname = user.get("shortName", "N/A")
                save_shortname(meshtastic_id, shortname)


def _store_message_map_core(
    cursor: sqlite3.Cursor,
    meshtastic_id: int | str,
    matrix_event_id: str,
    matrix_room_id: str,
    meshtastic_text: str,
    meshtastic_meshnet: str | None = None,
) -> None:
    """
    Insert or update a mapping between a Meshtastic message (or node) and a Matrix event.

    Parameters:
        cursor (sqlite3.Cursor): Active database cursor used to execute the statement.
        meshtastic_id (int | str): Meshtastic message or node identifier.
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
                meshtastic_id,
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
    Retrieve the mapping between a Meshtastic message ID and its corresponding Matrix event.

    Returns:
        tuple: (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if a valid mapping exists, `None` otherwise.
    """
    manager = _get_db_manager()

    def _fetch(cursor: sqlite3.Cursor) -> tuple[Any, ...] | None:
        """
        Fetch a single row from message_map for the current Meshtastic ID and return its fields.

        Returns:
            tuple[Any, ...] | None: (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if a row is found, `None` otherwise.
        """
        cursor.execute(
            "SELECT matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE meshtastic_id=?",
            (meshtastic_id,),
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
) -> tuple[int | str, str, str, str | None] | None:
    """
    Retrieve the mapping row for a given Matrix event ID.

    Parameters:
        matrix_event_id (str): Matrix event ID to look up.

    Returns:
        tuple[int | str, str, str, str | None] | None: A tuple (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if a matching row exists, `None` otherwise.
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
    Store or update a mapping between a Meshtastic message and a Matrix event in the database.

    Parameters:
        meshtastic_id (int | str): Meshtastic message identifier.
        matrix_event_id (str): Matrix event ID to map to.
        matrix_room_id (str): Matrix room ID where the Matrix event was posted.
        meshtastic_text (str): Text content of the Meshtastic message.
        meshtastic_meshnet (str | None): Optional meshnet identifier associated with the message.
    """
    manager = _get_db_manager()

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
                meshtastic_id,
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
    Prune the message_map table to keep only the most recent entries.

    Parameters:
        msgs_to_keep (int): Number of most recent message_map rows to retain; older rows will be deleted.
    """
    manager = _get_db_manager()

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
