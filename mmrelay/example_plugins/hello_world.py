from plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "helloworld"

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        self.logger.debug("Hello world, Meshtastic")

    async def handle_room_message(self, room, event, full_message):
        self.logger.debug("Hello world, Matrix")
