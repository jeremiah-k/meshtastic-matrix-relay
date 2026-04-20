"""
Tests to improve patch coverage for Core Utils (meshtastic_utils.py and matrix_utils.py).
Focuses on specific functions and edge cases that are currently missing test coverage.
"""

import os
import sys
import unittest
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any
from unittest.mock import Mock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay import matrix_utils, meshtastic_utils
from mmrelay.constants.network import CONNECTION_TYPE_SERIAL


class TestMeshtasticUtilsCoverage(unittest.TestCase):
    """Tests for missing coverage in meshtastic_utils.py"""

    def setUp(self):
        """Set up test fixtures"""
        self.mock_config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            },
            "matrix_rooms": [{"id": "!room1:example.com", "meshtastic_channel": 0}],
        }

    @staticmethod
    def _immediate_metadata_submit(callback: Callable[[], Any]) -> Future[None]:
        """
        Run a metadata probe callback immediately and return a completed Future.
        
        Parameters:
            callback (Callable[[], Any]): Function to execute synchronously; its return value is ignored.
        
        Returns:
            Future[None]: A Future already completed with result `None` after `callback` returns.
        
        Raises:
            Exception: Any exception raised by `callback` is propagated to the caller.
        """
        future: Future[None] = Future()
        callback()
        future.set_result(None)
        return future

    def test_get_device_metadata_console_output_truncation(self):
        """Test that console output is truncated when too long"""
        mock_interface = Mock()
        mock_interface.localNode = Mock()

        # Create a very long output string (> 4096 chars)
        long_output = "firmware_version: 1.2.3\n" + "x" * 5000

        mock_interface.localNode.getMetadata.side_effect = lambda: print(
            long_output, end=""
        )
        with patch(
            "mmrelay.meshtastic_utils._submit_metadata_probe",
            side_effect=self._immediate_metadata_submit,
        ):
            result = meshtastic_utils._get_device_metadata(mock_interface)

        # Should truncate and add ellipsis
        self.assertIn("raw_output", result)
        self.assertTrue(len(result["raw_output"]) <= 4097)  # 4096 + 1 for ellipsis
        self.assertTrue(result["raw_output"].endswith("…"))
        self.assertEqual(result["firmware_version"], "1.2.3")
        self.assertTrue(result["success"])

    def test_get_device_metadata_firmware_version_parsing(self):
        """Test various firmware version parsing scenarios"""
        mock_interface = Mock()
        mock_interface.localNode = Mock()

        test_cases = [
            # (output, expected_version)
            ('firmware_version: "1.2.3"', "1.2.3"),
            ("firmware_version: '2.0.0'", "2.0.0"),
            ("firmware_version:   1.3.5   ", "1.3.5"),
            ("FIRMWARE_VERSION: 1.4.0", "1.4.0"),  # Case insensitive
            ('firmware_version: "  1.5.0  "', "1.5.0"),  # Whitespace handling
        ]

        for output, expected in test_cases:
            with self.subTest(output=output):
                mock_interface.localNode.getMetadata.side_effect = (
                    lambda out=output: print(out, end="")
                )
                with patch(
                    "mmrelay.meshtastic_utils._submit_metadata_probe",
                    side_effect=self._immediate_metadata_submit,
                ):
                    result = meshtastic_utils._get_device_metadata(mock_interface)
                self.assertEqual(result["firmware_version"], expected)
                self.assertTrue(result["success"])

    def test_get_device_metadata_no_firmware_version_found(self):
        """Test when no firmware version is found in output"""
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.getMetadata.side_effect = lambda: print(
            "some other output", end=""
        )

        with patch(
            "mmrelay.meshtastic_utils._submit_metadata_probe",
            side_effect=self._immediate_metadata_submit,
        ):
            result = meshtastic_utils._get_device_metadata(mock_interface)

        self.assertEqual(result["firmware_version"], "unknown")
        self.assertFalse(result["success"])
        self.assertIn("raw_output", result)

    def test_get_device_metadata_exception_handling(self):
        """Test exception handling in get_device_metadata"""
        mock_interface = Mock()
        mock_interface.localNode = Mock()
        mock_interface.localNode.getMetadata.side_effect = Exception("Test error")

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = meshtastic_utils._get_device_metadata(mock_interface)

            self.assertFalse(result["success"])
            mock_logger.debug.assert_called_once()

    @patch("mmrelay.meshtastic_utils.config", None)
    def test_connect_meshtastic_no_config(self):
        """Test connect_meshtastic when no config is available"""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = meshtastic_utils.connect_meshtastic()

            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No configuration available. Cannot connect to Meshtastic."
            )

    def test_connect_meshtastic_no_meshtastic_section(self):
        """Test connect_meshtastic when meshtastic section is missing"""
        config_without_meshtastic = {"matrix": {"homeserver": "example.com"}}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = meshtastic_utils.connect_meshtastic(
                passed_config=config_without_meshtastic
            )

            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No Meshtastic configuration section found. Cannot connect to Meshtastic."
            )

    def test_connect_meshtastic_no_connection_type(self):
        """Test connect_meshtastic when connection_type is missing"""
        config_no_connection_type = {"meshtastic": {"serial_port": "/dev/ttyUSB0"}}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = meshtastic_utils.connect_meshtastic(
                passed_config=config_no_connection_type
            )

            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No connection type specified in Meshtastic configuration. Cannot connect to Meshtastic."
            )

    def test_connect_meshtastic_force_connect_closes_existing(self):
        """Test that force_connect closes existing connection"""
        mock_client = Mock()

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            with patch("mmrelay.meshtastic_utils.logger"):
                # This will fail due to missing config, but we want to test the close logic
                meshtastic_utils.connect_meshtastic(force_connect=True)

                mock_client.close.assert_called_once()

    def test_connect_meshtastic_close_exception_handling(self):
        """Test exception handling when closing existing connection"""
        mock_client = Mock()
        mock_client.close.side_effect = Exception("Close error")

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                # This will fail due to missing config, but we want to test the exception handling
                meshtastic_utils.connect_meshtastic(force_connect=True)

                mock_logger.warning.assert_called_once()
                call_args = mock_logger.warning.call_args
                self.assertEqual(
                    call_args[0][0], "Error closing previous connection: %s"
                )
                self.assertEqual(call_args[0][1].args[0], "Close error")
                self.assertTrue(call_args[1]["exc_info"])


