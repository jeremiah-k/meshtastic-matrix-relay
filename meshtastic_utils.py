# meshtastic_utils.py
import asyncio
import time
import threading
import os
import socket  # Import socket for TCP exceptions
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from typing import List
import sys
import contextlib

import meshtastic.tcp_interface
import meshtastic.serial_interface
import meshtastic.ble_interface
from bleak.exc import BleakDBusError, BleakError
from pubsub import pub

from config import relay_config
from db_utils import get_logger
from plugin_loader import load_plugins

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

@contextlib.contextmanager
def suppress_stdout():
    """
    A context manager that suppresses stdout temporarily.
    """
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        sys.stdout = devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

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
        logger.info("Shutdown in progress. Not attempting to connect.")
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

        while not successful and (retry_limit == 0 or attempts <= retry_limit) and not shutting_down:
            try:
                if connection_type == "serial":
                    serial_port = relay_config["meshtastic"]["serial_port"]
                    logger.info(f"Connecting to serial port {serial_port} ...")

                    # Check if serial port exists
                    if not serial_port_exists(serial_port):
                        logger.warning(f"Serial port {serial_port} does not exist. Waiting...")
                        time.sleep(5)
                        attempts += 1
                        continue

                    meshtastic_client = meshtastic.serial_interface.SerialInterface(serial_port)
                elif connection_type == "ble":
                    ble_address = relay_config["meshtastic"].get("ble_address")
                    if ble_address:
                        logger.info(f"Connecting to BLE address {ble_address} ...")
                        meshtastic_client = meshtastic.ble_interface.BLEInterface(
                            address=ble_address,
                            noProto=False,
                            debugOut=None,
                            noNodes=False
                        )
                    else:
                        logger.error("No BLE address provided.")
                        return None
                elif connection_type == "network":
                    target_host = relay_config["meshtastic"]["host"]
                    target_port = relay_config["meshtastic"].get("port", 4403)
                    logger.info(f"Connecting to host {target_host}:{target_port} ...")
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(
                        hostname=target_host,
                        portNumber=target_port
                    )
                else:
                    logger.error(f"Invalid connection_type: {connection_type}")
                    return None

                successful = True
                nodeInfo = meshtastic_client.getMyNodeInfo()
                if nodeInfo and 'user' in nodeInfo:
                    logger.info(f"Connected to {nodeInfo['user']['shortName']} / {nodeInfo['user']['hwModel']}")
                else:
                    logger.info("Connected to Meshtastic device.")

                # Get metadata and log firmware version
                metadata = meshtastic_client.localNode.getMetadata()
                logger.info(f"Firmware version: {metadata.get('firmware_version')}")
                logger.info(f"Device state version: {metadata.get('device_state_version')}")

                # Subscribe to message events
                pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                pub.subscribe(on_lost_meshtastic_connection, "meshtastic.connection.lost")

            except (serial.SerialException, serial.SerialTimeoutException) as e:
                if shutting_down:
                    logger.info("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 30)  # Cap wait time to 30 seconds
                    logger.warning(f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Could not connect after {retry_limit} attempts: {e}")
                    return None
            except (BleakError, BleakDBusError) as e:
                if shutting_down:
                    logger.info("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 30)  # Cap wait time to 30 seconds
                    logger.warning(f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Could not connect after {retry_limit} attempts: {e}")
                    return None
            except (socket.error, OSError) as e:
                if shutting_down:
                    logger.info("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 30)  # Cap wait time to 30 seconds
                    logger.warning(f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Could not connect after {retry_limit} attempts: {e}")
                    return None
            except Exception as e:
                if shutting_down:
                    logger.info("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 30)  # Cap wait time to 30 seconds
                    logger.warning(f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}")
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
            logger.info("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.info("Reconnection already in progress. Skipping additional reconnection attempt.")
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
                logger.info(f"Reconnection attempt starting in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
                if shutting_down:
                    logger.info("Shutdown in progress. Aborting reconnection attempts.")
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
    Handle incoming Meshtastic messages and relay them to Matrix.
    """
    from matrix_utils import matrix_relay
    global event_loop

    if shutting_down:
        logger.info("Shutdown in progress. Ignoring incoming messages.")
        return

    if event_loop is None:
        logger.error("Event loop is not set. Cannot process message.")
        return

    loop = event_loop

    sender = packet.get("fromId", packet.get("from"))

    decoded = packet.get("decoded", {})
    text = decoded.get("text")

    if text:
        # Determine the channel
        channel = packet.get("channel")
        if channel is None:
            if decoded.get("portnum") == "TEXT_MESSAGE_APP" or decoded.get("portnum") == 1:
                channel = 0
            elif decoded.get("portnum") == "DETECTION_SENSOR_APP":
                channel = 0
            else:
                logger.debug(f"Unknown portnum {decoded.get('portnum')}, cannot determine channel")
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
        if (decoded.get("portnum") == "DETECTION_SENSOR_APP"
            and not relay_config["meshtastic"].get("detection_sensor", False)):
            logger.debug("Detection sensor packet received, but detection sensor processing is disabled.")
            return

        logger.info(f"Processing inbound radio message from {sender} on channel {channel}")

        longname = get_logger(sender) or sender
        shortname = get_logger(sender) or sender
        meshnet_name = relay_config["meshtastic"]["meshnet_name"]

        formatted_message = f"[{longname}/{meshnet_name}]: {text}"

        # Plugin functionality
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

        if found_matching_plugin:
            return

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
                    ),
                    loop=loop,
                )
    else:
        # Handle non-text messages via plugins
        portnum = decoded.get("portnum")
        plugins = load_plugins()
        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                result = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(
                        packet, formatted_message=None, longname=None, meshnet_name=None
                    ),
                    loop=loop,
                )
                found_matching_plugin = result.result()
                if found_matching_plugin:
                    logger.debug(f"Processed {portnum} with plugin {plugin.plugin_name}")

async def check_connection():
    """
    Periodically checks the Meshtastic connection and attempts to reconnect if lost.
    """
    global meshtastic_client, shutting_down
    connection_type = relay_config["meshtastic"]["connection_type"]
    while not shutting_down:
        if meshtastic_client:
            try:
                # Suppress stdout when calling getMetadata() - it's spammy otherwise
                with suppress_stdout():
                    # Preserve these methods & comments.
                    # A Meshtastic python dev recommended we use getMetadata()
                    meshtastic_client.localNode.getMetadata()
            except Exception as e:
                logger.error(f"{connection_type.capitalize()} connection lost: {e}")
                on_lost_meshtastic_connection(meshtastic_client)
        await asyncio.sleep(5)  # Check connection every 5 seconds

# Note: Removed the __main__ block as it is not needed in this module
