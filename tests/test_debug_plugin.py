#!/usr/bin/env python3
"""
Test suite for the MMRelay debug plugin.

Tests the debug plugin functionality including:
- Packet logging and debugging
- Raw data stripping
- Message handling without interception
- Priority and configuration
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.debug_plugin import Plugin


@pytest.mark.usefixtures("mock_event_loop")
class TestDebugPlugin(unittest.TestCase):
    """Test cases for the debug plugin functionality."""

    def setUp(self):
        """
        Initialize a Plugin instance and replace its logger with a MagicMock before each test.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

    def test_plugin_name(self):
        """
        Verify that the plugin's name is set to "debug".
        """
        self.assertEqual(self.plugin.plugin_name, "debug")

    def test_plugin_priority(self):
        """
        Verify that the debug plugin's priority is set to 1, ensuring it executes early in the plugin chain.
        """
        self.assertEqual(self.plugin.priority, 1)

    def test_description_property(self):
        """Test that the plugin has a description."""
        # The debug plugin doesn't override the description property,
        # so it should return the default empty string from BasePlugin
        description = self.plugin.description
        self.assertEqual(description, "")

    @patch.object(Plugin, "strip_raw")
    def test_handle_meshtastic_message_logs_packet(self, mock_strip_raw):
        """
        Verify that handle_meshtastic_message logs the cleaned packet after stripping raw data and returns False.

        This test mocks the strip_raw method to ensure the cleaned packet is logged and confirms that the message handler does not intercept messages.
        """
        # Mock the strip_raw method to return a cleaned packet
        cleaned_packet = {"decoded": {"text": "test message"}, "fromId": "!12345678"}
        mock_strip_raw.return_value = cleaned_packet

        # Test packet
        original_packet = {
            "decoded": {"text": "test message", "raw": b"binary_data"},
            "fromId": "!12345678",
        }

        async def run_test():
            """
            Execute an asynchronous test for the debug plugin's Meshtastic message handler, verifying packet processing, logging, and return value.
            """
            result = await self.plugin.handle_meshtastic_message(
                original_packet, "formatted_message", "TestNode", "TestMesh"
            )

            # Should call strip_raw with the original packet
            mock_strip_raw.assert_called_once_with(original_packet)

            # Should log the cleaned packet
            self.plugin.logger.debug.assert_called_once_with(
                f"Packet received: {cleaned_packet}"
            )

            # Should return False (never intercepts messages)
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_returns_false(self):
        """
        Test that handle_meshtastic_message always returns False, confirming the plugin never intercepts messages.
        """
        packet = {"decoded": {"text": "test message"}, "fromId": "!12345678"}

        async def run_test():
            """
            Run an asynchronous test to verify that the plugin's handle_meshtastic_message method returns False for a given packet.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_room_message_returns_false(self):
        """
        Test that the debug plugin's handle_room_message method always returns False, confirming it never intercepts room messages.
        """
        room = MagicMock()
        event = MagicMock()
        full_message = "test message"

        async def run_test():
            """
            Asynchronously tests that the plugin's handle_room_message method does not intercept messages.

            Returns:
                None
            """
            result = await self.plugin.handle_room_message(room, event, full_message)
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_with_empty_packet(self):
        """
        Test that the plugin processes an empty packet without errors and logs it.

        Verifies that `handle_meshtastic_message` logs empty packets and returns False, ensuring graceful handling of minimal input.
        """
        empty_packet = {}

        async def run_test():
            """
            Asynchronously tests that handling an empty packet logs the packet and returns False.
            """
            result = await self.plugin.handle_meshtastic_message(
                empty_packet, "", "", ""
            )

            # Should still log the packet (even if empty)
            self.plugin.logger.debug.assert_called_once()

            # Should return False
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_with_complex_packet(self):
        """
        Verify that the plugin correctly logs and processes a complex Meshtastic packet with nested and binary data, and always returns False to indicate no message interception.
        """
        complex_packet = {
            "decoded": {
                "text": "complex message",
                "portnum": 1,
                "payload": b"binary_payload",
            },
            "fromId": "!12345678",
            "toId": "!87654321",
            "channel": 0,
            "hopLimit": 3,
            "wantAck": True,
            "priority": 10,
            "rxTime": 1234567890,
            "rxSnr": -5.5,
            "rxRssi": -95,
        }

        async def run_test():
            """
            Asynchronously tests that the debug plugin logs a complex Meshtastic packet and does not intercept the message.

            This function verifies that the plugin's logger is called when handling a complex packet and that the method returns False, indicating the message is not intercepted.
            """
            result = await self.plugin.handle_meshtastic_message(
                complex_packet, "formatted_message", "TestNode", "TestMesh"
            )

            # Should log the packet
            self.plugin.logger.debug.assert_called_once()

            # Should return False
            self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    def test_plugin_never_intercepts_messages(self):
        """
        Verify that the debug plugin's message handling methods always return False, ensuring the plugin never intercepts messages and allows further processing by other plugins.
        """
        # This is a behavioral test to ensure the plugin always returns False
        # from both message handling methods, which is critical for its role as a debug tool

        packet = {"decoded": {"text": "!important_command"}, "fromId": "!12345678"}
        room = MagicMock()
        event = MagicMock()

        async def run_test():
            # Test meshtastic message handling
            """
            Asynchronously tests that the plugin's message handling methods do not intercept messages.

            Runs both Meshtastic and Matrix room message handling methods and asserts that they always return False, ensuring the plugin allows message processing to continue.
            """
            meshtastic_result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "TestNode", "TestMesh"
            )
            self.assertFalse(meshtastic_result)

            # Test matrix room message handling
            matrix_result = await self.plugin.handle_room_message(
                room, event, "!important_command"
            )
            self.assertFalse(matrix_result)

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
