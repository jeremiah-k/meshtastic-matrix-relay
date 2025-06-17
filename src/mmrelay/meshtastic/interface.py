import asyncio
import contextlib
import io
import os # Not strictly used here but often useful with file paths or env vars
import threading
import time

import meshtastic.ble_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial  # For serial port exceptions
import serial.tools.list_ports
from bleak.exc import BleakDBusError, BleakError # For BLE connection errors
from pubsub import pub

from mmrelay.log_utils import get_logger
from mmrelay.common.sys_utils import is_running_as_service

# Global config variable, will be set by connect_meshtastic or main application flow
config = None
# matrix_rooms configuration, also typically passed via main config
matrix_rooms = []

logger = get_logger(name="MeshtasticInterface")

meshtastic_client = None # Holds the active Meshtastic interface object
event_loop = None  # Stores the asyncio event loop used by the application
meshtastic_lock = threading.Lock() # Protects access to shared Meshtastic resources
reconnecting = False # Flag to indicate if a reconnection attempt is in progress
shutting_down = False # Flag to signal application shutdown
reconnect_task = None # Holds the asyncio task for reconnection

def get_meshtastic_interface():
    """Returns the current Meshtastic client interface."""
    return meshtastic_client

def set_event_loop(loop: asyncio.AbstractEventLoop):
    """Sets the global event loop for async operations."""
    global event_loop
    event_loop = loop

def get_event_loop() -> asyncio.AbstractEventLoop | None:
    """Gets the global event loop."""
    return event_loop

def get_shutting_down_flag() -> bool:
    """Checks if the application is shutting down."""
    return shutting_down

def set_shutting_down_flag(value: bool):
    """Sets the shutting down flag and cancels reconnect task if shutting down."""
    global shutting_down, reconnect_task
    shutting_down = value
    if value and reconnect_task and not reconnect_task.done():
        reconnect_task.cancel()
        logger.info("Meshtastic reconnect task cancelled due to shutdown.")


def serial_port_exists(port_name: str) -> bool:
    """Checks if a given serial port name exists in the system."""
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports

def connect_meshtastic(passed_config=None, force_connect: bool = False):
    """
    Establishes and returns a connection to the Meshtastic device.
    Manages global config and client state.
    """
    global meshtastic_client, config, matrix_rooms # Allow modification of these globals

    if get_shutting_down_flag():
        logger.debug("Shutdown in progress. Not attempting Meshtastic connection.")
        return None

    if passed_config is not None:
        config = passed_config # Update global config if new one is passed
        if config and "matrix_rooms" in config:
            matrix_rooms = config["matrix_rooms"] # Update matrix_rooms from new config

    with meshtastic_lock: # Ensure thread-safe access to client creation
        if meshtastic_client and not force_connect:
            logger.debug("Returning existing Meshtastic client.")
            return meshtastic_client

        if meshtastic_client: # If forcing connect, close existing client first
            try:
                meshtastic_client.close()
                logger.info("Closed existing Meshtastic client for forced reconnection.")
            except Exception as e:
                logger.warning(f"Error closing previous Meshtastic connection: {e}")
            meshtastic_client = None # Clear client before creating a new one

        if config is None: # Config is essential for connection params
            logger.error("No configuration available. Cannot connect to Meshtastic.")
            return None

        connection_type = config.get("meshtastic", {}).get("connection_type", "serial") # Default to serial
        if connection_type == "network": connection_type = "tcp" # Handle legacy "network" type

        retry_limit = config.get("meshtastic", {}).get("retry_limit", 0) # 0 for infinite
        attempts = 0 # Start attempts from 0 for easier comparison with retry_limit

        while not get_shutting_down_flag(): # Loop until connected or shutdown
            attempts += 1
            try:
                logger.info(f"Attempting Meshtastic connection ({connection_type}), attempt #{attempts}...")
                if connection_type == "serial":
                    serial_port = config["meshtastic"]["serial_port"]
                    if not serial_port_exists(serial_port):
                        logger.warning(f"Serial port {serial_port} not found. Waiting...")
                        time.sleep(5)
                        if retry_limit != 0 and attempts >= retry_limit: break
                        continue
                    meshtastic_client = meshtastic.serial_interface.SerialInterface(devPath=serial_port) # Use devPath
                elif connection_type == "ble":
                    ble_address = config["meshtastic"].get("ble_address")
                    if not ble_address: logger.error("No BLE address provided."); return None
                    meshtastic_client = meshtastic.ble_interface.BLEInterface(address=ble_address, noProto=False)
                elif connection_type == "tcp":
                    target_host = config["meshtastic"]["host"]
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(hostname=target_host)
                else:
                    logger.error(f"Unknown Meshtastic connection type: {connection_type}"); return None

                # Ensure getMyNodeInfo is safe to call if client is None (though logic prevents it)
                nodeInfo = meshtastic_client.getMyNodeInfo() if meshtastic_client else {}
                logger.info(f"Connected to Meshtastic: {nodeInfo.get('user',{}).get('shortName','N/A')} / {nodeInfo.get('user',{}).get('hwModel','N/A')}")

                # Connection successful, break from retry loop
                break
            except (serial.SerialException, BleakDBusError, BleakError, Exception) as e:
                logger.warning(f"Meshtastic connection attempt #{attempts} failed: {e}")
                if get_shutting_down_flag(): break # Exit if shutting down
                if retry_limit != 0 and attempts >= retry_limit:
                    logger.error(f"Could not connect to Meshtastic after {retry_limit} attempts.")
                    return None # Failed to connect after all retries
                wait_time = min(attempts * 2, 30) # Exponential backoff
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

        if get_shutting_down_flag() and not meshtastic_client: # If shutdown during attempts and no success
             logger.info("Meshtastic connection aborted due to shutdown signal.")
             return None

        return meshtastic_client # Return the client (or None if failed after retries)


