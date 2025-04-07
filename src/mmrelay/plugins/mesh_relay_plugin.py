import base64
import json
import re
from typing import List

from meshtastic import mesh_pb2

from config import relay_config
from plugins.base_plugin import BasePlugin

matrix_rooms: List[dict] = relay_config["matrix_rooms"]


class Plugin(BasePlugin):
    plugin_name = "mesh_relay"
    max_data_rows_per_node = 50

    def normalize(self, dict_obj):
        """
        Packets are either a dict, string dict or string
        """
        if not isinstance(dict_obj, dict):
            try:
                dict_obj = json.loads(dict_obj)
            except (json.JSONDecodeError, TypeError):
                dict_obj = {"decoded": {"text": dict_obj}}

        return self.strip_raw(dict_obj)

    def process(self, packet):
        packet = self.normalize(packet)

        if "decoded" in packet and "payload" in packet["decoded"]:
            if isinstance(packet["decoded"]["payload"], bytes):
                packet["decoded"]["payload"] = base64.b64encode(
                    packet["decoded"]["payload"]
                ).decode("utf-8")

        return packet

    def get_matrix_commands(self):
        return []

    def get_mesh_commands(self):
        return []

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        from matrix_utils import connect_matrix

        packet = self.process(packet)
        matrix_client = await connect_matrix()

        packet_type = packet["decoded"]["portnum"]
        if "channel" in packet:
            channel = packet["channel"]
        else:
            channel = 0

        channel_mapped = False
        for room in matrix_rooms:
            if room["meshtastic_channel"] == channel:
                channel_mapped = True
                break

        if not channel_mapped:
            self.logger.debug(f"Skipping message from unmapped channel {channel}")
            return

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

        return False

    def matches(self, event):
        # Check for the presence of necessary keys in the event
        content = event.source.get("content", {})
        body = content.get("body", "")

        if isinstance(body, str):
            match = re.match(r"^Processed (.+) radio packet$", body)
            return bool(match)
        return False

    async def handle_room_message(self, room, event, full_message):
        # Use the event for matching instead of full_message
        if not self.matches(event):
            return False

        channel = None
        for room in matrix_rooms:
            if room["id"] == room["id"]:
                channel = room["meshtastic_channel"]

        if not channel:
            self.logger.debug(f"Skipping message from unmapped channel {channel}")
            return False

        packet_json = event.source["content"].get("meshtastic_packet")
        if not packet_json:
            self.logger.debug("Missing embedded packet")
            return False

        try:
            packet = json.loads(packet_json)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(f"Error processing embedded packet: {e}")
            return

        from meshtastic_utils import connect_meshtastic

        meshtastic_client = connect_meshtastic()
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
