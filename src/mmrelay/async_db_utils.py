"""
Async database utilities for Meshtastic Matrix Relay.

Provides async wrapper functions for all database operations using aiosqlite,
enabling better performance in async-heavy application contexts.
"""

import json
import sqlite3
from typing import Any, List, Optional, Tuple

from mmrelay.log_utils import get_logger

logger = get_logger(name="async_db_utils")


# Import connection pool (lazy import to avoid circular dependencies)
def _get_async_db_connection():
    """Get async database connection using async connection pool."""
    # Import here to avoid circular imports
    from mmrelay import db_utils
    from mmrelay.async_db_pool import get_async_db_connection as get_pool_connection

    config = getattr(db_utils, "config", None)
    return get_pool_connection(config)


async def async_initialize_database():
    """
    Initialize of database with required tables if they don't exist.

    Creates tables for plugin_data, longnames, shortnames, and message_map
    with proper indexes for performance optimization.
    """

    from mmrelay.db_utils import get_db_path

    db_path = get_db_path()

    # Check if database file exists
    import os

    db_exists = os.path.exists(db_path)

    if db_exists:
        logger.info(f"Database already exists at: {db_path}")
    else:
        logger.info(f"Creating new database at: {db_path}")

    try:
        connection_manager = _get_async_db_connection()
        async with connection_manager as conn:
            cursor = await conn.cursor()

            # Updated table schema: matrix_event_id is now PRIMARY KEY, meshtastic_id is not necessarily unique
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS plugin_data (
                    plugin_name TEXT NOT NULL,
                    meshtastic_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (plugin_name, meshtastic_id)
                )
                """
            )

            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS longnames (
                    meshtastic_id TEXT PRIMARY KEY,
                    longname TEXT NOT NULL
                )
                """
            )

            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS shortnames (
                    meshtastic_id TEXT PRIMARY KEY,
                    shortname TEXT NOT NULL
                )
                """
            )

            # Updated table schema: matrix_event_id is now PRIMARY KEY, meshtastic_id is not necessarily unique
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS message_map (
                    meshtastic_id TEXT NOT NULL,
                    matrix_event_id TEXT PRIMARY KEY,
                    matrix_room_id TEXT NOT NULL,
                    meshtastic_text TEXT,
                    meshtastic_meshnet TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            # Create indexes for better performance
            try:
                await cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_plugin_data_name ON plugin_data(plugin_name)"
                )
                await cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_message_map_meshtastic_id ON message_map(meshtastic_id)"
                )
                await cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_message_map_created_at ON message_map(created_at)"
                )
            except sqlite3.OperationalError:
                # Index creation failed, continue without it
                pass

            await conn.commit()
    except sqlite3.Error:
        logger.exception("Async database initialization failed")
        raise


async def async_store_plugin_data(plugin_name: str, meshtastic_id: str, data: Any):
    """
    Store plugin-specific data in the database asynchronously.

    Args:
        plugin_name (str): The name of the plugin storing the data.
        meshtastic_id (str): The Meshtastic node ID associated with the plugin data.
        data (Any): The plugin data to be serialized and stored.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            serialized_data = json.dumps(data)
            await cursor.execute(
                """
                INSERT OR REPLACE INTO plugin_data (plugin_name, meshtastic_id, data) 
                VALUES (?, ?, ?) 
                ON CONFLICT (plugin_name, meshtastic_id) DO UPDATE SET data = ?
                """,
                (plugin_name, meshtastic_id, serialized_data, serialized_data),
            )
            await conn.commit()
    except sqlite3.Error:
        logger.exception(f"Failed to store plugin data for {plugin_name}")
        raise


async def async_delete_plugin_data(plugin_name: str, meshtastic_id: str):
    """
    Delete plugin-specific data from the database asynchronously.

    Args:
        plugin_name (str): The name of the plugin whose data is to be deleted.
        meshtastic_id (str): The Meshtastic node ID associated with the plugin data.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "DELETE FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
                (plugin_name, meshtastic_id),
            )
            await conn.commit()
    except sqlite3.Error:
        logger.exception(f"Failed to delete plugin data for {plugin_name}")
        raise


async def async_get_plugin_data_for_node(
    plugin_name: str, meshtastic_id: str
) -> List[Any]:
    """
    Retrieve plugin-specific data for a specific node asynchronously.

    Args:
        plugin_name (str): The name of the plugin whose data is to be retrieved.
        meshtastic_id (str): The Meshtastic node ID associated with the plugin data.

    Returns:
        list: The deserialized plugin data as a list, or an empty list if no data is found or on error.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT data FROM plugin_data WHERE plugin_name=? AND meshtastic_id=?",
                (plugin_name, meshtastic_id),
            )
            result = await cursor.fetchone()

            if result:
                try:
                    return [json.loads(result[0])]
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in plugin data for {plugin_name}")
                    return []
            return []
    except sqlite3.Error:
        logger.exception(f"Failed to retrieve plugin data for {plugin_name}")
        return []


