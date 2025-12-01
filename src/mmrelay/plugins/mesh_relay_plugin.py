# Note: This plugin was experimental and is not functional.

import asyncio
import base64
import json
import re

from meshtastic import mesh_pb2

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

    def normalize(self, dict_obj):
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

        return self.strip_raw(dict_obj)

    def process(self, packet):
        """Process and prepare packet data for relay.

        Args:
            packet: Raw packet data to process

        Returns:
            dict: Processed packet with base64-encoded binary payloads

        Normalizes packet format and encodes binary payloads as base64
        for JSON serialization and Matrix transmission.
        """
        packet = self.normalize(packet)

        if "decoded" in packet and "payload" in packet["decoded"]:
            if isinstance(packet["decoded"]["payload"], bytes):
                packet["decoded"]["payload"] = base64.b64encode(
                    packet["decoded"]["payload"]
                ).decode("utf-8")

        return packet

    def get_matrix_commands(self):
        """Get Matrix commands handled by this plugin.

        Returns:
            list: Empty list (this plugin handles all traffic, not specific commands)
        """
        return []

    def get_mesh_commands(self):
        """Get mesh commands handled by this plugin.

        Returns:
            list: Empty list (this plugin handles all traffic, not specific commands)
        """
        return []

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
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
        from mmrelay.matrix_utils import connect_matrix

        packet = self.process(packet)
        matrix_client = await connect_matrix()

        packet_type = packet["decoded"]["portnum"]
        if "channel" in packet:
            channel = packet["channel"]
        else:
            channel = 0

        channel_mapped = False
        if config is not None:
            matrix_rooms = config.get("matrix_rooms", [])
            for room in matrix_rooms:
                if room["meshtastic_channel"] == channel:
                    channel_mapped = True
                    break

        if not channel_mapped:
            self.logger.debug(f"Skipping message from unmapped channel {channel}")
            return False

        await matrix_client.room_send(
            room_id=room["id"],
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "mmrelay_suppress": True,
                "meshtastic_packet": json.dumps(packet),
                "body": f"Processed {packet_type} radio packet",
            },
        )

        return True

    def matches(self, event):
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

    async def handle_room_message(self, room, event, full_message) -> bool:
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
        # Use the event for matching instead of full_message
        if not self.matches(event):
            return False

        channel = None
        if config is not None:
            matrix_rooms = config.get("matrix_rooms", [])
            for room_config in matrix_rooms:
                if room_config["id"] == room.room_id:
                    channel = room_config["meshtastic_channel"]

        if channel is None:
            self.logger.debug(f"Skipping message from unmapped channel {channel}")
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
        meshPacket = mesh_pb2.MeshPacket()
        meshPacket.channel = channel
        meshPacket.decoded.payload = base64.b64decode(packet["decoded"]["payload"])
        meshPacket.decoded.portnum = packet["decoded"]["portnum"]
        meshPacket.decoded.want_response = False
        meshPacket.id = meshtastic_client._generatePacketId()

        self.logger.debug("Relaying packet to Radio")

        meshtastic_client._sendPacket(
            meshPacket=meshPacket, destinationId=packet["toId"]
        )
        return True