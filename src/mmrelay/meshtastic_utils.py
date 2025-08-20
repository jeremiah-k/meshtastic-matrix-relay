import asyncio
import contextlib
import inspect
import io
import os
import threading
import time
from concurrent.futures import Future
from typing import List

import meshtastic.ble_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from meshtastic.protobuf import mesh_pb2, portnums_pb2
from pubsub import pub

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_MESHNET_NAME,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_DETECTION_SENSOR,
)
from mmrelay.constants.formats import (
    DETECTION_SENSOR_APP,
    EMOJI_FLAG_VALUE,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import (
    DEFAULT_CHANNEL_VALUE,
    PORTNUM_NUMERIC_VALUE,
)
from mmrelay.constants.network import (
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_SERIAL_PORT,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BACKOFF_TIME,
    DEFAULT_RETRY_ATTEMPTS,
    ERRNO_BAD_FILE_DESCRIPTOR,
    INFINITE_RETRIES,
    SYSTEMD_INIT_SYSTEM,
)

# Import BLE exceptions conditionally
try:
    from bleak.exc import BleakDBusError, BleakError
except ImportError:
    # Define dummy exception classes if bleak is not available
    class BleakDBusError(Exception):
        pass

    class BleakError(Exception):
        pass


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

# Subscription flags to prevent duplicate subscriptions
subscribed_to_messages = False
subscribed_to_connection_lost = False


def _submit_coro(coro, loop=None):
    """
    Submit an asyncio coroutine for execution on the appropriate event loop and return a Future representing its result.

    If `loop` (or the module-level `event_loop`) is an open asyncio event loop, the coroutine is scheduled thread-safely via `asyncio.run_coroutine_threadsafe`. If there is a currently running loop in the calling thread, the coroutine is scheduled with that loop's `create_task`. If no running loop exists, the coroutine is executed synchronously with `asyncio.run` and its result (or raised exception) is wrapped in a completed Future. If `coro` is not a coroutine, returns None.

    Parameters:
        coro: The coroutine object to execute.
        loop: Optional asyncio event loop to target. If omitted, the module-level `event_loop` is used.

    Returns:
        A Future-like object representing the coroutine's eventual result, or None if `coro` is not a coroutine.
    """
    if not inspect.iscoroutine(coro):
        # Defensive guard for tests that mistakenly patch async funcs to return None
        return None
    loop = loop or event_loop
    if loop and isinstance(loop, asyncio.AbstractEventLoop) and not loop.is_closed():
        return asyncio.run_coroutine_threadsafe(coro, loop)
    # Fallback: schedule on a real loop if present; tests can override this.
    try:
        running = asyncio.get_running_loop()
        return running.create_task(coro)
    except RuntimeError:
        # No running loop: run synchronously and wrap the result in a completed Future
        f = Future()
        try:
            result = asyncio.run(coro)
            f.set_result(result)
        except Exception as e:
            f.set_exception(e)
        return f


def is_running_as_service():
    """
    Determine if the application is running as a systemd service.

    Returns:
        bool: True if the process is running under systemd, either by detecting the INVOCATION_ID environment variable or by checking if the parent process is systemd; otherwise, False.
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
                        return p.read().strip() == SYSTEMD_INIT_SYSTEM
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
    Establishes a connection to a Meshtastic device using serial, BLE, or TCP, with automatic retries and event subscriptions.

    If a configuration is provided, updates the global configuration and Matrix room mappings. Prevents concurrent or duplicate connection attempts, validates required configuration fields, and supports both legacy and current connection types. Verifies serial port existence before connecting and handles connection failures with exponential backoff. Subscribes to message and connection lost events upon successful connection.

    Parameters:
        passed_config (dict, optional): Configuration dictionary for the connection.
        force_connect (bool, optional): If True, forces a new connection even if one already exists.

    Returns:
        The connected Meshtastic client instance, or None if connection fails or shutdown is in progress.
    """
    global meshtastic_client, shutting_down, reconnecting, config, matrix_rooms
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    if reconnecting and not force_connect:
        logger.debug("Reconnection already in progress. Not attempting new connection.")
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

        # Check if meshtastic config section exists
        if (
            CONFIG_SECTION_MESHTASTIC not in config
            or config[CONFIG_SECTION_MESHTASTIC] is None
        ):
            logger.error(
                "No Meshtastic configuration section found. Cannot connect to Meshtastic."
            )
            return None

        # Check if connection_type is specified
        if (
            CONFIG_KEY_CONNECTION_TYPE not in config[CONFIG_SECTION_MESHTASTIC]
            or config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE] is None
        ):
            logger.error(
                "No connection type specified in Meshtastic configuration. Cannot connect to Meshtastic."
            )
            return None

        # Determine connection type and attempt connection
        connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

        # Support legacy "network" connection type (now "tcp")
        if connection_type == CONNECTION_TYPE_NETWORK:
            connection_type = CONNECTION_TYPE_TCP
            logger.warning(
                "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
            )
        retry_limit = INFINITE_RETRIES  # 0 means infinite retries
        attempts = DEFAULT_RETRY_ATTEMPTS
        successful = False

        while (
            not successful
            and (retry_limit == 0 or attempts <= retry_limit)
            and not shutting_down
        ):
            try:
                if connection_type == CONNECTION_TYPE_SERIAL:
                    # Serial connection
                    serial_port = config["meshtastic"].get(CONFIG_KEY_SERIAL_PORT)
                    if not serial_port:
                        logger.error(
                            "No serial port specified in Meshtastic configuration."
                        )
                        return None

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

                elif connection_type == CONNECTION_TYPE_BLE:
                    # BLE connection
                    ble_address = config["meshtastic"].get(CONFIG_KEY_BLE_ADDRESS)
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

                elif connection_type == CONNECTION_TYPE_TCP:
                    # TCP connection
                    target_host = config["meshtastic"].get(CONFIG_KEY_HOST)
                    if not target_host:
                        logger.error(
                            "No host specified in Meshtastic configuration for TCP connection."
                        )
                        return None

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

                # Safely access node info fields
                user_info = nodeInfo.get("user", {}) if nodeInfo else {}
                short_name = user_info.get("shortName", "unknown")
                hw_model = user_info.get("hwModel", "unknown")
                logger.info(f"Connected to {short_name} / {hw_model}")

                # Subscribe to message and connection lost events (only once per application run)
                global subscribed_to_messages, subscribed_to_connection_lost
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

            except (TimeoutError, ConnectionRefusedError, MemoryError) as e:
                # Handle critical errors that should not be retried
                logger.error(f"Critical connection error: {e}")
                return None
            except (serial.SerialException, BleakDBusError, BleakError) as e:
                # Handle specific connection errors
                if shutting_down:
                    logger.debug("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(
                        2**attempts, 60
                    )  # Consistent exponential backoff, capped at 60s
                    logger.warning(
                        f"Connection attempt {attempts} failed: {e}. Retrying in {wait_time} seconds..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Connection failed after {attempts} attempts: {e}")
                    return None
            except Exception as e:
                if shutting_down:
                    logger.debug("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(
                        2**attempts, 60
                    )  # Consistent exponential backoff, capped at 60s
                    logger.warning(
                        f"An unexpected error occurred on attempt {attempts}: {e}. Retrying in {wait_time} seconds..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"Connection failed after {attempts} attempts: {e}")
                    return None

    return meshtastic_client


def on_lost_meshtastic_connection(interface=None, detection_source="unknown"):
    """
    Mark the Meshtastic connection as lost, close the current client, and initiate an asynchronous reconnect.

    If a shutdown is in progress or a reconnect is already underway this function returns immediately. Otherwise it:
    - sets the module-level `reconnecting` flag,
    - attempts to close and clear the module-level `meshtastic_client` (handles already-closed file descriptors),
    - schedules the reconnect() coroutine on the global event loop if that loop exists and is open.

    Parameters:
        detection_source (str): Identifier for where or how the loss was detected; used in log messages.
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
                if e.errno == ERRNO_BAD_FILE_DESCRIPTOR:
                    # Bad file descriptor, already closed
                    pass
                else:
                    logger.warning(f"Error closing Meshtastic client: {e}")
            except Exception as e:
                logger.warning(f"Error closing Meshtastic client: {e}")
        meshtastic_client = None

        if event_loop and not event_loop.is_closed():
            reconnect_task = event_loop.create_task(reconnect())


async def reconnect():
    """
    Attempt to re-establish a Meshtastic connection with exponential backoff.
    
    This coroutine repeatedly tries to reconnect by invoking connect_meshtastic(force_connect=True)
    in a thread executor until a connection is obtained, the global shutting_down flag is set,
    or the task is cancelled. It begins with DEFAULT_BACKOFF_TIME and doubles the wait after each
    failed attempt, capping the backoff at 300 seconds. The function ensures the module-level
    reconnecting flag is cleared before it returns. asyncio.CancelledError is handled (logged)
    and causes the routine to stop.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = DEFAULT_BACKOFF_TIME
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
                loop = asyncio.get_running_loop()
                # Pass force_connect=True without overwriting the global config
                meshtastic_client = await loop.run_in_executor(
                    None, connect_meshtastic, None, True
                )
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
    Handle an incoming Meshtastic packet and relay it to Matrix rooms or plugins as configured.

    This function inspects a Meshtastic `packet` (expected as a dict), applies interaction rules (reactions, replies, replies storage, detection-sensor filtering), and either:
    - relays reactions or replies as appropriate to the mapped Matrix event/room,
    - relays normal text messages to all Matrix rooms mapped to the message's Meshtastic channel (unless the message is a direct message to the relay node or a plugin handles it),
    - or dispatches non-text or unhandled packets to plugins for processing.

    Behavior notes:
    - Uses global configuration and matrix_rooms mappings; returns immediately if configuration or event loop is missing or if shutdown is in progress.
    - Resolves sender display names from a local DB or node info and persists them when found.
    - Honors interaction settings for reactions and replies, and the meshtastic `detection_sensor` configuration when handling detection sensor packets.
    - Uses _submit_coro to schedule Matrix/plugin coroutines on the configured event loop.
    - Side effects: schedules Matrix relays, may call plugin handlers, and may store sender metadata and message->Matrix mappings via other utilities.

    No return value.
    """
    global config, matrix_rooms

    # Validate packet structure
    if not packet or not isinstance(packet, dict):
        logger.error("Received malformed packet: packet is None or not a dict")
        return

    # Log that we received a message (without the full packet details)
    decoded = packet.get("decoded")
    if decoded and isinstance(decoded, dict) and decoded.get("text"):
        logger.info(f"Received Meshtastic message: {decoded.get('text')}")
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
    if packet.get("decoded", {}).get("portnum") == TEXT_MESSAGE_APP:
        decoded = packet.get("decoded", {})
        # Filter out reactions if reactions are disabled
        if (
            not interactions["reactions"]
            and "emoji" in decoded
            and decoded.get("emoji") == EMOJI_FLAG_VALUE
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
    emoji_flag = "emoji" in decoded and decoded["emoji"] == EMOJI_FLAG_VALUE

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

    meshnet_name = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_MESHNET_NAME]

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

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reaction
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)

            reaction_symbol = text.strip() if (text and text.strip()) else "⚠️"
            reaction_message = (
                f'\n {prefix}reacted {reaction_symbol} to "{abbreviated_text}"'
            )

            # Relay the reaction as emote to Matrix, preserving the original meshnet name
            _submit_coro(
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

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reply
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
            formatted_message = f"{prefix}{text}"

            logger.info(f"Relaying Meshtastic reply from {longname} to Matrix")

            # Relay the reply to Matrix with proper reply formatting
            _submit_coro(
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
                decoded.get("portnum") == TEXT_MESSAGE_APP
                or decoded.get("portnum") == PORTNUM_NUMERIC_VALUE
                or decoded.get("portnum") == DETECTION_SENSOR_APP
            ):
                channel = DEFAULT_CHANNEL_VALUE
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
        if decoded.get(
            "portnum"
        ) == DETECTION_SENSOR_APP and not get_meshtastic_config_value(
            config, "detection_sensor", DEFAULT_DETECTION_SENSOR
        ):
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

        # Import the matrix prefix function
        from mmrelay.matrix_utils import get_matrix_prefix

        # Get the formatted prefix
        prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        from mmrelay.plugin_loader import load_plugins

        plugins = load_plugins()

        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                try:
                    result = _submit_coro(
                        plugin.handle_meshtastic_message(
                            packet, formatted_message, longname, meshnet_name
                        ),
                        loop=loop,
                    )
                    found_matching_plugin = result.result()
                    if found_matching_plugin:
                        logger.debug(f"Processed by plugin {plugin.plugin_name}")
                except Exception as e:
                    logger.error(f"Plugin {plugin.plugin_name} failed: {e}")
                    # Continue processing other plugins

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
                    _submit_coro(
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
                try:
                    result = _submit_coro(
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
                except Exception as e:
                    logger.error(f"Plugin {plugin.plugin_name} failed: {e}")
                    # Continue processing other plugins


async def check_connection():
    """
    Periodically checks the health of the Meshtastic connection and triggers reconnection if the device becomes unresponsive.

    For non-BLE connections, performs a metadata check at configurable intervals to verify device responsiveness. If the check fails or the firmware version is missing, initiates reconnection unless already in progress. BLE connections are excluded from periodic checks due to real-time disconnection detection. The function runs continuously until shutdown is requested, with health check behavior controlled by configuration.
    """
    global meshtastic_client, shutting_down, config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot check connection.")
        return

    connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

    # Get health check configuration
    health_config = config["meshtastic"].get("health_check", {})
    health_check_enabled = health_config.get("enabled", True)
    heartbeat_interval = health_config.get("heartbeat_interval", 60)

    # Support legacy heartbeat_interval configuration for backward compatibility
    if "heartbeat_interval" in config["meshtastic"]:
        heartbeat_interval = config["meshtastic"]["heartbeat_interval"]

    # Exit early if health checks are disabled
    if not health_check_enabled:
        logger.info("Connection health checks are disabled in configuration")
        return

    ble_skip_logged = False

    while not shutting_down:
        if meshtastic_client and not reconnecting:
            # BLE has real-time disconnection detection in the library
            # Skip periodic health checks to avoid duplicate reconnection attempts
            if connection_type == CONNECTION_TYPE_BLE:
                if not ble_skip_logged:
                    logger.info(
                        "BLE connection uses real-time disconnection detection - health checks disabled"
                    )
                    ble_skip_logged = True
            else:
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
                    # Only trigger reconnection if we're not already reconnecting
                    if not reconnecting:
                        logger.error(
                            f"{connection_type.capitalize()} connection health check failed: {e}"
                        )
                        on_lost_meshtastic_connection(
                            interface=meshtastic_client,
                            detection_source=f"health check failed: {str(e)}",
                        )
                    else:
                        logger.debug(
                            "Skipping reconnection trigger - already reconnecting"
                        )
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
    Sends a text message as a reply to a specific previous message via the Meshtastic interface.

    Parameters:
        interface: The Meshtastic interface to send through.
        text (str): The message content to send.
        reply_id (int): The ID of the message being replied to.
        destinationId: The recipient address (defaults to broadcast).
        wantAck (bool): Whether to request acknowledgment for the message.
        channelIndex (int): The channel index to send the message on.

    Returns:
        The sent MeshPacket with its ID field populated, or None if sending fails or the interface is unavailable.
    """
    logger.debug(f"Sending text reply: '{text}' replying to message ID {reply_id}")

    # Check if interface is available
    if interface is None:
        logger.error("No Meshtastic interface available for sending reply")
        return None

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
    try:
        return interface._sendPacket(
            mesh_packet, destinationId=destinationId, wantAck=wantAck
        )
    except Exception as e:
        logger.error(f"Failed to send text reply: {e}")
        return None


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop  # Set the event loop for use in callbacks
    loop.create_task(check_connection())
    loop.run_forever()
