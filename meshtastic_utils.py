import asyncio
import threading
import time
import contextlib
import io
from typing import List

import meshtastic.ble_interface
import meshtastic.serial_interface
import meshtastic.tcp_interface
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from bleak.exc import BleakDBusError, BleakError
from pubsub import pub

# We need protobuf for forced heartbeat
from meshtastic.protobuf import mesh_pb2

from config import relay_config
from db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports

# Extract matrix rooms configuration
matrix_rooms: List[dict] = relay_config["matrix_rooms"]

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


def serial_port_exists(port_name):
    """
    Check if the specified serial port exists.
    This prevents attempting connections on non-existent ports.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports


def connect_meshtastic(force_connect=False):
    """
    Establish a connection to the Meshtastic device.
    Attempts a connection based on connection_type (serial/ble/network).
    Retries until successful or shutting_down is set.
    If already connected and not force_connect, returns the existing client.
    """
    global meshtastic_client, shutting_down
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

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

        # Determine connection type and attempt connection
        connection_type = relay_config["meshtastic"]["connection_type"]
        retry_limit = 0  # 0 == infinite
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
                    serial_port = relay_config["meshtastic"]["serial_port"]
                    logger.info(f"Connecting to serial port {serial_port} ...")

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
                    # Network (TCP) connection
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


def get_node_firmware(client):
    """
    Attempt to call localNode.getMetadata(), capture its stdout spam,
    and parse out the firmware_version line. If we cannot find it or
    we hit an exception, return None.

    This is used to confirm that the node is responding at all.
    """
    if not client:
        return None

    output_capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(
            output_capture
        ):
            client.localNode.getMetadata()
    except Exception:
        logger.debug("get_node_firmware() - exception while calling getMetadata()")
        return None

    console_output = output_capture.getvalue()
    # Look for "firmware_version: x.x.x" to confirm the node responded
    if "firmware_version" not in console_output:
        logger.debug("get_node_firmware() - no firmware_version in metadata output")
        return None

    try:
        ver = console_output.split("firmware_version: ")[1].split("\n")[0]
        return ver.strip()
    except Exception as e:
        logger.debug(f"get_node_firmware() - parse error: {e}")
        return None


def _sendHeartbeat(client):
    """
    Force-send a heartbeat packet to the radio. If the connection is truly broken,
    often this call will raise an OSError or BrokenPipeError.
    """
    if not client:
        return

    heartbeat_msg = mesh_pb2.ToRadio()
    heartbeat_msg.heartbeat.CopyFrom(mesh_pb2.Heartbeat())

    # The library uses an internal _sendToRadio() function. This might raise an error if disconnected:
    client._sendToRadio(heartbeat_msg)
    logger.debug("_sendHeartbeat() - Heartbeat sent to device")


async def _dm_self_wantAck_check(client):
    """
    Fallback: Attempt sending a self-DM with wantAck=True.
    If no ack is received within a few seconds, assume link is dead.
    This approach forces a genuine ack check from the radio.
    """
    logger.debug("_dm_self_wantAck_check() - Attempting fallback ack-check with a self-DM")

    # We'll track if we got an ack by hooking a short callback
    ack_received = asyncio.Future()

    def onAckNak(p):
        # The library calls this callback for ack/nak if ackPermitted is True
        # We set the future result to True if "errorReason" is "NONE", else it's an error
        decoded = p.get("decoded", {})
        routing = decoded.get("routing")
        if routing and routing.get("errorReason") == "NONE":
            # We got an actual ack
            if not ack_received.done():
                ack_received.set_result(True)
        else:
            # We consider this a failure
            if not ack_received.done():
                ack_received.set_result(False)

    try:
        # We need to get our own numeric node ID
        if not client.myInfo:
            logger.debug("_dm_self_wantAck_check() - no myInfo in client, cannot do fallback ack check.")
            return False

        # Prepare a short text
        test_msg = "HealthCheck"
        node_num = client.myInfo.my_node_num
        # Use the library's direct "sendData" approach with wantAck and onResponse=onAckNak
        # This ensures we see ack/nak events
        client.sendData(
            data=test_msg.encode("utf-8"),
            destinationId=node_num,
            portNum=1,  # TEXT_MESSAGE_APP
            wantAck=True,
            wantResponse=False,
            onResponse=onAckNak,
            onResponseAckPermitted=True,  # so onAckNak is called for ack
            channelIndex=0,
        )

        logger.debug("_dm_self_wantAck_check() - Sent self-DM with wantAck")

        try:
            # Wait up to 7 seconds for the ack future
            result = await asyncio.wait_for(ack_received, timeout=7.0)
            return bool(result)
        except asyncio.TimeoutError:
            logger.debug("_dm_self_wantAck_check() - No ack received within 7s")
            return False

    except Exception as e:
        logger.debug(f"_dm_self_wantAck_check() - exception: {e}")
        return False


def on_meshtastic_message(packet, interface):
    """
    Handle incoming Meshtastic messages. For reaction messages, if relay_reactions is False,
    we do not store message maps and thus won't be able to relay reactions back to Matrix.
    If relay_reactions is True, message maps are stored inside matrix_relay().
    """
    # Apply reaction filtering based on config
    relay_reactions = relay_config["meshtastic"].get("relay_reactions", False)

    # If relay_reactions is False, filter out reaction/tapback packets to avoid complexity
    if packet.get("decoded", {}).get("portnum") == "TEXT_MESSAGE_APP":
        decoded = packet.get("decoded", {})
        if not relay_reactions and ("emoji" in decoded or "replyId" in decoded):
            logger.debug(
                "Filtered out reaction/tapback packet due to relay_reactions=false."
            )
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

    meshnet_name = relay_config["meshtastic"]["meshnet_name"]

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
        if decoded.get("portnum") == "DETECTION_SENSOR_APP" and not relay_config[
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
                        ln_val = user.get("longName")
                        if ln_val:
                            save_longname(sender, ln_val)
                            longname = ln_val
                    if not shortname:
                        sn_val = user.get("shortName")
                        if sn_val:
                            save_shortname(sender, sn_val)
                            shortname = sn_val
            else:
                logger.debug(f"Node info for sender {sender} not available yet.")

        # If still not available, fallback to sender ID
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        formatted_message = f"[{longname}/{meshnet_name}]: {text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        from plugin_loader import load_plugins

        plugins = load_plugins()

        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                future = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(
                        packet, formatted_message, longname, meshnet_name
                    ),
                    loop=loop,
                )
                handled = future.result()
                if handled:
                    logger.debug(f"Processed by plugin {plugin.plugin_name}")
                    found_matching_plugin = True

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
        logger.info(
            f"Processing inbound radio message from {sender} on channel {channel}"
        )
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                # Storing the message_map (if enabled) occurs inside matrix_relay() now,
                # controlled by relay_reactions.
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
    else:
        # Non-text messages via plugins
        portnum = decoded.get("portnum")
        from plugin_loader import load_plugins

        plugins = load_plugins()
        found_matching_plugin = False
        for plugin in plugins:
            if not found_matching_plugin:
                future = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(
                        packet, None, None, None
                    ),
                    loop=loop,
                )
                handled = future.result()
                if handled:
                    logger.debug(
                        f"Processed {portnum} with plugin {plugin.plugin_name}"
                    )
                    found_matching_plugin = True


async def check_connection():
    """
    Periodically verifies that the TCP/serial/BLE link to the Meshtastic node is still alive.
    We'll do the following checks every ~5 seconds:
      1) Force-send a heartbeat using _sendHeartbeat(). If the link is broken, we often get OSError.
      2) Call get_node_firmware() to see if the node’s getMetadata() call is still working.
      3) If both pass but we still suspect a stale link, attempt a fallback self-DM with wantAck=True.
         If we can't get an ack, we assume the link is lost.

    If any check fails, we trigger on_lost_meshtastic_connection().
    """
    global meshtastic_client, shutting_down
    connection_type = relay_config["meshtastic"]["connection_type"]

    while not shutting_down:
        if meshtastic_client:
            try:
                # 1) Attempt an outbound heartbeat
                logger.debug("check_connection() - Attempting heartbeat")
                _sendHeartbeat(meshtastic_client)

                # 2) Try reading firmware via get_node_firmware
                logger.debug("check_connection() - Checking metadata firmware_version")
                fw = get_node_firmware(meshtastic_client)
                if not fw:
                    raise RuntimeError("No firmware_version detected; node might be offline.")

                # 3) Fallback ack-check
                # If you have a fully functional library environment where heartbeat fails on a broken link,
                # you might skip this step. But let's do it anyway to forcibly confirm ack traffic.
                logger.debug("check_connection() - Doing fallback wantAck DM check")
                ack_ok = await _dm_self_wantAck_check(meshtastic_client)
                if not ack_ok:
                    raise RuntimeError("Self-DM ack check timed out or failed")

            except Exception as e:
                logger.error(f"{connection_type.capitalize()} link check failed: {e}")
                on_lost_meshtastic_connection(meshtastic_client)

        await asyncio.sleep(5)


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop  # Set the event loop for use in callbacks
    loop.create_task(check_connection())
    loop.run_forever()
