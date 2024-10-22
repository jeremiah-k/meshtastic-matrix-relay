import json
import sqlite3
from log_utils import get_logger

logger = get_logger(name="Database")


# Initialize SQLite database
def initialize_database():
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS longnames (meshtastic_id TEXT PRIMARY KEY, longname TEXT)"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS shortnames (meshtastic_id TEXT PRIMARY KEY, shortname TEXT)"
            )
            cursor.execute(
                "CREATE TABLE IF NOT EXISTS plugin_data (plugin_name TEXT, meshtastic_id TEXT, data TEXT, PRIMARY KEY (plugin_name, meshtastic_id))"
            )
            conn.commit()
            logger.info("Database initialized successfully.")
    except sqlite3.Error as e:
        logger.error(f"Error initializing database: {e}")


def store_plugin_data(plugin_name, meshtastic_id, data):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO plugin_data (plugin_name, meshtastic_id, data)
                   VALUES (?, ?, ?)
                """,
                (plugin_name, meshtastic_id, json.dumps(data)),
            )
            conn.commit()
            logger.debug(f"Plugin data stored for {plugin_name}, node {meshtastic_id}")
    except sqlite3.Error as e:
        logger.error(f"Error storing plugin data: {e}")


def delete_plugin_data(plugin_name, meshtastic_id):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
                (plugin_name, meshtastic_id),
            )
            conn.commit()
            logger.debug(f"Plugin data deleted for {plugin_name}, node {meshtastic_id}")
    except sqlite3.Error as e:
        logger.error(f"Error deleting plugin data: {e}")

# Get the data for a given plugin and Meshtastic ID
def get_plugin_data_for_node(plugin_name, meshtastic_id):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
                (plugin_name, meshtastic_id),
            )
            result = cursor.fetchone()
        return json.loads(result[0]) if result else []
    except sqlite3.Error as e:
        logger.error(f"Error retrieving plugin data: {e}")
        return []

# Get the data for a given plugin
def get_plugin_data(plugin_name):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT data FROM plugin_data WHERE plugin_name=?",
                (plugin_name,),
            )
            results = cursor.fetchall()
        return [json.loads(row[0]) for row in results]
    except sqlite3.Error as e:
        logger.error(f"Error retrieving plugin data: {e}")
        return []


def get_longname(meshtastic_id):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT longname FROM longnames WHERE meshtastic_id=?", (meshtastic_id,)
            )
            result = cursor.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Error retrieving longname: {e}")
        return None


def get_shortname(meshtastic_id):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT shortname FROM shortnames WHERE meshtastic_id=?", (meshtastic_id,)
            )
            result = cursor.fetchone()
        return result[0] if result else None
    except sqlite3.Error as e:
        logger.error(f"Error retrieving shortname: {e}")
        return None


def save_name(table, meshtastic_id, name):
    try:
        with sqlite3.connect("meshtastic.sqlite") as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"INSERT OR REPLACE INTO {table} (meshtastic_id, {table[:-1]}) VALUES (?, ?)",
                (meshtastic_id, name),
            )
            conn.commit()
            logger.debug(f"Saved {table[:-1]} for {meshtastic_id}")
    except sqlite3.Error as e:
        logger.error(f"Error saving {table[:-1]}: {e}")


def update_names(nodes, name_type):
    if nodes:
        table = 'longnames' if name_type == 'longName' else 'shortnames'
        for node in nodes.values():
            user = node.get("user")
            if user:
                meshtastic_id = user["id"]
                name = user.get(name_type, "N/A")
                save_name(table, meshtastic_id, name)
    else:
        logger.debug("No nodes available to update names.")


def update_longnames(nodes):
    update_names(nodes, 'longName')


def update_shortnames(nodes):
    update_names(nodes, 'shortName')
