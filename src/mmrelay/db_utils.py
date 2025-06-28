import json
import os
import sqlite3

from mmrelay.config import get_data_dir
from mmrelay.log_utils import get_logger

# Global config variable that will be set from main.py
config = None

# Cache for database path to avoid repeated logging and path resolution
_cached_db_path = None
_db_path_logged = False
_cached_config_hash = None

logger = get_logger(name="db_utils")


def clear_db_path_cache():
    """Clear the cached database path to force re-resolution on next call.

    This is useful for testing or if the application supports runtime
    configuration changes.
    """
    global _cached_db_path, _db_path_logged, _cached_config_hash
    _cached_db_path = None
    _db_path_logged = False
    _cached_config_hash = None


# Get the database path
def get_db_path():
    """
    Resolve and return the file path to the SQLite database, using configuration overrides if provided.

    By default, returns the path to `meshtastic.sqlite` in the standard data directory (`~/.mmrelay/data`).
    If a custom path is specified in the configuration under `database.path` (preferred) or `db.path` (legacy),
    that path is used instead. The resolved path is cached for subsequent calls, and the directory is created
    if it does not exist. Cache is automatically invalidated if the relevant configuration changes.
    """
    global config, _cached_db_path, _db_path_logged, _cached_config_hash

    # Create a hash of the relevant config sections to detect changes
    current_config_hash = None
    if config is not None:
        # Hash only the database-related config sections
        db_config = {
            "database": config.get("database", {}),
            "db": config.get("db", {}),  # Legacy format
        }
        current_config_hash = hash(str(sorted(db_config.items())))

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
                    os.makedirs(db_dir, exist_ok=True)

                # Cache the path and log only once
                _cached_db_path = custom_path
                if not _db_path_logged:
                    logger.info(f"Using database path from config: {custom_path}")
                    _db_path_logged = True
                return custom_path

        # Check legacy format (db section)
        if "db" in config and "path" in config["db"]:
            custom_path = config["db"]["path"]
            if custom_path:
                # Ensure the directory exists
                db_dir = os.path.dirname(custom_path)
                if db_dir:
                    os.makedirs(db_dir, exist_ok=True)

                # Cache the path and log only once
                _cached_db_path = custom_path
                if not _db_path_logged:
                    logger.warning(
                        "Using 'db.path' configuration (legacy). 'database.path' is now the preferred format and 'db.path' will be deprecated in a future version."
                    )
                    _db_path_logged = True
                return custom_path

    # Use the standard data directory
    default_path = os.path.join(get_data_dir(), "meshtastic.sqlite")
    _cached_db_path = default_path
    return default_path


# Initialize SQLite database
def initialize_database():
    db_path = get_db_path()
    # Check if database exists
    if os.path.exists(db_path):
        logger.info(f"Loading database from: {db_path}")
    else:
        logger.info(f"Creating new database at: {db_path}")
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Updated table schema: matrix_event_id is now PRIMARY KEY, meshtastic_id is not necessarily unique
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS longnames (meshtastic_id TEXT PRIMARY KEY, longname TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS shortnames (meshtastic_id TEXT PRIMARY KEY, shortname TEXT)"
        )
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS plugin_data (plugin_name TEXT, meshtastic_id TEXT, data TEXT, PRIMARY KEY (plugin_name, meshtastic_id))"
        )
        # Changed the schema for message_map: matrix_event_id is now primary key
        # Added a new column 'meshtastic_meshnet' to store the meshnet origin of the message.
        # If table already exists, we try adding the column if it doesn't exist.
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS message_map (meshtastic_id INTEGER, matrix_event_id TEXT PRIMARY KEY, matrix_room_id TEXT, meshtastic_text TEXT, meshtastic_meshnet TEXT)"
        )

        # Attempt to add meshtastic_meshnet column if it's missing (for upgrades)
        # This is a no-op if the column already exists.
        # If user runs fresh, it will already be there from CREATE TABLE IF NOT EXISTS.
        try:
            cursor.execute("ALTER TABLE message_map ADD COLUMN meshtastic_meshnet TEXT")
        except sqlite3.OperationalError:
            # Column already exists, or table just created with it
            pass

        conn.commit()


def store_plugin_data(plugin_name, meshtastic_id, data):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO plugin_data (plugin_name, meshtastic_id, data) VALUES (?, ?, ?) ON CONFLICT (plugin_name, meshtastic_id) DO UPDATE SET data = ?",
            (plugin_name, meshtastic_id, json.dumps(data), json.dumps(data)),
        )
        conn.commit()


def delete_plugin_data(plugin_name, meshtastic_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
            (plugin_name, meshtastic_id),
        )
        conn.commit()


# Get the data for a given plugin and Meshtastic ID
def get_plugin_data_for_node(plugin_name, meshtastic_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT data FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
            (
                plugin_name,
                meshtastic_id,
            ),
        )
        result = cursor.fetchone()
    return json.loads(result[0] if result else "[]")


