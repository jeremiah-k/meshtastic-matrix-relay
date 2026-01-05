#!/usr/bin/env python3
"""
Additional test suite for uncovered lines in telemetry plugin in MMRelay.

This file contains tests for previously uncovered code paths including:
- Data list conversion in handle_meshtastic_message (lines 98-99)
- No node data path in calculate_averages (line 203)
- No telemetry data message (line 210)
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.telemetry_plugin import Plugin


class TestTelemetryPluginDataHandling(unittest.TestCase):
    """Test cases for data list conversion in telemetry plugin."""

    def setUp(self):
        """Initialize plugin and mocks."""
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock database operations
        self.plugin.get_node_data = MagicMock()
        self.plugin.set_node_data = MagicMock()

    @patch("mmrelay.plugins.telemetry_plugin.plugin_loader")
    def test_handle_meshtastic_message_single_record_to_list(self, mock_plugin_loader):
        """Test handle_meshtastic_message converts single record to list."""
        # Mock load_plugins to return empty list
        mock_loader = MagicMock()
        mock_loader.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_loader

        # Return single record (not a list)
        single_record = {
            "time": 1234567890,
            "batteryLevel": 85,
            "voltage": 4.1,
            "airUtilTx": 0.1,
        }
        self.plugin.get_node_data.return_value = single_record

        packet = {
            "fromId": 123456789,
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "deviceMetrics": {
                        "batteryLevel": 80,
                        "voltage": 4.0,
                        "airUtilTx": 0.2,
                    }
                },
                "time": 1234567890,
            },
        }

        # Run the handler
        result = asyncio.run(
            self.plugin.handle_meshtastic_message(
                packet,
                "test message",
                "TestNode",
                "test_mesh",
            )
        )

        # Should convert single record to list and append
        self.assertFalse(result)
        self.plugin.set_node_data.assert_called_once()

        # Verify the data was converted to list
        call_args = self.plugin.set_node_data.call_args
        telemetry_data = call_args[0][1]  # Second positional arg (node_data)

        # Should be a list now
        self.assertIsInstance(telemetry_data, list)
        self.assertEqual(len(telemetry_data), 2)  # Original + new

    @patch("mmrelay.plugins.telemetry_plugin.plugin_loader")
    def test_handle_meshtastic_message_dict_to_list(self, mock_plugin_loader):
        """Test handle_meshtastic_message handles dict data correctly."""
        # Mock load_plugins to return empty list
        mock_loader = MagicMock()
        mock_loader.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_loader

        # Return dict (should be wrapped in list)
        dict_data = {
            "time": 1234567890,
            "batteryLevel": 85,
            "voltage": 4.1,
            "airUtilTx": 0.1,
        }
        self.plugin.get_node_data.return_value = dict_data

        packet = {
            "fromId": 123456789,
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "deviceMetrics": {
                        "batteryLevel": 80,
                        "voltage": 4.0,
                        "airUtilTx": 0.2,
                    }
                },
                "time": 1234567890,
            },
        }

        # Run the handler
        result = asyncio.run(
            self.plugin.handle_meshtastic_message(
                packet,
                "test message",
                "TestNode",
                "test_mesh",
            )
        )

        # Should convert dict to list and append
        self.assertFalse(result)
        self.plugin.set_node_data.assert_called_once()

        # Verify the data was converted to list
        call_args = self.plugin.set_node_data.call_args
        telemetry_data = call_args[0][1]

        # Should be a list now
        self.assertIsInstance(telemetry_data, list)


class TestTelemetryPluginNoNodeData(unittest.TestCase):
    """Test cases for handling missing node data in telemetry plugin."""

    def setUp(self):
        """Initialize plugin and mocks."""
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock database operations
        self.plugin.get_node_data = MagicMock()
        self.plugin.set_node_data = MagicMock()
        self.plugin.get_data = MagicMock()

        # Mock Matrix client and methods
        self.plugin.send_matrix_message = AsyncMock()
        from nio import MatrixRoom

        self.mock_room = MagicMock(spec=MatrixRoom)
        self.mock_room.room_id = "!test:matrix.org"

    @patch("mmrelay.plugins.telemetry_plugin.plugin_loader")
    @patch("mmrelay.plugins.telemetry_plugin.connect_matrix")
    async def test_handle_room_message_no_node_data(
        self, mock_connect_matrix, mock_plugin_loader
    ):
        """Test handle_room_message sends notice when node has no data."""
        # Mock load_plugins to return empty list
        mock_loader = MagicMock()
        mock_loader.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_loader

        # Mock connect_matrix to return None
        mock_connect_matrix.return_value = None

        # Return no data for node
        self.plugin.get_node_data.return_value = None

        # Mock Matrix event
        from nio import RoomMessageText

        mock_event = MagicMock(spec=RoomMessageText)
        mock_event.body = "batteryLevel TestNode"

        # Mock get_matching_matrix_command to return a match
        self.plugin.get_matching_matrix_command = MagicMock(return_value="batteryLevel")
        self.plugin.extract_command_args = MagicMock(return_value="TestNode")

        # Run the handler
        result = await self.plugin.handle_room_message(
            self.mock_room,
            mock_event,
            "batteryLevel TestNode",
        )

        # Should return False (no graph generated)
        self.assertFalse(result)

        # Should have sent "No telemetry data found" message
        self.plugin.send_matrix_message.assert_called_once()
        call_args = self.plugin.send_matrix_message.call_args

        # Verify the correct message was sent
        sent_message = call_args[0][2]  # Third positional arg
        self.assertIn("No telemetry data found", sent_message)
        self.assertIn("TestNode", sent_message)

        # Should not call send_image
        # (implicitly verified by return value and message check)

    @patch("mmrelay.plugins.telemetry_plugin.plugin_loader")
    @patch("mmrelay.plugins.telemetry_plugin.connect_matrix")
    async def test_handle_room_message_empty_list_node_data(
        self, mock_connect_matrix, mock_plugin_loader
    ):
        """Test handle_room_message sends notice when node data is empty list."""
        # Mock load_plugins to return empty list
        mock_loader = MagicMock()
        mock_loader.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_loader

        # Mock connect_matrix to return None
        mock_connect_matrix.return_value = None

        # Return empty list for node
        self.plugin.get_node_data.return_value = []

        # Mock Matrix event
        from nio import RoomMessageText

        mock_event = MagicMock(spec=RoomMessageText)
        mock_event.body = "voltage TestNode"

        # Mock get_matching_matrix_command
        self.plugin.get_matching_matrix_command = MagicMock(return_value="voltage")
        self.plugin.extract_command_args = MagicMock(return_value="TestNode")

        # Run the handler
        result = await self.plugin.handle_room_message(
            self.mock_room,
            mock_event,
            "voltage TestNode",
        )

        # Should return False
        self.assertFalse(result)

        # Should have sent "No telemetry data found" message
        self.plugin.send_matrix_message.assert_called_once()
        call_args = self.plugin.send_matrix_message.call_args

        # Verify the correct message was sent
        sent_message = call_args[0][2]
        self.assertIn("No telemetry data found", sent_message)
        self.assertIn("TestNode", sent_message)


class TestTelemetryPluginCalculateAveragesNoData(unittest.TestCase):
    """Test cases for calculate_averages when no node data."""

    def setUp(self):
        """Initialize plugin and mocks."""
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock database operations
        self.plugin.get_node_data = MagicMock()
        self.plugin.set_node_data = MagicMock()

        # Mock Matrix client and methods
        self.plugin.send_matrix_message = AsyncMock()
        from nio import MatrixRoom

        self.mock_room = MagicMock(spec=MatrixRoom)
        self.mock_room.room_id = "!test:matrix.org"

    @patch("mmrelay.plugins.telemetry_plugin.plugin_loader")
    @patch("mmrelay.plugins.telemetry_plugin.connect_matrix")
    @patch("mmrelay.plugins.telemetry_plugin.datetime")
    async def test_handle_room_message_network_no_data(
        self, mock_datetime, mock_connect_matrix, mock_plugin_loader
    ):
        """Test handle_room_message with network-wide request and no data."""
        # Mock load_plugins to return empty list
        mock_loader = MagicMock()
        mock_loader.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_loader

        # Mock connect_matrix to return None
        mock_connect_matrix.return_value = None

        # Mock get_data to return empty list (no node data)
        self.plugin.get_data.return_value = []

        # Mock datetime for time generation
        from datetime import datetime as dt

        mock_now = dt(2024, 1, 15, 12, 0, 0)
        mock_datetime.now.return_value = mock_now
        mock_datetime.fromtimestamp = dt.fromtimestamp

        # Mock Matrix event for network-wide request (no node specified)
        from nio import RoomMessageText

        mock_event = MagicMock(spec=RoomMessageText)
        mock_event.body = "batteryLevel"

        # Mock get_matching_matrix_command (no args means network)
        self.plugin.get_matching_matrix_command = MagicMock(return_value="batteryLevel")
        self.plugin.extract_command_args = MagicMock(return_value="")

        # Run the handler
        result = await self.plugin.handle_room_message(
            self.mock_room,
            mock_event,
            "batteryLevel",
        )

        # Should return False (no graph generated)
        self.assertFalse(result)

        # Should NOT have sent message (network request with no data doesn't send message)
        # The path at line 203 is only triggered when a specific node is requested
        # and that node has no data
        self.plugin.send_matrix_message.assert_not_called()


if __name__ == "__main__":
    unittest.main()
