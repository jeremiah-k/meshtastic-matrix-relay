import re

from mmrelay.plugin_loader import load_plugins
from mmrelay.plugins.base_plugin import BasePlugin


class Plugin(BasePlugin):
    """Help command plugin for listing available commands.

    Provides users with information about available relay commands
    and plugin functionality.

    Commands:
        !help: List all available commands
        !help <command>: Show detailed help for a specific command

    Dynamically discovers available commands from all loaded plugins
    and their descriptions.
    """

    plugin_name = "help"

    @property
    def description(self):
        """Get plugin description.

        Returns:
            str: Description of help functionality
        """
        return "List supported relay commands"

    async def handle_meshtastic_message(
        self, packet, formatted_message, longname, meshnet_name
    ):
        return False

    def get_matrix_commands(self):
        """Get Matrix commands handled by this plugin.

        Returns:
            list: List containing the help command
        """
        return [self.plugin_name]

    def get_mesh_commands(self):
        """Get mesh commands handled by this plugin.

        Returns:
            list: Empty list (help only works via Matrix)
        """
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
