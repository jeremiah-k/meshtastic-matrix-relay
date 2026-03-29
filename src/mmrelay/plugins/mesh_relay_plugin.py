# Core mesh-to-Matrix relay plugin providing bidirectional message bridging.

import asyncio
import base64
import binascii
import json
from typing import Any, Iterable, cast

from meshtastic import mesh_pb2

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.database import DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY
from mmrelay.constants.domain import MATRIX_EVENT_TYPE_ROOM_MESSAGE
from mmrelay.constants.formats import (
    DEFAULT_TEXT_ENCODING,
    FORMAT_PROCESSED_PACKET,
    MATRIX_PACKET_KEY,
    MATRIX_SUPPRESS_KEY,
)
from mmrelay.constants.plugins import MESH_PACKET_DEFAULT_ID, PROCESSED_PACKET_REGEX
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
        Prepare a Meshtastic packet for transport by normalizing it and encoding any binary payloads as base64 strings.

        Parameters:
            packet (Any): Raw packet data to normalize and prepare.

        Returns:
            dict[str, Any]: The normalized packet. If `decoded.payload` was bytes, it is replaced with a base64-encoded UTF-8 string.
        """
        result = self.normalize(packet)

        if "decoded" in result and "payload" in result["decoded"]:
            if isinstance(result["decoded"]["payload"], bytes):
                result["decoded"]["payload"] = base64.b64encode(
                    result["decoded"]["payload"]
                ).decode(DEFAULT_TEXT_ENCODING)

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
        Declare which Meshtastic/mesh commands the plugin handles.

        Returns:
            list[str]: An empty list indicating the plugin handles all mesh traffic rather than specific commands.
        """
        return []

    def _iter_room_configs(self) -> list[dict[str, Any]]:
        """
        Normalize configured Matrix room entries and return them as a list of dictionaries.

        Accepts either a mapping or a list from the global `matrix_rooms` configuration, filters out any non-dictionary entries, and returns an empty list if the global config or `matrix_rooms` is missing or malformed.

        Returns:
            A list of room configuration dictionaries.
        """
        # matrix_rooms live in the global relay config, not per-plugin config.
        global_config = config
        if global_config is None:
            return []

        matrix_rooms = global_config.get("matrix_rooms", [])
        iterable_rooms: Iterable[Any] | None = None
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
        # Keep parameter names for keyword-arg compatibility in tests and plugin API.
        # Unused for routing in this plugin.
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
            self.logger.debug("Skipping message from unmapped channel %s", channel)
            return False
        if not target_room_id:
            self.logger.error(
                "Skipping message: no Matrix room id mapped for channel %s",
                channel,
            )
            return False

        await matrix_client.room_send(
            room_id=target_room_id,
            message_type=MATRIX_EVENT_TYPE_ROOM_MESSAGE,
            content={
                "msgtype": "m.text",
                MATRIX_SUPPRESS_KEY: True,
                MATRIX_PACKET_KEY: json.dumps(packet),
                "body": FORMAT_PROCESSED_PACKET.format(packet_type=packet_type),
            },
        )

        return True

    def matches(self, event: Any) -> bool:
        """
        Determine whether a Matrix event is a bridged-packet marker.

        Primary check: Looks for MATRIX_SUPPRESS_KEY=True in the event content.
        Fallback: Checks event.source["content"]["body"] (when it is a string)
        against the anchored pattern `^Processed (.+) radio packet$` for backward
        compatibility with older messages.

        Note:
            This method has a side effect: when matching legacy packet bodies,
            it mutates `event.source["content"]` by adding MATRIX_PACKET_KEY
            for consistency with newer message formats.

        Policy: This method prioritizes correctness over backward compatibility.
        It will not match legacy body-only markers unless packet data can be
        reconstructed, because handle_room_message() needs packet data to relay
        back onto the mesh. Historical Matrix-room replay/backfill is not a
        supported use case; current and future correctness matters more.

        Parameters:
            event: Matrix event object whose `.source` mapping is expected to contain a `"content"` dict.

        Returns:
            True if MATRIX_SUPPRESS_KEY is True or if the content body matches the regex, False otherwise.
        """
        content = event.source.get("content", {})

        # Normalize dict packet payloads to JSON string before any early returns
        packet = content.get(MATRIX_PACKET_KEY)
        if isinstance(packet, dict):
            content[MATRIX_PACKET_KEY] = json.dumps(packet)

        if content.get(MATRIX_SUPPRESS_KEY) is True and content.get(MATRIX_PACKET_KEY):
            return True

        body = content.get("body", "")
        if isinstance(body, str):
            match = PROCESSED_PACKET_REGEX.match(body)
            if match:
                if not content.get(MATRIX_PACKET_KEY):
                    legacy_packet = content.get("packet") or content.get(
                        "meshtastic_packet"
                    )
                    if isinstance(legacy_packet, dict):
                        content[MATRIX_PACKET_KEY] = json.dumps(legacy_packet)
                    elif isinstance(legacy_packet, str) and legacy_packet.strip():
                        content[MATRIX_PACKET_KEY] = legacy_packet
                    else:
                        legacy_body_payload = match.group(1).strip()
                        if legacy_body_payload.startswith(
                            "{"
                        ) and legacy_body_payload.endswith("}"):
                            content[MATRIX_PACKET_KEY] = legacy_body_payload
                return bool(content.get(MATRIX_PACKET_KEY))
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
        # Keep parameter name for keyword-arg compatibility in tests and plugin API.
        _ = full_message
        if not self.matches(event):
            return False

        channel = None
        for room_config in self._iter_room_configs():
            if room_config.get("id") == room.room_id:
                channel = room_config.get("meshtastic_channel")
                break

        if channel is None:
            self.logger.debug("Skipping message from unmapped room %s", room.room_id)
            return False

        packet_json = event.source.get("content", {}).get(MATRIX_PACKET_KEY)
        if not packet_json:
            self.logger.debug("Missing embedded packet")
            return False

        try:
            packet = json.loads(packet_json)
        except (json.JSONDecodeError, TypeError):
            self.logger.exception("Error processing embedded packet")
            return False

        if not isinstance(packet, dict):
            self.logger.error("Embedded packet must be a JSON object")
            return False

        from mmrelay.meshtastic_utils import connect_meshtastic

        meshtastic_client = await asyncio.to_thread(connect_meshtastic)
        if meshtastic_client is None:
            self.logger.error("Meshtastic client unavailable")
            return False

        try:
            decoded = packet.get("decoded", {})
            if not isinstance(decoded, dict):
                self.logger.error("Embedded packet decoded field must be a JSON object")
                return False
            payload_b64 = decoded.get("payload")
            portnum = decoded.get("portnum")
            to_id = packet.get("toId")
            if not payload_b64 or portnum is None or to_id is None:
                self.logger.error("Packet missing required fields for relay")
                return False

            mesh_packet = mesh_pb2.MeshPacket()
            mesh_packet.channel = channel
            mesh_packet.decoded.payload = base64.b64decode(payload_b64)
            mesh_packet.decoded.portnum = portnum
            mesh_packet.decoded.want_response = False
            mesh_packet.id = MESH_PACKET_DEFAULT_ID
        except (TypeError, ValueError, binascii.Error):
            self.logger.exception("Error reconstructing packet")
            return False

        self.logger.debug("Relaying packet to Radio")

        # _sendPacket is required for relaying raw MeshPacket payloads.
        # Note: this is a private API; monitor upstream Meshtastic changes.
        try:
            meshtastic_client._sendPacket(meshPacket=mesh_packet, destinationId=to_id)
        except AttributeError:
            self.logger.exception(
                "_sendPacket method not available; Meshtastic API may have changed"
            )
            return False
        return True
