#!/usr/bin/env python3
"""
Test suite for the MMRelay health plugin.

Tests the mesh health statistics functionality including:
- Battery level statistics (average, median, low battery count)
- Air utilization statistics
- SNR (Signal-to-Noise Ratio) statistics
- Node counting and health reporting
- Matrix room message handling
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.health_plugin import Plugin


class TestHealthPlugin(unittest.TestCase):
    """Test cases for the health plugin."""

    def setUp(self):
        """
        Initializes the Plugin instance and prepares sample node data for health statistics tests.

        Sets up mocked logger and Matrix message sending methods, and defines a variety of node data scenarios including normal, low battery, missing metrics, and None values.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock Matrix client methods
        self.plugin.send_matrix_message = AsyncMock()

        # Create sample node data for testing
        self.sample_nodes = {
            "node1": {
                "deviceMetrics": {"batteryLevel": 85, "airUtilTx": 12.5},
                "snr": 10.2,
            },
            "node2": {
                "deviceMetrics": {"batteryLevel": 45, "airUtilTx": 8.3},
                "snr": -5.1,
            },
            "node3": {
                "deviceMetrics": {"batteryLevel": 5, "airUtilTx": 15.7},  # Low battery
                "snr": 2.8,
            },
            "node4": {
                # Node with missing deviceMetrics
                "snr": 7.5
            },
            "node5": {
                "deviceMetrics": {
                    "batteryLevel": 92
                    # Missing airUtilTx
                },
                "snr": None,  # None SNR value
            },
        }

    def test_plugin_name(self):
        """
        Test that the plugin's name attribute is set to "health".
        """
        self.assertEqual(self.plugin.plugin_name, "health")

    def test_description_property(self):
        """
        Test that the plugin's description property returns the expected summary string.
        """
        description = self.plugin.description
        self.assertEqual(
            description, "Show mesh health using avg battery, SNR, AirUtil"
        )

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_full_data(self, mock_connect):
        """
        Verifies that the plugin generates a correct health summary response when provided with complete node data.

        Checks that the response includes accurate node count, battery statistics (average, median, low battery count), air utilization statistics (average, median), and SNR statistics (average, median), with values rounded as expected.
        """
        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = self.sample_nodes
        mock_connect.return_value = mock_meshtastic_client

        response = self.plugin.generate_response()

        # Should contain node count
        self.assertIn("Nodes: 5", response)

        # Should contain battery statistics
        # Battery levels: [85, 45, 5, 92] -> avg=56.75, median=65.0
        self.assertIn("Battery: 56.8%", response)  # Rounded to 1 decimal
        self.assertIn("65.0%", response)  # Median

        # Should contain low battery count (< 10%)
        self.assertIn("Nodes with Low Battery (< 10): 1", response)

        # Should contain air util statistics
        # Air util: [12.5, 8.3, 15.7] -> avg=12.17, median=12.5
        self.assertIn("Air Util: 12.17", response)
        self.assertIn("12.50", response)

        # Should contain SNR statistics
        # SNR: [10.2, -5.1, 2.8, 7.5] (None filtered out) -> avg=3.85, median=5.15
        self.assertIn("SNR: 3.85", response)
        self.assertIn("5.15", response)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_empty_nodes(self, mock_connect):
        """
        Test that generating a response with no nodes returns a graceful string response.

        Verifies that the plugin handles empty node data gracefully and returns "No nodes discovered yet."
        instead of raising an exception when no nodes are available.
        """
        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = {}
        mock_connect.return_value = mock_meshtastic_client

        result = self.plugin.generate_response()
        self.assertEqual(result, "No nodes discovered yet.")

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_nodes_but_no_health_metrics(self, mock_connect):
        """
        Test that generating a response with nodes but no health metrics returns appropriate message.

        Verifies that the plugin handles the scenario where nodes are discovered but none
        have health metrics (battery, air utilization, SNR), returning "No nodes with health metrics found."
        instead of showing misleading 0.0% values.
        """
        # Nodes exist but have no health metrics
        nodes_without_metrics = {
            "node1": {
                "user": {"longName": "Node 1"},
                # No deviceMetrics or snr
            },
            "node2": {
                "user": {"longName": "Node 2"},
                "deviceMetrics": {},  # Empty deviceMetrics
                # No snr
            },
            "node3": {
                "user": {"longName": "Node 3"},
                "deviceMetrics": {"batteryLevel": None},  # None battery level
                "snr": None,  # None SNR
            },
        }

        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = nodes_without_metrics
        mock_connect.return_value = mock_meshtastic_client

        result = self.plugin.generate_response()
        self.assertEqual(result, "Nodes: 3\nNo nodes with health metrics found.")

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_minimal_data(self, mock_connect):
        """
        Test that generating a response with minimal node data returns a graceful string response.

        This test verifies that the plugin handles missing metrics gracefully and returns a string
        containing "Nodes:" when some metrics are missing, rather than raising an exception.
        """
        minimal_nodes = {
            "node1": {
                "deviceMetrics": {"batteryLevel": 50}
                # No SNR or airUtilTx
            }
        }

        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = minimal_nodes
        mock_connect.return_value = mock_meshtastic_client

        result = self.plugin.generate_response()
        self.assertIn("Nodes:", result)
        self.assertIn("Battery: 50.0% / 50.0%", result)
        self.assertIn("Air Util: 0.00 / 0.00", result)
        self.assertIn("SNR: 0.00 / 0.00", result)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_with_all_low_battery(self, mock_connect):
        """
        Test that response generation handles low battery nodes gracefully when air utilization data is missing.

        This verifies that the plugin returns a string response containing "Nodes:" even when
        air utilization data is missing, rather than raising an exception.
        """
        low_battery_nodes = {
            "node1": {"deviceMetrics": {"batteryLevel": 5}, "snr": 10.0},
            "node2": {"deviceMetrics": {"batteryLevel": 8}, "snr": 5.0},
        }

        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = low_battery_nodes
        mock_connect.return_value = mock_meshtastic_client

        result = self.plugin.generate_response()
        self.assertIn("Nodes:", result)
        self.assertIn("Nodes with Low Battery (< 10): 2", result)
        self.assertIn("Battery: 6.5% / 6.5%", result)

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_generate_response_filters_none_values(self, mock_connect):
        """
        Verify that the health plugin correctly filters out None values from node statistics before calculating and reporting mesh health metrics.
        """
        nodes_with_none = {
            "node1": {
                "deviceMetrics": {
                    "batteryLevel": 75,
                    "airUtilTx": None,  # None value should be filtered
                },
                "snr": None,  # None value should be filtered
            },
            "node2": {
                "deviceMetrics": {"batteryLevel": 25, "airUtilTx": 10.0},
                "snr": 5.0,
            },
        }

        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = nodes_with_none
        mock_connect.return_value = mock_meshtastic_client

        response = self.plugin.generate_response()

        # Should only use non-None values for statistics
        self.assertIn("Nodes: 2", response)
        self.assertIn("Battery: 50.0%", response)  # Average of 75 and 25
        self.assertIn("Air Util: 10.00", response)  # Only one valid value
        self.assertIn("10.00", response)  # Median same as single value
        self.assertIn("SNR: 5.00", response)  # Only one valid value

    def test_handle_meshtastic_message_always_false(self):
        """
        Test that handle_meshtastic_message consistently returns False regardless of input.
        """

        async def run_test():
            """
            Asynchronously tests that the plugin's handle_meshtastic_message method returns False when called with sample input.
            """
            result = await self.plugin.handle_meshtastic_message(
                {}, "formatted_message", "longname", "meshnet_name"
            )
            self.assertFalse(result)

        asyncio.run(run_test())

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False and does not send a message when the event does not match the plugin criteria.
        """
        self.plugin.matches = MagicMock(return_value=False)

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that `handle_room_message` returns False when the event does not match and ensures no Matrix message is sent.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_not_called()

        asyncio.run(run_test())

    @patch("mmrelay.meshtastic_utils.connect_meshtastic")
    def test_handle_room_message_with_match(self, mock_connect):
        """
        Tests that handle_room_message sends a health summary message to the Matrix room when the event matches.

        Verifies that the message contains the correct node count, is sent to the expected room, and the formatted flag is set to False.
        """
        mock_meshtastic_client = MagicMock()
        mock_meshtastic_client.nodes = self.sample_nodes
        mock_connect.return_value = mock_meshtastic_client

        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        room.room_id = "!test:matrix.org"
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that a Matrix room message event matching the plugin triggers a health summary message to be sent.

            Verifies that `handle_room_message` returns True, the event matcher is called once, and the Matrix message is sent with the correct room ID, message content, and formatting flag.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")

            self.assertTrue(result)
            self.plugin.matches.assert_called_once_with(event)
            self.plugin.send_matrix_message.assert_called_once()

            # Check the call arguments
            call_args = self.plugin.send_matrix_message.call_args
            self.assertEqual(call_args[0][0], "!test:matrix.org")  # room_id
            self.assertIn("Nodes: 5", call_args[0][1])  # message content
            self.assertEqual(call_args.kwargs["formatted"], False)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
