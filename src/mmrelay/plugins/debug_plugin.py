from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    """Debug plugin for logging packet information.

    A low-priority plugin that logs all received meshtastic packets
    for debugging and development purposes. Strips raw binary data
    before logging to keep output readable.

    Configuration:
        priority: 1 (runs first, before other plugins)

    Never intercepts messages (always returns False) so other plugins
    can still process the same packets.
    """

    plugin_name = "debug"
    is_core_plugin = True
    priority = 1

    async def handle_meshtastic_message(
        self,
        packet: dict[str, Any],
        formatted_message: str,
        longname: str,
        meshnet_name: str,
    ) -> bool:
        """
        Log a Meshtastic packet after removing raw binary fields.

        Strips raw binary fields from `packet` for readability and logs the sanitized packet at debug level. The other parameters are accepted for compatibility but are not used. This plugin does not intercept the message.

        Parameters:
            packet: The received Meshtastic packet; raw binary fields will be removed before logging.

        Returns:
            `True` if the message is intercepted, `False` otherwise.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = formatted_message, longname, meshnet_name
        packet = self.strip_raw(packet)

        self.logger.debug(f"Packet received: {packet}")
        return False

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Declines to handle room messages so they remain available to other plugins.

        Parameters:
            room: The room or channel associated with the message.
            event: Metadata describing the room event.

        Returns:
            bool: `False` to indicate that the message is not handled.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = room, event, full_message
        return False