async def async_get_plugin_data(plugin_name: str) -> List[Any]:
    """
    Get all data for a given plugin asynchronously.

    Args:
        plugin_name (str): The name of the plugin whose data is to be retrieved.

    Returns:
        list: A list of all deserialized plugin data entries, or an empty list if no data is found or on error.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT data FROM plugin_data WHERE plugin_name=? ",
                (plugin_name,),
            )
            results = await cursor.fetchall()

            data_list = []
            for result in results:
                try:
                    data_list.append(json.loads(result[0]))
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in plugin data for {plugin_name}")
            return data_list
    except sqlite3.Error:
        logger.exception(f"Failed to retrieve plugin data for {plugin_name}")
        return []


async def async_get_longname(meshtastic_id: str) -> Optional[str]:
    """
    Retrieve the long name for a given Meshtastic ID asynchronously.

    Args:
        meshtastic_id (str): The Meshtastic node ID.

    Returns:
        str | None: The long name if found, otherwise None.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT longname FROM longnames WHERE meshtastic_id=?", (meshtastic_id,)
            )
            result = await cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error:
        logger.exception(f"Failed to retrieve longname for {meshtastic_id}")
        return None


async def async_save_longname(meshtastic_id: str, longname: str):
    """
    Save the long name for a given Meshtastic ID asynchronously.

    Args:
        meshtastic_id (str): The Meshtastic node ID.
        longname: The full/display name to store for the node (string).
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "INSERT OR REPLACE INTO longnames (meshtastic_id, longname) VALUES (?, ?)",
                (meshtastic_id, longname),
            )
            await conn.commit()
    except sqlite3.Error:
        logger.exception(f"Failed to save longname for {meshtastic_id}")
        raise


async def async_get_shortname(meshtastic_id: str) -> Optional[str]:
    """
    Retrieve the short name for a given Meshtastic ID asynchronously.

    Args:
        meshtastic_id (str): The Meshtastic node ID.

    Returns:
        str or None: The short name if found, or None if not found or on database error.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT shortname FROM shortnames WHERE meshtastic_id=?",
                (meshtastic_id,),
            )
            result = await cursor.fetchone()
            return result[0] if result else None
    except sqlite3.Error:
        logger.exception(f"Failed to retrieve shortname for {meshtastic_id}")
        return None


