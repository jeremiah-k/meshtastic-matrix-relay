import asyncio
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

from config import relay_config
from db_utils import get_longname, get_shortname, save_longname, save_shortname, get_message_map_by_meshtastic_id
from log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports

# Extract matrix rooms configuration
matrix_rooms: List[dict] = relay_config["matrix_rooms"]

# Initialize logger
logger = get_logger(name="Meshtastic")

# Global variables
meshtastic_client = None
event_loop = None  # Will be set from main.py

meshtastic_lock = threading.Lock()  # To prevent race conditions

reconnecting = False
shutting_down = False
reconnect_task = None  # To keep track of the reconnect task


def serial_port_exists(port_name):
    """
    Check if the specified serial port exists.

    Args:
        port_name (str): The name of the serial port (e.g., 'COM15' or '/dev/ttyACM0').

    Returns:
        bool: True if the port exists, False otherwise.
    """
    ports = [port.device for port in serial.tools.list_ports.comports()]
    return port_name in ports


def connect_meshtastic(force_connect=False):
    """
    Establish a connection to the Meshtastic device.

    Args:
        force_connect (bool): If True, forces a new connection even if one exists.

    Returns:
        The Meshtastic client interface or None if connection fails.
    """
    global meshtastic_client, shutting_down
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    with meshtastic_lock:
        if meshtastic_client and not force_connect:
            return meshtastic_client

        # Ensure previous connection is closed
        if meshtastic_client:
            try:
                meshtastic_client.close()
            except Exception as e:
                logger.warning(f"Error closing previous connection: {e}")
            meshtastic_client = None

        # Initialize Meshtastic interface based on connection type
        connection_type = relay_config["meshtastic"]["connection_type"]
        retry_limit = 0  # 0 for infinite retries
        attempts = 1
        successful = False

        while (
            not successful
            and (retry_limit == 0 or attempts <= retry_limit)
            and not shutting_down
        ):
            try:
                if connection_type == "serial":
                    serial_port = relay_config["meshtastic"]["serial_port"]
                    logger.info(f"Connecting to serial port {serial_port} ...")

                    # Check if serial port exists
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
                    ble_address = relay_config["meshtastic"].get("ble_address")
                    if ble_address:
                        logger.info(f"Connecting to BLE address {ble_address} ...")
                        meshtastic_client = meshtastic.ble_interface.BLEInterface(
                            address=ble_address,
                            noProto=False,
                            debugOut=None,
                            noNodes=False,
                        )
                    else:
                        logger.error("No BLE address provided.")
                        return None
                else:
                    target_host = relay_config["meshtastic"]["host"]
                    logger.info(f"Connecting to host {target_host} ...")
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(
                        hostname=target_host
                    )

                successful = True
                nodeInfo = meshtastic_client.getMyNodeInfo()
                logger.info(
                    f"Connected to {nodeInfo['user']['shortName']} / {nodeInfo['user']['hwModel']}"
                )

                # Subscribe to message events
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
                    wait_time = min(attempts * 2, 30)  # Cap wait time to 30 seconds
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
    Callback function invoked when the Meshtastic connection is lost.
    """
    global meshtastic_client, reconnecting, shutting_down, event_loop, reconnect_task
    with meshtastic_lock:
        if shutting_down:
            logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.info(
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
    Asynchronously attempts to reconnect to the Meshtastic device with exponential backoff.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = 10
    try:
        while not shutting_down:
            try:
                logger.info(
                    f"Reconnection attempt starting in {backoff_time} seconds..."
                )
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
    # Apply reaction filtering based on config
    relay_reactions = relay_config["meshtastic"].get("relay_reactions", True)

    # Filter out TEXT_MESSAGE_APP packets with emoji or replyId if relay_reactions is False
    if packet.get('decoded', {}).get('portnum') == 'TEXT_MESSAGE_APP':
        decoded = packet.get('decoded', {})
        if not relay_reactions and ('emoji' in decoded or 'replyId' in decoded):
            logger.debug('Filtered out reaction/tapback packet due to relay_reactions=false.')
            return

    from matrix_utils import matrix_relay

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
    emoji_flag = 'emoji' in decoded and decoded['emoji'] == 1

    # Determine if the message is a direct message
    myId = interface.myInfo.my_node_num  # Get relay's own node number
    from meshtastic.mesh_interface import BROADCAST_NUM

    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM:
        is_direct_message = False
    else:
        # Message to someone else; we may ignore it
        is_direct_message = False

    meshnet_name = relay_config["meshtastic"]["meshnet_name"]

    # If this is a reaction packet (has replyId and emoji) and relay_reactions is True
    # we will convert it into an emote in Matrix.
    # We'll need to find the original message from the DB using replyId.
    # replyId corresponds to meshtastic_id in DB.
    if replyId and emoji_flag and relay_reactions:
        # This is a reaction message
        # Get user names
        longname = get_longname(sender) or str(sender)
        shortname = get_shortname(sender) or str(sender)
        # Retrieve original message
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            matrix_event_id, matrix_room_id, meshtastic_text = orig
            abbreviated_text = meshtastic_text[:40] + "..." if len(meshtastic_text) > 40 else meshtastic_text
            # Construct emote message
            # Add a newline and a bullet to create a nice list-style formatting in m.emote
            full_display_name = f"{longname}/{meshnet_name}"
            # Use the actual text as reaction, or if no text is given, just use the emoji we know was set
            reaction_symbol = text if (text and text.strip()) else '👍'
            # Construct emote message (with a newline and bullet)
            reaction_message = f"\n [{full_display_name}] reacted {reaction_symbol} to \"{abbreviated_text}\""
            # Send as m.emote
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
                    emoji=True
                ),
                loop=loop,
            )
        else:
            logger.debug("Original message for reaction not found in DB.")
        return

    if text:
        # Determine the channel
        channel = packet.get("channel")
        if channel is None:
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

        # Check if the channel is mapped to a Matrix room
        channel_mapped = False
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                channel_mapped = True
                break

        if not channel_mapped:
            logger.debug(f"Skipping message from unmapped channel {channel}")
            return
        if decoded.get("portnum") == "DETECTION_SENSOR_APP" and not relay_config[
            "meshtastic"
        ].get("detection_sensor", False):
            logger.debug(
                "Detection sensor packet received, but detection sensor processing is disabled."
            )
            return

        # Attempt to get longname from database
        longname = get_longname(sender)
        shortname = get_shortname(sender)

        if not longname or not shortname:
            # Try to get node info from interface.nodes
            node = interface.nodes.get(sender)
            if node:
                user = node.get("user")
                if user:
                    if not longname:
                        longname = user.get("longName")
                        if longname:
                            save_longname(sender, longname)
                    if not shortname:
                        shortname = user.get("shortName")
                        if shortname:
                            save_shortname(sender, shortname)
            else:
                # Node info not available yet
                logger.debug(f"Node info for sender {sender} not available yet.")

        # If still not available, use sender ID as longname and shortname
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        formatted_message = f"[{longname}/{meshnet_name}]: {text}"

        # Plugin functionality
        from plugin_loader import load_plugins  # Import here to avoid circular imports

        plugins = load_plugins()  # Load plugins within the function

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

        # **Added DM Check Here**
        # If the message is a DM or handled by a plugin, do not relay it to Matrix
        if is_direct_message:
            logger.debug(
                f"Received a direct message from {longname}. Not relaying to Matrix."
            )
            return
        if found_matching_plugin:
            logger.debug("Message was handled by a plugin. Not relaying to Matrix.")
            return

        logger.info(
            f"Processing inbound radio message from {sender} on channel {channel}"
        )
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")

        # Relay message to Matrix rooms
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                asyncio.run_coroutine_threadsafe(
                    matrix_relay(
                        room["id"],
                        formatted_message,
                        longname,
                        shortname,
                        meshnet_name,
                        decoded.get("portnum"),
                        meshtastic_id=packet.get("id"),
                        meshtastic_text=text
                    ),
                    loop=loop,
                )
    else:
        # Handle non-text messages via plugins
        portnum = decoded.get("portnum")
        from plugin_loader import load_plugins  # Import here to avoid circular imports

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
    Periodically checks the Meshtastic connection and attempts to reconnect if lost.
    """
    global meshtastic_client, shutting_down
    connection_type = relay_config["meshtastic"]["connection_type"]
    while not shutting_down:
        if meshtastic_client:
            try:
                # Send a ping to check the connection
                meshtastic_client.sendPing()
            except Exception as e:
                logger.error(f"{connection_type.capitalize()} connection lost: {e}")
                on_lost_meshtastic_connection(meshtastic_client)
        await asyncio.sleep(5)  # Check connection every 5 seconds


if __name__ == "__main__":
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop  # Set the event loop
    loop.create_task(check_connection())
    loop.run_forever()
