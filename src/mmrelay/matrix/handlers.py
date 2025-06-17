import asyncio
import re
from typing import Union

# import meshtastic.protobuf.portnums_pb2 # Not directly used for portnum name string comparison
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    RoomMemberEvent # Added for type hint
)
# from nio.events.room_events import RoomMemberEvent # Already imported above

from mmrelay.db_utils import get_message_map_by_matrix_event_id, prune_message_map, store_message_map
from mmrelay.log_utils import get_logger
from mmrelay.common.text_utils import truncate_message, strip_quoted_lines

# For Meshtastic interactions, dynamically import to avoid issues if meshtastic module changes
# from mmrelay.meshtastic.interface import connect_meshtastic, get_meshtastic_interface # connect_meshtastic not used here
# from mmrelay.meshtastic.sender import send_text_to_meshtastic, send_data_to_meshtastic

from mmrelay.matrix.client import (
    get_bot_start_time,
    get_bot_user_id,
    get_bot_user_name,
    get_matrix_rooms_config,
    get_matrix_client,
    config as global_app_config # Main application config from client module
)
# from mmrelay.plugin_loader import load_plugins # Deferred import

logger = get_logger(name="MatrixHandlers")

def bot_command(command: str, event: Union[RoomMessageText, RoomMessageNotice, RoomMessageEmote]):
    bot_user_id_val = get_bot_user_id()
    bot_user_name_val = get_bot_user_name()

    # If bot details aren't loaded, command cannot be for the bot.
    if not bot_user_id_val or not bot_user_name_val:
        logger.debug("Bot user ID or name not available for bot_command check. Assuming not a bot command.")
        return False

    full_message = getattr(event, 'body', '').strip()
    content = getattr(event, 'source', {}).get("content", {})
    formatted_body = content.get("formatted_body", "")
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip() # Plain text from formatted body

    # Direct command check (e.g., !help)
    if full_message.startswith(f"!{command}") or text_content.startswith(f"!{command}"):
        return True

    # Mention-based command check (e.g., @bot:server.com: !ping or BotName: !ping)
    # Regex to match bot mention (user ID or display name) followed by optional punctuation and the command
    # Handles cases like "@bot:matrix.org, !cmd" or "BotName: !cmd"
    mention_pattern = rf"^(?:{re.escape(bot_user_id_val)}|{re.escape(bot_user_name_val)}|[#@].*?)\s*[:;,]?\s*!{command}"

    if re.match(mention_pattern, full_message, re.IGNORECASE) or \
       re.match(mention_pattern, text_content, re.IGNORECASE):
        return True

    return False


