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
import meshtastic.mesh_pb2 as mesh_pb2

from config import relay_config
from db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from log_utils import get_logger


# Extract matrix rooms configuration
matrix_rooms: List[dict] = relay_config["matrix_rooms"]

logger = get_logger(name="Meshtastic")

meshtastic_client = None
event_loop = None  # Set from main.py

meshtastic_lock = threading.Lock()
reconnecting = False
shutting_down = False
reconnect_task = None


def serial_port_exists(port_name):
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

        if meshtastic_client:
            try:
                meshtastic_client.close()
            except Exception as e:
                logger.warning(f"Error closing previous connection: {e}")
            meshtastic_client = None

        connection_type = relay_config["meshtastic"]["connection_type"]
        retry_limit = 0  # 0 == infinite
        attempts = 1
        successful = False

        while not successful and (retry_limit == 0 or attempts <= retry_limit) and not shutting_down:
            try:
                if connection_type == "serial":
                    serial_port = relay_config["meshtastic"]["serial_port"]
                    logger.info(f"Connecting to serial port {serial_port} ...")
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
                            address=ble_address, noProto=False, debugOut=None, noNodes=False
                        )
                    else:
                        logger.error("No BLE address provided.")
                        return None

                else:
                    target_host = relay_config["meshtastic"]["host"]
                    logger.info(f"Connecting to host {target_host} ...")
                    meshtastic_client = meshtastic.tcp_interface.TCPInterface(hostname=target_host)

                successful = True
                nodeInfo = meshtastic_client.getMyNodeInfo()
                logger.info(f"Connected to {nodeInfo['user']['shortName']} / {nodeInfo['user']['hwModel']}")

                # Subscribe events
                pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                pub.subscribe(on_lost_meshtastic_connection, "meshtastic.connection.lost")

            except (serial.SerialException, BleakDBusError, BleakError, Exception) as e:
                if shutting_down:
                    logger.debug("Shutdown in progress. Aborting connection attempts.")
                    break
                attempts += 1
                if retry_limit == 0 or attempts <= retry_limit:
                    wait_time = min(attempts * 2, 30)
                    logger.warning(f"Attempt #{attempts - 1} failed. Retrying in {wait_time} secs: {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Could not connect after {retry_limit} attempts: {e}")
                    return None

    return meshtastic_client


def on_lost_meshtastic_connection(interface=None):
    """
    Called when the Meshtastic connection is lost.
    Initiates a reconnect sequence unless shutting_down is True.
    """
    global meshtastic_client, reconnecting, shutting_down, event_loop, reconnect_task
    with meshtastic_lock:
        if shutting_down:
            logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.info("Reconnection already in progress. Skipping additional reconnect attempt.")
            return
        reconnecting = True
        logger.error("Lost connection. Reconnecting...")

        if meshtastic_client:
            try:
                meshtastic_client.close()
            except OSError as e:
                if e.errno == 9:
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
    Asynchronously tries to reconnect with exponential backoff.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = 10
    try:
        while not shutting_down:
            try:
                logger.info(f"Reconnection attempt starting in {backoff_time} seconds...")
                await asyncio.sleep(backoff_time)
                if shutting_down:
                    logger.debug("Shutdown in progress. Aborting reconnection attempts.")
                    break
                meshtastic_client = connect_meshtastic(force_connect=True)
                if meshtastic_client:
                    logger.info("Reconnected successfully.")
                    break
            except Exception as e:
                if shutting_down:
                    break
                logger.error(f"Reconnection attempt failed: {e}")
                backoff_time = min(backoff_time * 2, 300)
    except asyncio.CancelledError:
        logger.info("Reconnection task was cancelled.")
    finally:
        reconnecting = False


def get_node_firmware(client):
    """
    Attempt to call localNode.getMetadata(), capture its stdout spam,
    and parse out the firmware_version line. If we cannot find it or
    we hit an exception, return None.
    """
    if not client:
        return None
    output_capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(output_capture), contextlib.redirect_stderr(output_capture):
            client.localNode.getMetadata()
    except Exception:
        return None
    console_output = output_capture.getvalue()
    # look for "firmware_version: x.x.x"
    if "firmware_version" not in console_output:
        return None
    try:
        # just parse the line that starts "firmware_version:"
        ver = console_output.split("firmware_version: ")[1].split("\n")[0]
        return ver.strip()
    except Exception:
        return None


def _sendHeartbeat(client):
    """
    Force-send a heartbeat packet to the radio. If the connection is truly broken,
    often this call will throw an OSError or BrokenPipeError.
    """
    if not client:
        return
    p = mesh_pb2.ToRadio()
    p.heartbeat.CopyFrom(mesh_pb2.Heartbeat())
    # This is an internal call in the library. If the connection is gone,
    # it might raise an Exception.
    client._sendToRadio(p)


