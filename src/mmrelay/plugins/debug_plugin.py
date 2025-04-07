from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
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
