import asyncio
import io
import re
import ssl
import time
from typing import List, Union

import certifi
import meshtastic.protobuf.portnums_pb2
from nio import (
    AsyncClient,
    AsyncClientConfig,
    MatrixRoom,
    RoomMessageNotice,
    RoomMessageText,
    UploadResponse,
    WhoamiError,
    ReactionEvent,
)

from PIL import Image

from config import relay_config
from log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from meshtastic_utils import connect_meshtastic

# Extract Matrix configuration
matrix_homeserver = relay_config["matrix"]["homeserver"]
matrix_rooms: List[dict] = relay_config["matrix_rooms"]
matrix_access_token = relay_config["matrix"]["access_token"]

bot_user_id = relay_config["matrix"]["bot_user_id"]
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * 1000
)  # Timestamp when the bot starts, used to filter out old messages

logger = get_logger(name="Matrix")

matrix_client = None


def bot_command(command, payload):
    return f"{bot_user_name}: !{command}" in payload


async def connect_matrix():
    """
    Establish a connection to the Matrix homeserver.
    """
    global matrix_client
    global bot_user_name
    if matrix_client:
        return matrix_client

    # Create SSL context using certifi's certificates
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Initialize the Matrix client with custom SSL context
    config = AsyncClientConfig(encryption_enabled=False)
    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        config=config,
        ssl=ssl_context,
    )

    # Set the access_token and user_id
    matrix_client.access_token = matrix_access_token
    matrix_client.user_id = bot_user_id

    # Attempt to retrieve the device_id using whoami()
    whoami_response = await matrix_client.whoami()
    if isinstance(whoami_response, WhoamiError):
        logger.error(f"Failed to retrieve device_id: {whoami_response.message}")
        matrix_client.device_id = None
    else:
        matrix_client.device_id = whoami_response.device_id
        if matrix_client.device_id:
            logger.debug(f"Retrieved device_id: {matrix_client.device_id}")
        else:
            logger.warning("device_id not returned by whoami()")

    # Fetch the bot's display name
    response = await matrix_client.get_displayname(bot_user_id)
    if hasattr(response, "displayname"):
        bot_user_name = response.displayname
    else:
        bot_user_name = bot_user_id  # Fallback if display name is not set

    return matrix_client


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """Join a Matrix room by its ID or alias."""
    try:
        if room_id_or_alias.startswith("#"):
            response = await matrix_client.room_resolve_alias(room_id_or_alias)
            if not response.room_id:
                logger.error(
                    f"Failed to resolve room alias '{room_id_or_alias}': {response.message}"
                )
                return
            room_id = response.room_id
            # Update the room ID in the matrix_rooms list
            for room_config in matrix_rooms:
                if room_config["id"] == room_id_or_alias:
                    room_config["id"] = room_id
                    break

        else:
            room_id = room_id_or_alias

        if room_id not in matrix_client.rooms:
            response = await matrix_client.join(room_id)
            if response and hasattr(response, "room_id"):
                logger.info(f"Joined room '{room_id_or_alias}' successfully")
            else:
                logger.error(
                    f"Failed to join room '{room_id_or_alias}': {response.message}"
                )
        else:
            logger.debug(f"Bot is already in room '{room_id_or_alias}'")
    except Exception as e:
        logger.error(f"Error joining room '{room_id_or_alias}': {e}")


# Add ReactionEvent to callbacks so we catch m.reaction events
# Previously we only listened for (RoomMessageText, RoomMessageNotice)
# Now we include ReactionEvent as well
async def matrix_relay(room_id, message, longname, shortname, meshnet_name, portnum, meshtastic_id=None, meshtastic_replyId=None, meshtastic_text=None, emote=False, emoji=False):
    matrix_client = await connect_matrix()
    try:
        content = {
            "msgtype": "m.text" if not emote else "m.emote",
            "body": message,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": meshnet_name,
            "meshtastic_portnum": portnum,
        }
        if meshtastic_id is not None:
            content["meshtastic_id"] = meshtastic_id
        if meshtastic_replyId is not None:
            content["meshtastic_replyId"] = meshtastic_replyId
        if meshtastic_text is not None:
            content["meshtastic_text"] = meshtastic_text
        if emoji:
            content["meshtastic_emoji"] = 1

        # Increased timeout to give matrix more time to respond
        response = await asyncio.wait_for(
            matrix_client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            ),
            timeout=5.0,  # Changed from 0.5 to 5.0 seconds
        )
        logger.info(f"Sent inbound radio message to matrix room: {room_id}")

        # Only store if this is not a reaction message (emote=True with emoji=True is a reaction)
        # For inbound meshtastic messages, we store them here as before.
        # Already handled meshtastic->matrix message storing logic here.
        if meshtastic_id is not None and not emote:
            from db_utils import store_message_map
            store_message_map(meshtastic_id, response.event_id, room_id, meshtastic_text if meshtastic_text else message)

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception as e:
        logger.error(f"Error sending radio message to matrix room {room_id}: {e}")


