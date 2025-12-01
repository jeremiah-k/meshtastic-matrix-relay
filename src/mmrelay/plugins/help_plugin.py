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

    is_core_plugin = True
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
    ) -> bool:
        """
        Indicate that this plugin does not handle incoming Meshtastic messages.

        Parameters:
            packet: Raw Meshtastic packet data.
            formatted_message: Human-readable string representation of the message.
            longname: Sender's long display name.
            meshnet_name: Name of the mesh network the message originated from.

        Returns:
            `True` if the message was handled by the plugin, `False` otherwise. This implementation always returns `False`.
        """
        return False

    def get_matrix_commands(self):
        """
        List Matrix commands provided by this plugin.

        Returns:
            list: Command names handled by this plugin (e.g., ['help']).
        """
        return [self.plugin_name]

    def get_mesh_commands(self):
        """Get mesh commands handled by this plugin.

        Returns:
            list: Empty list (help only works via Matrix)
        """
        return []

    async def handle_room_message(self, room, event, full_message) -> bool:
        """
        Handle an incoming Matrix room message for the help command and reply with either a list of available commands or details for a specific command.

        Parameters:
            room: The Matrix room object where the message originated; must provide `room_id`.
            event: The incoming Matrix event; used to determine whether this plugin should handle the message.
            full_message (str): The raw message text from the room.

        Returns:
            handled (bool): `True` if the plugin processed the message and sent a reply, `False` if the event did not match and was not handled.
        """
        # Maintain legacy matches() call for tests/compatibility but do not gate handling on it
        self.matches(event)
        matched_command = await self.get_matching_matrix_command(event)
        if not matched_command:
            return False
        command = self.extract_command_args(matched_command, full_message) or ""

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
