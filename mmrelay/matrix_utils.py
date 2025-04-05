# ./mmrelay/matrix_utils.py:
import asyncio
import io
import re
import ssl
import time
from typing import List, Union
import logging # Import logging module

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
    LoginError, # Import LoginError
)
from PIL import Image

# Import the global config dict - DO NOT access specific keys here
from mmrelay.config import relay_config
from mmrelay.db_utils import get_message_map_by_matrix_event_id, prune_message_map, store_message_map
from mmrelay.log_utils import get_logger

# --- Import connect_meshtastic here --- Needed for on_room_message
# This is okay as matrix_utils is typically imported after meshtastic_utils setup
# Or consider passing the meshtastic_interface instance if needed.
from mmrelay.meshtastic_utils import connect_meshtastic


# --- REMOVED TOP-LEVEL CONFIG ACCESS ---
# matrix_homeserver = relay_config["matrix"]["homeserver"]
# matrix_rooms: List[dict] = relay_config["matrix_rooms"]
# matrix_access_token = relay_config["matrix"]["access_token"]
# bot_user_id = relay_config["matrix"]["bot_user_id"]
# --- Config will be accessed inside functions using relay_config.get() ---


# Get logger instance
logger = get_logger(name="Matrix")


# Global variables for Matrix client and bot info
matrix_client = None
bot_user_name = None  # Detected upon logon
bot_user_id = None # Set during connect_matrix
bot_start_time = int(
    time.time() * 1000
)  # Timestamp when the bot starts, used to filter out old messages


def bot_command(command, event):
    """
    Checks if the given command is directed at the bot,
    accounting for variations in different Matrix clients.
    Requires bot_user_id and bot_user_name to be set globally first.
    """
    # Ensure bot name/ID are available before checking
    if not bot_user_id or not bot_user_name:
         # This might happen during initial startup before connect_matrix finishes
         # logger.warning("bot_command called before bot user ID/name were determined.")
         return False # Cannot check command if bot identity unknown

    full_message = event.body.strip() if hasattr(event, 'body') else ''
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    # Use safe patterns
    bot_id_pattern = re.escape(bot_user_id)
    bot_name_pattern = re.escape(bot_user_name)
    # Match mention at the start, followed by optional punctuation/space, then !command
    # Ensure command is followed by space or end of string to avoid partial matches
    command_pattern = rf"^(?:{bot_id_pattern}|{bot_name_pattern}|[#@].*?)(?:[:,;]?)?\s*!{re.escape(command)}(?:\s+.*|$)"

    # Check plain body first
    if re.match(command_pattern, full_message, re.IGNORECASE):
        return True
    # Check text extracted from formatted body if plain body didn't match
    if formatted_body and re.match(command_pattern, text_content, re.IGNORECASE):
        return True

    return False


