import asyncio
import io
import re
import ssl
import time
from typing import Union

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
from nio.events.room_events import RoomMemberEvent
from PIL import Image

from mmrelay.db_utils import (
    get_message_map_by_matrix_event_id,
    prune_message_map,
    store_message_map,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic


def get_interaction_settings(config):
    """
    Returns a dictionary indicating whether message reactions and replies are enabled based on the provided configuration.

    Supports both the new `message_interactions` structure and the legacy `relay_reactions` flag for backward compatibility. Defaults to disabling both features if not specified.
    """
    if config is None:
        return {"reactions": False, "replies": False}

    meshtastic_config = config.get("meshtastic", {})

    # Check for new structured configuration first
    if "message_interactions" in meshtastic_config:
        interactions = meshtastic_config["message_interactions"]
        return {
            "reactions": interactions.get("reactions", False),
            "replies": interactions.get("replies", False),
        }

    # Fall back to legacy relay_reactions setting
    if "relay_reactions" in meshtastic_config:
        enabled = meshtastic_config["relay_reactions"]
        logger.warning(
            "Configuration setting 'relay_reactions' is deprecated. "
            "Please use 'message_interactions: {reactions: bool, replies: bool}' instead. "
            "Legacy mode: enabling reactions only."
        )
        return {
            "reactions": enabled,
            "replies": False,
        }  # Only reactions for legacy compatibility

    # Default to privacy-first (both disabled)
    return {"reactions": False, "replies": False}


def message_storage_enabled(interactions):
    """
    Return True if message storage is required for enabled message interactions.

    Parameters:
        interactions (dict): Dictionary with 'reactions' and 'replies' boolean flags.

    Returns:
        bool: True if reactions or replies are enabled; otherwise, False.
    """
    return interactions["reactions"] or interactions["replies"]


# Global config variable that will be set from config.py
config = None

# These will be set in connect_matrix()
matrix_homeserver = None
matrix_rooms = None
matrix_access_token = None
bot_user_id = None
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

    # Check for simple !command format first
    if full_message.startswith(f"!{command}") or text_content.startswith(f"!{command}"):
        return True

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


async def connect_matrix(passed_config=None):
    """
    Establish a connection to the Matrix homeserver.
    Sets global matrix_client and detects the bot's display name.

    Args:
        passed_config: The configuration dictionary to use (will update global config)
    """
    global matrix_client, bot_user_name, matrix_homeserver, matrix_rooms, matrix_access_token, bot_user_id, config

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    # Extract Matrix configuration
    matrix_homeserver = config["matrix"]["homeserver"]
    matrix_rooms = config["matrix_rooms"]
    matrix_access_token = config["matrix"]["access_token"]
    bot_user_id = config["matrix"]["bot_user_id"]

    # Check if client already exists
    if matrix_client:
        return matrix_client

    # Create SSL context using certifi's certificates
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    # Initialize the Matrix client with custom SSL context
    client_config = AsyncClientConfig(encryption_enabled=False)
    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        config=client_config,
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
    reply_to_event_id=None,
):
    """
    Relays a message from Meshtastic to a Matrix room, supporting replies and message mapping for interactions.

    If `reply_to_event_id` is provided, sends the message as a Matrix reply, formatting the content to include quoted original text and appropriate Matrix reply relations. When message interactions (reactions or replies) are enabled in the configuration, stores a mapping between the Meshtastic message ID and the resulting Matrix event ID to enable cross-referencing for future interactions. Prunes old message mappings based on configuration to limit storage.

    Parameters:
        room_id (str): The Matrix room ID to send the message to.
        message (str): The message content to relay.
        longname (str): The sender's long display name from Meshtastic.
        shortname (str): The sender's short display name from Meshtastic.
        meshnet_name (str): The originating meshnet name.
        portnum (int): The Meshtastic port number.
        meshtastic_id (str, optional): The Meshtastic message ID for mapping.
        meshtastic_replyId (str, optional): The Meshtastic message ID being replied to, if any.
        meshtastic_text (str, optional): The original Meshtastic message text.
        emote (bool, optional): Whether to send the message as an emote.
        emoji (bool, optional): Whether the message is an emoji reaction.
        reply_to_event_id (str, optional): The Matrix event ID being replied to, if sending a reply.

    Returns:
        None
    """
    global config

    # Log the current state of the config
    logger.debug(f"matrix_relay: config is {'available' if config else 'None'}")

    matrix_client = await connect_matrix()

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot relay message to Matrix.")
        return

    # Get interaction settings
    interactions = get_interaction_settings(config)
    storage_enabled = message_storage_enabled(interactions)

    # Retrieve db config for message_map pruning
    # Check database config for message map settings (preferred format)
    database_config = config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})

    # If not found in database config, check legacy db config
    if not msg_map_config:
        db_config = config.get("db", {})
        legacy_msg_map_config = db_config.get("msg_map", {})

        if legacy_msg_map_config:
            msg_map_config = legacy_msg_map_config
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )
    msgs_to_keep = msg_map_config.get(
        "msgs_to_keep", 500
    )  # Default is 500 if not specified

    try:
        # Always use our own local meshnet_name for outgoing events
        local_meshnet_name = config["meshtastic"]["meshnet_name"]
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

        # Add Matrix reply formatting if this is a reply
        if reply_to_event_id:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}
            # For Matrix replies, we need to format the body with quoted content
            # Get the original message details for proper quoting
            try:
                orig = get_message_map_by_matrix_event_id(reply_to_event_id)
                if orig:
                    # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
                    _, _, original_text, original_meshnet = orig

                    # Use the relay bot's user ID for attribution (this is correct for relay messages)
                    bot_user_id = matrix_client.user_id
                    original_sender_display = f"{longname}/{original_meshnet}"

                    # Create the quoted reply format
                    quoted_text = f"> <@{bot_user_id}> [{original_sender_display}]: {original_text}"
                    content["body"] = f"{quoted_text}\n\n{message}"
                    content["format"] = "org.matrix.custom.html"

                    # Create formatted HTML version with better readability
                    reply_link = f"https://matrix.to/#/{room_id}/{reply_to_event_id}"
                    bot_link = f"https://matrix.to/#/@{bot_user_id}"
                    blockquote_content = f'<a href="{reply_link}">In reply to</a> <a href="{bot_link}">@{bot_user_id}</a><br>[{original_sender_display}]: {original_text}'
                    content["formatted_body"] = (
                        f"<mx-reply><blockquote>{blockquote_content}</blockquote></mx-reply>{message}"
                    )
                else:
                    logger.warning(
                        f"Could not find original message for reply_to_event_id: {reply_to_event_id}"
                    )
            except Exception as e:
                logger.error(f"Error formatting Matrix reply: {e}")

        try:
            # Ensure matrix_client is not None
            if not matrix_client:
                logger.error("Matrix client is None. Cannot send message.")
                return

            # Send the message with a timeout
            response = await asyncio.wait_for(
                matrix_client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                ),
                timeout=10.0,  # Increased timeout
            )

            # Log at info level, matching one-point-oh pattern
            logger.info(f"Sent inbound radio message to matrix room: {room_id}")
            # Additional details at debug level
            if hasattr(response, "event_id"):
                logger.debug(f"Message event_id: {response.event_id}")

        except asyncio.TimeoutError:
            logger.error(f"Timeout sending message to Matrix room {room_id}")
            return
        except Exception as e:
            logger.error(f"Error sending message to Matrix room {room_id}: {e}")
            return

        # Only store message map if any interactions are enabled and conditions are met
        # This enables reactions and/or replies functionality based on configuration
        if (
            storage_enabled
            and meshtastic_id is not None
            and not emote
            and hasattr(response, "event_id")
        ):
            try:
                # Store the message map
                store_message_map(
                    meshtastic_id,
                    response.event_id,
                    room_id,
                    meshtastic_text if meshtastic_text else message,
                    meshtastic_meshnet=local_meshnet_name,
                )
                logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")

                # If msgs_to_keep > 0, prune old messages after inserting a new one
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)
            except Exception as e:
                logger.error(f"Error storing message map: {e}")

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
    Removes lines starting with '>' from the input text.

    This is typically used to exclude quoted content from Matrix replies, such as when processing reaction text.
    """
    lines = text.splitlines()
    filtered = [line for line in lines if not line.strip().startswith(">")]
    return " ".join(filtered).strip()


async def get_user_display_name(room, event):
    """
    Retrieve the display name of a Matrix user, preferring the room-specific name if available.

    Returns:
        str: The user's display name, or their Matrix ID if no display name is set.
    """
    room_display_name = room.user_name(event.sender)
    if room_display_name:
        return room_display_name

    display_name_response = await matrix_client.get_displayname(event.sender)
    return display_name_response.displayname or event.sender


def format_reply_message(full_display_name, text):
    """
    Format a reply message by prefixing a truncated display name and removing quoted lines.

    The resulting message is prefixed with the first five characters of the user's display name followed by "[M]: ", has quoted lines removed, and is truncated to fit within the allowed message length.

    Parameters:
        full_display_name (str): The user's full display name to be truncated for the prefix.
        text (str): The reply text, possibly containing quoted lines.

    Returns:
        str: The formatted and truncated reply message.
    """
    short_display_name = full_display_name[:5]
    prefix = f"{short_display_name}[M]: "

    # Strip quoted content from the reply text
    clean_text = strip_quoted_lines(text)
    reply_message = f"{prefix}{clean_text}"
    return truncate_message(reply_message)


async def send_reply_to_meshtastic(
    reply_message,
    full_display_name,
    room_config,
    room,
    event,
    text,
    storage_enabled,
    local_meshnet_name,
    reply_id=None,
):
    """
    Sends a reply message from Matrix to Meshtastic and optionally stores the message mapping for future interactions.

    If message storage is enabled and the message is successfully sent, stores a mapping between the Meshtastic packet ID and the Matrix event ID, after removing quoted lines from the reply text. Prunes old message mappings based on configuration to limit storage size. Logs errors if sending fails.

    Args:
        reply_id: If provided, sends as a structured Meshtastic reply to this message ID
    """
    meshtastic_interface = connect_meshtastic()
    from mmrelay.meshtastic_utils import logger as meshtastic_logger
    from mmrelay.meshtastic_utils import sendTextReply

    meshtastic_channel = room_config["meshtastic_channel"]

    if config["meshtastic"]["broadcast_enabled"]:
        try:
            if reply_id is not None:
                # Send as a structured reply using our custom function
                sent_packet = sendTextReply(
                    meshtastic_interface,
                    text=reply_message,
                    reply_id=reply_id,
                    channelIndex=meshtastic_channel,
                )
                meshtastic_logger.info(
                    f"Relaying Matrix reply from {full_display_name} to radio broadcast as structured reply to message {reply_id}"
                )
            else:
                # Send as regular message (fallback for when no reply_id is available)
                sent_packet = meshtastic_interface.sendText(
                    text=reply_message, channelIndex=meshtastic_channel
                )
                meshtastic_logger.info(
                    f"Relaying Matrix reply from {full_display_name} to radio broadcast as regular message"
                )

            # Store the reply in message map if storage is enabled
            if storage_enabled and sent_packet and hasattr(sent_packet, "id"):
                # Strip quoted lines from text before storing to prevent issues with reactions to replies
                cleaned_text = strip_quoted_lines(text)
                store_message_map(
                    sent_packet.id,
                    event.event_id,
                    room.room_id,
                    cleaned_text,
                    meshtastic_meshnet=local_meshnet_name,
                )
                # Prune old messages if configured
                database_config = config.get("database", {})
                msg_map_config = database_config.get("msg_map", {})
                if not msg_map_config:
                    db_config = config.get("db", {})
                    legacy_msg_map_config = db_config.get("msg_map", {})
                    if legacy_msg_map_config:
                        msg_map_config = legacy_msg_map_config
                msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)

        except Exception as e:
            meshtastic_logger.error(f"Error sending Matrix reply to Meshtastic: {e}")


async def handle_matrix_reply(
    room,
    event,
    reply_to_event_id,
    text,
    room_config,
    storage_enabled,
    local_meshnet_name,
):
    """
    Relays a Matrix reply to the corresponding Meshtastic message if a mapping exists.

    Looks up the original Meshtastic message using the Matrix event ID being replied to. If found, formats and sends the reply to Meshtastic, preserving conversational context. Returns True if the reply was successfully handled; otherwise, returns False to allow normal message processing.

    Returns:
        bool: True if the reply was relayed to Meshtastic, False otherwise.
    """
    # Look up the original message in the message map
    orig = get_message_map_by_matrix_event_id(reply_to_event_id)
    if not orig:
        logger.debug(
            f"Original message for Matrix reply not found in DB: {reply_to_event_id}"
        )
        return False  # Continue processing as normal message if original not found

    # Extract the original meshtastic_id to use as reply_id
    # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
    original_meshtastic_id = orig[0]

    # Get user display name
    full_display_name = await get_user_display_name(room, event)

    # Format the reply message
    reply_message = format_reply_message(full_display_name, text)

    logger.info(
        f"Relaying Matrix reply from {full_display_name} to Meshtastic as reply to message {original_meshtastic_id}"
    )

    # Send the reply to Meshtastic with the original message ID as reply_id
    await send_reply_to_meshtastic(
        reply_message,
        full_display_name,
        room_config,
        room,
        event,
        text,
        storage_enabled,
        local_meshnet_name,
        reply_id=original_meshtastic_id,
    )

    return True  # Reply was handled, stop further processing


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: Union[RoomMessageText, RoomMessageNotice, ReactionEvent, RoomMessageEmote],
) -> None:
    """
    Handles incoming Matrix room messages, reactions, and replies, relaying them to Meshtastic as appropriate.

    Processes events from Matrix rooms, including text messages, reactions, and replies. Relays supported messages to Meshtastic if broadcasting is enabled, applying message mapping for cross-referencing when reactions or replies are enabled. Prevents relaying of reactions to reactions and avoids processing messages from the bot itself or messages sent before the bot started. Integrates with plugins for command and message handling, and ensures that only supported messages are forwarded to Meshtastic.
    """
    # Importing here to avoid circular imports and to keep logic consistent
    # Note: We do not call store_message_map directly here for inbound matrix->mesh messages.
    # That logic occurs inside matrix_relay if needed.
    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    # We do not relay messages that occurred before the bot started
    if message_timestamp < bot_start_time:
        return

    # Do not process messages from the bot itself
    if event.sender == bot_user_id:
        return

    # Find the room_config that matches this room, if any
    room_config = None
    for room_conf in matrix_rooms:
        if room_conf["id"] == room.room_id:
            room_config = room_conf
            break

    # Only proceed if the room is supported
    if not room_config:
        return

    relates_to = event.source["content"].get("m.relates_to")
    global config

    # Check if config is available
    if not config:
        logger.error("No configuration available for Matrix message processing.")

    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Matrix message.")
        return

    # Get interaction settings
    interactions = get_interaction_settings(config)
    storage_enabled = message_storage_enabled(interactions)

    # Check if this is a Matrix ReactionEvent (usually m.reaction)
    if isinstance(event, ReactionEvent):
        # This is a reaction event
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            # Extract the reaction emoji and the original event it relates to
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            logger.debug(
                f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}"
            )

    # Check if this is a Matrix RoomMessageEmote (m.emote)
    if isinstance(event, RoomMessageEmote):
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        # For RoomMessageEmote, treat as remote reaction if meshtastic_replyId exists
        is_reaction = True
        # We need to manually extract the reaction emoji from the body
        reaction_body = event.source["content"].get("body", "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body)
        reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"

    text = event.body.strip() if (not is_reaction and hasattr(event, "body")) else ""

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

    # If a message has suppress flag, do not process
    if suppress:
        return

    # If this is a reaction and reactions are disabled, do nothing
    if is_reaction and not interactions["reactions"]:
        logger.debug(
            "Reaction event encountered but reactions are disabled. Doing nothing."
        )
        return

    local_meshnet_name = config["meshtastic"]["meshnet_name"]

    # Check if this is a Matrix reply (not a reaction)
    is_reply = False
    reply_to_event_id = None
    if not is_reaction and relates_to and "m.in_reply_to" in relates_to:
        reply_to_event_id = relates_to["m.in_reply_to"].get("event_id")
        if reply_to_event_id:
            is_reply = True
            logger.debug(f"Processing Matrix reply to event: {reply_to_event_id}")

    # If this is a reaction and reactions are enabled, attempt to relay it
    if is_reaction and interactions["reactions"]:
        # Check if we need to relay a reaction from a remote meshnet to our local meshnet.
        # If meshnet_name != local_meshnet_name and meshtastic_replyId is present and this is an emote,
        # it's a remote reaction that needs to be forwarded as a text message describing the reaction.
        if (
            meshnet_name
            and meshnet_name != local_meshnet_name
            and meshtastic_replyId
            and isinstance(event, RoomMessageEmote)
        ):
            logger.info(f"Relaying reaction from remote meshnet: {meshnet_name}")

            short_meshnet_name = meshnet_name[:4]

            # Format the reaction message for relaying to the local meshnet.
            # The necessary information is in the m.emote event
            if not shortname:
                shortname = longname[:3] if longname else "???"

            meshtastic_text_db = event.source["content"].get("meshtastic_text", "")
            # Strip out any quoted lines from the text
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:40] + "..."
                if len(meshtastic_text_db) > 40
                else meshtastic_text_db
            )

            reaction_message = f'{shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

            # Relay the remote reaction to the local meshnet.
            meshtastic_interface = connect_meshtastic()
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if config["meshtastic"]["broadcast_enabled"]:
                meshtastic_logger.info(
                    f"Relaying reaction from remote meshnet {meshnet_name} to radio broadcast"
                )
                logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                meshtastic_interface.sendText(
                    text=reaction_message, channelIndex=meshtastic_channel
                )
            # We've relayed the remote reaction to our local mesh, so we're done.
            return

        # If original_matrix_event_id is set, this is a reaction to some other matrix event
        if original_matrix_event_id:
            orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
            if not orig:
                # If we don't find the original message in the DB, we suspect it's a reaction-to-reaction scenario
                logger.debug(
                    "Original message for reaction not found in DB. Possibly a reaction-to-reaction scenario. Not forwarding."
                )
                return

            # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            meshtastic_id, matrix_room_id, meshtastic_text_db, meshtastic_meshnet_db = (
                orig
            )
            # Get room-specific display name if available, fallback to global display name
            room_display_name = room.user_name(event.sender)
            if room_display_name:
                full_display_name = room_display_name
            else:
                # Fallback to global display name if room-specific name is not available
                display_name_response = await matrix_client.get_displayname(
                    event.sender
                )
                full_display_name = display_name_response.displayname or event.sender

            # If not from a remote meshnet, proceed as normal to relay back to the originating meshnet
            short_display_name = full_display_name[:5]
            prefix = f"{short_display_name}[M]: "

            # Remove quoted lines so we don't bring in the original '>' lines from replies
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:40] + "..."
                if len(meshtastic_text_db) > 40
                else meshtastic_text_db
            )

            # Always use our local meshnet_name for outgoing events
            reaction_message = (
                f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
            )
            meshtastic_interface = connect_meshtastic()
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if config["meshtastic"]["broadcast_enabled"]:
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

    # Handle Matrix replies to Meshtastic messages (only if replies are enabled)
    if is_reply and reply_to_event_id and interactions["replies"]:
        reply_handled = await handle_matrix_reply(
            room,
            event,
            reply_to_event_id,
            text,
            room_config,
            storage_enabled,
            local_meshnet_name,
        )
        if reply_handled:
            return

    # For Matrix->Mesh messages from a remote meshnet, rewrite the message format
    if longname and meshnet_name:
        # Always include the meshnet_name in the full display name.
        full_display_name = f"{longname}/{meshnet_name}"

        if meshnet_name != local_meshnet_name:
            # A message from a remote meshnet relayed into Matrix, now going back out
            logger.info(f"Processing message from remote meshnet: {meshnet_name}")
            short_meshnet_name = meshnet_name[:4]
            # If shortname is not available, derive it from the longname
            if shortname is None:
                shortname = longname[:3] if longname else "???"
            # Remove the original prefix "[longname/meshnet]: " to avoid double-tagging
            text = re.sub(rf"^\[{full_display_name}\]: ", "", text)
            text = truncate_message(text)
            full_message = f"{shortname}/{short_meshnet_name}: {text}"
        else:
            # If this message is from our local meshnet (loopback), we ignore it
            return
    else:
        # Normal Matrix message from a Matrix user
        # Get room-specific display name if available, fallback to global display name
        room_display_name = room.user_name(event.sender)
        if room_display_name:
            full_display_name = room_display_name
        else:
            # Fallback to global display name if room-specific name is not available
            display_name_response = await matrix_client.get_displayname(event.sender)
            full_display_name = display_name_response.displayname or event.sender
        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        text = truncate_message(text)

    # Plugin functionality
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                found_matching_plugin = await plugin.handle_room_message(
                    room, event, full_message
                )
                if found_matching_plugin:
                    logger.info(
                        f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                    )
            except Exception as e:
                logger.error(
                    f"Error processing message with plugin {plugin.plugin_name}: {e}"
                )

    # Check if the message is a command directed at the bot
    is_command = False
    for plugin in plugins:
        for command in plugin.get_matrix_commands():
            if bot_command(command, event):
                is_command = True
                break
        if is_command:
            break

    # If this is a command, we do not send it to the mesh
    if is_command:
        logger.debug("Message is a command, not sending to mesh")
        return

    # Connect to Meshtastic
    meshtastic_interface = connect_meshtastic()
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not meshtastic_interface:
        logger.error("Failed to connect to Meshtastic. Cannot relay message.")
        return

    meshtastic_channel = room_config["meshtastic_channel"]

    # If message is from Matrix and broadcast_enabled is True, relay to Meshtastic
    # Note: If relay_reactions is False, we won't store message_map, but we can still relay.
    # The lack of message_map storage just means no reaction bridging will occur.
    if not found_matching_plugin:
        if config["meshtastic"]["broadcast_enabled"]:
            portnum = event.source["content"].get("meshtastic_portnum")
            if portnum == "DETECTION_SENSOR_APP":
                # If detection_sensor is enabled, forward this data as detection sensor data
                if config["meshtastic"].get("detection_sensor", False):
                    sent_packet = meshtastic_interface.sendData(
                        data=full_message.encode("utf-8"),
                        channelIndex=meshtastic_channel,
                        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
                    )
                    # Note: Detection sensor messages are not stored in message_map because they are never replied to
                    # Only TEXT_MESSAGE_APP messages need to be stored for reaction handling
                else:
                    meshtastic_logger.debug(
                        f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
                    )
            else:
                meshtastic_logger.info(
                    f"Relaying message from {full_display_name} to radio broadcast"
                )

                try:
                    sent_packet = meshtastic_interface.sendText(
                        text=full_message, channelIndex=meshtastic_channel
                    )
                except Exception as e:
                    meshtastic_logger.error(f"Error sending message to Meshtastic: {e}")
                    return
                # Store message_map only if storage is enabled and only for TEXT_MESSAGE_APP
                # (these are the only messages that can be replied to and thus need reaction handling)
                if storage_enabled and sent_packet and hasattr(sent_packet, "id"):
                    # Strip quoted lines from text before storing to prevent issues with reactions to replies
                    cleaned_text = strip_quoted_lines(text)
                    store_message_map(
                        sent_packet.id,
                        event.event_id,
                        room.room_id,
                        cleaned_text,
                        meshtastic_meshnet=local_meshnet_name,
                    )
                    # Check database config for message map settings (preferred format)
                    database_config = config.get("database", {})
                    msg_map_config = database_config.get("msg_map", {})

                    # If not found in database config, check legacy db config
                    if not msg_map_config:
                        db_config = config.get("db", {})
                        legacy_msg_map_config = db_config.get("msg_map", {})

                        if legacy_msg_map_config:
                            msg_map_config = legacy_msg_map_config
                            logger.warning(
                                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
                            )
                    msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
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
        content={"msgtype": "m.image", "url": upload_response.content_uri, "body": ""},
    )


async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
    """
    Callback to handle room member events, specifically tracking room-specific display name changes.
    This ensures we detect when users update their display names in specific rooms.

    Note: This callback doesn't need to do any explicit processing since matrix-nio
    automatically updates the room state and room.user_name() will return the
    updated room-specific display name immediately after this event.
    """
    # The callback is registered to ensure matrix-nio processes the event,
    # but no explicit action is needed since room.user_name() automatically
    # handles room-specific display names after the room state is updated.
    pass
