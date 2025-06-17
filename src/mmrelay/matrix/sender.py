import asyncio
import io

from nio import AsyncClient, UploadResponse # AsyncClient not directly used, but UploadResponse is
from PIL import Image # Needed for upload_image type hint if we add one

from mmrelay.db_utils import (
    # get_message_map_by_matrix_event_id, # Not used in this file
    prune_message_map,
    store_message_map,
)
from mmrelay.log_utils import get_logger
# connect_matrix is needed if relay_meshtastic_to_matrix might be called before client is surely up.
# get_matrix_client is the primary way to get the client.
# config (the global variable) from client module is used to access main app config.
from mmrelay.matrix.client import connect_matrix, get_matrix_client, config as global_app_config

logger = get_logger(name="MatrixSender")

async def relay_meshtastic_to_matrix(
    room_id,
    message,
    longname,
    shortname,
    meshnet_name,
    portnum,
    meshtastic_id=None,
    meshtastic_replyId=None,
    meshtastic_text=None,
    emote=False,
    emoji=False,
):
    # Use the config imported from mmrelay.matrix.client
    current_config = global_app_config
    if not current_config:
        logger.error("Main configuration not available from matrix.client. Cannot relay message to Matrix.")
        # Optionally, try to call connect_matrix if essential, but it needs the full config.
        # For now, error out if config isn't pre-loaded via connect_matrix in main flow.
        return

    # Get client, attempting connection if necessary (connect_matrix handles existing client)
    # Pass current_config to connect_matrix if it might not have been initialized with it yet.
    client = await connect_matrix(current_config)

    if not client:
        logger.error("Matrix client is None (connection failed or not initialized). Cannot send message.")
        return

    # Config access for specific settings
    relay_reactions = current_config.get("meshtastic", {}).get("relay_reactions", False)
    database_config = current_config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})
    if not msg_map_config: # Legacy db config fallback
        db_config = current_config.get("db", {})
        legacy_msg_map_config = db_config.get("msg_map", {})
        if legacy_msg_map_config:
            msg_map_config = legacy_msg_map_config
            logger.warning("Using 'db.msg_map' (legacy). 'database.msg_map' is preferred.")
    msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)

    try:
        # Ensure 'meshtastic' key exists before accessing 'meshnet_name'
        local_meshnet_name = current_config.get("meshtastic", {}).get("meshnet_name", "UnknownMeshnet")

        content = {
            "msgtype": "m.text" if not emote else "m.emote",
            "body": message,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": local_meshnet_name,
            "meshtastic_portnum": str(portnum), # Ensure portnum is string if it could be enum/int
        }
        if meshtastic_id is not None: content["meshtastic_id"] = str(meshtastic_id) # Ensure IDs are strings
        if meshtastic_replyId is not None: content["meshtastic_replyId"] = str(meshtastic_replyId)
        if meshtastic_text is not None: content["meshtastic_text"] = meshtastic_text
        if emoji: content["meshtastic_emoji"] = 1 # Matrix spec doesn't have this, custom field

        response = await asyncio.wait_for(
            client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            ),
            timeout=10.0,
        )
        logger.info(f"Sent inbound radio message to matrix room: {room_id}")
        if hasattr(response, "event_id"): logger.debug(f"Message event_id: {response.event_id}")

        if relay_reactions and meshtastic_id is not None and not emote and hasattr(response, "event_id"):
            store_message_map(
                str(meshtastic_id), # Ensure ID is string for DB consistency
                response.event_id,
                room_id,
                meshtastic_text if meshtastic_text else message, # Original text for map
                meshtastic_meshnet=local_meshnet_name,
            )
            logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")
            if msgs_to_keep > 0: prune_message_map(msgs_to_keep)

    except asyncio.TimeoutError:
        logger.error(f"Timeout sending message to Matrix room {room_id}")
    except Exception as e:
        logger.error(f"Error sending radio message to matrix room {room_id}: {e}", exc_info=True)


async def upload_image(image_pil: Image.Image, filename: str) -> UploadResponse:
    client = get_matrix_client()
    if not client:
        logger.error("Matrix client not available for image upload.")
        return None

    buffer = io.BytesIO()
    image_pil.save(buffer, format="PNG")
    image_data = buffer.getvalue()

    try:
        response, _ = await client.upload( # Ignoring 'maybe_keys'
            io.BytesIO(image_data),
            content_type="image/png",
            filename=filename,
            filesize=len(image_data),
        )
        return response
    except Exception as e:
        logger.error(f"Error uploading image '{filename}': {e}", exc_info=True)
        return None


async def send_room_image(room_id: str, upload_response: UploadResponse):
    client = get_matrix_client()
    if not client:
        logger.error("Matrix client not available for sending image to room.")
        return
    if not upload_response or not hasattr(upload_response, 'content_uri'):
        logger.error("Invalid upload_response provided to send_room_image.")
        return

    try:
        await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.image", "url": upload_response.content_uri, "body": ""}, # Empty body is typical for images
        )
        logger.info(f"Sent image to room {room_id} with URI {upload_response.content_uri}")
    except Exception as e:
        logger.error(f"Error sending image to room {room_id}: {e}", exc_info=True)
