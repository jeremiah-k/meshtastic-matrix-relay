import asyncio
import contextlib
import io
import os
import threading
import time
from typing import List

import meshtastic.ble_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from bleak.exc import BleakDBusError, BleakError
from meshtastic.protobuf import mesh_pb2, portnums_pb2
from pubsub import pub

from mmrelay.db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from mmrelay.log_utils import get_logger

# Global config variable that will be set from config.py
config = None

# Do not import plugin_loader here to avoid circular imports

# Initialize matrix rooms configuration
matrix_rooms: List[dict] = []

# Initialize logger for Meshtastic
logger = get_logger(name="Meshtastic")

# Global variables for the Meshtastic connection and event loop management
meshtastic_client = None
event_loop = None  # Will be set from main.py

meshtastic_lock = (
    threading.Lock()
)  # To prevent race conditions on meshtastic_client access

reconnecting = False
shutting_down = False
reconnect_task = None  # To keep track of the reconnect task

# Track pubsub subscription state to prevent duplicate subscriptions during reconnections
subscribed_to_messages = False
subscribed_to_connection_lost = False
subscribed_to_connection_established = False


def is_running_as_service():
    """
    Check if the application is running as a systemd service.
    
    Returns:
        True if the process is running under systemd, either by detecting the INVOCATION_ID environment variable or by verifying that the parent process is systemd; otherwise, False.
    """
    # Check for INVOCATION_ID environment variable (set by systemd)
    if os.environ.get("INVOCATION_ID"):
        return True

    # Check if parent process is systemd
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("PPid:"):
                    ppid = int(line.split()[1])
                    with open(f"/proc/{ppid}/comm") as p:
                        return p.read().strip() == "systemd"
    except (FileNotFoundError, PermissionError, ValueError):
        pass

    return False


