import re

from haversine import haversine

from mmrelay.constants.database import DEFAULT_DISTANCE_KM_FALLBACK, DEFAULT_RADIUS_KM
from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.plugins import SPECIAL_NODE_MESSAGES
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "drop"
    is_core_plugin = True
    special_node = SPECIAL_NODE_MESSAGES

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    def get_position(self, meshtastic_client, node_id):
        for _node, info in meshtastic_client.nodes.items():
            if info["user"]["id"] == node_id:
                if "position" in info:
                    return info["position"]
                else:
                    return None
        return None

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        """
        Manage delivery and storage of "drop" messages based on an incoming packet and node locations.

        When a packet arrives, attempt to deliver any stored dropped messages to the sender if the sender's known position is within the configured radius and the sender is not the original dropper. If the packet contains a valid "!drop <message>" command, record the message together with the sender's current location for later delivery.

        Returns:
            True if a drop command was processed and stored (or processing occurred but the dropper's position was unavailable), False otherwise.
        """
        meshtastic_client = connect_meshtastic()
        nodeInfo = meshtastic_client.getMyNodeInfo()

        # Attempt message drop to packet originator if not relay
        if "fromId" in packet and packet["fromId"] != nodeInfo["user"]["id"]:
            position = self.get_position(meshtastic_client, packet["fromId"])
            if position and "latitude" in position and "longitude" in position:
                packet_location = (
                    position["latitude"],
                    position["longitude"],
                )

                self.logger.debug(f"Packet originates from: {packet_location}")
                messages = self.get_node_data(self.special_node) or []
                unsent_messages = []
                for message in messages:
                    # You cannot pickup what you dropped
                    if (
                        "originator" in message
                        and message["originator"] == packet["fromId"]
                    ):
                        unsent_messages.append(message)
                        continue

                    try:
                        distance_km = haversine(
                            (packet_location[0], packet_location[1]),
                            message["location"],
                        )
                    except (ValueError, TypeError):
                        distance_km = DEFAULT_DISTANCE_KM_FALLBACK
                    radius_km = self.config.get("radius_km", DEFAULT_RADIUS_KM)
                    if distance_km <= radius_km:
                        target_node = packet["fromId"]
                        self.logger.debug(f"Sending dropped message to {target_node}")
                        meshtastic_client.sendText(
                            text=message["text"], destinationId=target_node
                        )
                    else:
                        unsent_messages.append(message)
                self.set_node_data(self.special_node, unsent_messages)
                total_unsent_messages = len(unsent_messages)
                if total_unsent_messages > 0:
                    self.logger.debug(f"{total_unsent_messages} message(s) remaining")

        # Attempt to drop a message
        if (
            "decoded" in packet
            and "portnum" in packet["decoded"]
            and packet["decoded"]["portnum"] == TEXT_MESSAGE_APP
        ):
            text = packet["decoded"].get("text") or ""
            if f"!{self.plugin_name}" not in text:
                return False

            match = re.search(r"!drop\s+(.+)$", text)
            if not match:
                return False

            drop_message = match.group(1)

            position = {}
            for _node, info in meshtastic_client.nodes.items():
                if info["user"]["id"] == packet["fromId"]:
                    if "position" in info:
                        position = info["position"]
                    else:
                        continue

            if "latitude" not in position or "longitude" not in position:
                self.logger.debug(
                    "Position of dropping node is not known. Skipping ..."
                )
                return True

            self.store_node_data(
                self.special_node,
                {
                    "location": (position["latitude"], position["longitude"]),
                    "text": drop_message,
                    "originator": packet["fromId"],
                },
            )
            self.logger.debug(f"Dropped a message: {drop_message}")
            return True

    async def handle_room_message(self, room, event, full_message):
        # Pass the event to matches() instead of full_message
        if self.matches(event):
            return True
