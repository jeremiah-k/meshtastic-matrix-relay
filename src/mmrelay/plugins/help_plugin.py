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
        Return a short human-readable description of the plugin.
        
        Returns:
            A brief description string for the plugin, e.g., "List supported relay commands".
        """
        return "List supported relay commands"

    async def handle_meshtastic_message(
        self, packet: Any, formatted_message: Any, longname: Any, meshnet_name: Any
    ) -> bool:
        """
        Indicates the plugin does not handle messages originating from Meshtastic.
        
        Parameters:
            packet (Any): Raw Meshtastic packet data (unused).
            formatted_message (Any): Human-readable representation of the message (unused).
            longname (Any): Sender's long display name (unused).
            meshnet_name (Any): Name of the mesh network the message originated from (unused).
        
        Returns:
            bool: `False` to indicate the message was not handled.
        """
        # Keep parameter names for compatibility with keyword calls in tests.
        _ = packet, formatted_message, longname, meshnet_name
        return False

    def get_matrix_commands(self) -> list[str]:
        """
        Lists Matrix command names provided by this plugin.
        
        Returns:
            list[str]: Command names handled by this plugin (for example, ['help']).
        """
        return [self.plugin_name]

    def get_mesh_commands(self) -> list[str]:
        """
        Report mesh commands provided by this plugin.
        
        Returns:
            list[str]: An empty list indicating this plugin exposes no mesh commands.
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
            room (MatrixRoom): Matrix room object; its `room_id` is used to send the reply.
            event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): Incoming Matrix event used to determine whether this plugin should handle the message.
            full_message (str): Raw message text from the room; used to extract command arguments.
        
        Returns:
            True if the incoming event matched this plugin and a reply was sent, False otherwise.
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