import re

from plugin_loader import load_plugins
from plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    plugin_name = "help"

    @property
    def description(self):
        return "List supported relay commands"

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        return False

    def get_matrix_commands(self):
        return [self.plugin_name]

    def get_mesh_commands(self):
        return []

    async def handle_room_message(self, room, event, full_message):
        # Pass the event to matches()
        if not self.matches(event):
            return False

        command = None

        match = re.match(r"^.*: !help\s+(.+)$", full_message)
        if match:
            command = match.group(1)

        plugins = load_plugins()

        if command:
            reply = f"No such command: {command}"

            for plugin in plugins:
                if command in plugin.get_matrix_commands():
                    reply = f"`!{command}`: {plugin.description}"
        else:
            commands = []
            for plugin in plugins:
                commands.extend(plugin.get_matrix_commands())
            reply = "Available commands: " + ", ".join(commands)

        await self.send_matrix_message(room.room_id, reply)
        return True