# Get the data for a given plugin
def get_plugin_data(plugin_name):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT data FROM plugin_data WHERE plugin_name=? ",
            (plugin_name,),
        )
        return cursor.fetchall()


# Get the longname for a given Meshtastic ID
def get_longname(meshtastic_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT longname FROM longnames WHERE meshtastic_id=?", (meshtastic_id,)
        )
        result = cursor.fetchone()
    return result[0] if result else None


def save_longname(meshtastic_id, longname):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO longnames (meshtastic_id, longname) VALUES (?, ?)",
            (meshtastic_id, longname),
        )
        conn.commit()


def update_longnames(nodes):
    if nodes:
        for node in nodes.values():
            user = node.get("user")
            if user:
                meshtastic_id = user["id"]
                longname = user.get("longName", "N/A")
                save_longname(meshtastic_id, longname)


def get_shortname(meshtastic_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT shortname FROM shortnames WHERE meshtastic_id=?", (meshtastic_id,)
        )
        result = cursor.fetchone()
    return result[0] if result else None


def save_shortname(meshtastic_id, shortname):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO shortnames (meshtastic_id, shortname) VALUES (?, ?)",
            (meshtastic_id, shortname),
        )
        conn.commit()


def update_shortnames(nodes):
    if nodes:
        for node in nodes.values():
            user = node.get("user")
            if user:
                meshtastic_id = user["id"]
                shortname = user.get("shortName", "N/A")
                save_shortname(meshtastic_id, shortname)


def store_message_map(
    meshtastic_id,
    matrix_event_id,
    matrix_room_id,
    meshtastic_text,
    meshtastic_meshnet=None,
):
    """
    Stores a message map in the database.

    :param meshtastic_id: The Meshtastic message ID (integer or None)
    :param matrix_event_id: The Matrix event ID (string, primary key)
    :param matrix_room_id: The Matrix room ID (string)
    :param meshtastic_text: The text of the Meshtastic message
    :param meshtastic_meshnet: The name of the meshnet this message originated from.
                               This helps us identify remote vs local mesh origins.
    """
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        logger.debug(
            f"Storing message map: meshtastic_id={meshtastic_id}, matrix_event_id={matrix_event_id}, matrix_room_id={matrix_room_id}, meshtastic_text={meshtastic_text}, meshtastic_meshnet={meshtastic_meshnet}"
        )
        cursor.execute(
            "INSERT OR REPLACE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) VALUES (?, ?, ?, ?, ?)",
            (
                meshtastic_id,
                matrix_event_id,
                matrix_room_id,
                meshtastic_text,
                meshtastic_meshnet,
            ),
        )
        conn.commit()


def get_message_map_by_meshtastic_id(meshtastic_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE meshtastic_id=?",
            (meshtastic_id,),
        )
        result = cursor.fetchone()
        logger.debug(
            f"Retrieved message map by meshtastic_id={meshtastic_id}: {result}"
        )
        if result:
            # result = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            return result[0], result[1], result[2], result[3]
        return None


def get_message_map_by_matrix_event_id(matrix_event_id):
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE matrix_event_id=?",
            (matrix_event_id,),
        )
        result = cursor.fetchone()
        logger.debug(
            f"Retrieved message map by matrix_event_id={matrix_event_id}: {result}"
        )
        if result:
            # result = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            return result[0], result[1], result[2], result[3]
        return None


def wipe_message_map():
    """
    Wipes all entries from the message_map table.
    Useful when database.msg_map.wipe_on_restart or db.msg_map.wipe_on_restart is True,
    ensuring no stale data remains.
    """
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM message_map")
        conn.commit()
    logger.info("message_map table wiped successfully.")


def prune_message_map(msgs_to_keep):
    """
    Prune the message_map table to keep only the most recent msgs_to_keep entries
    in order to prevent database bloat.
    We use the matrix_event_id's insertion order as a heuristic.
    Note: matrix_event_id is a string, so we rely on the rowid or similar approach.

    Approach:
    - Count total rows.
    - If total > msgs_to_keep, delete oldest entries based on rowid.
    """
    with sqlite3.connect(get_db_path()) as conn:
        cursor = conn.cursor()
        # Count total entries
        cursor.execute("SELECT COUNT(*) FROM message_map")
        total = cursor.fetchone()[0]

        if total > msgs_to_keep:
            # Delete oldest entries by rowid since matrix_event_id is primary key but not necessarily numeric.
            # rowid is auto-incremented and reflects insertion order.
            to_delete = total - msgs_to_keep
            cursor.execute(
                "DELETE FROM message_map WHERE rowid IN (SELECT rowid FROM message_map ORDER BY rowid ASC LIMIT ?)",
                (to_delete,),
            )
            conn.commit()
            logger.info(
                f"Pruned {to_delete} old message_map entries, keeping last {msgs_to_keep}."
            )
