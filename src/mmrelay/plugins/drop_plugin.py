import asyncio
import re
from typing import TYPE_CHECKING, Any

from haversine import haversine  # type: ignore[import-untyped]

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.constants.database import DEFAULT_DISTANCE_KM_FALLBACK, DEFAULT_RADIUS_KM
from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.plugins import SPECIAL_NODE_MESSAGES
from mmrelay.meshtastic_utils import connect_meshtastic
from mmrelay.plugins.base_plugin import BasePlugin

if TYPE_CHECKING:
    from meshtastic.mesh_interface import MeshInterface  # type: ignore[import-untyped]


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
        for _node, info in meshtastic_client.nodes.items():
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
        Handle stored "drop" messages and record new drops from an incoming Meshtastic packet.
        
        If the packet is from another node whose position is known, deliver any stored drops whose saved location lies within the configured radius to that node (excluding messages the node originally dropped). If the packet contains a "!drop <message>" command and the dropper's position is known, store the message with the dropper's location and originator id for later delivery.
        
        Returns:
            `True` if the packet contained and processed a drop command (including cases where the command was acknowledged but the dropper's position was unavailable), `False` otherwise.
        """
        # Keep parameter names for keyword-arg compatibility in tests and plugin API.
        _ = (formatted_message, longname, meshnet_name)
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
            return bool(is_drop_command)
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
                data = self.get_node_data(self.special_node)
                messages: list[dict[str, Any]] = (
                    data if isinstance(data, list) else [data] if data else []
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
                    "originator": dropping_from_id,
                },
            )
            self.logger.debug(f"Dropped a message: {drop_message}")
            return True

        # Packet did not contain a drop command or was not processable
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        # Pass the event to matches() instead of full_message
        """
        Route a Matrix room event to the plugin's matching logic.

        Parameters:
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The room event to evaluate; forwarded to `matches()`.

        Returns:
            bool: `True` if the event matches the plugin's criteria, `False` otherwise.
        """
        # Preserve parameter names for keyword-arg compatibility in tests and plugin API.
        _ = (room, full_message)
        return self.matches(event)