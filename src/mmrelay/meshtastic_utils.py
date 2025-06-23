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


def is_running_as_service():
    """
    Check if the application is running as a systemd service.
    This is used to determine whether to show Rich progress indicators.

    Returns:
        bool: True if running as a service, False otherwise
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
    Establish a connection to the Meshtastic device.
    Attempts a connection based on connection_type (serial/ble/network).
    Retries until successful or shutting_down is set.
    If already connected and not force_connect, returns the existing client.

    Args:
        passed_config: The configuration dictionary to use (will update global config)
        force_connect: Whether to force a new connection even if one exists
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

                # Subscribe to message and connection lost events
                pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                pub.subscribe(
                    on_lost_meshtastic_connection, "meshtastic.connection.lost"
                )

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


def on_lost_meshtastic_connection(interface=None):
    """
    Callback invoked when the Meshtastic connection is lost.
    Initiates a reconnect sequence unless shutting_down is True.
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
        logger.error("Lost connection. Reconnecting...")

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
    Asynchronously attempts to reconnect with exponential backoff.
    Stops if shutting_down is set.
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


def on_meshtastic_message(packet, interface):
    """
    Handle incoming Meshtastic messages. For reaction messages, if relay_reactions is False,
    we do not store message maps and thus won't be able to relay reactions back to Matrix.
    If relay_reactions is True, message maps are stored inside matrix_relay().
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

    # Apply reaction filtering based on config
    relay_reactions = config["meshtastic"].get("relay_reactions", False)

    # If relay_reactions is False, filter out reaction/tapback packets to avoid complexity
    if packet.get("decoded", {}).get("portnum") == "TEXT_MESSAGE_APP":
        decoded = packet.get("decoded", {})
        if not relay_reactions and ("emoji" in decoded or "replyId" in decoded):
            logger.debug(
                "Filtered out reaction/tapback packet due to relay_reactions=false."
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
    # If replyId and emoji_flag are present and relay_reactions is True, we relay as text reactions in Matrix
    if replyId and emoji_flag and relay_reactions:
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
    Periodically checks the Meshtastic connection by calling localNode.getMetadata().
    If it fails or doesn't return the firmware version, we assume the connection is lost
    and attempt to reconnect.
    """
    global meshtastic_client, shutting_down, config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot check connection.")
        return

    connection_type = config["meshtastic"]["connection_type"]
    while not shutting_down:
        if meshtastic_client:
            try:
                output_capture = io.StringIO()
                with contextlib.redirect_stdout(
                    output_capture
                ), contextlib.redirect_stderr(output_capture):
                    meshtastic_client.localNode.getMetadata()

                console_output = output_capture.getvalue()
                if "firmware_version" not in console_output:
                    raise Exception("No firmware_version in getMetadata output.")

            except Exception as e:
                logger.error(f"{connection_type.capitalize()} connection lost: {e}")
                on_lost_meshtastic_connection(meshtastic_client)
        await asyncio.sleep(30)  # Check connection every 30 seconds


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop  # Set the event loop for use in callbacks
    loop.create_task(check_connection())
    loop.run_forever()