def serial_port_exists(port_name):
    """
    Check if the specified serial port exists.
    This prevents attempting connections on non-existent ports.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports


def connect_meshtastic(passed_config=None, force_connect=False):
    """
    Establishes and manages a connection to a Meshtastic device using the specified connection type (serial, BLE, or TCP).
    
    If a configuration is provided, updates the global configuration and Matrix room mappings. Handles reconnection logic, including closing any existing connection if `force_connect` is True. Retries connection attempts with exponential backoff until successful or shutdown is initiated. Subscribes to message and connection events only once per process to avoid duplicate subscriptions.
    
    Parameters:
        passed_config (dict, optional): Configuration dictionary to use for the connection. If provided, updates the global configuration.
        force_connect (bool, optional): If True, forces a new connection even if one already exists.
    
    Returns:
        Meshtastic interface client if the connection is successful, or None if the connection fails or shutdown is in progress.
    """
    global meshtastic_client, shutting_down, config, matrix_rooms
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

        # If config is valid, extract matrix_rooms
        if config and "matrix_rooms" in config:
            matrix_rooms = config["matrix_rooms"]

    with meshtastic_lock:
        if meshtastic_client and not force_connect:
            return meshtastic_client

        # Close previous connection if exists
        if meshtastic_client:
            try:
                meshtastic_client.close()
            except Exception as e:
                logger.warning(f"Error closing previous connection: {e}")
            meshtastic_client = None

        # Check if config is available
        if config is None:
            logger.error("No configuration available. Cannot connect to Meshtastic.")
            return None

        # Determine connection type and attempt connection
        connection_type = config["meshtastic"]["connection_type"]

        # Support legacy "network" connection type (now "tcp")
        if connection_type == "network":
            connection_type = "tcp"
            logger.warning(
                "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
            )
        retry_limit = 0  # 0 means infinite retries
        attempts = 1
        successful = False

        while (
            not successful
            and (retry_limit == 0 or attempts <= retry_limit)
            and not shutting_down
        ):
            try:
                if connection_type == "serial":
                    # Serial connection
                    serial_port = config["meshtastic"]["serial_port"]
                    logger.info(f"Connecting to serial port {serial_port}")

                    # Check if serial port exists before connecting
                    if not serial_port_exists(serial_port):
                        logger.warning(
                            f"Serial port {serial_port} does not exist. Waiting..."
                        )
                        time.sleep(5)
                        attempts += 1
                        continue

                    meshtastic_client = meshtastic.serial_interface.SerialInterface(
                        serial_port
                    )

                elif connection_type == "ble":
                    # BLE connection
                    ble_address = config["meshtastic"].get("ble_address")
                    if ble_address:
                        logger.info(f"Connecting to BLE address {ble_address}")

                        # Connect without progress indicator
                        meshtastic_client = meshtastic.ble_interface.BLEInterface(
                            address=ble_address,
                            noProto=False,
                            debugOut=None,
                            noNodes=False,
                        )
                    else:
                        logger.error("No BLE address provided.")
                        return None

                elif connection_type == "tcp":
                    # TCP connection
                    target_host = config["meshtastic"]["host"]
                    logger.info(f"Connecting to host {target_host}")

                    # Connect without progress indicator
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(
                        hostname=target_host
                    )
                else:
                    logger.error(f"Unknown connection type: {connection_type}")
                    return None

                successful = True
                nodeInfo = meshtastic_client.getMyNodeInfo()
                logger.info(
                    f"Connected to {nodeInfo['user']['shortName']} / {nodeInfo['user']['hwModel']}"
                )

                # Subscribe to message and connection events (only if not already subscribed)
                global subscribed_to_messages, subscribed_to_connection_lost, subscribed_to_connection_established
                if not subscribed_to_messages:
                    pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                    subscribed_to_messages = True
                    logger.debug("Subscribed to meshtastic.receive")

                if not subscribed_to_connection_lost:
                    pub.subscribe(
                        on_lost_meshtastic_connection, "meshtastic.connection.lost"
                    )
                    subscribed_to_connection_lost = True
                    logger.debug("Subscribed to meshtastic.connection.lost")

                if not subscribed_to_connection_established:
                    pub.subscribe(
                        on_established_meshtastic_connection, "meshtastic.connection.established"
                    )
                    subscribed_to_connection_established = True
                    logger.debug("Subscribed to meshtastic.connection.established")

            except (
                serial.SerialException,
                BleakDBusError,
                BleakError,
                Exception,
            ) as e:
                if shutting_down:
                    logger.debug("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(
                        attempts * 2, 30
                    )  # Exponential backoff capped at 30s
                    logger.warning(
                        f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}"
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Could not connect after {retry_limit} attempts: {e}")
                    return None

    return meshtastic_client


def on_lost_meshtastic_connection(interface=None, detection_source="detected by library"):
    """
    Handles Meshtastic connection loss by initiating a reconnection sequence unless a shutdown is in progress or a reconnection is already underway.
    
    Parameters:
        interface: The interface that lost connection (unused; present for compatibility).
        detection_source (str): Description of how the disconnection was detected.
    """
    global meshtastic_client, reconnecting, shutting_down, event_loop, reconnect_task
    with meshtastic_lock:
        if shutting_down:
            logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.debug(
                "Reconnection already in progress. Skipping additional reconnection attempt."
            )
            return
        reconnecting = True
        logger.error(f"Lost connection ({detection_source}). Reconnecting...")

        if meshtastic_client:
            try:
                meshtastic_client.close()
            except OSError as e:
                if e.errno == 9:
                    # Bad file descriptor, already closed
                    pass
                else:
                    logger.warning(f"Error closing Meshtastic client: {e}")
            except Exception as e:
                logger.warning(f"Error closing Meshtastic client: {e}")
        meshtastic_client = None

        if event_loop:
            reconnect_task = asyncio.run_coroutine_threadsafe(reconnect(), event_loop)


async def reconnect():
    """
    Asynchronously attempts to reconnect to the Meshtastic device using exponential backoff, stopping if a shutdown is initiated.
    
    The function increases the wait time between attempts up to a maximum of 5 minutes and provides a progress bar if not running as a service. The reconnection process halts immediately if the shutdown flag is set or if reconnection succeeds.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = 10
    try:
        while not shutting_down:
            try:
                logger.info(
                    f"Reconnection attempt starting in {backoff_time} seconds..."
                )

                # Show reconnection countdown with Rich (if not in a service)
                if not is_running_as_service():
                    from rich.progress import (
                        BarColumn,
                        Progress,
                        TextColumn,
                        TimeRemainingColumn,
                    )

                    with Progress(
                        TextColumn("[cyan]Meshtastic: Reconnecting in"),
                        BarColumn(),
                        TextColumn("[cyan]{task.percentage:.0f}%"),
                        TimeRemainingColumn(),
                        transient=True,
                    ) as progress:
                        task = progress.add_task("Waiting", total=backoff_time)
                        for _ in range(backoff_time):
                            if shutting_down:
                                break
                            await asyncio.sleep(1)
                            progress.update(task, advance=1)
                else:
                    await asyncio.sleep(backoff_time)
                if shutting_down:
                    logger.debug(
                        "Shutdown in progress. Aborting reconnection attempts."
                    )
                    break
                meshtastic_client = connect_meshtastic(force_connect=True)
                if meshtastic_client:
                    logger.info("Reconnected successfully.")
                    break
            except Exception as e:
                if shutting_down:
                    break
                logger.error(f"Reconnection attempt failed: {e}")
                backoff_time = min(backoff_time * 2, 300)  # Cap backoff at 5 minutes
    except asyncio.CancelledError:
        logger.info("Reconnection task was cancelled.")
    finally:
        reconnecting = False


