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
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    UploadResponse,
    WhoamiError,
)
from PIL import Image

from mmrelay.config import config
from mmrelay.db_utils import (
    get_message_map_by_matrix_event_id,
    prune_message_map,
    store_message_map,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic

# Extract Matrix configuration
matrix_homeserver = config.matrix_homeserver
matrix_rooms: List[dict] = config.matrix_rooms
matrix_access_token = config.matrix_access_token

bot_user_id = config.matrix_bot_user_id
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * 1000
)  # Timestamp when the bot starts, used to filter out old messages

logger = get_logger(name="Matrix")

matrix_client = None


def bot_command(command, event):
    """
    Checks if the given command is directed at the bot,
    accounting for variations in different Matrix clients.
    """
    full_message = event.body.strip()
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    # Check if the message starts with bot_user_id or bot_user_name
    if full_message.startswith(bot_user_id) or text_content.startswith(bot_user_id):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    elif full_message.startswith(bot_user_name) or text_content.startswith(
        bot_user_name
    ):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    else:
        return False


async def connect_matrix():
    """
    Establish a connection to the Matrix homeserver.
    Sets global matrix_client and detects the bot's display name.
    """
    global matrix_client
    global bot_user_name
    if matrix_client:
        return matrix_client

    # Create SSL context using certifi's certificates
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Initialize the Matrix client with custom SSL context
    config_matrix = AsyncClientConfig(encryption_enabled=False)
    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        config=config_matrix,
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
            # If it's a room alias, resolve it to a room ID
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

        # Attempt to join the room if not already joined
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


