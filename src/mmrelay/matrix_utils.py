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
):
    """
    Relay a message from Meshtastic to Matrix, optionally storing message maps.

    IMPORTANT CHANGE: Now, we only store message maps if `relay_reactions` is True.
    If `relay_reactions` is False, we skip storing to the message map entirely.
    This helps maintain privacy and prevents message_map usage unless needed.

    Additionally, if `msgs_to_keep` > 0, we prune the oldest messages after storing
    to prevent database bloat and maintain privacy.
    """
    global config

    # Log the current state of the config
    logger.debug(f"matrix_relay: config is {'available' if config else 'None'}")

    matrix_client = await connect_matrix()

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot relay message to Matrix.")
        return

    # Retrieve relay_reactions configuration; default to False now if not specified.
    relay_reactions = config["meshtastic"].get("relay_reactions", False)

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

        # Only store message map if relay_reactions is True and meshtastic_id is present and not an emote.
        # If relay_reactions is False, we skip storing entirely.
        if (
            relay_reactions
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

    # Retrieve relay_reactions option from config, now defaulting to False
    relay_reactions = config["meshtastic"].get("relay_reactions", False)

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

    # If this is a reaction and relay_reactions is False, do nothing
    if is_reaction and not relay_reactions:
        logger.debug(
            "Reaction event encountered but relay_reactions is disabled. Doing nothing."
        )
        return

    local_meshnet_name = config["meshtastic"]["meshnet_name"]

    # If this is a reaction and relay_reactions is True, attempt to relay it
    if is_reaction and relay_reactions:
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
                # If we don't find the original message in the DB, it might be a reaction to a Matrix reply
                # that contains quoted content. Try to fetch the Matrix message content directly.
                try:
                    # Fetch the Matrix message content by event ID
                    event_response = await matrix_client.room_get_event(room.room_id, original_matrix_event_id)
                    if hasattr(event_response, 'event') and hasattr(event_response.event, 'content'):
                        matrix_message_content = event_response.event.content.get('body', '')

                        # Check if this is a Matrix message with quoted content (contains '>')
                        if '>' in matrix_message_content:
                            logger.debug(f"Found Matrix reply with quoted content: {matrix_message_content[:50]}...")

                            # Strip quoted lines from the Matrix message content
                            stripped_content = strip_quoted_lines(matrix_message_content)
                            stripped_content = stripped_content.replace("\n", " ").replace("\r", " ")

                            if stripped_content.strip():  # Only proceed if there's content after stripping
                                # Get user display name for the reaction
                                room_display_name = room.user_name(event.sender)
                                if room_display_name:
                                    full_display_name = room_display_name
                                else:
                                    display_name_response = await matrix_client.get_displayname(event.sender)
                                    full_display_name = display_name_response.displayname or event.sender

                                short_display_name = full_display_name[:5]
                                prefix = f"{short_display_name}[M]: "

                                abbreviated_text = (
                                    stripped_content[:40] + "..."
                                    if len(stripped_content) > 40
                                    else stripped_content
                                )

                                reaction_message = f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'

                                # Relay the reaction to Meshtastic
                                meshtastic_interface = connect_meshtastic()
                                from mmrelay.meshtastic_utils import logger as meshtastic_logger

                                meshtastic_channel = room_config["meshtastic_channel"]

                                if config["meshtastic"]["broadcast_enabled"]:
                                    meshtastic_logger.info(
                                        f"Relaying reaction to Matrix reply from {full_display_name} to radio broadcast"
                                    )
                                    logger.debug(
                                        f"Sending reaction to Meshtastic: {reaction_message}"
                                    )
                                    meshtastic_interface.sendText(
                                        text=reaction_message, channelIndex=meshtastic_channel
                                    )
                                return
                            else:
                                logger.debug("Matrix message content was empty after stripping quoted lines")
                        else:
                            logger.debug("Matrix message found but no quoted content detected")
                    else:
                        logger.debug("Could not retrieve Matrix message content")
                except Exception as e:
                    logger.debug(f"Failed to fetch Matrix message content: {e}")

                # If we get here, it's likely a reaction-to-reaction scenario or other edge case
                logger.debug(
                    "Original message for reaction not found in DB and could not process as Matrix reply. Possibly a reaction-to-reaction scenario. Not forwarding."
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
                    # If relay_reactions is True, we store the message map for these messages as well.
                    # If False, skip storing.
                    if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                        store_message_map(
                            sent_packet.id,
                            event.event_id,
                            room.room_id,
                            text,
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
                # Store message_map only if relay_reactions is True
                if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
                    store_message_map(
                        sent_packet.id,
                        event.event_id,
                        room.room_id,
                        text,
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