def on_lost_meshtastic_connection(interface=None):
    """Callback for when the Meshtastic connection is lost."""
    global meshtastic_client, reconnecting, reconnect_task, event_loop
    with meshtastic_lock:
        if get_shutting_down_flag() or reconnecting: # Avoid multiple reconnect tasks or if shutting down
            logger.debug(f"Shutdown: {get_shutting_down_flag()}, Reconnecting: {reconnecting}. Skipping new reconnect.")
            return
        reconnecting = True # Set flag to prevent multiple entries
        logger.error("Lost Meshtastic connection. Attempting to reconnect...")

        if meshtastic_client: # Try to close the faulty client
            try:
                meshtastic_client.close()
            except OSError as e: # Catch specific OS errors like bad file descriptor
                if e.errno != 9: logger.warning(f"Error closing Meshtastic client (OS): {e}")
            except Exception as e:
                logger.warning(f"Generic error closing Meshtastic client: {e}")
        meshtastic_client = None # Clear the client global

        current_event_loop = get_event_loop()
        if current_event_loop and not current_event_loop.is_closed():
            logger.info("Scheduling Meshtastic reconnection task.")
            reconnect_task = asyncio.run_coroutine_threadsafe(reconnect_async(), current_event_loop)
        else:
            logger.error("Event loop not available or closed. Cannot schedule Meshtastic reconnect task.")
            reconnecting = False # Reset flag as no task is scheduled


async def reconnect_async():
    """Asynchronously handles Meshtastic reconnection with backoff."""
    global meshtastic_client, reconnecting # Allow modification of these globals
    backoff_time = 10 # Initial backoff time
    try:
        while not get_shutting_down_flag(): # Loop until connected or shutdown
            logger.info(f"Meshtastic reconnection attempt will start in {backoff_time} seconds...")

            # Use asyncio.sleep for non-blocking wait
            try:
                await asyncio.sleep(backoff_time)
            except asyncio.CancelledError: # Handle if sleep itself is cancelled
                logger.info("Reconnect sleep cancelled. Aborting reconnect.")
                break

            if get_shutting_down_flag():
                logger.info("Shutdown signalled during reconnect wait. Aborting.")
                break

            logger.info("Attempting to reconnect to Meshtastic...")
            # Pass current global config (from this module) to connect_meshtastic
            # force_connect=True ensures it tries to establish a new connection
            temp_client = connect_meshtastic(passed_config=config, force_connect=True)
            if temp_client:
                meshtastic_client = temp_client # Assign to global on success
                logger.info("Reconnected to Meshtastic successfully.")
                # Re-subscribe to connection lost as new client object is created
                # pub.subscribe(on_lost_meshtastic_connection, "meshtastic.connection.lost")
                # This is already done at module level, and pubsub handles single subscription.
                break # Exit reconnect loop on success

            backoff_time = min(backoff_time * 2, 300) # Exponential backoff, max 5 mins
            logger.warning(f"Meshtastic reconnection failed. Retrying in {backoff_time} seconds.")

    except asyncio.CancelledError: # Catch cancellation of the reconnect_async task itself
        logger.info("Meshtastic reconnection task was cancelled.")
    finally:
        reconnecting = False # Reset flag when task ends (success, failure, or cancellation)


async def check_connection():
    """Periodically checks the Meshtastic connection integrity."""
    global meshtastic_client # Needs access to the client

    if not config: # Config needed for connection_type (cosmetic, but good practice)
        logger.error("No configuration for Meshtastic connection check. Task will not run.")
        return

    connection_type = config.get("meshtastic",{}).get("connection_type", "N/A")
    while not get_shutting_down_flag():
        await asyncio.sleep(30) # Check every 30 seconds
        if get_shutting_down_flag(): break # Check flag again after sleep

        if meshtastic_client:
            try:
                # Redirect stdout/stderr to avoid Meshtastic library's direct prints
                output_capture = io.StringIO()
                with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(output_capture):
                    # getMetadata can be problematic if node is sleeping.
                    # A more robust check might be needed if this causes false positives.
                    if hasattr(meshtastic_client, 'nodes') and meshtastic_client.nodes: # Check if nodes exist
                         # Accessing localNode might be problematic if no nodes are available
                         if meshtastic_client.localNode and hasattr(meshtastic_client.localNode, 'getMetadata'):
                            meshtastic_client.localNode.getMetadata()
                         else: # Fallback or alternative check if localNode is not reliable
                            meshtastic_client.getMyNodeInfo() # Simpler check perhaps
                    else: # If no nodes, perhaps a simple getMyNodeInfo is better
                        meshtastic_client.getMyNodeInfo()

                console_output = output_capture.getvalue()
                # This check is very basic; may need refinement based on actual output
                if "firmware_version" not in console_output and "my_node_num" not in console_output:
                    raise Exception("Key info not in getMetadata/getMyNodeInfo output.")
                logger.debug("Meshtastic connection check: OK")
            except Exception as e:
                logger.error(f"Meshtastic {connection_type.capitalize()} connection check failed or lost: {e}")
                on_lost_meshtastic_connection() # Trigger reconnect logic
        else:
            logger.debug("Meshtastic client not available for connection check. Attempting reconnect.")
            on_lost_meshtastic_connection() # Try to reconnect if client is None


# Subscribe to connection lost events. This ensures that if other parts of the system
# publish this event, our on_lost_meshtastic_connection handler is called.
pub.subscribe(on_lost_meshtastic_connection, "meshtastic.connection.lost")
logger.info("Subscribed on_lost_meshtastic_connection to 'meshtastic.connection.lost'")