async def async_save_shortname(meshtastic_id: str, shortname: str):
    """
    Save the short name for a given Meshtastic ID asynchronously.

    Args:
        meshtastic_id (str): The Meshtastic node ID.
        shortname (str): Display name to store for the node.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "INSERT OR REPLACE INTO shortnames (meshtastic_id, shortname) VALUES (?, ?)",
                (meshtastic_id, shortname),
            )
            await conn.commit()
    except sqlite3.Error:
        logger.exception(f"Failed to save shortname for {meshtastic_id}")
        raise


async def async_store_message_map(
    meshtastic_id: str,
    matrix_event_id: str,
    matrix_room_id: str,
    meshtastic_text: Optional[str] = None,
    meshtastic_meshnet: Optional[str] = None,
):
    """
    Store a mapping between Meshtastic message ID and Matrix event ID asynchronously.

    Args:
        meshtastic_id: The unique identifier for the Meshtastic message.
        matrix_event_id: The unique identifier for the Matrix event.
        matrix_room_id: The Matrix room ID where the message was sent.
        meshtastic_text: Optional text content of the Meshtastic message.
        meshtastic_meshnet: Optional name of the meshnet where the message originated, used to distinguish remote from local mesh origins.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            logger.debug(
                f"Storing message map: meshtastic_id={meshtastic_id}, matrix_event_id={matrix_event_id}, matrix_room_id={matrix_room_id}, meshtastic_text={meshtastic_text}, meshtastic_meshnet={meshtastic_meshnet}"
            )
            await cursor.execute(
                """
                INSERT OR REPLACE INTO message_map 
                (meshtastic_id, matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) 
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    meshtastic_id,
                    matrix_event_id,
                    matrix_room_id,
                    meshtastic_text,
                    meshtastic_meshnet,
                ),
            )
            await conn.commit()
    except sqlite3.Error:
        logger.exception("Failed to store message map")
        raise


async def async_get_message_map_by_meshtastic_id(
    meshtastic_id: str,
) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """
    Retrieve Matrix event information for a given Meshtastic message ID asynchronously.

    Args:
        meshtastic_id (str): The unique identifier for the Meshtastic message.

    Returns:
        tuple or None: A tuple (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if found and valid, or None if not found, on malformed data, or if a database error occurs.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE meshtastic_id=?",
                (meshtastic_id,),
            )
            result = await cursor.fetchone()

            if result:
                try:
                    # Validate that we have the expected number of fields
                    if len(result) >= 2:
                        matrix_event_id, matrix_room_id = result[0], result[1]
                        meshtastic_text = result[2] if len(result) > 2 else None
                        meshtastic_meshnet = result[3] if len(result) > 3 else None

                        # Validate that required fields are not empty
                        if matrix_event_id and matrix_room_id:
                            return (
                                matrix_event_id,
                                matrix_room_id,
                                meshtastic_text,
                                meshtastic_meshnet,
                            )
                        else:
                            logger.warning(
                                f"Invalid message map data for meshtastic_id {meshtastic_id}: missing required fields"
                            )
                            return None
                    else:
                        logger.warning(
                            f"Invalid message map data for meshtastic_id {meshtastic_id}: insufficient fields"
                        )
                        return None
                except (ValueError, IndexError) as e:
                    logger.warning(
                        f"Invalid message map data for meshtastic_id {meshtastic_id}: {e}"
                    )
                    return None
            return None
    except (UnicodeDecodeError, sqlite3.Error) as e:
        logger.error(
            f"Database error retrieving message map for meshtastic_id {meshtastic_id}: {e}"
        )
        return None


async def async_get_message_map_by_matrix_event_id(
    matrix_event_id: str,
) -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """
    Retrieve Meshtastic message information for a given Matrix event ID asynchronously.

    Args:
        matrix_event_id (str): The unique identifier for the Matrix event.

    Returns:
        tuple or None: A tuple (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet) if found, or None if not found or on error.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute(
                "SELECT meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet FROM message_map WHERE matrix_event_id=?",
                (matrix_event_id,),
            )
            result = await cursor.fetchone()

            if result:
                try:
                    # Validate that we have the expected number of fields
                    if len(result) >= 2:
                        meshtastic_id, matrix_room_id = result[0], result[1]
                        meshtastic_text = result[2] if len(result) > 2 else None
                        meshtastic_meshnet = result[3] if len(result) > 3 else None

                        # Validate that required fields are not empty
                        if meshtastic_id and matrix_room_id:
                            return (
                                meshtastic_id,
                                matrix_room_id,
                                meshtastic_text,
                                meshtastic_meshnet,
                            )
                        else:
                            logger.warning(
                                f"Invalid message map data for matrix_event_id {matrix_event_id}: missing required fields"
                            )
                            return None
                    else:
                        logger.warning(
                            f"Invalid message map data for matrix_event_id {matrix_event_id}: insufficient fields"
                        )
                        return None
                except (ValueError, IndexError) as e:
                    logger.warning(
                        f"Invalid message map data for matrix_event_id {matrix_event_id}: {e}"
                    )
                    return None
            return None
    except sqlite3.Error as e:
        logger.error(
            f"Database error retrieving message map for matrix_event_id {matrix_event_id}: {e}"
        )
        return None


async def async_wipe_message_map():
    """
    Remove all entries from the message_map table asynchronously.

    Useful when database.msg_map.wipe_on_restart or db.msg_map.wipe_on_restart is True,
    ensuring no stale data remains.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            await cursor.execute("DELETE FROM message_map")
            await conn.commit()
        logger.info("message_map table wiped successfully.")
    except sqlite3.Error:
        logger.exception("Failed to wipe message_map table")
        raise


async def async_prune_message_map(msgs_to_keep: int):
    """
    Prune old entries from the message_map table asynchronously.

    Keeps only the most recent N entries based on rowid.

    Args:
        msgs_to_keep (int): Number of recent messages to keep.

    Logic:
    - Count total rows.
    - If total > msgs_to_keep, delete oldest entries based on rowid.
    """
    try:
        async with _get_async_db_connection() as conn:
            cursor = await conn.cursor()
            # Count total entries
            await cursor.execute("SELECT COUNT(*) FROM message_map")
            total_entries = (await cursor.fetchone())[0]

            if total_entries > msgs_to_keep:
                # Calculate how many to delete
                entries_to_delete = total_entries - msgs_to_keep

                # Delete oldest entries based on rowid
                await cursor.execute(
                    """
                    DELETE FROM message_map 
                    WHERE rowid IN (
                        SELECT rowid FROM message_map 
                        ORDER BY rowid ASC 
                        LIMIT ?
                    )
                    """,
                    (entries_to_delete,),
                )
                await conn.commit()
                logger.info(
                    f"Pruned {entries_to_delete} old entries from message_map table."
                )
            else:
                logger.debug(
                    f"No pruning needed: {total_entries} entries <= {msgs_to_keep} limit"
                )
    except sqlite3.Error:
        logger.exception("Failed to prune message_map table")
        raise
