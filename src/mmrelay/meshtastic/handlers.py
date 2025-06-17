import asyncio
from pubsub import pub # For subscribing to Meshtastic messages

from mmrelay.db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from mmrelay.log_utils import get_logger
# Import the main relay function from the Matrix module's sender part
from mmrelay.matrix.sender import relay_meshtastic_to_matrix
# Import necessary items from this module's interface part
from mmrelay.meshtastic.interface import (
    get_event_loop,
    get_shutting_down_flag,
    config as global_meshtastic_config, # Access the config from interface.py
    matrix_rooms as global_matrix_rooms_config # Access matrix_rooms from interface.py
)

logger = get_logger(name="MeshtasticHandlers")

def on_meshtastic_message(packet, interface): # 'interface' is the meshtastic_client instance
    """Handles incoming messages from the Meshtastic device."""

    # Use config and matrix_rooms from the meshtastic.interface module
    current_config = global_meshtastic_config
    current_matrix_rooms = global_matrix_rooms_config

    if not current_config: # Should always be populated if connect_meshtastic was called
        logger.error("Configuration not available in MeshtasticHandlers. Cannot process message.")
        return

    if get_shutting_down_flag():
        logger.debug("Shutdown in progress. Ignoring incoming Meshtastic message.")
        return

    loop = get_event_loop() # Get the asyncio event loop
    if not loop or loop.is_closed():
        logger.error("Asyncio event loop not available or closed in MeshtasticHandlers. Cannot process message.")
        return

    # Basic logging of message receipt
    decoded_packet = packet.get("decoded", {})
    if decoded_packet.get("text"):
        logger.info(f"Received Meshtastic text message: {decoded_packet.get('text')}")
    elif decoded_packet.get("portnum"): # Log other packet types by portnum if not text
        logger.info(f"Received Meshtastic data on portnum: {decoded_packet.get('portnum')}")
    else:
        logger.debug(f"Received Meshtastic packet (no text/portnum in decoded): {packet}")

    # Filter reactions if relay_reactions is disabled in config
    relay_reactions_enabled = current_config.get("meshtastic", {}).get("relay_reactions", False)
    if decoded_packet.get("portnum") == "TEXT_MESSAGE_APP": # Check only for text messages
        if not relay_reactions_enabled and ("emoji" in decoded_packet or "replyId" in decoded_packet):
            logger.debug("Filtered out Meshtastic reaction/tapback packet (relay_reactions=false).")
            return

    sender_id = packet.get("fromId") or packet.get("from", "UnknownSender") # fromId preferred
    to_id = packet.get("to", "UnknownRecipient")

    text_content = decoded_packet.get("text")
    reply_id = decoded_packet.get("replyId") # For reactions
    is_emoji_reaction = "emoji" in decoded_packet and decoded_packet["emoji"] == 1

    # Determine if it's a direct message (DM) to the relay node
    from meshtastic.mesh_interface import BROADCAST_NUM # Constant for broadcast address
    my_node_id = interface.myInfo.my_node_num if interface and hasattr(interface, 'myInfo') else None
    is_direct_message = (my_node_id is not None and to_id == my_node_id)

    # Get meshnet_name from config, default if not found
    meshnet_name_for_display = current_config.get("meshtastic", {}).get("meshnet_name", "Meshnet")

    # Handle reactions (Meshtastic emoji reaction -> Matrix text reaction)
    if reply_id and is_emoji_reaction and relay_reactions_enabled:
        logger.debug(f"Processing Meshtastic reaction: replyId={reply_id}, emoji={text_content}")
        longname_val = get_longname(sender_id) or str(sender_id)
        shortname_val = get_shortname(sender_id) or str(sender_id)

        original_message_map = get_message_map_by_meshtastic_id(str(reply_id)) # Ensure ID is string
        if original_message_map:
            _orig_matrix_event_id, matrix_room_id_for_reaction, original_meshtastic_text, _orig_meshnet = original_message_map

            abbreviated_orig_text = (original_meshtastic_text[:40] + "..." if len(original_meshtastic_text) > 40 else original_meshtastic_text)
            reactor_display_name = f"{longname_val}/{meshnet_name_for_display}" # Reactor's name with their meshnet
            reaction_symbol = text_content.strip() if (text_content and text_content.strip()) else "👍" # Default emoji

            # Construct the reaction message for Matrix (as an emote)
            matrix_reaction_message = f'\n [{reactor_display_name}] reacted {reaction_symbol} to "{abbreviated_orig_text}"'

            logger.info(f"Relaying Meshtastic reaction to Matrix room {matrix_room_id_for_reaction}: {matrix_reaction_message.strip()}")
            asyncio.run_coroutine_threadsafe(
                relay_meshtastic_to_matrix(
                    room_id=matrix_room_id_for_reaction,
                    message=matrix_reaction_message,
                    longname=longname_val, # Reactor's longname
                    shortname=shortname_val, # Reactor's shortname
                    meshnet_name=meshnet_name_for_display, # Reactor's meshnet name
                    portnum=decoded_packet.get("portnum"),
                    meshtastic_id=str(packet.get("id")), # ID of this reaction packet
                    meshtastic_replyId=str(reply_id), # ID of the message being replied to
                    meshtastic_text=original_meshtastic_text, # Text of the original message
                    emote=True, # Send as m.emote
                    emoji=True, # Custom flag indicating it's an emoji reaction
                ), loop=loop,
            )
        else:
            logger.debug(f"Original message for Meshtastic reaction (replyId: {reply_id}) not found in DB.")
        return # Reaction processed or skipped, no further handling for this packet

    # Handle regular text messages or other data packets
    if text_content or decoded_packet.get("portnum") != "TEXT_MESSAGE_APP": # Process if text or not a standard text portnum (e.g. sensor data)
        channel_id = packet.get("channel")
        if channel_id is None: # Deduce channel if not explicitly in packet (common for some firmwares/ports)
            portnum_val = decoded_packet.get("portnum")
            if portnum_val == "TEXT_MESSAGE_APP" or portnum_val == 1: channel_id = 0 # Default channel for text
            elif portnum_val == "DETECTION_SENSOR_APP": channel_id = 0 # Default for detection sensor
            else: logger.debug(f"Unknown portnum {portnum_val} in packet, cannot determine channel. Ignoring."); return

        # Check if this Meshtastic channel is mapped to any Matrix room
        if not any(room.get("meshtastic_channel") == channel_id for room in current_matrix_rooms):
            logger.debug(f"Skipping message from Meshtastic channel {channel_id} (not mapped to any Matrix room)."); return

        # Special handling for DETECTION_SENSOR_APP if its processing is disabled
        if decoded_packet.get("portnum") == "DETECTION_SENSOR_APP" and \
           not current_config.get("meshtastic", {}).get("detection_sensor", False):
            logger.debug("Detection sensor packet received, but 'detection_sensor' processing is disabled in config."); return

        # Get sender's longname/shortname (from DB or node list)
        longname_val = get_longname(sender_id)
        shortname_val = get_shortname(sender_id)
        if interface and hasattr(interface, 'nodes') and interface.nodes: # Check if interface and nodes exist
            node_info = interface.nodes.get(sender_id)
            if node_info and "user" in node_info:
                user_data = node_info["user"]
                if not longname_val and "longName" in user_data: longname_val = user_data["longName"]; save_longname(sender_id, longname_val)
                if not shortname_val and "shortName" in user_data: shortname_val = user_data["shortName"]; save_shortname(sender_id, shortname_val)

        longname_val = longname_val or str(sender_id) # Fallback to ID if name not found
        shortname_val = shortname_val or str(sender_id)

        # Format message for Matrix: "[Longname/MeshnetName]: ActualText"
        # The meshnet_name_for_display is the one configured for *this* relay instance.
        # If the packet has source meshnet info, that could be used, but Meshtastic packets don't typically carry it.
        final_formatted_message = f"[{longname_val}/{meshnet_name_for_display}]: {text_content if text_content else str(decoded_packet)}"


        # Plugin handling
        from mmrelay.plugin_loader import load_plugins # Deferred import
        plugins = load_plugins()
        plugin_handled = False
        for plugin in plugins:
            try:
                # Plugins receive the raw packet, the formatted message, and sender details
                if await plugin.handle_meshtastic_message(packet, final_formatted_message, longname_val, meshnet_name_for_display):
                    plugin_handled = True
                    logger.debug(f"Meshtastic message/packet processed by plugin: {plugin.plugin_name}")
                    break
            except Exception as e:
                logger.error(f"Error executing plugin {plugin.plugin_name} for Meshtastic message: {e}", exc_info=True)

        # Conditions for not relaying to Matrix:
        if is_direct_message: logger.debug(f"Direct message from {longname_val} to relay node. Not forwarding to Matrix."); return
        if plugin_handled: logger.debug("Message handled by a plugin. Not forwarding to Matrix."); return

        # Relay to all configured Matrix rooms that are mapped to this Meshtastic channel
        logger.info(f"Relaying Meshtastic message from {longname_val} to Matrix rooms on channel {channel_id}")
        if not current_matrix_rooms: logger.error("No Matrix rooms configured. Cannot relay."); return

        for room_config in current_matrix_rooms:
            if room_config.get("meshtastic_channel") == channel_id:
                logger.debug(f"Relaying to Matrix room: {room_config.get('id')}")
                asyncio.run_coroutine_threadsafe(
                    relay_meshtastic_to_matrix(
                        room_id=room_config["id"],
                        message=final_formatted_message,
                        longname=longname_val,
                        shortname=shortname_val,
                        meshnet_name=meshnet_name_for_display, # Name of this relay's meshnet
                        portnum=decoded_packet.get("portnum"),
                        meshtastic_id=str(packet.get("id")), # ID of this Meshtastic packet
                        meshtastic_text=text_content # Original text content for mapping
                    ), loop=loop,
                )
    else:
        logger.debug(f"Meshtastic packet from {sender_id} had no text and was not a known data type for plugin handling. Ignoring.")


# Subscribe the handler to 'meshtastic.receive' events from pubsub.
# This is usually triggered by the Meshtastic Python library when a packet is received.
pub.subscribe(on_meshtastic_message, "meshtastic.receive")
logger.info("Subscribed 'on_meshtastic_message' handler to 'meshtastic.receive' events.")