async def matrix_relay(
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
    """
    Relay a message from Meshtastic to Matrix, optionally storing message maps.

    IMPORTANT CHANGE: Now, we only store message maps if `relay_reactions` is True.
    If `relay_reactions` is False, we skip storing to the message map entirely.
    This helps maintain privacy and prevents message_map usage unless needed.

    Additionally, if `msgs_to_keep` > 0, we prune the oldest messages after storing
    to prevent database bloat and maintain privacy.
    """
    matrix_client = await connect_matrix()

    relay_reactions = config.meshtastic_relay_reactions
    msgs_to_keep = config.db_msgs_to_keep

    try:
        local_meshnet_name = config.meshtastic_meshnet_name
        content = {
            "msgtype": "m.text" if not emote else "m.emote",
            "body": message,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": local_meshnet_name,
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

        response = await asyncio.wait_for(
            matrix_client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content=content,
            ),
            timeout=5.0,
        )
        logger.info(f"Sent inbound radio message to matrix room: {room_id}")

        if relay_reactions and meshtastic_id is not None and not emote:
            store_message_map(
                meshtastic_id,
                response.event_id,
                room_id,
                meshtastic_text if meshtastic_text else message,
                meshtastic_meshnet=local_meshnet_name,
            )
            if msgs_to_keep > 0:
                prune_message_map(msgs_to_keep)

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception as e:
        logger.error(f"Error sending radio message to matrix room {room_id}: {e}")


def truncate_message(text, max_bytes=227):
    """
    Truncate the given text to fit within the specified byte size.

    :param text: The text to truncate.
    :param max_bytes: The maximum allowed byte size for the truncated text.
    :return: The truncated text.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Remove lines that begin with '>' to avoid including
    the original quoted part of a Matrix reply in reaction text.
    """
    lines = text.splitlines()
    filtered = [line for line in lines if not line.strip().startswith(">")]
    return " ".join(filtered).strip()

# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: Union[RoomMessageText, RoomMessageNotice, ReactionEvent, RoomMessageEmote],
) -> None:
    """
    Handle new messages and reactions in Matrix. For reactions, we ensure that when relaying back
    to Meshtastic, we always apply our local meshnet_name to outgoing events.

    We must be careful not to relay reactions to reactions (reaction-chains),
    especially remote reactions that got relayed into the room as m.emote events,
    as we do not store them in the database. If we can't find the original message in the DB,
    it likely means it's a reaction to a reaction, and we stop there.

    Additionally, we only deal with message_map storage (and thus reaction linking)
    if relay_reactions is True. If it's False, none of these mappings are stored or used.
    """
    from mmrelay.plugin_loader import load_plugins
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    if message_timestamp < bot_start_time:
        return

    room_config = None
    for config_room in matrix_rooms:
        if config_room["id"] == room.room_id:
            room_config = config_room
            break

    if not room_config:
        return

    relates_to = event.source["content"].get("m.relates_to")
    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None

    relay_reactions = config.meshtastic_relay_reactions

    if isinstance(event, ReactionEvent):
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            logger.debug(
                f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}"
            )

    if isinstance(event, RoomMessageEmote):
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        is_reaction = True
        reaction_body = event.source["content"].get("body", "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body)
        reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"

    text = event.body.strip() if (not is_reaction and hasattr(event, "body")) else ""

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

    if suppress:
        return

    if is_reaction and not relay_reactions:
        logger.debug(
            "Reaction event encountered but relay_reactions is disabled. Doing nothing."
        )
        return

    local_meshnet_name = config.meshtastic_meshnet_name

    # Remote reaction from remote meshnet
    if (
        is_reaction
        and relay_reactions
        and meshnet_name
        and meshnet_name != local_meshnet_name
        and meshtastic_replyId
        and isinstance(event, RoomMessageEmote)
    ):
        logger.info(f"Relaying reaction from remote meshnet: {meshnet_name}")

        short_meshnet_name = meshnet_name[:4]
        if not shortname:
            shortname = longname[:3] if longname else "???"

        meshtastic_text_db = event.source["content"].get("meshtastic_text", "")
        meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
        meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace("\r", " ")

        abbreviated_text = (
            meshtastic_text_db[:40] + "..."
            if len(meshtastic_text_db) > 40
            else meshtastic_text_db
        )

        reaction_message = f'{shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

        meshtastic_interface = connect_meshtastic()
        meshtastic_channel = room_config["meshtastic_channel"]

        if config.meshtastic_broadcast_enabled:
            meshtastic_logger.info(
                f"Relaying reaction from remote meshnet {meshnet_name} to radio broadcast"
            )
            logger.debug(
                f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
            )
            meshtastic_interface.sendText(
                text=reaction_message, channelIndex=meshtastic_channel
            )
        return

    if is_reaction and relay_reactions and original_matrix_event_id:
        orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
        if not orig:
            logger.debug(
                "Original message for reaction not found in DB. Possibly a reaction-to-reaction scenario. Not forwarding."
            )
            return

        meshtastic_id, matrix_room_id, meshtastic_text_db, meshtastic_meshnet_db = orig
        display_name_response = await matrix_client.get_displayname(event.sender)
        full_display_name = display_name_response.displayname or event.sender

        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "

        meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
        meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace("\r", " ")

        abbreviated_text = (
            meshtastic_text_db[:40] + "..."
            if len(meshtastic_text_db) > 40
            else meshtastic_text_db
        )

        reaction_message = (
            f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
        )
        meshtastic_interface = connect_meshtastic()
        meshtastic_channel = room_config["meshtastic_channel"]

        if config.meshtastic_broadcast_enabled:
            meshtastic_logger.info(
                f"Relaying reaction from {full_display_name} to radio broadcast"
            )
            logger.debug(
                f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
            )
            meshtastic_interface.sendText(
                text=reaction_message, channelIndex=meshtastic_channel
            )
        return

    # Matrix message from remote meshnet
    if longname and meshnet_name:
        full_display_name = f"{longname}/{meshnet_name}"

        if meshnet_name != local_meshnet_name:
            logger.info(f"Processing message from remote meshnet: {meshnet_name}")
            short_meshnet_name = meshnet_name[:4]
            if shortname is None:
                shortname = longname[:3] if longname else "???"
            text = re.sub(rf"^\[{full_display_name}\]: ", "", text)
            text = truncate_message(text)
            full_message = f"{shortname}/{short_meshnet_name}: {text}"
        else:
            return
    else:
        display_name_response = await matrix_client.get_displayname(event.sender)
        full_display_name = display_name_response.displayname or event.sender
        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        text = truncate_message(text)

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            found_matching_plugin = await plugin.handle_room_message(
                room, event, full_message
            )
            if found_matching_plugin:
                logger.debug(f"Processed by plugin {plugin.plugin_name}")

    is_command = False
    for plugin in plugins:
        for command in plugin.get_matrix_commands():
            if bot_command(command, event):
                is_command = True
                break
        if is_command:
            break

    if is_command:
        logger.debug("Message is a command, not sending to mesh")
        return

    meshtastic_interface = connect_meshtastic()
    meshtastic_channel = room_config["meshtastic_channel"]

    if not found_matching_plugin and event.sender != bot_user_id:
        if config.meshtastic_broadcast_enabled:
            portnum = event.source["content"].get("meshtastic_portnum")
            if portnum == "DETECTION_SENSOR_APP":
                if config.meshtastic_detection_sensor:
                    sent_packet = meshtastic_interface.sendData(
                        data=full_message.encode("utf-8"),
                        channelIndex=meshtastic_channel,
                        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
                    )
                    if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                        store_message_map(
                            sent_packet.id,
                            event.event_id,
                            room.room_id,
                            text,
                            meshtastic_meshnet=local_meshnet_name,
                        )
                        msgs_to_keep = config.db_msgs_to_keep
                        if msgs_to_keep > 0:
                            prune_message_map(msgs_to_keep)
                else:
                    meshtastic_logger.debug(
                        f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
                    )
            else:
                meshtastic_logger.info(
                    f"Relaying message from {full_display_name} to radio broadcast"
                )
                sent_packet = meshtastic_interface.sendText(
                    text=full_message, channelIndex=meshtastic_channel
                )
                if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                    store_message_map(
                        sent_packet.id,
                        event.event_id,
                        room.room_id,
                        text,
                        meshtastic_meshnet=local_meshnet_name,
                    )
                    msgs_to_keep = config.db_msgs_to_keep
                    if msgs_to_keep > 0:
                        prune_message_map(msgs_to_keep)
        else:
            logger.debug(
                f"Broadcast not supported: Message from {full_display_name} dropped."
            )

async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> UploadResponse:
    """
    Uploads an image to Matrix and returns the UploadResponse containing the content URI.
    """
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
    """
    Sends an already uploaded image to the specified room.
    """
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={
            "msgtype": "m.image",
            "url": upload_response.content_uri,
            "body": "",
        },
    )