class TestMatrixUtilsCoverage(unittest.TestCase):
    """Tests for missing coverage in matrix_utils.py"""

    def test_display_room_channel_mappings_no_rooms(self):
        """Test _display_room_channel_mappings when no rooms are provided"""
        config = {"matrix_rooms": []}
        e2ee_status = {"overall_status": "ready"}

        with patch("mmrelay.matrix_utils.logger") as mock_logger:
            matrix_utils._display_room_channel_mappings({}, config, e2ee_status)

            mock_logger.info.assert_called_with("Bot is not in any Matrix rooms")

    def test_display_room_channel_mappings_no_matrix_rooms_config(self):
        """Test _display_room_channel_mappings when matrix_rooms config is empty"""
        rooms = {"!room1:example.com": Mock(display_name="Room 1", encrypted=False)}
        config = {"matrix_rooms": []}
        e2ee_status = {"overall_status": "ready"}

        with patch("mmrelay.matrix_utils.logger") as mock_logger:
            matrix_utils._display_room_channel_mappings(rooms, config, e2ee_status)

            mock_logger.info.assert_called_with("No matrix_rooms configuration found")

    def test_display_room_channel_mappings_missing_matrix_rooms_config(self):
        """Test _display_room_channel_mappings when matrix_rooms config is missing"""
        rooms = {"!room1:example.com": Mock(display_name="Room 1", encrypted=False)}
        config = {}
        e2ee_status = {"overall_status": "ready"}

        with patch("mmrelay.matrix_utils.logger") as mock_logger:
            matrix_utils._display_room_channel_mappings(rooms, config, e2ee_status)

            mock_logger.info.assert_called_with("No matrix_rooms configuration found")

    def test_display_room_channel_mappings_dict_format(self):
        """Test _display_room_channel_mappings with dict format matrix_rooms config"""
        rooms = {
            "!room1:example.com": Mock(display_name="Room 1", encrypted=False),
            "!room2:example.com": Mock(display_name="Room 2", encrypted=True),
        }
        config = {
            "matrix_rooms": {
                "room1": {"id": "!room1:example.com", "meshtastic_channel": 0},
                "room2": {"id": "!room2:example.com", "meshtastic_channel": 1},
            }
        }
        e2ee_status = {"overall_status": "ready"}

        with patch("mmrelay.matrix_utils.logger") as mock_logger:
            matrix_utils._display_room_channel_mappings(rooms, config, e2ee_status)

            # Should not log "No matrix_rooms configuration found"
            calls = [call.args[0] for call in mock_logger.info.call_args_list]
            self.assertNotIn("No matrix_rooms configuration found", calls)


if __name__ == "__main__":
    unittest.main()
