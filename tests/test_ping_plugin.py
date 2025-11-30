#!/usr/bin/env python3
"""
Test suite for the MMRelay ping plugin.

Tests the ping/pong functionality including:
- Case matching utility function
- Ping detection with various punctuation patterns
- Direct message vs broadcast handling
- Response delay and message routing
- Matrix room message handling
- Channel enablement checking
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from meshtastic.mesh_interface import BROADCAST_NUM

from mmrelay.plugins.ping_plugin import Plugin, match_case


class TestMatchCase(unittest.TestCase):
    """Test cases for the match_case utility function."""

    def test_match_case_all_lowercase(self):
        """Test match_case with all lowercase source."""
        result = match_case("ping", "pong")
        self.assertEqual(result, "pong")

    def test_match_case_all_uppercase(self):
        """
        Tests that match_case converts the target string to all uppercase when the source string is all uppercase.
        """
        result = match_case("PING", "pong")
        self.assertEqual(result, "PONG")

    def test_match_case_mixed_case(self):
        """
        Test that match_case adjusts the target string to match a mixed case source string.
        """
        result = match_case("PiNg", "pong")
        self.assertEqual(result, "PoNg")

    def test_match_case_first_letter_uppercase(self):
        """
        Tests that match_case returns a target string with its first letter capitalized to match a source string with an initial uppercase letter.
        """
        result = match_case("Ping", "pong")
        self.assertEqual(result, "Pong")

    def test_match_case_different_lengths(self):
        """
        Test that match_case returns a target string adjusted to the source's length and case when the strings have different lengths.
        """
        result = match_case("Pi", "pong")
        self.assertEqual(result, "Po")

    def test_match_case_empty_strings(self):
        """
        Test that match_case returns an empty string when the source string is empty.
        """
        result = match_case("", "pong")
        self.assertEqual(result, "")


class TestPingPlugin(unittest.TestCase):
    """Test cases for the ping plugin."""

    def setUp(self):
        """
        Initializes the Plugin instance and mocks its dependencies for isolated testing.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock the is_channel_enabled method
        self.plugin.is_channel_enabled = MagicMock(return_value=True)

        # Mock the get_response_delay method
        self.plugin.get_response_delay = MagicMock(return_value=1.0)

        # Mock Matrix client methods
        self.plugin.send_matrix_message = AsyncMock()

    def test_plugin_name(self):
        """
        Verify that the plugin's name attribute is set to "ping".
        """
        self.assertEqual(self.plugin.plugin_name, "ping")

    def test_description_property(self):
        """
        Verify that the plugin's description property returns the expected string.
        """
        description = self.plugin.description
        self.assertEqual(
            description,
            "Check connectivity with the relay or respond to pings over the mesh",
        )

    def test_get_matrix_commands(self):
        """
        Test that the plugin's get_matrix_commands method returns a list containing the "ping" command.
        """
        commands = self.plugin.get_matrix_commands()
        self.assertEqual(commands, ["ping"])

    def test_get_mesh_commands(self):
        """
        Test that the plugin's get_mesh_commands method returns a list containing the "ping" command.
        """
        commands = self.plugin.get_mesh_commands()
        self.assertEqual(commands, ["ping"])

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_missing_myinfo(self, mock_connect):
        """
        Ensure the handler exits cleanly when myInfo is missing.
        """

        mock_client = MagicMock()
        mock_client.myInfo = None
        mock_client.nodes = {}
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test():
            """
            Verify that handle_meshtastic_message does not process a packet when the Meshtastic client lacks `myInfo`.

            This test awaits plugin.handle_meshtastic_message with the provided `packet` and asserts that it returns `False`, indicating no handling occurred for a client with missing `myInfo`.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_simple_ping_broadcast(
        self, mock_sleep, mock_connect
    ):
        """
        Test that a simple "ping" broadcast message is correctly handled by the plugin.

        Verifies that the plugin checks channel enablement, waits for the configured response delay, sends a "pong" broadcast response on the same channel, logs the processing, and returns True to indicate the message was handled.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Asynchronously tests that the plugin correctly handles a Meshtastic broadcast ping message.

            Verifies that the plugin checks channel enablement, waits for the configured response delay, sends a broadcast "pong" response, and logs the processing. Asserts that the message handling result is True.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertTrue(result)

            # Should check channel enablement for broadcast
            self.plugin.is_channel_enabled.assert_called_once_with(
                0, is_direct_message=False
            )

            # Should wait for response delay
            mock_sleep.assert_called_once_with(1.0)

            # Should send broadcast response
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

            # Should log processing
            self.plugin.logger.info.assert_called_once()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_ping_direct_message(
        self, mock_sleep, mock_connect
    ):
        """
        Test that the plugin correctly handles a "ping" message received as a direct Meshtastic message.

        Verifies that the plugin checks channel enablement for direct messages, sends a direct "pong" response to the sender, and returns True to indicate the message was handled.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "ping"},
            "channel": 1,
            "fromId": "!12345678",
            "to": 123456789,  # Direct to relay
        }

        async def run_test():
            """
            Asynchronously tests that the plugin correctly handles a direct "ping" Meshtastic message.

            Verifies that the plugin checks channel enablement for direct messages, sends a direct "pong" response to the sender, and returns True to indicate the message was handled.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertTrue(result)

            # Should check channel enablement for direct message
            self.plugin.is_channel_enabled.assert_called_once_with(
                1, is_direct_message=True
            )

            # Should send direct message response
            mock_client.sendText.assert_called_once_with(
                text="pong", destinationId="!12345678"
            )

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_ping_with_punctuation(
        self, mock_sleep, mock_connect
    ):
        """
        Test that a Meshtastic "ping" message with punctuation receives a "pong" response preserving the original punctuation.

        Verifies that the plugin responds to a ping message containing punctuation (e.g., "!ping!") by sending a pong message with matching punctuation (e.g., "!pong!"), and confirms the response is sent on the correct channel.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!ping!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin responds to a Meshtastic ping message with a correctly punctuated pong response.

            Returns:
                bool: True if the plugin handled the message as expected.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertTrue(result)

            # Should preserve punctuation in response
            mock_client.sendText.assert_called_once_with(text="!pong!", channelIndex=0)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_ping_excessive_punctuation(
        self, mock_sleep, mock_connect
    ):
        """
        Test that the plugin responds with "Pong..." when handling a Meshtastic ping message containing excessive punctuation.

        Verifies that when a ping message with more than five punctuation characters is received, the plugin sends a "Pong..." response on the same channel.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "!!!ping!!!"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin responds with "Pong..." when handling a ping message containing excessive punctuation.

            Returns:
                bool: True if the plugin handled the message as expected.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertTrue(result)

            # Should use "Pong..." for excessive punctuation (>5 chars)
            mock_client.sendText.assert_called_once_with(text="Pong...", channelIndex=0)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_ping_case_matching(
        self, mock_sleep, mock_connect
    ):
        """
        Tests that the plugin responds to a Meshtastic "PING" message with a "PONG" reply that matches the original message's case.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "PING"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,  # BROADCAST_NUM
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin responds to a Meshtastic "ping" message with a correctly cased "PONG" response.

            Returns:
                bool: True if the plugin handled the message as expected.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertTrue(result)

            # Should match case of original ping
            mock_client.sendText.assert_called_once_with(text="PONG", channelIndex=0)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_no_ping(self, mock_connect):
        """
        Test that the plugin does not respond to Meshtastic messages that do not contain a ping command.

        Verifies that no message is sent and the handler returns False when the incoming message lacks a ping trigger.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"text": "Hello world"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,
        }

        async def run_test():
            """
            Asynchronously runs a test to verify that no response is sent when handling a Meshtastic message that should not trigger a reply.

            Returns:
                None
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertFalse(result)

            # Should not send any message
            mock_client.sendText.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_meshtastic_message_channel_disabled(self, mock_connect):
        """
        Test that no response is sent when handling a ping message on a disabled channel.

        Verifies that the plugin does not respond to a "ping" Meshtastic message if the channel is disabled, and that no message is sent.
        """
        # Mock meshtastic client
        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        # Mock channel as disabled
        self.plugin.is_channel_enabled = MagicMock(return_value=False)

        packet = {
            "decoded": {"text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,
        }

        async def run_test():
            """
            Asynchronously runs a test to verify that no response is sent when handling a Meshtastic message that should not trigger a reply.

            Returns:
                None
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertFalse(result)

            # Should not send any message
            mock_client.sendText.assert_not_called()

        asyncio.run(run_test())

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False and does not send a message when the event does not match the plugin's criteria.
        """
        self.plugin.matches = MagicMock(return_value=False)

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that no Matrix message is sent when the event does not match the plugin's criteria.

            Returns:
                bool: False, indicating the message was not handled.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_not_called()

        asyncio.run(run_test())

    def test_handle_room_message_ping_match(self):
        """
        Tests that handle_room_message sends a "pong!" response when a ping command is detected in a Matrix room message.
        """
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin responds to a ping command in a Matrix room message.

            Asserts that the plugin correctly matches the event, sends a "pong!" response to the specified Matrix room, and returns True.
            """
            result = await self.plugin.handle_room_message(room, event, "bot: !ping")

            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_called_once_with(
                "!test:matrix.org", "pong!"
            )

        asyncio.run(run_test())

    def test_handle_meshtastic_message_no_decoded(self):
        """
        Test that the plugin does not handle a Meshtastic packet missing the "decoded" field.

        Verifies that when the "decoded" field is absent from the packet, the handler returns False, indicating the message was not processed.
        """
        packet = {"channel": 0, "fromId": "!12345678", "to": 4294967295}

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin does not handle a given Meshtastic message.

            Returns:
                bool: False, indicating the message was not handled by the plugin.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertFalse(result)

        asyncio.run(run_test())

    def test_handle_meshtastic_message_no_text(self):
        """
        Test that the plugin does not handle a Meshtastic packet missing the "text" field in the "decoded" section.

        Verifies that no response is sent and the handler returns False.
        """
        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP"},
            "channel": 0,
            "fromId": "!12345678",
            "to": 4294967295,
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that the plugin does not handle a given Meshtastic message.

            Returns:
                bool: False, indicating the message was not handled by the plugin.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )

            self.assertFalse(result)

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    @patch("asyncio.sleep")
    def test_handle_meshtastic_message_broadcast_num(self, mock_sleep, mock_connect):
        """Test handle_meshtastic_message with BROADCAST_NUM."""

        mock_client = MagicMock()
        mock_client.myInfo.my_node_num = 123456789
        mock_connect.return_value = mock_client

        packet = {
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "ping"},
            "channel": 0,
            "fromId": "!12345678",
            "to": BROADCAST_NUM,
        }

        async def run_test():
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertTrue(result)  # Should handle broadcast ping
            # For broadcast messages, plugin sends Meshtastic reply, not Matrix message
            mock_client.sendText.assert_called_once_with(text="pong", channelIndex=0)

        asyncio.run(run_test())
        mock_sleep.assert_called()  # ensure delay path exercised if applicable


if __name__ == "__main__":
    unittest.main()
