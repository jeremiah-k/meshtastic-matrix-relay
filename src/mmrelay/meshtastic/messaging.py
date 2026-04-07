from typing import Any, cast

import mmrelay.meshtastic_utils as facade
from mmrelay.meshtastic.packet_routing import _get_portnum_name

__all__ = [
    "_get_node_display_name",
    "_get_packet_details",
    "_get_portnum_name",
    "_normalize_room_channel",
    "sendTextReply",
    "send_text_reply",
]


def _normalize_room_channel(room: dict[str, Any]) -> int | None:
    """
    Normalize a room's configured `meshtastic_channel` value to an integer.

    Parameters:
        room (dict[str, Any]): Room configuration dictionary; expected to contain the
            'meshtastic_channel' key. An optional 'id' key may be used in warnings.

    Returns:
        int | None: The channel as an `int`, or `None` if the key is missing or the
        value cannot be converted to an integer.

    Notes:
        Logs a warning mentioning the room `id` when the channel value is present but
        invalid.
    """
    room_channel = room.get("meshtastic_channel")
    if room_channel is None:
        return None
    try:
        return int(room_channel)
    except (ValueError, TypeError):
        facade.logger.warning(
            "Invalid meshtastic_channel value %r in room config "
            "for room %s, skipping this room",
            room_channel,
            room.get("id", "unknown"),
        )
        return None


def _get_packet_details(
    decoded: dict[str, Any] | None, packet: dict[str, Any], portnum_name: str
) -> dict[str, Any]:
    """
    Extract telemetry, signal, relay, and priority fields from a Meshtastic packet for logging.

    Parameters:
        decoded: Decoded packet payload (may be None); used to extract telemetry fields when present.
        packet: Full packet dictionary; used to extract signal (RSSI/SNR), relay, and priority information.
        portnum_name: Port identifier name (e.g., "TELEMETRY_APP") that determines telemetry parsing.

    Returns:
        dict: Mapping of short detail keys to formatted string values (e.g., 'batt': '85%', 'signal': 'RSSI:-70 SNR:7.5').
    """
    details = {}

    if decoded and isinstance(decoded, dict) and portnum_name == "TELEMETRY_APP":
        if (telemetry := decoded.get("telemetry")) and isinstance(telemetry, dict):
            if (metrics := telemetry.get("deviceMetrics")) and isinstance(
                metrics, dict
            ):
                if (batt := metrics.get("batteryLevel")) is not None:
                    details["batt"] = f"{batt}%"
                if (voltage := metrics.get("voltage")) is not None:
                    details["voltage"] = f"{voltage:.2f}V"
            elif (metrics := telemetry.get("environmentMetrics")) and isinstance(
                metrics, dict
            ):
                if (temp := metrics.get("temperature")) is not None:
                    details["temp"] = f"{temp:.1f}\u00b0C"
                if (humidity := metrics.get("relativeHumidity")) is not None:
                    details["humidity"] = f"{humidity:.0f}%"

    signal_info = []
    rssi = packet.get("rxRssi")
    if rssi is not None:
        signal_info.append(f"RSSI:{rssi}")
    snr = packet.get("rxSnr")
    if snr is not None:
        signal_info.append(f"SNR:{snr:.1f}")
    if signal_info:
        details["signal"] = " ".join(signal_info)

    relay = packet.get("relayNode")
    if relay is not None and relay != 0:
        details["relayed"] = f"via {relay}"

    priority = packet.get("priority")
    if priority and priority != "NORMAL":
        details["priority"] = priority

    return details


def _get_node_display_name(
    from_id: int | str, interface: Any, fallback: str | None = None
) -> str:
    """
    Get a human-readable display name for a Meshtastic node.

    Prioritizes short name from interface, then short name from database,
    then long name from database, falling back to node ID if none found.

    Parameters:
        from_id: Meshtastic node identifier (int or str)
        interface: Meshtastic interface with nodes mapping
        fallback: Optional fallback string if no name found; when None, uses the node ID

    Returns:
        str: Node display name or node ID if no name available
    """
    from_id_str = str(from_id)

    if interface and hasattr(interface, "nodes"):
        nodes = interface.nodes
        if nodes and isinstance(nodes, dict):
            if from_id_str in nodes:
                node = nodes[from_id_str]
                if isinstance(node, dict):
                    user = node.get("user")
                    if user and isinstance(user, dict):
                        if short_name := user.get("shortName"):
                            return cast(str, short_name)

    from mmrelay.db_utils import get_longname, get_shortname

    if short_name := get_shortname(from_id_str):
        return short_name

    if long_name := get_longname(from_id_str):
        return long_name

    return fallback if fallback is not None else from_id_str


def send_text_reply(
    interface: Any,
    text: str,
    reply_id: int,
    destinationId: Any = facade.meshtastic.BROADCAST_ADDR,
    wantAck: bool = False,
    channelIndex: int = 0,
) -> Any:
    """
    Send a Meshtastic text message that references (replies to) a previous Meshtastic message.

    Parameters:
        interface (Any): Meshtastic interface used to send the packet.
        text (str): UTF-8 text to send.
        reply_id (int): ID of the Meshtastic message being replied to.
        destinationId (Any, optional): Recipient address or node ID; defaults to broadcast.
        wantAck (bool, optional): If True, request an acknowledgement for the packet.
        channelIndex (int, optional): Channel index to send the packet on.

    Returns:
        The result returned by the interface's _sendPacket call (typically the sent MeshPacket), or
        `None` if the interface is unavailable or sending fails.
    """
    facade.logger.debug(
        f"Sending text reply: '{text}' replying to message ID {reply_id}"
    )

    # Check if interface is available
    if interface is None:
        facade.logger.error("No Meshtastic interface available for sending reply")
        return None

    # Create the Data protobuf message with reply_id set
    data_msg = facade.mesh_pb2.Data()
    data_msg.portnum = facade.portnums_pb2.PortNum.TEXT_MESSAGE_APP
    data_msg.payload = text.encode(facade.MESHTASTIC_TEXT_ENCODING)
    data_msg.reply_id = reply_id

    # Create the MeshPacket
    mesh_packet = facade.mesh_pb2.MeshPacket()
    mesh_packet.channel = channelIndex
    mesh_packet.decoded.CopyFrom(data_msg)
    mesh_packet.id = interface._generatePacketId()

    # Send the packet using the existing infrastructure
    try:
        return interface._sendPacket(
            mesh_packet, destinationId=destinationId, wantAck=wantAck
        )
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        facade.logger.exception("Failed to send text reply")
        return None
    except SystemExit:
        facade.logger.debug("SystemExit encountered, preserving for graceful shutdown")
        raise


# Backward-compatible alias for older call sites.
sendTextReply = send_text_reply
