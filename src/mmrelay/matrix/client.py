import ssl
import time
import asyncio # Added for close/logout calls

import certifi
from nio import (
    AsyncClient,
    AsyncClientConfig,
    MatrixRoom, # Not directly used here but good for context
    WhoamiError,
)

from mmrelay.log_utils import get_logger

# Global config variable that will be set from config.py by connect_matrix
config = None

matrix_client: AsyncClient = None
matrix_homeserver = None
matrix_rooms = None # This will be a list of room configs
matrix_access_token = None
bot_user_id = None
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * 1000
)  # Timestamp when the bot starts, used to filter out old messages

logger = get_logger(name="MatrixClient")

def get_matrix_client() -> AsyncClient:
    return matrix_client

def get_bot_start_time() -> int:
    return bot_start_time

def get_bot_user_id() -> str:
    return bot_user_id

def get_bot_user_name() -> str:
    return bot_user_name

def get_matrix_rooms_config() -> list:
    return matrix_rooms

def get_matrix_config() -> dict:
    return config['matrix'] if config and 'matrix' in config else None

async def connect_matrix(passed_config=None):
    global matrix_client, bot_user_name, matrix_homeserver, matrix_rooms
    global matrix_access_token, bot_user_id, config, bot_start_time # Added bot_start_time init here

    if passed_config is not None:
        config = passed_config
        # Re-initialize bot_start_time when config is passed, e.g. on startup/reconnect
        bot_start_time = int(time.time() * 1000)


    if config is None:
        logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    matrix_cfg = config["matrix"]
    matrix_homeserver = matrix_cfg["homeserver"]
    matrix_rooms = config["matrix_rooms"] # This is top-level
    matrix_access_token = matrix_cfg["access_token"]
    bot_user_id = matrix_cfg["bot_user_id"]

    if matrix_client:
        if matrix_client.user_id == bot_user_id and matrix_client.access_token == matrix_access_token:
             logger.debug("Returning existing Matrix client.")
             return matrix_client
        else:
            logger.info("Matrix client exists but config changed or stale. Reconnecting.")
            try:
                if hasattr(matrix_client, 'logout') and asyncio.iscoroutinefunction(matrix_client.logout):
                    await matrix_client.logout()
                if hasattr(matrix_client, 'close') and asyncio.iscoroutinefunction(matrix_client.close):
                    await matrix_client.close()
            except Exception as e:
                logger.error(f"Error closing existing matrix client: {e}")
            matrix_client = None


    ssl_context = ssl.create_default_context(cafile=certifi.where())
    # Original code used encryption_enabled=False, ensure this matches if Matrix server is plain HTTP or for specific setups
    client_config = AsyncClientConfig(encryption_enabled=True if "https" in matrix_homeserver else False)

    new_matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        config=client_config,
        ssl=ssl_context,
    )
    new_matrix_client.access_token = matrix_access_token
    new_matrix_client.user_id = bot_user_id

    try:
        whoami_response = await new_matrix_client.whoami()
        if isinstance(whoami_response, WhoamiError):
            logger.error(f"Failed to retrieve device_id: {whoami_response.message}")
            new_matrix_client.device_id = None
        else:
            new_matrix_client.device_id = whoami_response.device_id
            if new_matrix_client.device_id:
                logger.debug(f"Retrieved device_id: {new_matrix_client.device_id}")
            else:
                logger.warning("device_id not returned by whoami()")
    except Exception as e:
        logger.error(f"Error during whoami call: {e}")
        # Depending on severity, might want to not assign client or return None
        # For now, proceed to get display name

    try:
        response = await new_matrix_client.get_displayname(bot_user_id)
        if hasattr(response, "displayname") and response.displayname:
            bot_user_name = response.displayname
        else:
            bot_user_name = bot_user_id # Fallback
    except Exception as e:
        logger.error(f"Error fetching display name for {bot_user_id}: {e}")
        bot_user_name = bot_user_id # Fallback

    logger.info(f"Matrix bot display name set to: {bot_user_name}")

    matrix_client = new_matrix_client # Assign new client to global
    return matrix_client


async def join_matrix_room(room_id_or_alias: str) -> None:
    global matrix_rooms

    client = get_matrix_client()
    if not client:
        logger.error("Matrix client not initialized. Cannot join room.")
        return

    try:
        resolved_room_id = room_id_or_alias
        if room_id_or_alias.startswith("#"):
            logger.debug(f"Resolving room alias '{room_id_or_alias}'")
            response = await client.room_resolve_alias(room_id_or_alias)
            if not response or not hasattr(response, 'room_id') or not response.room_id:
                err_msg = response.message if hasattr(response, 'message') else 'Unknown error'
                logger.error(
                    f"Failed to resolve room alias '{room_id_or_alias}': {err_msg}"
                )
                return
            resolved_room_id = response.room_id
            logger.debug(f"Alias '{room_id_or_alias}' resolved to room ID '{resolved_room_id}'")

            if matrix_rooms: # Ensure matrix_rooms is populated
                for room_config in matrix_rooms:
                    if room_config.get("id") == room_id_or_alias:
                        logger.debug(f"Updating room ID in config from {room_id_or_alias} to {resolved_room_id}")
                        room_config["id"] = resolved_room_id
                        break
            else:
                logger.warning("matrix_rooms global not set or empty, cannot update resolved alias in config.")

        # Check if already in room using client.rooms (values are MatrixRoom objects)
        if resolved_room_id not in client.rooms:
            logger.info(f"Attempting to join room '{resolved_room_id}' (original: '{room_id_or_alias}')")
            join_response = await client.join(resolved_room_id)
            if join_response and hasattr(join_response, "room_id"):
                logger.info(f"Joined room '{resolved_room_id}' successfully")
            else:
                err_msg = join_response.message if hasattr(join_response, 'message') else 'Unknown error'
                logger.error(
                    f"Failed to join room '{resolved_room_id}': {err_msg}"
                )
        else:
            logger.debug(f"Bot is already in room '{resolved_room_id}'")
    except Exception as e:
        logger.error(f"Error joining room '{room_id_or_alias}': {e}", exc_info=True)
