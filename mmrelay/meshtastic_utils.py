# ./mmrelay/meshtastic_utils.py:
import asyncio
import contextlib # Keep for redirect_stdout/stderr
import io # Keep for StringIO
import logging # Keep for logger check
import sys # Keep for debugOut target
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

# Import the global config dict - DO NOT access specific keys here
from mmrelay.config import relay_config
from mmrelay.db_utils import get_longname, get_message_map_by_meshtastic_id, get_shortname, save_longname, save_shortname
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports


# --- REMOVED TOP-LEVEL CONFIG ACCESS ---
# matrix_rooms: List[dict] = relay_config["matrix_rooms"]
# --- Config will be accessed inside functions ---


# Initialize logger for Meshtastic
logger = get_logger(name="Meshtastic")

# Global variables for the Meshtastic connection and event loop management
meshtastic_client = None
event_loop = None  # Will be set later

meshtastic_lock = (
    threading.Lock()
)  # To prevent race conditions on meshtastic_client access

reconnecting = False
shutting_down = False
reconnect_task = None  # To keep track of the reconnect task


def serial_port_exists(port_name):
    """
    Check if the specified serial port exists.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports


def connect_meshtastic(force_connect=False):
    """
    Establish a connection to the Meshtastic device using config.
    """
    global meshtastic_client, shutting_down
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    with meshtastic_lock:
        if meshtastic_client and not force_connect:
            return meshtastic_client

        if meshtastic_client:
            try: meshtastic_client.close()
            except Exception as e: logger.warning(f"Error closing previous connection: {e}", exc_info=True)
            meshtastic_client = None

        # Access config here
        meshtastic_config = relay_config.get("meshtastic", {})
        connection_type = meshtastic_config.get("connection_type", "serial")
        retry_limit = 0
        attempts = 1
        successful = False

        while not successful and (retry_limit == 0 or attempts <= retry_limit) and not shutting_down:
            try:
                debug_output = sys.stderr if logger.isEnabledFor(logging.DEBUG) else None

                if connection_type == "serial":
                    serial_port = meshtastic_config.get("serial_port")
                    if not serial_port: raise ValueError("'serial_port' not defined in config for serial connection")
                    logger.info(f"Attempting serial connection to {serial_port} ...")
                    if not serial_port_exists(serial_port):
                        logger.warning(f"Serial port {serial_port} does not exist. Waiting...")
                        time.sleep(5); attempts += 1; continue
                    meshtastic_client = meshtastic.serial_interface.SerialInterface(serial_port, debugOut=debug_output)

                elif connection_type == "ble":
                    ble_address = meshtastic_config.get("ble_address")
                    if not ble_address: raise ValueError("'ble_address' not defined in config for ble connection")
                    logger.info(f"Attempting BLE connection to {ble_address} ...")
                    meshtastic_client = meshtastic.ble_interface.BLEInterface(address=ble_address, noProto=False)

                elif connection_type == "network":
                    target_host = meshtastic_config.get("host")
                    if not target_host: raise ValueError("'host' not defined in config for network connection")
                    logger.info(f"Attempting network connection to host {target_host} ...")
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(hostname=target_host, debugOut=debug_output)

                else: raise ValueError(f"Unsupported connection_type: '{connection_type}'")

                # Verify connection with timeout
                nodeInfo = meshtastic_client.getMyNodeInfo(timeout=20.0)
                if not nodeInfo or not nodeInfo.get('myNodeNum'):
                    raise meshtastic.mesh_interface.MeshInterface.NoResponse("Failed to get node info after connection")

                logger.info(f"Connected via {connection_type} to {nodeInfo.get('user',{}).get('shortName','N/A')} / {nodeInfo.get('user',{}).get('hwModel','N/A')}")
                successful = True

                # Subscribe after success
                pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                pub.subscribe(on_lost_meshtastic_connection, "meshtastic.connection.lost")
                logger.debug("Subscribed to meshtastic receive/lost events.")

            except (ValueError, serial.SerialException, BleakDBusError, BleakError, ConnectionRefusedError, TimeoutError, meshtastic.mesh_interface.MeshInterface.NoResponse, Exception) as e:
                logger.warning(f"Meshtastic connection attempt #{attempts} failed: {type(e).__name__}: {e}")
                if meshtastic_client: # Close client if partially created before error
                    try: meshtastic_client.close()
                    except: pass
                    meshtastic_client = None
                if shutting_down: logger.info("Shutdown initiated, aborting."); break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 60); logger.info(f"Retrying in {wait_time}s..."); time.sleep(wait_time)
                else: logger.error(f"Could not connect via {connection_type} after {retry_limit} attempts."); return None
            except KeyboardInterrupt: logger.info("Connection attempt interrupted."); shutting_down = True; return None
    return meshtastic_client


def on_lost_meshtastic_connection(interface=None):
    """Callback for lost connection. Schedules reconnect."""
    global meshtastic_client, reconnecting, shutting_down, event_loop, reconnect_task
    with meshtastic_lock:
        if shutting_down or reconnecting: return
        if meshtastic_client is not None: # Only act if we thought we were connected
            reconnecting = True
            logger.error("Lost connection to Meshtastic device. Scheduling reconnection...")
            try: meshtastic_client.close()
            except Exception as e: logger.warning(f"Error closing old Meshtastic client: {e}", exc_info=True)
            finally: meshtastic_client = None
            if event_loop and event_loop.is_running() and not (reconnect_task and not reconnect_task.done()):
                logger.info("Submitting reconnect task to event loop.")
                reconnect_task = asyncio.run_coroutine_threadsafe(reconnect(), event_loop)
                try: reconnect_task.set_name("MeshtasticReconnect") # Optional name
                except AttributeError: pass
            else: logger.error("Cannot schedule reconnect: Event loop not ready or task pending."); reconnecting = False


async def reconnect():
    """Async task to attempt reconnection with backoff."""
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = 15; attempt = 1
    try:
        while not shutting_down:
            logger.info(f"Reconnection attempt #{attempt} in {backoff_time}s...")
            await asyncio.sleep(backoff_time)
            if shutting_down: logger.info("Shutdown during reconnect wait."); break
            logger.info(f"Attempting reconnect #{attempt}...")
            temp_client = connect_meshtastic(force_connect=True)
            if temp_client:
                logger.info("Reconnected successfully."); meshtastic_client = temp_client; break
            else: logger.warning(f"Reconnect attempt #{attempt} failed."); attempt += 1; backoff_time = min(backoff_time * 1.5, 600)
    except asyncio.CancelledError: logger.info("Reconnection task cancelled.")
    except Exception as e: logger.error(f"Error in reconnect task: {e}", exc_info=True)
    finally: logger.debug("Exiting reconnect task."); reconnecting = False


def on_meshtastic_message(packet, interface):
    """Handle incoming Meshtastic messages. Preserves original reaction logic."""
    global event_loop

    # --- Access config ---
    meshtastic_config = relay_config.get("meshtastic", {})
    relay_reactions = meshtastic_config.get("relay_reactions", False)
    matrix_rooms = relay_config.get("matrix_rooms", []) # Get room list here
    meshnet_name = meshtastic_config.get("meshnet_name", "Mesh")
    detection_sensor_enabled = meshtastic_config.get("detection_sensor", False)
    # ---------------------

    try: from mmrelay.matrix_utils import matrix_relay
    except ImportError: logger.error("Cannot import matrix_relay"); return

    if shutting_down or not event_loop or not event_loop.is_running():
        logger.debug("Ignoring message (shutdown or no loop)."); return

    logger.debug(f"Received Meshtastic packet: {packet}")
    decoded = packet.get("decoded", {}); portnum = decoded.get("portnum", "UNKNOWN")

    # Filter reactions if disabled
    if portnum == "TEXT_MESSAGE_APP" or portnum == 1:
        if not relay_reactions and ("emoji" in decoded or "replyId" in decoded):
            logger.debug("Filtered reaction packet (relay_reactions=false)."); return

    sender = packet.get("fromId") or packet.get("from")
    if not sender: logger.warning("Packet missing sender ID."); return

    toId = packet.get("to"); text = decoded.get("text"); replyId = decoded.get("replyId"); emoji_flag = decoded.get("emoji") == 1

    from meshtastic.mesh_interface import BROADCAST_NUM
    myId = interface.myInfo.my_node_num if interface and interface.myInfo else None
    if not myId: logger.warning("Cannot determine own node ID."); is_direct_message = False
    elif toId == myId: is_direct_message = True
    elif toId == BROADCAST_NUM: is_direct_message = False
    else: logger.debug(f"Ignoring msg from {sender} to other node {toId}."); return

    # --- Reaction handling (Meshtastic -> Matrix) - Original Logic ---
    if replyId and emoji_flag and relay_reactions:
        logger.debug(f"Processing Meshtastic reaction from {sender} reply to {replyId}")
        longname = get_longname(sender) or sender; shortname = get_shortname(sender) or sender[:4]
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            _m_eid, matrix_room_id, orig_m_text, orig_m_net = orig
            abbr_text = (orig_m_text[:40] + "...") if len(orig_m_text) > 40 else orig_m_text
            full_disp_name = f"{longname}/{meshnet_name}" # Use relay's meshnet name context
            react_sym = text.strip() if (text and text.strip()) else "👍" # Default symbol
            react_msg = f'\n [{full_disp_name}] reacted {react_sym} to "{abbr_text}"'
            asyncio.run_coroutine_threadsafe(
                matrix_relay(
                    matrix_room_id, react_msg, longname, shortname, meshnet_name, # Pass originating name
                    portnum, meshtastic_id=packet.get("id"), meshtastic_replyId=replyId,
                    meshtastic_text=orig_m_text, emote=True, emoji=True, # Use original emoji=True flag? Or the int 1? Using True.
                ), loop=event_loop,
            )
            logger.info(f"Relayed reaction from {sender} to Matrix room {matrix_room_id}")
        else: logger.warning(f"Original msg for reaction (ID: {replyId}) not in DB.")
        return # Handled reaction

    # --- Normal text / detection sensor messages - Original Logic ---
    # Check if text is not None OR if it's the detection sensor portnum
    if text is not None or portnum == "DETECTION_SENSOR_APP":
        channel = packet.get("channel")
        if channel is None:
            if portnum == "TEXT_MESSAGE_APP" or portnum == 1: channel = 0
            elif portnum == "DETECTION_SENSOR_APP": channel = 0
            else: logger.warning(f"Unknown portnum '{portnum}' and no channel, ignoring."); return

        target_room_ids = [r.get("id") for r in matrix_rooms if r.get("meshtastic_channel") == channel and r.get("id")]
        if not target_room_ids: logger.debug(f"Ignoring msg on unmapped Ch {channel}"); return
        if portnum == "DETECTION_SENSOR_APP" and not detection_sensor_enabled: logger.debug("Ignoring sensor packet (disabled)."); return

        longname = get_longname(sender); shortname = get_shortname(sender)
        if not longname or not shortname:
            if interface and sender in interface.nodes and (node := interface.nodes.get(sender)) and (user := node.get("user")):
                 if not longname: longname = user.get("longName"); save_longname(sender, longname)
                 if not shortname: shortname = user.get("shortName"); save_shortname(sender, shortname)
            if not longname: longname = sender;
            if not shortname: shortname = sender[:4]

        display_name = f"{longname}/{meshnet_name}"
        msg_content = text if text is not None else "[Detection Sensor Data]"
        formatted_message = f"[{display_name}]: {msg_content}"

        # --- Plugin Processing (Needs review for async safety) ---
        from mmrelay.plugin_loader import load_plugins
        plugins = load_plugins(); plugin_handled = False
        # Simplified check - assuming plugins have a synchronous method `handle_meshtastic_message_sync`
        # This avoids blocking the pubsub thread. A better async pattern is needed for complex plugins.
        for plugin in plugins:
             try:
                 if hasattr(plugin, 'handle_meshtastic_message_sync') and \
                    plugin.handle_meshtastic_message_sync(packet, formatted_message, longname, meshnet_name):
                     plugin_handled = True; logger.debug(f"Msg handled by plugin sync: {plugin.plugin_name}"); break
             except Exception as e: logger.error(f"Error checking plugin {plugin.plugin_name}: {e}", exc_info=True)

        # --- Relay to Matrix (Original Logic) ---
        if not plugin_handled and not is_direct_message:
            logger.info(f"Relaying msg from {longname} (Ch {channel}) to Matrix: {target_room_ids}")
            for room_id in target_room_ids:
                asyncio.run_coroutine_threadsafe(
                    matrix_relay(
                        room_id, formatted_message, longname, shortname, meshnet_name, # Originating name
                        portnum, meshtastic_id=packet.get("id"), meshtastic_text=text,
                    ), loop=event_loop,
                )
        elif plugin_handled: logger.debug("Not relaying to Matrix (plugin handled).")
        elif is_direct_message: logger.debug("Not relaying DM to Matrix.")

    # --- Non-text/non-sensor messages - Original Logic ---
    else:
        logger.debug(f"Received non-text packet portnum={portnum}. Checking plugins.")
        from mmrelay.plugin_loader import load_plugins
        plugins = load_plugins()
        for plugin in plugins:
             try:
                 if hasattr(plugin, 'handle_meshtastic_message_sync') and \
                    plugin.handle_meshtastic_message_sync(packet, None, None, None):
                     logger.debug(f"Non-text packet (portnum {portnum}) handled by plugin: {plugin.plugin_name}"); break
             except Exception as e: logger.error(f"Error checking plugin {plugin.plugin_name} non-text: {e}", exc_info=True)


async def check_connection():
    """Periodically checks connection using getMetadata(). Preserves original logic."""
    global meshtastic_client, shutting_down, reconnecting
    meshtastic_config = relay_config.get("meshtastic", {}); connection_type = meshtastic_config.get("connection_type", "serial")
    check_interval = 60; logger.info(f"Connection checker task started (interval: {check_interval}s).")
    while not shutting_down:
        try: await asyncio.sleep(check_interval)
        except asyncio.CancelledError: logger.info("Checker cancelled during sleep."); break
        if shutting_down: logger.info("Checker stopping due to shutdown."); break

        if meshtastic_client and not reconnecting:
            logger.debug(f"Performing periodic {connection_type} check using getMetadata()...")
            try:
                # --- Using getMetadata() as requested ---
                output_capture = io.StringIO() # Still need capture for check below
                if not hasattr(meshtastic_client, 'localNode') or not meshtastic_client.localNode:
                     raise AttributeError("localNode not available on client.")

                loop = asyncio.get_running_loop()
                # Run blocking call in executor, capturing output
                def run_get_metadata():
                    with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(output_capture):
                         meshtastic_client.localNode.getMetadata()
                    return output_capture.getvalue()

                console_output = await loop.run_in_executor(None, run_get_metadata)

                # Check output AND for exceptions (original logic)
                if "firmware_version" not in console_output:
                    # Log the captured output for debugging if check fails
                    logger.warning(f"getMetadata output missing 'firmware_version'. Output was: {console_output.strip()}")
                    raise ConnectionError("No firmware_version in getMetadata output.")
                else:
                    logger.debug("getMetadata() check successful ('firmware_version' found).")

            except (meshtastic.mesh_interface.MeshInterface.NoResponse, TimeoutError, ConnectionError, AttributeError, Exception) as e:
                logger.error(f"{connection_type.capitalize()} connection check failed: {type(e).__name__}: {e}. Assuming lost.")
                on_lost_meshtastic_connection(meshtastic_client)
            except Exception as e: # Catch any other unexpected errors
                 logger.error(f"Unexpected error during {connection_type} connection check: {e}", exc_info=True)
                 on_lost_meshtastic_connection(meshtastic_client)
        elif reconnecting: logger.debug("Skipping check: Reconnecting.")
        elif not meshtastic_client: logger.debug("Skipping check: Client not connected.")
    logger.info("Connection checker task finished.")


# --- Standalone testing block (Remains the same) ---
if __name__ == "__main__":
    # (Same testing code as before)
    print("Running meshtastic_utils directly (for testing only - config may be invalid)")
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s %(levelname)s:%(name)s:%(message)s')
    relay_config.update({
        "meshtastic": { "connection_type": "serial", "serial_port": "/dev/ttyACM0", "meshnet_name": "TestMesh", "relay_reactions": True, "detection_sensor": False },
        "matrix_rooms": [ {"id": "!test:matrix.org", "meshtastic_channel": 0} ], "logging": {"level": "DEBUG"},
        "db": {"msg_map": {"msgs_to_keep": 100, "wipe_on_restart": False}},
        "matrix": {"homeserver": "...", "bot_user_id": "...", "access_token": "..."}
    })
    loop = asyncio.get_event_loop(); event_loop = loop; client = connect_meshtastic()
    if client:
        logger.info("Direct run: Connected."); loop.create_task(check_connection())
        try: loop.run_forever()
        except KeyboardInterrupt: logger.info("Direct run: Shutting down..."); shutting_down = True; client.close(); tasks = asyncio.all_tasks(loop=loop); [task.cancel() for task in tasks]; loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True)); loop.close()
        logger.info("Direct run: Finished.")
    else: logger.error("Direct run: Failed to connect.")
