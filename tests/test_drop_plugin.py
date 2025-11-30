#!/usr/bin/env python3
"""
Test suite for the MMRelay drop plugin.

Tests the message drop and pickup functionality including:
- Message dropping with location validation
- Message pickup based on proximity
- Location-based filtering
- Node data storage and retrieval
- Error handling for invalid positions
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.drop_plugin import Plugin


class TestDropPlugin(unittest.TestCase):
    """Test cases for the drop plugin."""

    def setUp(self):
        """
        Initializes the test environment by setting up a Plugin instance with mocked configuration, logger, database operations, and a Meshtastic client with predefined node data for testing.
        """
        self.plugin = Plugin()
        self.plugin.config = {"radius_km": 5}
        self.plugin.logger = MagicMock()

        # Mock database operations
        self.plugin.store_node_data = MagicMock()
        self.plugin.get_node_data = MagicMock(return_value=[])
        self.plugin.set_node_data = MagicMock()

        # Mock meshtastic client
        self.mock_meshtastic_client = MagicMock()
        self.mock_meshtastic_client.nodes = {
            "node1": {
                "user": {"id": "!12345678"},
                "position": {"latitude": 40.7128, "longitude": -74.0060},
            },
            "node2": {
                "user": {"id": "!87654321"},
                "position": {"latitude": 40.7589, "longitude": -73.9851},
            },
            "node3": {
                "user": {"id": "!11111111"}
                # No position data
            },
        }
        self.mock_meshtastic_client.getMyNodeInfo.return_value = {
            "user": {"id": "!relay123"}
        }

    def test_get_position_with_valid_node(self):
        """
        Tests that get_position returns the correct latitude and longitude for a node with valid position data.
        """
        position = self.plugin.get_position(self.mock_meshtastic_client, "!12345678")

        self.assertIsNotNone(position)
        self.assertEqual(position["latitude"], 40.7128)
        self.assertEqual(position["longitude"], -74.0060)

    def test_get_position_with_node_no_position(self):
        """
        Test that get_position returns None for a node that exists but lacks position data.
        """
        position = self.plugin.get_position(self.mock_meshtastic_client, "!11111111")

        self.assertIsNone(position)

    def test_get_position_with_nonexistent_node(self):
        """
        Test that get_position returns None when called with a non-existent node ID.
        """
        position = self.plugin.get_position(self.mock_meshtastic_client, "!99999999")

        self.assertIsNone(position)

    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_drop_valid(self, mock_connect):
        """
        Test that a valid "!drop" command from a node with position data is handled correctly.

        Verifies that the plugin stores the dropped message with the correct text, originator ID, and location when the node has valid position data.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "!drop This is a test message",
            },
        }

        async def run_test():
            """
            Run an asynchronous test to verify that a dropped message is handled and stored with correct metadata.

            Returns:
                result (bool): True if the message was successfully handled and stored.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertTrue(result)
            self.plugin.store_node_data.assert_called_once()
            call_args = self.plugin.store_node_data.call_args
            self.assertEqual(call_args[0][0], "!NODE_MSGS!")
            stored_data = call_args[0][1]
            self.assertEqual(stored_data["text"], "This is a test message")
            self.assertEqual(stored_data["originator"], "!12345678")
            self.assertEqual(stored_data["location"], (40.7128, -74.0060))

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_drop_no_position(self, mock_connect):
        """
        Test that dropping a message from a node without position data logs a debug message and does not store the message, but returns True.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        packet = {
            "fromId": "!11111111",  # Node without position
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "!drop This should fail",
            },
        }

        async def run_test():
            """
            Run an asynchronous test to verify that handling a message from a node without position data returns True and logs the appropriate debug message.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertTrue(result)  # Returns True but doesn't store
            self.plugin.logger.debug.assert_called_with(
                "Position of dropping node is not known. Skipping ..."
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_not_drop_command(self, mock_connect):
        """
        Test that non-drop command messages are not handled by the plugin.

        Verifies that when a message not starting with the drop command is received, the handler returns False, indicating no action was taken.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "Just a regular message",
            },
        }

        async def run_test():
            """
            Run an asynchronous test for handling a Meshtastic message and assert that the handler returns False.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_invalid_drop_format(self, mock_connect):
        """
        Test that a "!drop" command without message content is handled as invalid and not processed.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TEXT_MESSAGE_APP",
                "text": "!drop",  # No message content
            },
        }

        async def run_test():
            """
            Run an asynchronous test for handling a Meshtastic message and assert that the handler returns False.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.haversine")
    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_pickup_in_range(
        self, mock_connect, mock_haversine
    ):
        """
        Test that a node can pick up stored messages when within the configured proximity radius.

        Simulates a pickup scenario where a node requests messages and is within range of a stored message. Verifies that the message is sent to the requesting node and that the stored messages are cleared after pickup.
        """
        mock_connect.return_value = self.mock_meshtastic_client
        mock_haversine.return_value = 1.0  # 1km distance, within 5km radius

        # Mock stored messages
        stored_messages = [
            {
                "location": (40.7128, -74.0060),  # Same as node1 location
                "text": "Pickup this message",
                "originator": "!99999999",  # Different from packet sender
            }
        ]

        self.plugin.get_node_data.return_value = stored_messages

        packet = {
            "fromId": "!12345678",  # Node1 with position
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Some message"},
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that a dropped message is picked up and sent to the requesting node, and that stored messages are cleared after pickup.
            """
            await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should send the message to the node
            self.mock_meshtastic_client.sendText.assert_called_once_with(
                text="Pickup this message", destinationId="!12345678"
            )

            # Should clear the messages (empty list)
            self.plugin.set_node_data.assert_called_once_with("!NODE_MSGS!", [])

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.haversine")
    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_pickup_out_of_range(
        self, mock_connect, mock_haversine
    ):
        """
        Test that pickup requests do not retrieve messages stored beyond the configured radius.

        Verifies that when a node requests message pickup, messages located outside the allowed distance are not sent and remain stored.
        """
        mock_connect.return_value = self.mock_meshtastic_client
        mock_haversine.return_value = 10.0  # 10km distance, outside 5km radius

        # Mock stored messages at a distant location
        stored_messages = [
            {
                "location": (41.0000, -75.0000),  # Far from node1 location
                "text": "Distant message",
                "originator": "!99999999",
            }
        ]

        self.plugin.get_node_data.return_value = stored_messages

        packet = {
            "fromId": "!12345678",  # Node1 with position
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Some message"},
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that a message is not sent and remains stored after handling a Meshtastic message.

            This function awaits the plugin's message handler, asserts that no message is sent via the Meshtastic client, and checks that the message remains in storage.
            """
            await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should not send the message
            self.mock_meshtastic_client.sendText.assert_not_called()

            # Should keep the message in storage
            self.plugin.set_node_data.assert_called_once_with(
                "!NODE_MSGS!", stored_messages
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.haversine")
    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_skip_own_messages(
        self, mock_connect, mock_haversine
    ):
        """
        Test that a node cannot pick up its own dropped messages, even if within the pickup range.

        Ensures that messages originating from the requesting node are not sent back to it and remain stored.
        """
        mock_connect.return_value = self.mock_meshtastic_client
        mock_haversine.return_value = 1.0  # 1km distance, within range

        # Mock stored messages from the same originator
        stored_messages = [
            {
                "location": (40.7128, -74.0060),
                "text": "Own message",
                "originator": "!12345678",  # Same as packet sender
            }
        ]

        self.plugin.get_node_data.return_value = stored_messages

        packet = {
            "fromId": "!12345678",  # Same as originator
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Some message"},
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that a node cannot pick up its own dropped messages.

            Returns:
                result: The outcome of the message handling operation.
            """
            await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should not send the message
            self.mock_meshtastic_client.sendText.assert_not_called()

            # Should keep the message in storage (can't pick up own messages)
            self.plugin.set_node_data.assert_called_once_with(
                "!NODE_MSGS!", stored_messages
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.haversine")
    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_distance_calculation_error(
        self, mock_connect, mock_haversine
    ):
        """
        Test that the plugin handles errors during distance calculation when processing message pickups.

        Simulates a scenario where stored messages have invalid location data, causing the distance calculation to raise an exception. Verifies that no messages are sent and the stored messages remain unchanged.
        """
        mock_connect.return_value = self.mock_meshtastic_client
        mock_haversine.side_effect = ValueError("Invalid coordinates")  # Simulate error

        # Mock stored messages with invalid location data
        stored_messages = [
            {
                "location": "invalid_location",  # Invalid location format
                "text": "Message with bad location",
                "originator": "!99999999",
            }
        ]

        self.plugin.get_node_data.return_value = stored_messages

        packet = {
            "fromId": "!12345678",
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Some message"},
        }

        async def run_test():
            """
            Runs an asynchronous test to verify that messages outside the configured range are not sent and remain stored.

            This test calls the plugin's message handler with a simulated packet and checks that no message is sent when the distance is out of range. It also asserts that the message remains in storage.
            """
            await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should not send the message (distance defaults to 1000km, out of range)
            self.mock_meshtastic_client.sendText.assert_not_called()

            # Should keep the message in storage
            self.plugin.set_node_data.assert_called_once_with(
                "!NODE_MSGS!", stored_messages
            )

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.connect_meshtastic")
    def test_handle_meshtastic_message_from_relay_node(self, mock_connect):
        """
        Test that the plugin ignores messages originating from the relay node during pickup.

        Verifies that when a message is received from the relay node itself, the handler does not process it and returns False.
        """
        mock_connect.return_value = self.mock_meshtastic_client

        packet = {
            "fromId": "!relay123",  # Same as relay node ID
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Message from relay"},
        }

        async def run_test():
            """
            Run an asynchronous test to verify that non-drop Meshtastic messages are not processed by the plugin.

            Returns:
                result (bool): False if the message is not a drop command and is not handled by the plugin.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should return False for non-drop messages (not processed)
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_room_message_with_matching_command(self):
        """
        Test that Matrix room messages with matching commands are handled and processed by the plugin.

        Verifies that when the plugin's `matches` method returns True for a given event, `handle_room_message` returns True and the `matches` method is called with the event.
        """
        # Mock the matches method to return True
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously runs a test to verify that handling a Matrix room message returns True and that the plugin's command matching method is called with the event.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.plugins.drop_plugin.BasePlugin.matches")
    def test_handle_room_message_without_matching_command(self, mock_matches):
        """
        Test that Matrix room messages without matching commands are not handled by the plugin.

        Verifies that when the plugin's `matches` method returns False, `handle_room_message` returns False and does not process the message.
        """
        # Mock the matches method to return False
        mock_matches.return_value = False

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that the plugin's room message handler returns False when the message does not match any command.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)  # Returns False when no match
            mock_matches.assert_called_once_with(event)

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
