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
    priority = 1

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        packet = self.strip_raw(packet)

        self.logger.debug(f"Packet received: {packet}")
        return False

    async def handle_room_message(self, room, event, full_message):
        return False
