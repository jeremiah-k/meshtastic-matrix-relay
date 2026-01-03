import asyncio
import re
from typing import TYPE_CHECKING, Any

from haversine import haversine  # type: ignore[import-not-found]
from nio import MatrixRoom  # type: ignore[import-not-found]

from mmrelay.constants.database import DEFAULT_DISTANCE_KM_FALLBACK, DEFAULT_RADIUS_KM
from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.plugins import SPECIAL_NODE_MESSAGES
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from meshtastic.mesh_interface import (
        MeshInterface,  # type: ignore[import-not-found]
    )


class Plugin(BasePlugin):
    plugin_name = "drop"
    is_core_plugin = True
    special_node = SPECIAL_NODE_MESSAGES

    # No __init__ method needed with the simplified plugin system
    # The BasePlugin will automatically use the class-level plugin_name

    def get_position(
        self, meshtastic_client: "MeshInterface", node_id: str
    ) -> dict[str, Any] | None:
        """
        Retrieve the geographic position for a Meshtastic node by its node ID.

        Parameters:
            meshtastic_client (MeshInterface): Connected Meshtastic client containing node information.
            node_id (str): The node's user ID to look up.

        Returns:
            position (dict[str, Any] | None): The node's `position` dictionary (typically containing latitude and longitude) if the node is found and has position data; `None` if the node is not found or has no `position`.
        """
        for _node, info in meshtastic_client.nodes.items():  # type: ignore[attr-defined]
            if info["user"]["id"] == node_id:
                if "position" in info:
                    pos: dict[str, Any] = info["position"]
                    return pos
                else:
                    return None
        return None

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Handle delivery of stored drop messages and record new drops from an incoming Meshtastic packet.

        If the packet originates from another node and that node's position is known, deliver any stored drops whose saved location lies within the configured radius to the originator (excluding messages the originator created). If the packet contains a "!drop <message>" command and the dropper's position is known, store the message together with the dropper's location and originator id for later delivery.

        Returns:
            `True` if a drop command was processed (including cases where processing occurred but the dropper's position was unavailable), `False` otherwise.
        """
        meshtastic_client = await asyncio.to_thread(connect_meshtastic)
        if meshtastic_client is None:
            self.logger.warning(
                "Meshtastic client unavailable; skipping drop message handling"
            )
            text = packet.get("decoded", {}).get("text", "")
            is_drop_command = (
                packet.get("decoded", {}).get("portnum") == TEXT_MESSAGE_APP
                and f"!{self.plugin_name}" in text
                and re.search(r"!drop\s+(.+)$", text)
            )
            return True if is_drop_command else False
        nodeInfo = meshtastic_client.getMyNodeInfo()

        # Attempt message drop to packet originator if not relay
        if "fromId" in packet and packet["fromId"] != nodeInfo["user"]["id"]:
            from_id: str = packet["fromId"]
            position = self.get_position(meshtastic_client, from_id)
            if position and "latitude" in position and "longitude" in position:
                packet_location = (
                    position["latitude"],
                    position["longitude"],
                )

                self.logger.debug(f"Packet originates from: {packet_location}")
                messages: list[dict[str, Any]] = (
                    self.get_node_data(self.special_node) or []
                )
                unsent_messages: list[dict[str, Any]] = []
                for message in messages:
                    # You cannot pickup what you dropped
                    if "originator" in message and message["originator"] == from_id:
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
                        target_node = from_id
                        self.logger.debug(f"Sending dropped message to {target_node}")
                        await asyncio.to_thread(
                            meshtastic_client.sendText,
                            text=message["text"],
                            destinationId=target_node,
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

            dropping_from_id: str | None = packet.get("fromId")
            if not dropping_from_id:
                self.logger.debug(
                    "Drop command missing fromId; cannot determine originator. Skipping ..."
                )
                return False

            position = self.get_position(meshtastic_client, dropping_from_id) or {}

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

        # Packet did not contain a drop command or was not processable
        return False

    async def handle_room_message(
        self, room: MatrixRoom, event: dict[str, Any], full_message: str
    ) -> bool:
        # Pass the event to matches() instead of full_message
        """
        Route a room event to the plugin's matching logic.

        Parameters:
            event (object): The room event to evaluate; forwarded to matches().

        Returns:
            bool: True if the event matches the plugin's criteria, False otherwise.
        """
        return self.matches(event)
