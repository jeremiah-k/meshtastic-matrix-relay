#!/usr/bin/env python3
"""
Test suite for the MMRelay telemetry plugin.

Tests the telemetry data collection and graphing functionality including:
- Telemetry data processing and storage
- Time period generation
- Matrix command handling
- Graph generation and image upload
- Device metrics parsing
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.plugins.telemetry_plugin import Plugin


class TestTelemetryPlugin(unittest.TestCase):
    """Test cases for the telemetry plugin."""

    def setUp(self):
        """
        Initializes the test environment by creating a Plugin instance and mocking its logger, database operations, and Matrix client methods.
        """
        self.plugin = Plugin()
        self.plugin.logger = MagicMock()

        # Mock database operations
        self.plugin.get_node_data = MagicMock(return_value=[])
        self.plugin.set_node_data = MagicMock()
        self.plugin.get_data = MagicMock(return_value=[])

        # Mock Matrix client methods
        self.plugin.send_matrix_message = AsyncMock()
        self.plugin.send_room_image = AsyncMock()
        self.plugin.upload_image = AsyncMock()
        self.plugin.get_require_bot_mention = MagicMock(return_value=False)

    def test_plugin_name(self):
        """
        Test that the plugin's name attribute is set to "telemetry".
        """
        self.assertEqual(self.plugin.plugin_name, "telemetry")

    def test_max_data_rows_per_node(self):
        """
        Verify that the plugin's maximum number of data rows per node is set to 50.
        """
        self.assertEqual(self.plugin.max_data_rows_per_node, 50)

    def test_commands(self):
        """
        Test that the plugin's commands method returns the expected list of telemetry commands.
        """
        commands = self.plugin.commands()
        expected = ["batteryLevel", "voltage", "airUtilTx"]
        self.assertEqual(commands, expected)

    def test_description(self):
        """
        Verify that the plugin's description method returns the expected summary string.
        """
        description = self.plugin.description
        self.assertEqual(
            description, "Graph of avg Mesh telemetry value for last 12 hours"
        )

    def test_get_matrix_commands(self):
        """
        Test that the plugin's get_matrix_commands method returns the expected list of Matrix commands.
        """
        commands = self.plugin.get_matrix_commands()
        expected = ["batteryLevel", "voltage", "airUtilTx"]
        self.assertEqual(commands, expected)

    def test_get_mesh_commands(self):
        """
        Test that the plugin's get_mesh_commands method returns an empty list.
        """
        commands = self.plugin.get_mesh_commands()
        self.assertEqual(commands, [])

    def test_generate_timeperiods_default(self):
        """
        Test that the default time period generation produces 13 hourly intervals spanning the last 12 hours.

        Verifies that the intervals start 12 hours before the mocked current time, end at the current time, and are spaced one hour apart.
        """
        with patch("mmrelay.plugins.telemetry_plugin.datetime") as mock_datetime:
            # Mock current time
            mock_now = datetime(2024, 1, 15, 12, 0, 0)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            intervals = self.plugin._generate_timeperiods()

            # Should have 13 intervals (12 hours + 1 for end time)
            self.assertEqual(len(intervals), 13)

            # First interval should be 12 hours ago
            expected_start = mock_now - timedelta(hours=12)
            self.assertEqual(intervals[0], expected_start)

            # Last interval should be current time
            self.assertEqual(intervals[-1], mock_now)

            # Each interval should be 1 hour apart
            for i in range(len(intervals) - 1):
                diff = intervals[i + 1] - intervals[i]
                self.assertEqual(diff, timedelta(hours=1))

    def test_generate_timeperiods_custom_hours(self):
        """
        Test that custom hour intervals are correctly generated for time period calculations.

        Verifies that the plugin's `_generate_timeperiods` method produces the expected number of intervals and correct start time when a custom hour range is specified.
        """
        with patch("mmrelay.plugins.telemetry_plugin.datetime") as mock_datetime:
            mock_now = datetime(2024, 1, 15, 12, 0, 0)
            mock_datetime.now.return_value = mock_now
            mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

            intervals = self.plugin._generate_timeperiods(hours=6)

            # Should have 7 intervals (6 hours + 1 for end time)
            self.assertEqual(len(intervals), 7)

            # First interval should be 6 hours ago
            expected_start = mock_now - timedelta(hours=6)
            self.assertEqual(intervals[0], expected_start)

    def test_handle_meshtastic_message_valid_telemetry(self):
        """
        Test that a valid telemetry Meshtastic message is processed and stored correctly.

        Verifies that the plugin extracts telemetry metrics from a properly formatted message, stores the data with expected fields and values, and does not relay the message to Matrix.
        """
        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "time": 1642248000,  # Unix timestamp
                    "deviceMetrics": {
                        "batteryLevel": 85,
                        "voltage": 4.2,
                        "airUtilTx": 12.5,
                    },
                },
            },
        }

        async def run_test():
            """
            Asynchronously tests that a valid telemetry message is processed and stored correctly by the plugin.

            Verifies that the plugin does not relay the message to Matrix, stores telemetry data with the expected structure and values, and associates the data with the correct node.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should return False (doesn't relay to Matrix)
            self.assertFalse(result)

            # Should store telemetry data
            self.plugin.set_node_data.assert_called_once()
            call_args = self.plugin.set_node_data.call_args
            self.assertEqual(call_args.kwargs["meshtastic_id"], "!12345678")

            # Check stored data structure
            stored_data = call_args.kwargs["node_data"]
            self.assertEqual(len(stored_data), 1)
            self.assertEqual(stored_data[0]["time"], 1642248000)
            self.assertEqual(stored_data[0]["batteryLevel"], 85)
            self.assertEqual(stored_data[0]["voltage"], 4.2)
            self.assertEqual(stored_data[0]["airUtilTx"], 12.5)

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_partial_metrics(self):
        """
        Test that the plugin correctly handles telemetry messages with missing device metrics by defaulting absent values to zero and storing the resulting data.
        """
        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "time": 1642248000,
                    "deviceMetrics": {
                        "batteryLevel": 75
                        # Missing voltage and airUtilTx
                    },
                },
            },
        }

        async def run_test():
            """
            Asynchronously tests handling of a Meshtastic telemetry message with partial device metrics, verifying that missing metrics are stored with default values.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            self.assertFalse(result)

            # Check stored data has default values for missing metrics
            call_args = self.plugin.set_node_data.call_args
            stored_data = call_args.kwargs["node_data"]
            self.assertEqual(stored_data[0]["batteryLevel"], 75)
            self.assertEqual(stored_data[0]["voltage"], 0)  # Default value
            self.assertEqual(stored_data[0]["airUtilTx"], 0)  # Default value

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_non_telemetry(self):
        """
        Tests that a non-telemetry Meshtastic message is ignored by the plugin and does not result in data storage.
        """
        packet = {
            "fromId": "!12345678",
            "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "Hello world"},
        }

        async def run_test():
            """
            Verify the plugin ignores a non-telemetry Meshtastic packet.

            Asserts that handle_meshtastic_message returns False and that no node data is stored (set_node_data is not called).
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should return False (not processed)
            self.assertFalse(result)

            # Should not store any data
            self.plugin.set_node_data.assert_not_called()

        import asyncio

        asyncio.run(run_test())

    def test_handle_meshtastic_message_missing_device_metrics(self):
        """
        Test that a telemetry message missing device metrics is ignored and no data is stored.
        """
        packet = {
            "fromId": "!12345678",
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "time": 1642248000
                    # Missing deviceMetrics
                },
            },
        }

        async def run_test():
            """
            Verify that a non-telemetry Meshtastic packet is ignored by the plugin.

            Calls handle_meshtastic_message with a non-telemetry packet and asserts it returns False and does not call set_node_data.
            """
            result = await self.plugin.handle_meshtastic_message(
                packet, "formatted_message", "longname", "meshnet_name"
            )

            # Should return False (not processed)
            self.assertFalse(result)

            # Should not store any data
            self.plugin.set_node_data.assert_not_called()

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.bot_command")
    def test_matches_with_valid_command(self, mock_bot_command):
        """
        Test that the matches method returns True when a valid telemetry command is present in the event.

        Verifies that the method checks all available commands and returns True upon finding a match.
        """
        mock_bot_command.side_effect = lambda cmd, *_, **__: cmd == "batteryLevel"

        event = MagicMock()
        result = self.plugin.matches(event)

        self.assertTrue(result)
        # Should check all commands until it finds a match
        self.assertTrue(mock_bot_command.called)

    @patch("mmrelay.matrix_utils.bot_command")
    def test_matches_with_no_command(self, mock_bot_command):
        """
        Test that the matches method returns False when no commands match the event.

        Verifies that all available commands are checked when there is no match.
        """
        mock_bot_command.return_value = False

        event = MagicMock()
        result = self.plugin.matches(event)

        self.assertFalse(result)
        # Should check all commands
        self.assertEqual(mock_bot_command.call_count, 3)

    def test_handle_room_message_no_match(self):
        """
        Test that handle_room_message returns False when the event does not match any command.

        Verifies that the matches method is called once with the event and that no further processing occurs when there is no command match.
        """
        self.plugin.matches = MagicMock(return_value=False)

        room = MagicMock()
        event = MagicMock()

        async def run_test():
            """
            Asynchronously tests that handling a room message returns False and verifies that the matches method is called once with the event.
            """
            result = await self.plugin.handle_room_message(room, event, "full_message")
            self.assertFalse(result)
            self.plugin.matches.assert_called_once_with(event)

        import asyncio

        asyncio.run(run_test())

    def test_handle_room_message_invalid_regex(self):
        """
        Test that handle_room_message returns False when given a message with an invalid command format.
        """
        self.plugin.matches = MagicMock(return_value=True)

        room = MagicMock()
        event = MagicMock()
        full_message = "some invalid message format"
        event.body = full_message
        event.source = {"content": {"formatted_body": ""}}

        # Mock Matrix client get_displayname to return a string
        with patch("mmrelay.matrix_utils.connect_matrix") as mock_connect:
            mock_matrix_client = AsyncMock()
            response_mock = MagicMock()
            response_mock.displayname = "TestBot"
            mock_matrix_client.get_displayname = AsyncMock(return_value=response_mock)
            mock_connect.return_value = mock_matrix_client
            import mmrelay.matrix_utils as mu

            mu.matrix_client = mock_matrix_client
            mu.bot_user_id = "@bot:matrix.org"

            async def run_test():
                """
                Runs the test for handling a Matrix room message and asserts that the result is False.
                """
                result = await self.plugin.handle_room_message(
                    room, event, full_message
                )
                self.assertFalse(result)

        import asyncio

        asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.connect_matrix")
    @patch("mmrelay.matrix_utils.upload_image")
    @patch("mmrelay.matrix_utils.send_room_image")
    @patch("mmrelay.plugins.telemetry_plugin.plt.xticks")
    @patch("mmrelay.plugins.telemetry_plugin.plt.subplots")
    def test_handle_room_message_valid_command_no_node(
        self, mock_subplots, mock_xticks, mock_send_image, mock_upload, mock_connect
    ):
        """
        Test that handle_room_message processes a valid command without a specified node, generates a plot, uploads the image, and sends it to the Matrix room.

        This test mocks plotting, image handling, and Matrix client operations to verify that the plugin creates and sends a graph in response to a valid command when no node is specified.
        """
        self.plugin.matches = MagicMock(return_value=True)

        # Mock matplotlib
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        mock_subplots.return_value = (mock_fig, mock_ax)

        # Mock canvas and image operations
        mock_canvas = MagicMock()
        mock_fig.canvas = mock_canvas

        # Mock PIL Image operations
        with patch("mmrelay.plugins.telemetry_plugin.Image") as mock_image_class:
            mock_image = MagicMock()
            mock_image.size = (800, 600)
            mock_image.tobytes.return_value = b"fake_image_data"
            mock_image_class.open.return_value = mock_image
            mock_image_class.frombytes.return_value = mock_image

            # Mock Matrix operations
            mock_matrix_client = AsyncMock()
            mock_connect.return_value = mock_matrix_client
            mock_upload.return_value = {"content_uri": "mxc://example.com/image"}

            room = MagicMock()
            room.room_id = "!test:matrix.org"
            event = MagicMock()
            full_message = "!batteryLevel"
            event.body = full_message
            event.source = {"content": {"formatted_body": ""}}

            async def run_test():
                """
                Run the async test that verifies handle_room_message processes a room message to produce and send a plot image.

                Verifies the handler returns a truthy result, creates a plot with expected labels ("Hour" x-axis, "batteryLevel" y-axis), and calls image upload and send operations.
                """
                result = await self.plugin.handle_room_message(
                    room, event, full_message
                )

                self.assertTrue(result)

                # Should create plot
                mock_subplots.assert_called_once()
                mock_ax.plot.assert_called_once()
                mock_ax.set_title.assert_called_once()
                mock_ax.set_xlabel.assert_called_once_with("Hour")
                mock_ax.set_ylabel.assert_called_once_with("batteryLevel")

                # Should upload and send image
                mock_upload.assert_called_once()
                mock_send_image.assert_called_once()

            import asyncio

            asyncio.run(run_test())

    @patch("mmrelay.matrix_utils.connect_matrix")
    @patch("mmrelay.matrix_utils.upload_image")
    @patch("mmrelay.matrix_utils.send_room_image")
    @patch("mmrelay.plugins.telemetry_plugin.plt.xticks")
    @patch("mmrelay.plugins.telemetry_plugin.plt.subplots")
    def test_handle_room_message_with_specific_node(
        self, mock_subplots, mock_xticks, mock_send_image, mock_upload, mock_connect
    ):
        """
        Test that handle_room_message processes a valid command with a specific node parameter, generates a voltage graph for the node, uploads the image, and sends it to the Matrix room.

        This test mocks node data retrieval, matplotlib plotting, image creation, and Matrix client operations to verify that the correct data is used, the plot title includes the node name and metric, and the method returns True.
        """
        self.plugin.matches = MagicMock(return_value=True)

        # Mock node data
        mock_node_data = [
            {"time": 1642248000, "voltage": 4.2},
            {"time": 1642251600, "voltage": 4.1},
        ]
        self.plugin.get_node_data.return_value = mock_node_data

        # Mock matplotlib
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        mock_subplots.return_value = (mock_fig, mock_ax)
        mock_canvas = MagicMock()
        mock_fig.canvas = mock_canvas

        # Mock PIL Image operations
        with patch("mmrelay.plugins.telemetry_plugin.Image") as mock_image_class:
            mock_image = MagicMock()
            mock_image.size = (800, 600)
            mock_image.tobytes.return_value = b"fake_image_data"
            mock_image_class.open.return_value = mock_image
            mock_image_class.frombytes.return_value = mock_image

            # Mock Matrix operations
            mock_matrix_client = AsyncMock()
            mock_connect.return_value = mock_matrix_client
            response_mock = MagicMock()
            response_mock.displayname = "TestBot"
            mock_matrix_client.get_displayname = AsyncMock(return_value=response_mock)
            import mmrelay.matrix_utils as mu

            mu.matrix_client = mock_matrix_client
            mu.bot_user_id = "@bot:matrix.org"
            mock_upload.return_value = {"content_uri": "mxc://example.com/image"}

            room = MagicMock()
            room.room_id = "!test:matrix.org"
            event = MagicMock()
            full_message = "!voltage NodeABC"
            event.body = full_message
            event.source = {"content": {"formatted_body": ""}}

            async def run_test():
                """
                Verify that handling a room message for a specific node requests that node's data and includes the node and metric in the plot title.

                Asserts that handle_room_message invokes get_node_data with the given node identifier and that the plot title contains both the node name ("NodeABC") and the requested metric ("voltage").
                """
                result = await self.plugin.handle_room_message(
                    room, event, full_message
                )

                self.assertTrue(result)

                # Should get data for specific node
                self.plugin.get_node_data.assert_called_with("NodeABC")

                # Should set title with node name
                title_call = mock_ax.set_title.call_args[0][0]
                self.assertIn("NodeABC", title_call)
                self.assertIn("voltage", title_call)

            import asyncio

            asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