def on_established_meshtastic_connection(interface=None):
    """
    Callback triggered when a Meshtastic connection is successfully established.
    
    Clears the reconnecting flag and logs the connection event.
    """
    global reconnecting
    with meshtastic_lock:
        logger.info("Connection established (detected by library)")
        reconnecting = False  # Clear reconnecting flag when connection is confirmed


def on_meshtastic_message(packet, interface):
    """
    Process incoming Meshtastic messages and relay them to Matrix rooms or plugins according to message type and interaction settings.
    
    Handles reactions and replies by relaying them to Matrix if enabled. Normal text messages are relayed to all mapped Matrix rooms unless handled by a plugin or directed to the relay node. Non-text messages are passed to plugins for processing. Messages from unmapped channels or disabled detection sensors are filtered out, and sender information is retrieved or stored as needed.
    """
    global config, matrix_rooms

    # Log that we received a message (without the full packet details)
    if packet.get("decoded", {}).get("text"):
        logger.info(
            f"Received Meshtastic message: {packet.get('decoded', {}).get('text')}"
        )
    else:
        logger.debug("Received non-text Meshtastic message")

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Meshtastic message.")
        return

    # Import the configuration helpers
    from mmrelay.matrix_utils import get_interaction_settings, message_storage_enabled

    # Get interaction settings
    interactions = get_interaction_settings(config)
    message_storage_enabled(interactions)

    # Filter packets based on interaction settings
    if packet.get("decoded", {}).get("portnum") == "TEXT_MESSAGE_APP":
        decoded = packet.get("decoded", {})
        # Filter out reactions if reactions are disabled
        if (
            not interactions["reactions"]
            and "emoji" in decoded
            and decoded.get("emoji") == 1
        ):
            logger.debug(
                "Filtered out reaction packet due to reactions being disabled."
            )
            return

    from mmrelay.matrix_utils import matrix_relay

    global event_loop

    if shutting_down:
        logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return

    if event_loop is None:
        logger.error("Event loop is not set. Cannot process message.")
        return

    loop = event_loop

    sender = packet.get("fromId") or packet.get("from")
    toId = packet.get("to")

    decoded = packet.get("decoded", {})
    text = decoded.get("text")
    replyId = decoded.get("replyId")
    emoji_flag = "emoji" in decoded and decoded["emoji"] == 1

    # Determine if this is a direct message to the relay node
    from meshtastic.mesh_interface import BROADCAST_NUM

    myId = interface.myInfo.my_node_num

    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM:
        is_direct_message = False
    else:
        # Message to someone else; ignoring for broadcasting logic
        is_direct_message = False

    meshnet_name = config["meshtastic"]["meshnet_name"]

    # Reaction handling (Meshtastic -> Matrix)
    # If replyId and emoji_flag are present and reactions are enabled, we relay as text reactions in Matrix
    if replyId and emoji_flag and interactions["reactions"]:
        longname = get_longname(sender) or str(sender)
        shortname = get_shortname(sender) or str(sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig
            abbreviated_text = (
                meshtastic_text[:40] + "..."
                if len(meshtastic_text) > 40
                else meshtastic_text
            )

            # Ensure that meshnet_name is always included, using our own meshnet for accuracy.
            full_display_name = f"{longname}/{meshnet_name}"

            reaction_symbol = text.strip() if (text and text.strip()) else "⚠️"
            reaction_message = f'\n [{full_display_name}] reacted {reaction_symbol} to "{abbreviated_text}"'

            # Relay the reaction as emote to Matrix, preserving the original meshnet name
            asyncio.run_coroutine_threadsafe(
                matrix_relay(
                    matrix_room_id,
                    reaction_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=meshtastic_text,
                    emote=True,
                    emoji=True,
                ),
                loop=loop,
            )
        else:
            logger.debug("Original message for reaction not found in DB.")
        return

    # Reply handling (Meshtastic -> Matrix)
    # If replyId is present but emoji is not (or not 1), this is a reply
    if replyId and not emoji_flag and interactions["replies"]:
        longname = get_longname(sender) or str(sender)
        shortname = get_shortname(sender) or str(sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig

            # Format the reply message for Matrix
            full_display_name = f"{longname}/{meshnet_name}"
            formatted_message = f"[{full_display_name}]: {text}"

            logger.info(f"Relaying Meshtastic reply from {longname} to Matrix")

            # Relay the reply to Matrix with proper reply formatting
            asyncio.run_coroutine_threadsafe(
                matrix_relay(
                    matrix_room_id,
                    formatted_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=text,
                    reply_to_event_id=matrix_event_id,
                ),
                loop=loop,
            )
        else:
            logger.debug("Original message for reply not found in DB.")
        return

    # Normal text messages or detection sensor messages
    if text:
        # Determine the channel for this message
        channel = packet.get("channel")
        if channel is None:
            # If channel not specified, deduce from portnum
            if (
                decoded.get("portnum") == "TEXT_MESSAGE_APP"
                or decoded.get("portnum") == 1
            ):
                channel = 0
            elif decoded.get("portnum") == "DETECTION_SENSOR_APP":
                channel = 0
            else:
                logger.debug(
                    f"Unknown portnum {decoded.get('portnum')}, cannot determine channel"
                )
                return

        # Check if channel is mapped to a Matrix room
        channel_mapped = False
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                channel_mapped = True
                break

        if not channel_mapped:
            logger.debug(f"Skipping message from unmapped channel {channel}")
            return

        # If detection_sensor is disabled and this is a detection sensor packet, skip it
        if decoded.get("portnum") == "DETECTION_SENSOR_APP" and not config[
            "meshtastic"
        ].get("detection_sensor", False):
            logger.debug(
                "Detection sensor packet received, but detection sensor processing is disabled."
            )
            return

        # Attempt to get longname/shortname from database or nodes
        longname = get_longname(sender)
        shortname = get_shortname(sender)

        if not longname or not shortname:
            node = interface.nodes.get(sender)
            if node:
                user = node.get("user")
                if user:
                    if not longname:
                        longname_val = user.get("longName")
                        if longname_val:
                            save_longname(sender, longname_val)
                            longname = longname_val
                    if not shortname:
                        shortname_val = user.get("shortName")
                        if shortname_val:
                            save_shortname(sender, shortname_val)
                            shortname = shortname_val
            else:
                logger.debug(f"Node info for sender {sender} not available yet.")

        # If still not available, fallback to sender ID
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        formatted_message = f"[{longname}/{meshnet_name}]: {text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        from mmrelay.plugin_loader import load_plugins

        plugins = load_plugins()

        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                result = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(
                        packet, formatted_message, longname, meshnet_name
                    ),
                    loop=loop,
                )
                found_matching_plugin = result.result()
                if found_matching_plugin:
                    logger.debug(f"Processed by plugin {plugin.plugin_name}")

        # If message is a DM or handled by plugin, do not relay further
        if is_direct_message:
            logger.debug(
                f"Received a direct message from {longname}: {text}. Not relaying to Matrix."
            )
            return
        if found_matching_plugin:
            logger.debug("Message was handled by a plugin. Not relaying to Matrix.")
            return

        # Relay the message to all Matrix rooms mapped to this channel
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")

        # Check if matrix_rooms is empty
        if not matrix_rooms:
            logger.error("matrix_rooms is empty. Cannot relay message to Matrix.")
            return

        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                # Storing the message_map (if enabled) occurs inside matrix_relay() now,
                # controlled by relay_reactions.
                try:
                    asyncio.run_coroutine_threadsafe(
                        matrix_relay(
                            room["id"],
                            formatted_message,
                            longname,
                            shortname,
                            meshnet_name,
                            decoded.get("portnum"),
                            meshtastic_id=packet.get("id"),
                            meshtastic_text=text,
                        ),
                        loop=loop,
                    )
                except Exception as e:
                    logger.error(f"Error relaying message to Matrix: {e}")
    else:
        # Non-text messages via plugins
        portnum = decoded.get("portnum")
        from mmrelay.plugin_loader import load_plugins

        plugins = load_plugins()
        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                result = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(
                        packet,
                        formatted_message=None,
                        longname=None,
                        meshnet_name=None,
                    ),
                    loop=loop,
                )
                found_matching_plugin = result.result()
                if found_matching_plugin:
                    logger.debug(
                        f"Processed {portnum} with plugin {plugin.plugin_name}"
                    )


