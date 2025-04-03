import json
import sqlite3

from log_utils import get_logger

logger = get_logger(name="db_utils")


# Initialize SQLite database
def initialize_database():
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO plugin_data (plugin_name, meshtastic_id, data) VALUES (?, ?, ?) ON CONFLICT (plugin_name, meshtastic_id) DO UPDATE SET data = ?",
            (plugin_name, meshtastic_id, json.dumps(data), json.dumps(data)),
        )
        conn.commit()


def delete_plugin_data(plugin_name, meshtastic_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
            (plugin_name, meshtastic_id),
        )
        conn.commit()


# Get the data for a given plugin and Meshtastic ID
def get_plugin_data_for_node(plugin_name, meshtastic_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT data FROM plugin_data WHERE plugin_name=? ",
            (plugin_name,),
        )
        return cursor.fetchall()


# Get the longname for a given Meshtastic ID
def get_longname(meshtastic_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT longname FROM longnames WHERE meshtastic_id=?", (meshtastic_id,)
        )
        result = cursor.fetchone()
    return result[0] if result else None


def save_longname(meshtastic_id, longname):
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT shortname FROM shortnames WHERE meshtastic_id=?", (meshtastic_id,)
        )
        result = cursor.fetchone()
    return result[0] if result else None


def save_shortname(meshtastic_id, shortname):
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    Useful when db.msg_map.wipe_on_restart is True, ensuring no stale data remains.
    """
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
    with sqlite3.connect("meshtastic.sqlite") as conn:
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
