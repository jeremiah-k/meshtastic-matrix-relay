from typing import Any

from nio import MatrixRoom  # type: ignore[import-not-found]

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
        Log a received Meshtastic packet after removing raw binary data.

        Parameters:
            packet: The raw Meshtastic packet object to inspect; raw binary fields will be stripped before logging.
            formatted_message: A human-friendly representation of the packet (already formatted for display).
            longname: The sender's long name or identifier.
            meshnet_name: The mesh network name the packet was received on.

        Returns:
            `False` to indicate this plugin does not intercept the message and allows further processing.
        """
        packet = self.strip_raw(packet)

        self.logger.debug(f"Packet received: {packet}")
        return False

    async def handle_room_message(
        self, room: MatrixRoom, event: dict[str, Any], full_message: str
    ) -> bool:
        """
        Declines to handle room messages so they remain available to other plugins.

        Parameters:
            room: The room or channel associated with the message.
            event: Metadata describing the room event.
            full_message: The complete message payload.

        Returns:
            `False` always, indicating the message is not intercepted and processing continues.
        """
        return False