async def check_connection():
    """
    Periodically verifies the health of the Meshtastic connection and triggers reconnection if needed.
    
    For non-BLE connections, this coroutine calls `localNode.getMetadata()` at a configurable interval to confirm the connection is alive. If the health check fails or does not return expected metadata, it invokes the connection lost handler to initiate reconnection. BLE connections are excluded from periodic checks, as their library provides real-time disconnection detection.
    
    This coroutine runs continuously until shutdown is requested.
    """
    global meshtastic_client, shutting_down, config, reconnecting

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot check connection.")
        return

    # Get heartbeat interval from config, default to 180 seconds
    heartbeat_interval = config["meshtastic"].get("heartbeat_interval", 180)
    connection_type = config["meshtastic"]["connection_type"]

    logger.info(
        f"Starting connection heartbeat monitor (interval: {heartbeat_interval}s)"
    )

    # Track if we've logged the BLE skip message to avoid spam
    ble_skip_logged = False

    while not shutting_down:
        if meshtastic_client and not reconnecting:
            # BLE has real-time disconnection detection in the library
            # Skip periodic health checks to avoid duplicate reconnection attempts
            if connection_type == "ble":
                if not ble_skip_logged:
                    logger.info("BLE connection uses real-time disconnection detection - health checks disabled")
                    ble_skip_logged = True
            else:
                try:
                    logger.debug(
                        f"Checking {connection_type} connection health using getMetadata()"
                    )
                    output_capture = io.StringIO()
                    with contextlib.redirect_stdout(
                        output_capture
                    ), contextlib.redirect_stderr(output_capture):
                        meshtastic_client.localNode.getMetadata()

                    console_output = output_capture.getvalue()
                    if "firmware_version" not in console_output:
                        raise Exception("No firmware_version in getMetadata output.")

                    logger.debug(f"{connection_type.capitalize()} connection healthy")

                except Exception as e:
                    # Only trigger reconnection if we're not already reconnecting
                    if not reconnecting:
                        logger.warning(
                            f"{connection_type.capitalize()} connection health check failed: {e}"
                        )
                        # Use existing handler with health check reason
                        on_lost_meshtastic_connection(detection_source=f"health check failed: {str(e)}")
                    else:
                        logger.debug("Skipping reconnection trigger - already reconnecting")
        elif reconnecting:
            logger.debug("Skipping connection check - reconnection in progress")
        elif not meshtastic_client:
            logger.debug("Skipping connection check - no client available")

        await asyncio.sleep(heartbeat_interval)


