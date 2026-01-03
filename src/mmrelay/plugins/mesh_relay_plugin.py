# Note: This plugin was experimental and is not functional.

import asyncio
import base64
import binascii
import json
import re
from typing import Any, cast

from meshtastic import mesh_pb2  # type: ignore[import-untyped]
from nio import (  # type: ignore[import-untyped]
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.database import DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY
from mmrelay.plugins.base_plugin import BasePlugin, config


class Plugin(BasePlugin):
    """Core mesh-to-Matrix relay plugin.

    Handles bidirectional message relay between Meshtastic mesh network
    and Matrix chat rooms. Processes radio packets and forwards them
    to configured Matrix rooms, and vice versa.

    This plugin is fundamental to the relay's core functionality and
    typically runs with high priority to ensure messages are properly
    bridged between the two networks.

    Configuration:
        max_data_rows_per_node: 50 (reduced storage for performance)
    """

    is_core_plugin = True
    plugin_name = "mesh_relay"
    max_data_rows_per_node = DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY

    def normalize(self, dict_obj: Any) -> dict[str, Any]:
        """
        Converts packet data in various formats (dict, JSON string, or plain string) into a normalized dictionary with raw data fields removed.

        Parameters:
            dict_obj: Packet data as a dictionary, JSON string, or plain string.

        Returns:
            A dictionary representing the normalized packet with raw fields stripped.
        """
        if not isinstance(dict_obj, dict):
            try:
                dict_obj = json.loads(dict_obj)
            except (json.JSONDecodeError, TypeError):
                dict_obj = {"decoded": {"text": dict_obj}}

        return cast(dict[str, Any], self.strip_raw(dict_obj))

    def process(self, packet: Any) -> dict[str, Any]:
        """
        Normalize a packet and encode any binary payloads for safe transport.

        Parameters:
            packet (Any): Raw packet data to normalize and prepare.

        Returns:
            dict[str, Any]: The normalized packet; if `decoded.payload` was bytes it is replaced with a base64-encoded UTF-8 string.
        """
        result = self.normalize(packet)

        if "decoded" in result and "payload" in result["decoded"]:
            if isinstance(result["decoded"]["payload"], bytes):
                result["decoded"]["payload"] = base64.b64encode(
                    result["decoded"]["payload"]
                ).decode("utf-8")

        return result

    def get_matrix_commands(self) -> list[str]:
        """
        Get the Matrix commands this plugin handles.

        Returns:
            list[str]: Empty list when the plugin handles all Matrix traffic instead of specific commands.
        """
        return []

    def get_mesh_commands(self) -> list[str]:
        """
        Indicate which Meshtastic/mesh commands this plugin handles.

        Returns:
            list[str]: An empty list because this plugin handles all mesh traffic rather than specific commands.
        """
        return []

    def _iter_room_configs(self) -> list[dict[str, Any]]:
        """
        Normalize matrix_rooms config entries into a list of dicts.

        Supports both list and dict shapes for backward compatibility and filters
        out non-dict entries to keep iteration safe.
        """
        if config is None:
            return []

        matrix_rooms = config.get("matrix_rooms", [])
        if isinstance(matrix_rooms, dict):
            iterable_rooms = matrix_rooms.values()
        elif isinstance(matrix_rooms, list):
            iterable_rooms = matrix_rooms
        else:
            self.logger.debug(
                "matrix_rooms expected list or dict, got %s",
                type(matrix_rooms).__name__,
            )
            return []

        return [room for room in iterable_rooms if isinstance(room, dict)]

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: str, longname: str, meshnet_name: str
    ) -> bool:
        """
        Relay a Meshtastic packet to the configured Matrix room for its channel.

        Normalizes and prepares the incoming Meshtastic packet and, if the packet's channel is mapped in the plugin configuration, sends a Matrix message that contains a JSON-serialized `meshtastic_packet` and a marker (`mmrelay_suppress`) identifying it as a bridged packet.

        Parameters:
            packet: Raw Meshtastic packet (dict, JSON string, or other) to be normalized and relayed.
            formatted_message (str): Human-readable text derived from the packet (informational; not used for routing).
            longname (str): Long name of the sending node (informational).
            meshnet_name (str): Name of the mesh network (informational).

        Returns:
            True if the packet was sent to a mapped Matrix room, False otherwise.
        """
        # formatted_message, longname, and meshnet_name are part of the public
        # plugin callback API but not used for routing in this plugin.
        _ = (formatted_message, longname, meshnet_name)
        from mmrelay.matrix_utils import connect_matrix

        matrix_client = await connect_matrix()
        if matrix_client is None:
            self.logger.error("Matrix client is None; skipping mesh relay to Matrix")
            return False

        packet = self.process(packet)
        decoded = packet.get("decoded", {})
        packet_type = decoded.get("portnum")
        if packet_type is None:
            self.logger.error("Packet missing required 'decoded.portnum' field")
            return False
        channel = packet.get("channel", 0)

        channel_mapped = False
        target_room_id = None
        for room_config in self._iter_room_configs():
            if room_config.get("meshtastic_channel") == channel:
                channel_mapped = True
                target_room_id = room_config.get("id")
                break

        if not channel_mapped:
            self.logger.debug(f"Skipping message from unmapped channel {channel}")
            return False
        if not target_room_id:
            self.logger.error(
                "Skipping message: no Matrix room id mapped for channel %s",
                channel,
            )
            return False

        await matrix_client.room_send(
            room_id=target_room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "mmrelay_suppress": True,
                "meshtastic_packet": json.dumps(packet),
                "body": f"Processed {packet_type} radio packet",
            },
        )

        return True

    def matches(self, event: Any) -> bool:
        """
        Determine whether a Matrix event's message body contains the bridged-packet marker.

        Checks event.source["content"]["body"] (when it is a string) against the anchored pattern `^Processed (.+) radio packet$`.

        Parameters:
            event: Matrix event object whose `.source` mapping is expected to contain a `"content"` dict with a `"body"` string.

        Returns:
            True if the content body matches `^Processed (.+) radio packet$`, False otherwise.
        """
        # Check for the presence of necessary keys in the event
        content = event.source.get("content", {})
        body = content.get("body", "")

        if isinstance(body, str):
            match = re.match(r"^Processed (.+) radio packet$", body)
            return bool(match)
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Relay an embedded Meshtastic packet from a Matrix room message to the Meshtastic mesh.

        If the Matrix event contains an embedded `meshtastic_packet` (detected via self.matches),
        this function finds the Meshtastic channel mapped to the Matrix room, parses the embedded
        JSON packet from the event content, reconstructs a MeshPacket (decoding the base64-encoded
        payload), and sends it on the radio via the Meshtastic client.

        Parameters:
            room: Matrix room object where the message was received; used to find the room→channel mapping.
            event: Matrix event containing the message; the embedded packet is read from event.source["content"].
            full_message: Unused — matching and extraction are performed against `event`.

        Returns:
            True if a packet was successfully sent to the mesh, False otherwise.
        """
        # Use the event for matching instead of full_message; keep full_message
        # in the signature for API compatibility.
        _ = full_message
        if not self.matches(event):
            return False

        channel = None
        for room_config in self._iter_room_configs():
            if room_config.get("id") == room.room_id:
                channel = room_config.get("meshtastic_channel")
                break

        if channel is None:
            self.logger.debug(f"Skipping message from unmapped room {room.room_id}")
            return False

        packet_json = event.source["content"].get("meshtastic_packet")
        if not packet_json:
            self.logger.debug("Missing embedded packet")
            return False

        try:
            packet = json.loads(packet_json)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("Error processing embedded packet")
            return False

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = await asyncio.to_thread(connect_meshtastic)
        if meshtastic_client is None:
            self.logger.error("Meshtastic client unavailable")
            return False

        try:
            decoded = packet.get("decoded", {})
            payload_b64 = decoded.get("payload")
            portnum = decoded.get("portnum")
            to_id = packet.get("toId")
            if not payload_b64 or portnum is None or to_id is None:
                self.logger.error("Packet missing required fields for relay")
                return False

            meshPacket = mesh_pb2.MeshPacket()
            meshPacket.channel = channel
            meshPacket.decoded.payload = base64.b64decode(payload_b64)
            meshPacket.decoded.portnum = portnum
            meshPacket.decoded.want_response = False
            meshPacket.id = 0
        except (TypeError, ValueError, binascii.Error):
            self.logger.exception("Error reconstructing packet")
            return False

        self.logger.debug("Relaying packet to Radio")

        # _sendPacket is required for relaying raw MeshPacket payloads.
        # Note: this is a private API; monitor upstream Meshtastic changes.
        meshtastic_client._sendPacket(meshPacket=meshPacket, destinationId=to_id)
        return True
