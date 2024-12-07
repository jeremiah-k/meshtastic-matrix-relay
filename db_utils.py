import json
import sqlite3
import logging

# Initialize logger for this module
logger = logging.getLogger("DBUtils")


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
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS message_map (meshtastic_id INTEGER, matrix_event_id TEXT PRIMARY KEY, matrix_room_id TEXT, meshtastic_text TEXT)"
        )
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


def store_message_map(meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO message_map (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text) VALUES (?, ?, ?, ?)",
            (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text),
        )
        conn.commit()


def get_message_map_by_meshtastic_id(meshtastic_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT matrix_event_id, matrix_room_id, meshtastic_text FROM message_map WHERE meshtastic_id=?",
            (meshtastic_id,),
        )
        return cursor.fetchone()


def get_message_map_by_matrix_event_id(matrix_event_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT meshtastic_id, matrix_room_id, meshtastic_text FROM message_map WHERE matrix_event_id=?",
            (matrix_event_id,),
        )
        result = cursor.fetchone()
        if result:
            logger.debug(
                f"Retrieved message from DB: matrix_event_id={matrix_event_id}, result={result}"
            )
        else:
            logger.debug(
                f"No message found in DB for matrix_event_id={matrix_event_id}"
            )
        return result

def update_message_map_meshtastic_id(matrix_event_id, meshtastic_id):
    with sqlite3.connect("meshtastic.sqlite") as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE message_map SET meshtastic_id=? WHERE matrix_event_id=?",
            (meshtastic_id, matrix_event_id),
        )
        conn.commit()
        logger.debug(f"Updated message_map with meshtastic_id={meshtastic_id} for matrix_event_id={matrix_event_id}")