def sendTextReply(
    interface,
    text: str,
    reply_id: int,
    destinationId=meshtastic.BROADCAST_ADDR,
    wantAck: bool = False,
    channelIndex: int = 0,
):
    """
    Send a text message as a reply to a previous message.

    This function creates a proper Meshtastic reply by setting the reply_id field
    in the Data protobuf message, which the standard sendText() method doesn't support.

    Args:
        interface: The Meshtastic interface to send through
        text: The text message to send
        reply_id: The ID of the message this is replying to
        destinationId: Where to send the message (default: broadcast)
        wantAck: Whether to request acknowledgment
        channelIndex: Which channel to send on

    Returns:
        The sent packet with populated ID field
    """
    logger.debug(f"Sending text reply: '{text}' replying to message ID {reply_id}")

    # Create the Data protobuf message with reply_id set
    data_msg = mesh_pb2.Data()
    data_msg.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    data_msg.payload = text.encode("utf-8")
    data_msg.reply_id = reply_id

    # Create the MeshPacket
    mesh_packet = mesh_pb2.MeshPacket()
    mesh_packet.channel = channelIndex
    mesh_packet.decoded.CopyFrom(data_msg)
    mesh_packet.id = interface._generatePacketId()

    # Send the packet using the existing infrastructure
    return interface._sendPacket(
        mesh_packet, destinationId=destinationId, wantAck=wantAck
    )


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop  # Set the event loop for use in callbacks
    loop.create_task(check_connection())
    loop.run_forever()