def on_meshtastic_message(packet, interface):
    """
    Handle inbound Meshtastic messages from the library.
    """
    relay_reactions = relay_config["meshtastic"].get("relay_reactions", False)
    if packet.get("decoded", {}).get("portnum") == "TEXT_MESSAGE_APP":
        decoded = packet.get("decoded", {})
        if not relay_reactions and ("emoji" in decoded or "replyId" in decoded):
            logger.debug("Filtered out reaction/tapback due to relay_reactions=false.")
            return

    from matrix_utils import matrix_relay

    global event_loop
    if shutting_down:
        logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return
    if not event_loop:
        logger.error("Event loop is not set. Cannot process message.")
        return

    loop = event_loop
    sender = packet.get("fromId") or packet.get("from")
    toId = packet.get("to")
    decoded = packet.get("decoded", {})
    text = decoded.get("text")
    replyId = decoded.get("replyId")
    emoji_flag = "emoji" in decoded and decoded["emoji"] == 1

    from meshtastic.mesh_interface import BROADCAST_NUM

    myId = interface.myInfo.my_node_num
    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM:
        is_direct_message = False
    else:
        is_direct_message = False

    meshnet_name = relay_config["meshtastic"]["meshnet_name"]

    # Reaction bridging
    if replyId and emoji_flag and relay_reactions:
        longname = get_longname(sender) or str(sender)
        shortname = get_shortname(sender) or str(sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig
            abbreviated_text = meshtastic_text[:40] + "..." if len(meshtastic_text) > 40 else meshtastic_text
            full_display_name = f"{longname}/{meshnet_name}"
            reaction_symbol = text.strip() if text and text.strip() else "⚠️"
            reaction_message = f'\n [{full_display_name}] reacted {reaction_symbol} to "{abbreviated_text}"'
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

    # Normal text or detection sensor
    if text:
        channel = packet.get("channel")
        if channel is None:
            if decoded.get("portnum") in ["TEXT_MESSAGE_APP", 1, "DETECTION_SENSOR_APP"]:
                channel = 0
            else:
                logger.debug(f"Unknown portnum {decoded.get('portnum')}, cannot determine channel.")
                return

        channel_mapped = False
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                channel_mapped = True
                break
        if not channel_mapped:
            logger.debug(f"Skipping message from unmapped channel {channel}")
            return

        if decoded.get("portnum") == "DETECTION_SENSOR_APP" and not relay_config["meshtastic"].get("detection_sensor", False):
            logger.debug("Detection sensor packet but detection_sensor is disabled.")
            return

        longname = get_longname(sender)
        shortname = get_shortname(sender)
        if not longname or not shortname:
            node = interface.nodes.get(sender)
            if node:
                user = node.get("user")
                if user:
                    if not longname:
                        ln = user.get("longName")
                        if ln:
                            save_longname(sender, ln)
                            longname = ln
                    if not shortname:
                        sn = user.get("shortName")
                        if sn:
                            save_shortname(sender, sn)
                            shortname = sn
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        formatted_message = f"[{longname}/{meshnet_name}]: {text}"

        from plugin_loader import load_plugins
        plugins = load_plugins()
        found_plugin = False
        for plugin in plugins:
            if not found_plugin:
                future = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(packet, formatted_message, longname, meshnet_name),
                    loop=loop,
                )
                handled = future.result()
                if handled:
                    logger.debug(f"Processed by plugin {plugin.plugin_name}")
                    found_plugin = True

        if is_direct_message:
            logger.debug(f"Received DM from {longname}: {text}. Not relaying.")
            return
        if found_plugin:
            logger.debug("Message was handled by a plugin. Not relaying to Matrix.")
            return

        logger.info(f"Processing inbound radio message from {sender} on channel {channel}")
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")
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
                        meshtastic_text=text,
                    ),
                    loop=loop,
                )
    else:
        # Possibly non-text plugin data
        portnum = decoded.get("portnum")
        from plugin_loader import load_plugins
        plugins = load_plugins()
        found_plugin = False
        for plugin in plugins:
            if not found_plugin:
                future = asyncio.run_coroutine_threadsafe(
                    plugin.handle_meshtastic_message(packet, None, None, None),
                    loop=loop,
                )
                handled = future.result()
                if handled:
                    logger.debug(f"Processed {portnum} with plugin {plugin.plugin_name}")
                    found_plugin = True


async def check_connection():
    """
    Periodically verifies that the TCP/serial/BLE link to the Meshtastic node is still alive.
    We'll do two checks:
      1) Force a Heartbeat message to the node, which often fails with an OSError if the node is offline.
      2) Capture the firmware_version from getMetadata(). If missing, assume no actual response.

    If either fails, we declare the link lost and trigger on_lost_meshtastic_connection().
    """
    global meshtastic_client, shutting_down
    connection_type = relay_config["meshtastic"]["connection_type"]

    while not shutting_down:
        if meshtastic_client:
            try:
                # 1) Force a heartbeat
                _sendHeartbeat(meshtastic_client)

                # 2) Try to read firmware_version
                fw = get_node_firmware(meshtastic_client)
                if not fw:
                    raise RuntimeError("No firmware_version detected; node may be offline.")

            except Exception as e:
                logger.error(f"{connection_type.capitalize()} link check failed: {e}")
                on_lost_meshtastic_connection(meshtastic_client)

        await asyncio.sleep(5)


if __name__ == "__main__":
    meshtastic_client = connect_meshtastic()
    loop = asyncio.get_event_loop()
    event_loop = loop
    loop.create_task(check_connection())
    loop.run_forever()