def truncate_message(
    text, max_bytes=227
):  # 227 is the maximum that we can run without an error so far. 228 throws an error.
    """
    Truncate the given text to fit within the specified byte size.

    :param text: The text to truncate.
    :param max_bytes: The maximum allowed byte size for the truncated text.
    :return: The truncated text.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom, event: Union[RoomMessageText, RoomMessageNotice, ReactionEvent]
) -> None:
    from db_utils import get_message_map_by_matrix_event_id, store_message_map
    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    # We do not relay the past
    if message_timestamp < bot_start_time:
        return

    room_config = None
    for config in matrix_rooms:
        if config["id"] == room.room_id:
            room_config = config
            break

    # Only relay supported rooms
    if not room_config:
        return

    # Check if this is a reaction event
    # Reaction events come as ReactionEvent now
    relates_to = event.source["content"].get("m.relates_to")
    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None
    # If event is ReactionEvent, it must have relates_to for sure
    # ReactionEvent does not have a body. We rely on m.relates_to
    if isinstance(event, ReactionEvent):
        # ReactionEvent has the reaction key in m.relates_to["key"]
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            logger.debug(f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}")

    # For normal messages (RoomMessageText, RoomMessageNotice) we use event.body
    text = event.body.strip() if (not is_reaction and hasattr(event, "body")) else ""

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    suppress = event.source["content"].get("mmrelay_suppress")
    local_meshnet_name = relay_config["meshtastic"]["meshnet_name"]
    relay_reactions = relay_config["meshtastic"].get("relay_reactions", True)

    # Do not process
    if suppress:
        return

    if is_reaction and relay_reactions:
        # We have a Matrix reaction
        # Get the original message from DB
        if original_matrix_event_id:
            orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
            logger.debug(f"Database lookup result for matrix event ID {original_matrix_event_id}: {orig}")
            if orig:
                meshtastic_id, matrix_room_id, meshtastic_text = orig
                display_name_response = await matrix_client.get_displayname(event.sender)
                full_display_name = display_name_response.displayname or event.sender
                short_display_name = full_display_name[:5]
                prefix = f"{short_display_name}[M]: "
                abbreviated_text = meshtastic_text[:40] + "..." if len(meshtastic_text) > 40 else meshtastic_text
                reaction_message = f"{prefix}reacted {reaction_emoji} to \"{abbreviated_text}\""
                meshtastic_interface = connect_meshtastic()
                from meshtastic_utils import logger as meshtastic_logger
                meshtastic_channel = room_config["meshtastic_channel"]
                if relay_config["meshtastic"]["broadcast_enabled"]:
                    meshtastic_logger.info(
                        f"Relaying reaction from {full_display_name} to radio broadcast"
                    )
                    # Relay the reaction to the meshnet
                    logger.debug(f"Sending reaction to Meshtastic: {reaction_message}")
                    meshtastic_interface.sendText(
                        text=reaction_message, channelIndex=meshtastic_channel
                    )
        return

    if longname and meshnet_name:
        full_display_name = f"{longname}/{meshnet_name}"
        if meshnet_name != local_meshnet_name:
            # A message from a remote meshnet
            logger.info(f"Processing message from remote meshnet: {text}")
            short_meshnet_name = meshnet_name[:4]
            # If shortname is None, truncate the longname to 3 characters
            if shortname is None:
                shortname = longname[:3]
            text = re.sub(
                rf"^\[{full_display_name}\]: ", "", text
            )  # Remove the original prefix from the text
            text = truncate_message(text)
            full_message = f"{shortname}/{short_meshnet_name}: {text}"
        else:
            # This is a message from a local user, it should be ignored
            return

    else:
        display_name_response = await matrix_client.get_displayname(event.sender)
        full_display_name = display_name_response.displayname or event.sender
        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        text = truncate_message(text)

    # Plugin functionality
    from plugin_loader import load_plugins
    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            found_matching_plugin = await plugin.handle_room_message(
                room, event, full_message
            )
            if found_matching_plugin:
                logger.debug(f"Processed by plugin {plugin.plugin_name}")

    # Check if the message is a command directed at the bot
    is_command = False
    for plugin in plugins:
        for command in plugin.get_matrix_commands():
            if bot_command(command, text):
                is_command = True
                break
        if is_command:
            break

    # Store this matrix-originated message in DB so we can reference it later for reactions
    # Only store if it's not a reaction and not a command, and from a mapped room
    # This ensures that all normal matrix messages are recorded
    if not is_reaction and not is_command and event.sender != bot_user_id:
        logger.debug(f"Storing matrix message: event_id={event.event_id}, room_id={room.room_id}, text={text}")
        store_message_map(None, event.event_id, room.room_id, text)

    if is_command:
        logger.debug("Message is a command, not sending to mesh")
        return

    meshtastic_interface = connect_meshtastic()
    from meshtastic_utils import logger as meshtastic_logger

    meshtastic_channel = room_config["meshtastic_channel"]

    if not found_matching_plugin and event.sender != bot_user_id:
        if relay_config["meshtastic"]["broadcast_enabled"]:
            if (
                event.source["content"].get("meshtastic_portnum")
                == "DETECTION_SENSOR_APP"
            ):
                if relay_config["meshtastic"].get("detection_sensor", False):
                    meshtastic_interface.sendData(
                        data=full_message.encode("utf-8"),
                        channelIndex=meshtastic_channel,
                        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
                    )
                else:
                    meshtastic_logger.debug(
                        f"Detection sensor packet received from {full_display_name}, "
                        + "but detection sensor processing is disabled."
                    )
            else:
                meshtastic_logger.info(
                    f"Relaying message from {full_display_name} to radio broadcast"
                )
                meshtastic_interface.sendText(
                    text=full_message, channelIndex=meshtastic_channel
                )
        else:
            logger.debug(
                f"Broadcast not supported: Message from {full_display_name} dropped."
            )


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> UploadResponse:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_data = buffer.getvalue()

    response, maybe_keys = await client.upload(
        io.BytesIO(image_data),
        content_type="image/png",
        filename=filename,
        filesize=len(image_data),
    )

    return response


async def send_room_image(
    client: AsyncClient, room_id: str, upload_response: UploadResponse
):
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={"msgtype": "m.image", "url": upload_response.content_uri, "body": ""},
    )
