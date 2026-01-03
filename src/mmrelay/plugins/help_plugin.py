from typing import Any

# matrix-nio is not marked py.typed; keep import-untyped for strict mypy.
from nio import (  # type: ignore[import-untyped]
    MatrixRoom,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
)

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
    def description(self) -> str:
        """
        Provide a short human-readable description of the plugin.

        Returns:
            str: Brief description of the plugin's functionality.
        """
        return "List supported relay commands"

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: Any, longname: Any, meshnet_name: Any
    ) -> bool:
        """
        State that this plugin does not handle messages originating from Meshtastic.

        Parameters:
            packet: Raw Meshtastic packet data.
            formatted_message: Human-readable representation of the message.
            longname: Sender's long display name.
            meshnet_name: Name of the mesh network the message originated from.

        Returns:
            True if the message was handled by the plugin, False otherwise. This implementation always returns False.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = packet, formatted_message, longname, meshnet_name
        return False

    def get_matrix_commands(self) -> list[str]:
        """
        List Matrix commands provided by this plugin.

        Returns:
            list: Command names handled by this plugin (e.g., ['help']).
        """
        return [self.plugin_name]

    def get_mesh_commands(self) -> list[str]:
        """
        List mesh commands provided by this plugin.

        Returns:
            list: An empty list — this plugin does not provide any mesh commands (help is available via Matrix only).
        """
        return []

    async def handle_room_message(
        self,
        room: MatrixRoom,
        event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
        full_message: str,
    ) -> bool:
        """
        Provide help for Matrix room messages by replying with either a list of available commands or details for a specific command.

        If the incoming event matches this plugin's Matrix help command, sends a reply to the room: either a comma-separated list of all available Matrix commands from loaded plugins or a description for a requested command.

        Parameters:
            room: Matrix room object; its `room_id` is used to send the reply.
            event: Incoming Matrix event used to determine whether this plugin should handle the message.
            full_message (str): Raw message text from the room.

        Returns:
            `True` if the message matched this plugin and a reply was sent, `False` otherwise.
        """
        # Maintain legacy matches() call for tests/compatibility but do not gate handling on it
        self.matches(event)
        matched_command = self.get_matching_matrix_command(event)
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