async def on_room_message(
    room: MatrixRoom,
    event: Union[RoomMessageText, RoomMessageNotice, ReactionEvent, RoomMessageEmote],
):
    bot_user_id_val = get_bot_user_id()
    bot_start_time_val = get_bot_start_time()
    matrix_rooms_config_val = get_matrix_rooms_config() # List of room configs

    current_config = global_app_config
    if not current_config:
        logger.error("Main configuration not available in on_room_message. Cannot process.")
        return

    if event.server_timestamp < bot_start_time_val: return
    if event.sender == bot_user_id_val: return

    room_config = next((rc for rc in matrix_rooms_config_val if rc.get("id") == room.room_id), None)
    if not room_config:
        logger.debug(f"Message in room {room.room_id} not processed: room not in config.")
        return

    relates_to = event.source.get("content", {}).get("m.relates_to")
    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None # For reactions to Matrix messages

    relay_reactions_enabled = current_config.get("meshtastic", {}).get("relay_reactions", False)

    if isinstance(event, ReactionEvent) and relates_to:
        is_reaction = True
        reaction_emoji = relates_to.get("key")
        original_matrix_event_id = relates_to.get("event_id")

    # Handling emotes as potential reactions (e.g. from bridged reactions)
    if isinstance(event, RoomMessageEmote):
        # This logic assumes emotes with "reacted" are bridged reactions
        reaction_body = getattr(event, 'body', "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body, re.IGNORECASE)
        if reaction_match:
            is_reaction = True # Treat as a reaction for processing
            reaction_emoji = reaction_match.group(1).strip()
            # If it's an m.emote that's a reaction, it might also have m.relates_to
            if relates_to and relates_to.get("event_id"):
                original_matrix_event_id = relates_to.get("event_id")
            # If not, the original logic relied on meshtastic_replyId from the emote's content for linking
            # This part is tricky because an emote IS a message, not a pure reaction event type.
            # The original code seemed to use meshtastic_replyId in content for this.


    text = getattr(event, 'body', "").strip() if not is_reaction else "" # Only use body for non-reactions directly

    # Extract custom fields from event content
    event_content = event.source.get("content", {})
    longname = event_content.get("meshtastic_longname")
    shortname = event_content.get("meshtastic_shortname")
    meshnet_name = event_content.get("meshtastic_meshnet") # Meshnet of the original Meshtastic sender
    meshtastic_replyId = event_content.get("meshtastic_replyId") # replyId from original Meshtastic msg
    suppress = event_content.get("mmrelay_suppress")

    if suppress:
        logger.debug(f"Message from {event.sender} suppressed by mmrelay_suppress flag.")
        return
    if is_reaction and not relay_reactions_enabled:
        logger.debug("Reaction event encountered but relay_reactions is disabled. Ignoring.")
        return

    local_meshnet_name = current_config.get("meshtastic", {}).get("meshnet_name", "MyMeshnet")
    matrix_client_instance = get_matrix_client()

    # Dynamic import for Meshtastic sender functions
    try:
        from mmrelay.meshtastic.sender import send_text_to_meshtastic, send_data_to_meshtastic
    except ImportError:
        logger.error("Failed to import Meshtastic sender functions. Cannot relay to Meshtastic.")
        return


    if is_reaction and relay_reactions_enabled:
        # Case 1: Reaction from a remote meshnet, relayed into Matrix as an emote by the bridge
        if (meshnet_name and meshnet_name != local_meshnet_name and
            meshtastic_replyId and isinstance(event, RoomMessageEmote) and reaction_emoji):
            logger.info(f"Relaying reaction from remote meshnet '{meshnet_name}' via emote event.")
            short_meshnet_name = meshnet_name[:4]
            eff_shortname = shortname or (longname[:3] if longname else "???")

            # meshtastic_text from the emote's content is the text of the original message reacted to
            original_msg_text = event_content.get("meshtastic_text", "")
            abbreviated_text = strip_quoted_lines(original_msg_text)
            abbreviated_text = (abbreviated_text[:40] + "...") if len(abbreviated_text) > 40 else abbreviated_text

            reaction_message = f'{eff_shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

            if current_config.get("meshtastic", {}).get("broadcast_enabled", False):
                logger.debug(f"Sending reaction to Meshtastic (remote reaction relay): {reaction_message}")
                await send_text_to_meshtastic(text=reaction_message, channel_index=room_config["meshtastic_channel"])
            return

        # Case 2: Direct Matrix reaction (m.reaction event type) to a message that originated from Meshtastic
        if original_matrix_event_id and reaction_emoji: # Ensure emoji is present
            orig_map = get_message_map_by_matrix_event_id(original_matrix_event_id)
            if not orig_map:
                logger.debug(f"Original message for reaction (Matrix event ID: {original_matrix_event_id}) not found in DB. Possibly a reaction to a non-Meshtastic message or reaction-to-reaction.")
                return

            _meshtastic_id, _matrix_room_id, meshtastic_text_db, _meshtastic_meshnet_db = orig_map

            # Get display name of the Matrix user who reacted
            reacting_user_display_name = room.user_name(event.sender) # Room-specific display name
            if not reacting_user_display_name and matrix_client_instance:
                try:
                    dn_response = await matrix_client_instance.get_displayname(event.sender)
                    reacting_user_display_name = dn_response.displayname if hasattr(dn_response, 'displayname') and dn_response.displayname else event.sender
                except Exception: # Fallback if get_displayname fails
                    reacting_user_display_name = event.sender
            elif not reacting_user_display_name: # Fallback if no client or room.user_name failed
                 reacting_user_display_name = event.sender

            short_display_name = reacting_user_display_name[:5] # Abbreviate display name
            prefix = f"{short_display_name}[M]: " # Prefix to indicate Matrix user

            abbreviated_text = strip_quoted_lines(meshtastic_text_db) # Clean and abbreviate original Meshtastic text
            abbreviated_text = (abbreviated_text[:40] + "..." if len(abbreviated_text) > 40 else abbreviated_text)

            reaction_message = f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'

            if current_config.get("meshtastic", {}).get("broadcast_enabled", False):
                logger.debug(f"Sending reaction to Meshtastic (local Matrix reaction relay): {reaction_message}")
                await send_text_to_meshtastic(text=reaction_message, channel_index=room_config["meshtastic_channel"])
            return

        # If it's a reaction but doesn't fit above, log and ignore
        if is_reaction:
            logger.debug(f"Unhandled reaction type or missing data. Emoji: {reaction_emoji}, OriginalMatrixID: {original_matrix_event_id}. Event: {event.event_id}")
            return


    # Regular message processing (not a reaction or reaction handling failed/skipped)
    full_display_name_prefix = "" # Used for logging and potentially message construction
    processed_text_for_mesh = text # Default to event.body (already stripped)

    if longname and meshnet_name: # Message originated from Meshtastic (relayed into Matrix, now potentially looping back)
        full_display_name_prefix = f"{longname}/{meshnet_name}"
        if meshnet_name != local_meshnet_name: # Message from a remote meshnet, relayed by this bridge
            logger.info(f"Processing message from remote meshnet '{meshnet_name}': {text}")
            short_meshnet_name = meshnet_name[:4]
            eff_shortname = shortname or (longname[:3] if longname else "???")

            # Remove the prefix added by matrix_relay (e.g., "[Longname/Meshnet]: actual message")
            # This is to avoid double prefixing if it goes back to another mesh.
            # Original code: text = re.sub(rf"^\[{full_display_name_prefix}\]: ", "", text) -> this used the unescaped prefix
            # Corrected:
            text_no_prefix = re.sub(rf"^\[{re.escape(full_display_name_prefix)}\]:\s*", "", text, 1)

            truncated_text_no_prefix = truncate_message(text_no_prefix) # Truncate the actual message content
            processed_text_for_mesh = f"{eff_shortname}/{short_meshnet_name}: {truncated_text_no_prefix}"
        else: # Message from local meshnet relayed into Matrix and now seen again. Ignore.
            logger.debug(f"Ignoring loopback message from local meshnet '{meshnet_name}' via Matrix sender {event.sender}")
            return
    else: # Normal Matrix user message
        matrix_user_display_name = room.user_name(event.sender)
        if not matrix_user_display_name and matrix_client_instance:
            try:
                dn_response = await matrix_client_instance.get_displayname(event.sender)
                matrix_user_display_name = dn_response.displayname if hasattr(dn_response, 'displayname') and dn_response.displayname else event.sender
            except Exception:
                 matrix_user_display_name = event.sender
        elif not matrix_user_display_name:
             matrix_user_display_name = event.sender

        short_display_name = matrix_user_display_name[:5]
        prefix = f"{short_display_name}[M]: "
        full_display_name_prefix = matrix_user_display_name # For logging

        truncated_original_text = truncate_message(text) # Truncate the original Matrix message text
        processed_text_for_mesh = f"{prefix}{truncated_original_text}"
        logger.debug(f"Processing matrix message from [{full_display_name_prefix}]: {text} -> For mesh: {processed_text_for_mesh}")


    # Plugin functionality
    from mmrelay.plugin_loader import load_plugins # Import here to ensure it's fresh if plugins change
    plugins = load_plugins()
    found_matching_plugin = False
    # The `full_message` for plugins was the `processed_text_for_mesh` in original logic.
    # Let's pass `event` as well, as plugins might need more context.
    for plugin in plugins:
        try:
            # Giving plugin the raw event, and the processed text intended for mesh
            if await plugin.handle_room_message(room, event, processed_text_for_mesh):
                found_matching_plugin = True
                logger.info(f"Processed message from {event.sender} with plugin: {plugin.plugin_name}")
                break
        except Exception as e:
            logger.error(f"Error processing message with plugin {plugin.plugin_name}: {e}", exc_info=True)

    # Check if it's a command for any plugin
    is_bot_command = any(bot_command(cmd, event) for p in plugins for cmd in p.get_matrix_commands())
    if is_bot_command:
        logger.debug(f"Message from {event.sender} is a bot command, not sending to mesh (plugin handler responsible if it was a command).")
        return # Plugins handle their own commands; don't relay command messages further by default.

    if not found_matching_plugin: # If no plugin handled it and it's not a command
        if current_config.get("meshtastic", {}).get("broadcast_enabled", False):
            portnum_str = event_content.get("meshtastic_portnum") # e.g. "TEXT_MESSAGE_APP" or "DETECTION_SENSOR_APP"

            sent_packet_id_for_map = None # Placeholder for Meshtastic packet ID if needed for map

            if portnum_str == "DETECTION_SENSOR_APP" and current_config.get("meshtastic",{}).get("detection_sensor", False):
                logger.info(f"Relaying Matrix message from {full_display_name_prefix} as DETECTION_SENSOR_APP data to Meshtastic: {processed_text_for_mesh}")
                # send_data_to_meshtastic should handle encoding if needed
                # Pass port_num_name for clarity, or the actual enum if available/required by sender
                await send_data_to_meshtastic(
                    data=processed_text_for_mesh, # Assuming send_data_to_meshtastic expects string or handles bytes
                    channel_index=room_config["meshtastic_channel"],
                    port_num_name="DETECTION_SENSOR_APP"
                )
                # sent_packet_id_for_map = result.id if result else None
            # Default to TEXT_MESSAGE_APP if portnum_str is not DETECTION_SENSOR_APP or if detection sensor is off
            elif portnum_str != "DETECTION_SENSOR_APP" or not current_config.get("meshtastic",{}).get("detection_sensor", False):
                logger.info(f"Relaying Matrix message from {full_display_name_prefix} as TEXT_MESSAGE_APP to Meshtastic: {processed_text_for_mesh}")
                await send_text_to_meshtastic(
                    text=processed_text_for_mesh,
                    channel_index=room_config["meshtastic_channel"]
                )
                # sent_packet_id_for_map = result.id if result else None

            if relay_reactions_enabled and sent_packet_id_for_map: # This part is currently non-functional due to sent_packet_id_for_map
                store_message_map(
                    str(sent_packet_id_for_map),
                    event.event_id,
                    room.room_id,
                    text, # original non-prefixed Matrix text
                    meshtastic_meshnet=local_meshnet_name, # It's a Matrix message, so associate with local meshnet
                )
                # Pruning logic
                database_config = current_config.get("database", {})
                msg_map_config = database_config.get("msg_map", {})
                if not msg_map_config:
                    db_config = current_config.get("db", {})
                    legacy_msg_map_config = db_config.get("msg_map", {})
                    if legacy_msg_map_config: msg_map_config = legacy_msg_map_config
                msgs_to_keep = msg_map_config.get("msgs_to_keep", 500)
                if msgs_to_keep > 0: prune_message_map(msgs_to_keep)
        else:
            logger.debug(f"Broadcast not enabled: Message from {full_display_name_prefix} (Matrix user {event.sender}) dropped.")


async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
    logger.debug(f"Room member event: User {event.state_key} in room {room.room_id}, membership {event.membership}, displayname {event.content.get('displayname')}")
    # nio client updates room state automatically.
    # Could add logic here if specific actions needed on member changes.
    pass