async def connect_matrix():
    """
    Establish a connection to the Matrix homeserver using config values.
    Sets global matrix_client and detects the bot's display name.
    """
    global matrix_client, bot_user_name, bot_user_id

    # Avoid reconnecting if already connected and seemingly logged in
    if matrix_client and getattr(matrix_client, 'logged_in', False):
        logger.debug("Matrix client already connected and logged in.")
        return matrix_client

    # --- Access config values here ---
    matrix_config = relay_config.get("matrix", {})
    matrix_homeserver = matrix_config.get("homeserver")
    bot_user_id_from_config = matrix_config.get("bot_user_id")
    matrix_access_token = matrix_config.get("access_token")
    # ---------------------------------

    if not all([matrix_homeserver, bot_user_id_from_config, matrix_access_token]):
        logger.critical("Matrix configuration incomplete (homeserver, bot_user_id, access_token required). Cannot connect.")
        return None

    # Set global bot_user_id (needed by bot_command and others)
    bot_user_id = bot_user_id_from_config

    logger.info(f"Attempting to connect to Matrix homeserver: {matrix_homeserver}")

    ssl_context = ssl.create_default_context(cafile=certifi.where())
    # TODO: Consider making store_path configurable using path_utils
    store_path = "matrix_store"
    client_config = AsyncClientConfig(
        store_path=store_path,
        store_encryption_enabled=False, # Simpler storage
        encryption_enabled=False # Assume no E2EE for relay bot
    )
    temp_client = AsyncClient(
        homeserver=matrix_homeserver, user=bot_user_id, device_id="MMRELAYBOT",
        config=client_config, ssl=ssl_context,
    )

    try:
        # Use access token for login
        login_response = await temp_client.login(token=matrix_access_token, device_name="MMRelayBotInstance")

        if isinstance(login_response, LoginError):
            logger.critical(f"Matrix login failed: {login_response.message}")
            await temp_client.close()
            return None
        # Verify login state after attempt
        if not temp_client.logged_in:
            logger.critical(f"Matrix login attempt did not result in logged_in state. Response: {login_response}")
            await temp_client.close()
            return None

        # Store token and user ID on client if needed (nio should handle this)
        temp_client.access_token = temp_client.access_token or matrix_access_token
        temp_client.user_id = temp_client.user_id or bot_user_id

        logger.info(f"Successfully logged into Matrix as {temp_client.user_id} (Device ID: {temp_client.device_id})")

    except Exception as e:
        logger.critical(f"Error during Matrix login sequence: {e}", exc_info=True)
        # Ensure client is closed if login fails partway through
        if temp_client:
             await temp_client.close()
        return None

    # Fetch bot display name after successful login
    try:
        response = await temp_client.get_displayname(temp_client.user_id)
        # Check response type and attribute existence carefully
        if hasattr(response, "displayname") and response.displayname is not None:
            bot_user_name = response.displayname
            logger.info(f"Bot display name detected as: '{bot_user_name}'")
        else:
            # Fallback logic if displayname is missing or empty
            match = re.match(r"@([^:]+):.*", temp_client.user_id)
            bot_user_name = match.group(1) if match else temp_client.user_id # Use localpart or full ID
            logger.info(f"Bot display name not set or empty, using fallback: '{bot_user_name}'")
    except Exception as e:
        logger.warning(f"Could not fetch bot display name: {e}", exc_info=True)
        # Use fallback name if fetching failed
        match = re.match(r"@([^:]+):.*", temp_client.user_id)
        bot_user_name = match.group(1) if match else temp_client.user_id

    # Assign to global variable only after successful connection and setup
    matrix_client = temp_client
    return matrix_client


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """Join a Matrix room by its ID or alias."""
    # Access matrix_rooms from config here if needed for update logic, but prefer read-only
    # matrix_rooms = relay_config.get("matrix_rooms", [])

    if not matrix_client or not getattr(matrix_client, 'logged_in', False):
         logger.error("Cannot join room: Matrix client not connected.")
         return

    try:
        room_id = room_id_or_alias
        if room_id_or_alias.startswith("#"):
            logger.debug(f"Resolving room alias: {room_id_or_alias}")
            # Use specific error types if nio provides them for alias resolution
            try:
                 response = await matrix_client.room_resolve_alias(room_id_or_alias)
                 if hasattr(response, 'room_id') and response.room_id:
                      room_id = response.room_id
                      logger.debug(f"Resolved {room_id_or_alias} to room ID: {room_id}")
                 else:
                      logger.error(f"Failed to resolve room alias '{room_id_or_alias}': No room ID in response {response}.")
                      return
            except Exception as resolve_error: # Catch potential exceptions during resolution
                 logger.error(f"Error resolving room alias '{room_id_or_alias}': {resolve_error}", exc_info=True)
                 return

        # Check if already in room using nio's internal state
        if room_id not in matrix_client.rooms:
            logger.info(f"Attempting to join room: {room_id}")
            try:
                 response = await matrix_client.join(room_id)
                 # Check response indicates success (e.g., contains the room_id)
                 if hasattr(response, 'room_id') and response.room_id == room_id:
                     logger.info(f"Joined room '{room_id_or_alias}' ({room_id}) successfully.")
                 else:
                      error_msg = getattr(response, 'message', 'Unknown error')
                      logger.error(f"Failed to join room '{room_id_or_alias}' ({room_id}): {error_msg}")
            except Exception as join_error: # Catch potential exceptions during join
                 logger.error(f"Error joining room '{room_id_or_alias}' ({room_id}): {join_error}", exc_info=True)
        else:
            logger.debug(f"Already in room '{room_id_or_alias}' ({room_id}).")

    except Exception as e:
        # Catch any unexpected errors in the overall logic
        logger.error(f"Unexpected error processing join for '{room_id_or_alias}': {e}", exc_info=True)


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
    Relay a message from Meshtastic to Matrix, using original custom keys.
    """
    client = await connect_matrix() # Get client instance
    if not client:
        logger.error("Cannot relay message to Matrix: Client not connected.")
        return

    # --- Access config values ---
    meshtastic_config = relay_config.get("meshtastic", {})
    relay_reactions = meshtastic_config.get("relay_reactions", False)
    # Use relay's configured meshnet name only for DB storage context if needed later?
    # The event itself should contain the *originating* meshnet name passed in `meshnet_name`.
    # local_meshnet_name_config = meshtastic_config.get("meshnet_name", "Mesh") # Relay's own name

    db_config = relay_config.get("db", {})
    msg_map_config = db_config.get("msg_map", {})
    msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
    # --------------------------

    try:
        # --- Using original keys for compatibility ---
        content = {
            "msgtype": "m.emote" if emote else "m.text",
            "body": message,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": meshnet_name, # Use the originating name passed in
            "meshtastic_portnum": str(portnum), # Ensure string
        }
        if meshtastic_id is not None: content["meshtastic_id"] = str(meshtastic_id)
        if meshtastic_replyId is not None: content["meshtastic_replyId"] = str(meshtastic_replyId)
        if meshtastic_text is not None: content["meshtastic_text"] = meshtastic_text
        if emoji: content["meshtastic_emoji"] = 1 # Use 1 as per original logic
        # -----------------------------------------

        response = await asyncio.wait_for(
            client.room_send(room_id=room_id, message_type="m.room.message", content=content),
            timeout=10.0, # Increased timeout
        )
        logger.info(f"Relayed Meshtastic message to Matrix room: {room_id} (Event ID: {response.event_id})")

        # --- Store message map if needed ---
        if relay_reactions and meshtastic_id is not None and not emote:
            logger.debug(f"Storing message map: MeshID {meshtastic_id} -> MatrixEvent {response.event_id}")
            store_message_map(
                meshtastic_id, response.event_id, room_id,
                meshtastic_text if meshtastic_text else message, # Prefer original text
                meshtastic_meshnet=meshnet_name, # Store originating meshnet name from packet
            )
            if msgs_to_keep > 0:
                prune_message_map(msgs_to_keep)

    except asyncio.TimeoutError:
        logger.error(f"Timed out sending message to Matrix room {room_id}")
    except Exception as e:
        logger.error(f"Error sending message to Matrix room {room_id}: {e}", exc_info=True)


def truncate_message(text, max_bytes=227):
    """Truncate the given text to fit within the specified byte size."""
    if not isinstance(text, str): text = str(text)
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """Remove lines that begin with '>'."""
    if not isinstance(text, str): return ""
    lines = text.splitlines()
    filtered = [line for line in lines if not line.strip().startswith(">")]
    return " ".join(filtered).strip()


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: Union[RoomMessageText, RoomMessageNotice, ReactionEvent, RoomMessageEmote],
) -> None:
    """
    Handle new messages and reactions in Matrix. Preserves original reaction logic.
    """
    # --- Access config values ---
    # Need these early for filtering/setup
    matrix_rooms = relay_config.get("matrix_rooms", [])
    meshtastic_config = relay_config.get("meshtastic", {})
    relay_reactions = meshtastic_config.get("relay_reactions", False)
    local_meshnet_name = meshtastic_config.get("meshnet_name", "Mesh")
    broadcast_enabled = meshtastic_config.get("broadcast_enabled", False)
    detection_sensor_enabled = meshtastic_config.get("detection_sensor", False)
    db_config = relay_config.get("db", {})
    msg_map_config = db_config.get("msg_map", {})
    msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
    # Bot user ID should be set globally by connect_matrix by now
    bot_user_id_local = bot_user_id
    # --------------------------

    # Basic checks (using bot_user_id_local)
    if not bot_user_id_local:
         logger.warning("Cannot process message: Bot User ID not set.")
         return
    if event.sender == bot_user_id_local:
        logger.debug("Ignoring message from self.")
        return
    if (ts := getattr(event, 'server_timestamp', None)) and ts < bot_start_time:
         logger.debug("Ignoring old message from before bot start.")
         return

    room_config = next((c for c in matrix_rooms if c.get("id") == room.room_id), None)
    if not room_config:
        logger.debug(f"Ignoring message in unconfigured room: {room.room_id}")
        return

    # --- Process Event Content (Preserving Original Logic) ---
    content = event.source.get("content", {})
    relates_to = content.get("m.relates_to")
    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None # Only set for *Matrix* reactions to Matrix events

    # Check if this is a Matrix ReactionEvent (m.reaction)
    # Use original logic's check for ReactionEvent type
    if isinstance(event, ReactionEvent):
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        # Ensure relates_to structure is as expected for reactions
        if relates_to and relates_to.get("rel_type") == "m.annotation" and "event_id" in relates_to and "key" in relates_to:
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"] # This is the key for Matrix->Mesh reaction relay
            logger.debug(f"Matrix Reaction details: Emoji='{reaction_emoji}', OriginalEventID='{original_matrix_event_id}'")
        else:
             # If it's a ReactionEvent but doesn't look like a valid annotation, ignore it
             logger.warning(f"Ignoring ReactionEvent with unexpected m.relates_to: {relates_to}")
             return

    # Check if this is a Matrix RoomMessageEmote (m.emote) that might be a relayed reaction
    # Use original logic's check for meshtastic_replyId inside the emote content
    # Need to read the custom keys with original names
    meshtastic_replyId = content.get("meshtastic_replyId")
    is_meshtastic_emoji = content.get("meshtastic_emoji") == 1 # Check if it's 1

    if isinstance(event, RoomMessageEmote) and meshtastic_replyId and is_meshtastic_emoji:
        logger.debug(f"Processing emote potentially representing relayed Meshtastic reaction: {event.source}")
        # This is an emote representing a reaction *from* Meshtastic
        # Set is_reaction for filtering, but don't set original_matrix_event_id
        is_reaction = True
        reaction_body = content.get("body", "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body)
        reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?" # Extracted for logging/context
        # original_matrix_event_id remains None for this case

    # Extract text if not already handled as a specific reaction type
    text = ""
    if hasattr(event, "body") and not is_reaction: # Get text only if it's not a reaction being processed
         text = event.body.strip()
    elif is_reaction and not original_matrix_event_id and hasattr(event, "body"):
         # It's a reaction-emote from Meshtastic, keep body for context if needed
         text = event.body.strip()


    # --- Read original custom keys for context ---
    longname = content.get("meshtastic_longname")
    shortname = content.get("meshtastic_shortname")
    meshnet_name = content.get("meshtastic_meshnet") # Originating meshnet
    portnum_str = content.get("meshtastic_portnum", "TEXT_MESSAGE_APP")
    suppress = content.get("mmrelay_suppress", False)
    # Note: meshtastic_replyId and is_meshtastic_emoji were read earlier

    # --- Decision Logic (Preserving Original Flow) ---
    if suppress:
        logger.debug("Ignoring message with suppression flag.")
        return
    if is_reaction and not relay_reactions:
        logger.debug("Ignoring reaction (relay_reactions=false).")
        return

    # --- Handle Relaying Reactions (Matrix -> Meshtastic) - Preserving Original Logic ---
    if is_reaction and relay_reactions:
        # Case 1: Matrix reaction (ReactionEvent) to a known Matrix event
        if original_matrix_event_id:
            logger.debug(f"Processing Matrix reaction to original Matrix event {original_matrix_event_id}")
            orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
            if not orig:
                logger.warning(f"Original Meshtastic message for Matrix event {original_matrix_event_id} not found. Cannot relay reaction.")
                return

            # orig = (meshtastic_id, matrix_room_id, meshtastic_text, original_meshnet)
            orig_meshtastic_id, _orig_matrix_room_id, orig_meshtastic_text, _orig_meshnet = orig

            # Get reacting user's display name
            client = await connect_matrix() # Ensure client is available
            if not client: return # Cannot proceed without client
            try:
                 display_name_response = await client.get_displayname(event.sender)
                 full_display_name = display_name_response.displayname or event.sender
            except Exception: # Handle error fetching display name
                 full_display_name = event.sender

            short_display_name = full_display_name[:5]
            prefix = f"{short_display_name}[M]: "
            orig_text_clean = strip_quoted_lines(orig_meshtastic_text).replace("\n", " ").replace("\r", " ")
            abbreviated_text = (orig_text_clean[:40] + "...") if len(orig_text_clean) > 40 else orig_text_clean
            reaction_message = f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'

            # Send reaction text back to Meshtastic
            meshtastic_interface = connect_meshtastic()
            if meshtastic_interface and broadcast_enabled:
                 meshtastic_channel = room_config.get("meshtastic_channel")
                 if meshtastic_channel is not None:
                     logger.info(f"Relaying Matrix reaction from {full_display_name} to Meshtastic channel {meshtastic_channel}")
                     try:
                         meshtastic_interface.sendText(text=reaction_message, channelIndex=meshtastic_channel)
                     except Exception as send_e:
                          logger.error(f"Failed to send reaction to Meshtastic: {send_e}", exc_info=True)
                 else: logger.warning(f"Cannot relay reaction: Room {room.room_id} has no meshtastic_channel configured.")
            elif not broadcast_enabled: logger.debug("Cannot relay reaction: Meshtastic broadcast_enabled is false.")
            elif not meshtastic_interface: logger.warning("Cannot relay reaction: Meshtastic client not connected.")

            return # Handled reaction, stop processing

        # Case 2: Emote representing a reaction *from* Meshtastic (original logic's check)
        # This section handles relaying reactions *between Meshtastic nets* via Matrix
        elif isinstance(event, RoomMessageEmote) and meshtastic_replyId and is_meshtastic_emoji:
            # This emote came *from* meshtastic_utils.on_meshtastic_message
            # Check if it's from a *remote* meshnet that needs relaying to *local*
            if meshnet_name and meshnet_name != local_meshnet_name:
                logger.info(f"Relaying reaction from remote meshnet '{meshnet_name}' to local meshnet")

                short_meshnet_name = meshnet_name[:4]
                # Sender info should be in the emote's custom keys
                emote_shortname = shortname or (longname[:3] if longname else "???")
                emote_orig_text = content.get("meshtastic_text", "") # Get original text from emote

                emote_orig_text_clean = strip_quoted_lines(emote_orig_text).replace("\n", " ").replace("\r", " ")
                abbreviated_emote_text = (emote_orig_text_clean[:40] + "...") if len(emote_orig_text_clean) > 40 else emote_orig_text_clean

                reaction_message_for_local = f'{emote_shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_emote_text}"'

                meshtastic_interface = connect_meshtastic()
                if meshtastic_interface and broadcast_enabled:
                    meshtastic_channel = room_config.get("meshtastic_channel")
                    if meshtastic_channel is not None:
                        logger.info(f"Relaying remote reaction from {meshnet_name} to local radio broadcast (Channel {meshtastic_channel})")
                        try:
                            meshtastic_interface.sendText(text=reaction_message_for_local, channelIndex=meshtastic_channel)
                        except Exception as send_e:
                            logger.error(f"Failed to relay remote reaction to Meshtastic: {send_e}", exc_info=True)
                    else: logger.warning(f"Cannot relay remote reaction: Room {room.room_id} has no meshtastic_channel.")
                elif not broadcast_enabled: logger.debug("Cannot relay remote reaction: broadcast_enabled is false.")
                elif not meshtastic_interface: logger.warning("Cannot relay remote reaction: Meshtastic client not connected.")

            else:
                # Reaction emote was from local meshnet, ignore to prevent loop
                logger.debug("Ignoring reaction emote originating from local meshnet.")

            return # Handled this type of reaction emote

    # --- Handle Normal Messages (Matrix -> Meshtastic) - Preserving Original Logic ---
    if not text: # If text is empty after reaction checks, ignore
         logger.debug(f"Ignoring event with no remaining text body: {event.event_id}")
         return

    # Check for loopback / format message (using original logic)
    if longname and meshnet_name: # Message has Meshtastic context
        if meshnet_name == local_meshnet_name:
            logger.debug(f"Ignoring message relayed from own meshnet ({meshnet_name}).")
            return
        else: # Message from a remote meshnet, format for relaying
            logger.info(f"Processing message relayed from remote meshnet: {meshnet_name}")
            short_meshnet_name = meshnet_name[:4]
            shortname = shortname or (longname[:3] if longname else "???")
            # Original logic didn't remove prefix, so we keep it:
            # text = re.sub(rf"^\[{re.escape(longname)}/{re.escape(meshnet_name)}\]: ", "", text)
            # Format using original sender info + short remote mesh name
            full_message_to_mesh = f"[{longname}/{short_meshnet_name}]: {text}"
            full_message_to_mesh = truncate_message(full_message_to_mesh)
    else: # Normal message from a Matrix user
        client = await connect_matrix()
        if not client: return # Need client
        try:
             display_name_response = await client.get_displayname(event.sender)
             full_display_name = display_name_response.displayname or event.sender
        except Exception:
             full_display_name = event.sender
        short_display_name = full_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message_to_mesh = f"{prefix}{text}"
        full_message_to_mesh = truncate_message(full_message_to_mesh)
        text = truncate_message(text) # Truncate original text


    # --- Plugin Processing ---
    # Import here to ensure plugins loaded after config
    from mmrelay.plugin_loader import load_plugins
    plugins = load_plugins()
    plugin_handled = False
    is_command = False

    for plugin in plugins:
        # Check commands first using original bot_command logic
        for command in plugin.get_matrix_commands():
             if bot_command(command, event):
                 is_command = True
                 logger.info(f"Matrix command '!{command}' detected for plugin '{plugin.plugin_name}'")
                 try:
                      # Pass event object and the message formatted for mesh
                      if await plugin.handle_room_message(room, event, full_message_to_mesh):
                           plugin_handled = True
                           logger.debug(f"Command handled by plugin {plugin.plugin_name}")
                 except Exception as e: logger.error(f"Error executing plugin {plugin.plugin_name} for command: {e}", exc_info=True)
                 break
        if plugin_handled: break

        # Check general message handling if not a command
        if not is_command and not plugin_handled:
             try:
                 if await plugin.handle_room_message(room, event, full_message_to_mesh):
                     plugin_handled = True
                     logger.debug(f"General message handled by plugin {plugin.plugin_name}")
                     break
             except Exception as e: logger.error(f"Error executing plugin {plugin.plugin_name} for general message: {e}", exc_info=True)


    # --- Relay to Meshtastic (Original Logic) ---
    if is_command:
        logger.debug("Message identified as a command, not relaying to mesh.")
        return
    if plugin_handled:
        logger.debug("Message handled by a plugin, not relaying to mesh.")
        return

    if broadcast_enabled:
        meshtastic_interface = connect_meshtastic()
        if not meshtastic_interface:
             logger.warning("Cannot relay message to Meshtastic: Client not connected.")
             return
        meshtastic_channel = room_config.get("meshtastic_channel")
        if meshtastic_channel is None:
             logger.warning(f"Cannot relay message: Room {room.room_id} has no meshtastic_channel configured.")
             return

        # Determine PortNum (using original logic's check)
        send_portnum = meshtastic.protobuf.portnums_pb2.PortNum.TEXT_MESSAGE_APP
        # Use portnum_str read from content earlier
        if portnum_str == "DETECTION_SENSOR_APP" and detection_sensor_enabled:
            logger.info(f"Relaying Matrix message from {full_display_name} as Detection Sensor data")
            send_portnum = meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP
            try:
                 sent_packet = meshtastic_interface.sendData(
                     data=full_message_to_mesh.encode("utf-8"), channelIndex=meshtastic_channel, portNum=send_portnum,
                 )
            except Exception as send_e:
                 logger.error(f"Failed to send sensor data to Meshtastic: {send_e}", exc_info=True)
                 sent_packet = None # Ensure sent_packet is None on error
        else:
            logger.info(f"Relaying Matrix message from {full_display_name} to Meshtastic channel {meshtastic_channel}")
            try:
                 sent_packet = meshtastic_interface.sendText(text=full_message_to_mesh, channelIndex=meshtastic_channel)
            except Exception as send_e:
                 logger.error(f"Failed to send text to Meshtastic: {send_e}", exc_info=True)
                 sent_packet = None

        # Store message map if needed (using original logic)
        if relay_reactions and sent_packet and hasattr(sent_packet, "id"):
            logger.debug(f"Storing message map: MatrixEvent {event.event_id} -> MeshID {sent_packet.id}")
            store_message_map(
                sent_packet.id, event.event_id, room.room_id, text, # Store original truncated text
                meshtastic_meshnet=local_meshnet_name, # Sent from relay's meshnet
            )
            if msgs_to_keep > 0: prune_message_map(msgs_to_keep)
    else:
         logger.debug(f"Not relaying message from Matrix (broadcast_enabled={broadcast_enabled}).")


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str = "image.png"
) -> UploadResponse | None:
    """Uploads an image to Matrix."""
    # Simplified implementation from previous correct version
    if not client or not getattr(client, 'logged_in', False):
         logger.error("Cannot upload image: Matrix client not connected.")
         return None
    buffer = io.BytesIO()
    try:
        image.save(buffer, format="PNG")
        image_data = buffer.getvalue()
        filesize = len(image_data)
        logger.debug(f"Uploading image '{filename}' ({filesize} bytes)...")
        response, _ = await client.upload(
            io.BytesIO(image_data), content_type="image/png", filename=filename, filesize=filesize,
        )
        if isinstance(response, UploadResponse) and response.content_uri:
            logger.info(f"Image uploaded successfully: {response.content_uri}")
            return response
        else:
            error_msg = getattr(response, 'message', 'Unknown upload error')
            logger.error(f"Failed to upload image '{filename}': {error_msg}")
            return None
    except Exception as e:
        logger.error(f"Error preparing or uploading image '{filename}': {e}", exc_info=True)
        return None


async def send_room_image(
    client: AsyncClient, room_id: str, upload_response: UploadResponse, body: str = "image.png"
):
    """Sends an already uploaded image to the specified room."""
    # Simplified implementation from previous correct version
    if not client or not getattr(client, 'logged_in', False):
         logger.error("Cannot send image: Matrix client not connected.")
         return
    if not upload_response or not upload_response.content_uri:
         logger.error("Cannot send image: Invalid UploadResponse.")
         return
    try:
        await client.room_send(
            room_id=room_id, message_type="m.room.message",
            content={ "msgtype": "m.image", "body": body, "url": upload_response.content_uri, },
        )
        logger.info(f"Sent image {upload_response.content_uri} to room {room_id}")
    except Exception as e:
         logger.error(f"Error sending image to room {room_id}: {e}", exc_info=True)
