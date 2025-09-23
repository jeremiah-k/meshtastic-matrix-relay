#!/usr/bin/env python3
"""
Test suite for the MMRelay help plugin.

Tests the help command functionality including:
- General help command listing all available commands
- Specific help command for individual plugins
- Command discovery from loaded plugins
- Matrix room message handling
- Plugin description retrieval
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.help_plugin import Plugin


@pytest.mark.usefixtures("mock_event_loop")
class TestHelpPlugin(unittest.TestCase):
    """Test cases for the help plugin."""

    def setUp(self):
        """
        Initialize the test environment by creating a Plugin instance, mocking its logger and message sending method, and setting up mock plugins with predefined commands and descriptions.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock Matrix client methods
        self.plugin.send_matrix_message = AsyncMock()

        # Create mock plugins for testing
        self.mock_plugin1 = MagicMock()
        self.mock_plugin1.get_matrix_commands.return_value = ["nodes", "health"]
        self.mock_plugin1.description = "Show mesh nodes and health"

        self.mock_plugin2 = MagicMock()
        self.mock_plugin2.get_matrix_commands.return_value = ["weather"]
        self.mock_plugin2.description = "Show weather forecast"

        self.mock_plugin3 = MagicMock()
        self.mock_plugin3.get_matrix_commands.return_value = ["help"]
        self.mock_plugin3.description = "List supported relay commands"

    def test_plugin_name(self):
        """
        Verify that the plugin's name is set to "help".
        """
        self.assertEqual(self.plugin.plugin_name, "help")

    def test_description_property(self):
        """
        Test that the help plugin's description property returns the expected string.
        """
        description = self.plugin.description
        self.assertEqual(description, "List supported relay commands")

    def test_get_matrix_commands(self):
        """
        Test that the help plugin reports 'help' as its supported Matrix command.
        """
        commands = self.plugin.get_matrix_commands()
        self.assertEqual(commands, ["help"])

    def test_get_mesh_commands(self):
        """
        Test that the help plugin reports no supported Meshtastic commands.
        """
        commands = self.plugin.get_mesh_commands()
        self.assertEqual(commands, [])

    def test_handle_meshtastic_message_always_false(self):
        """
        Test that handle_meshtastic_message always returns False, regardless of input.
        """

        async def run_test():
            """
            Asynchronously tests that handle_meshtastic_message always returns False for the help plugin.
            """
            result = await self.plugin.handle_meshtastic_message(
                {}, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False and does not send a message when the event does not match the help command.
        """
        self.plugin.matches = MagicMock(return_value=False)

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that handle_room_message returns False and does not send a message when the event does not match the help command.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_not_called()

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_general_help(self, mock_load_plugins):
        """
        Test that a general help command triggers a message listing all available commands from loaded plugins.

        Verifies that when the help command is invoked, the plugin responds with a message containing all supported commands, and that the message is sent to the correct Matrix room.
        """
        mock_load_plugins.return_value = [
            self.mock_plugin1,
            self.mock_plugin2,
            self.mock_plugin3,
        ]
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help"

        async def run_test():
            """
            Asynchronously tests that the help plugin sends a message listing all available commands when handling a general help request.

            Verifies that the plugin's `handle_room_message` method returns True, calls the `matches` and `send_matrix_message` methods once, and that the sent message includes all expected command names.
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_called_once()

            # Check the call arguments
            call_args = self.plugin.send_matrix_message.call_args
            self.assertEqual(call_args[0][0], "!test:matrix.org")  # room_id

            # Should contain all available commands
            message = call_args[0][1]
            self.assertIn("Available commands:", message)
            self.assertIn("nodes", message)
            self.assertIn("health", message)
            self.assertIn("weather", message)
            self.assertIn("help", message)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_specific_help_found(self, mock_load_plugins):
        """
        Test that handle_room_message sends specific help information when a known command is requested.

        Verifies that when a help request for an existing command is received, the plugin responds with the correct command description.
        """
        mock_load_plugins.return_value = [
            self.mock_plugin1,
            self.mock_plugin2,
            self.mock_plugin3,
        ]
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help weather"

        async def run_test():
            """
            Asynchronously tests that requesting help for a specific command sends the correct help message.

            Verifies that invoking the help plugin with a specific command triggers sending a message containing the command and its description.
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_called_once()

            # Check the call arguments
            call_args = self.plugin.send_matrix_message.call_args
            message = call_args[0][1]

            # Should contain specific help for weather command
            self.assertIn("`!weather`:", message)
            self.assertIn("Show weather forecast", message)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_specific_help_not_found(self, mock_load_plugins):
        """
        Test that requesting help for a nonexistent command results in an appropriate error message.

        Verifies that when a specific help command is issued for a command that does not exist, the plugin responds with an error message indicating the command was not found.
        """
        mock_load_plugins.return_value = [
            self.mock_plugin1,
            self.mock_plugin2,
            self.mock_plugin3,
        ]
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help nonexistent"

        async def run_test():
            """
            Asynchronously tests that requesting help for a nonexistent command returns an appropriate error message.

            Verifies that the help plugin responds with "No such command: nonexistent" and sends a Matrix message when a nonexistent command is queried.
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            self.plugin.send_matrix_message.assert_called_once()

            # Check the call arguments
            call_args = self.plugin.send_matrix_message.call_args
            message = call_args[0][1]

            # Should contain error message
            self.assertEqual(message, "No such command: nonexistent")

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_multiple_commands_per_plugin(self, mock_load_plugins):
        """
        Test that handle_room_message lists all commands from plugins with multiple commands.

        Verifies that when the help command is invoked and plugins provide multiple commands, the help message includes all commands from all loaded plugins.
        """
        # Plugin with multiple commands
        multi_command_plugin = MagicMock()
        multi_command_plugin.get_matrix_commands.return_value = ["cmd1", "cmd2", "cmd3"]
        multi_command_plugin.description = "Multi-command plugin"

        mock_load_plugins.return_value = [multi_command_plugin, self.mock_plugin2]
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help"

        async def run_test():
            """
            Asynchronously tests that the help plugin's room message handler returns True and sends a message containing all available commands from loaded plugins.
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            call_args = self.plugin.send_matrix_message.call_args
            message = call_args[0][1]

            # Should contain all commands from all plugins
            self.assertIn("cmd1", message)
            self.assertIn("cmd2", message)
            self.assertIn("cmd3", message)
            self.assertIn("weather", message)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_specific_help_multi_command_plugin(
        self, mock_load_plugins
    ):
        """
        Test that requesting help for a specific command from a plugin with multiple commands returns the correct help message.

        Verifies that the help message includes the requested command and the plugin's description when a multi-command plugin is loaded.
        """
        # Plugin with multiple commands
        multi_command_plugin = MagicMock()
        multi_command_plugin.get_matrix_commands.return_value = ["cmd1", "cmd2", "cmd3"]
        multi_command_plugin.description = "Multi-command plugin"

        mock_load_plugins.return_value = [multi_command_plugin]
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help cmd2"

        async def run_test():
            """
            Runs an asynchronous test to verify that requesting help for a specific command returns the correct help message, including the command and its plugin description.
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            call_args = self.plugin.send_matrix_message.call_args
            message = call_args[0][1]

            # Should show help for cmd2
            self.assertIn("`!cmd2`:", message)
            self.assertIn("Multi-command plugin", message)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.help_plugin.load_plugins")
    def test_handle_room_message_no_plugins(self, mock_load_plugins):
        """
        Test that handle_room_message sends an empty command list message when no plugins are loaded.

        Verifies that when the help command is invoked and no plugins are available, the plugin responds with a message indicating no commands are present.
        """
        mock_load_plugins.return_value = []
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()
        full_message = "bot: !help"

        async def run_test():
            """
            Asynchronously tests that the help plugin returns an empty command list message when no plugins are loaded.

            Returns:
                None
            """
            result = await self.plugin.handle_room_message(room, event, full_message)

            self.assertTrue(result)
            call_args = self.plugin.send_matrix_message.call_args
            message = call_args[0][1]

            # Should show empty command list
            self.assertEqual(message, "Available commands: ")

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
