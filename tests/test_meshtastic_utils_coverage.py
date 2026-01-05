#!/usr/bin/env python3
"""
Additional test suite for uncovered lines in Meshtastic utilities in MMRelay.

This file contains tests for previously uncovered code paths including:
- Ultimate fallback in _submit_coro
- Timeout parsing with edge cases
- Interface nodes fallback for names
- SystemExit handling in send_text_reply
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestAsyncHelperEdgeCases(unittest.TestCase):
    """Test cases for edge cases in async helper functions."""

    def setUp(self):
        """Initialize test fixtures for async helper tests."""
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.event_loop = None
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.event_loop", None)
    @patch("asyncio.get_running_loop")
    def test_submit_coro_ultimate_fallback_runtime_error(
        self, mock_get_running_loop, mock_logger
    ):
        """Test _submit_coro ultimate fallback when RuntimeError occurs."""
        from mmrelay.meshtastic_utils import _submit_coro

        # Simulate RuntimeError in create new loop
        with patch("asyncio.new_event_loop", side_effect=RuntimeError("test error")):
            result = _submit_coro(asyncio.sleep(0))

        # Should return a Future with exception set
        self.assertIsNotNone(result)
        self.assertIsInstance(result, asyncio.Future)
        with self.assertRaises(RuntimeError):
            result.result(timeout=1)

        # Should log ultimate fallback message
        mock_logger.debug.assert_called()
        debug_call = mock_logger.debug.call_args[0][0]
        self.assertIn("Ultimate fallback triggered for _submit_coro", debug_call)
        self.assertIn("RuntimeError", debug_call)

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.event_loop", None)
    @patch("asyncio.get_running_loop")
    def test_submit_coro_ultimate_fallback_os_error(
        self, mock_get_running_loop, mock_logger
    ):
        """Test _submit_coro ultimate fallback when OSError occurs."""
        from mmrelay.meshtastic_utils import _submit_coro

        # Simulate OSError in create new loop
        with patch("asyncio.new_event_loop", side_effect=OSError("test error")):
            result = _submit_coro(asyncio.sleep(0))

        # Should return a Future with exception set
        self.assertIsNotNone(result)
        self.assertIsInstance(result, asyncio.Future)
        with self.assertRaises(OSError):
            result.result(timeout=1)

        # Should log ultimate fallback message
        mock_logger.debug.assert_called()
        debug_call = mock_logger.debug.call_args[0][0]
        self.assertIn("Ultimate fallback triggered for _submit_coro", debug_call)
        self.assertIn("OSError", debug_call)


class TestTimeoutParsingEdgeCases(unittest.TestCase):
    """Test cases for timeout configuration parsing edge cases."""

    def setUp(self):
        """Reset global state for timeout parsing tests."""
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True)
    @patch(
        "meshtastic.serial_interface.SerialInterface",
        side_effect=Exception("timeout not used"),
    )
    def test_connect_meshtastic_negative_timeout(self, mock_serial, mock_logger):
        """Test connect_meshtastic with negative timeout value."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "timeout": -10,
            },
            "matrix_rooms": [],
        }

        result = connect_meshtastic(passed_config=config)

        # Should log warning about non-positive timeout
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Non-positive meshtastic.timeout value" in str(call)
        ]
        self.assertEqual(len(warning_calls), 1)
        self.assertIn("-10", str(warning_calls[0]))
        self.assertIn("fallback", str(warning_calls[0]).lower())

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True)
    @patch(
        "meshtastic.serial_interface.SerialInterface",
        side_effect=Exception("timeout not used"),
    )
    def test_connect_meshtastic_zero_timeout(self, mock_serial, mock_logger):
        """Test connect_meshtastic with zero timeout value."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "timeout": 0,
            },
            "matrix_rooms": [],
        }

        result = connect_meshtastic(passed_config=config)

        # Should log warning about non-positive timeout
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Non-positive meshtastic.timeout value" in str(call)
        ]
        self.assertEqual(len(warning_calls), 1)
        self.assertIn("0", str(warning_calls[0]))

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True)
    @patch(
        "meshtastic.serial_interface.SerialInterface",
        side_effect=Exception("timeout not used"),
    )
    def test_connect_meshtastic_invalid_string_timeout(self, mock_serial, mock_logger):
        """Test connect_meshtastic with invalid string timeout value."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "timeout": "invalid",
            },
            "matrix_rooms": [],
        }

        result = connect_meshtastic(passed_config=config)

        # Should log warning about invalid timeout
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "Invalid meshtastic.timeout value" in str(call)
        ]
        self.assertEqual(len(warning_calls), 1)
        self.assertIn("invalid", str(warning_calls[0]))
        self.assertIn("fallback", str(warning_calls[0]).lower())

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True)
    @patch(
        "meshtastic.serial_interface.SerialInterface",
        side_effect=Exception("timeout not used"),
    )
    def test_connect_meshtastic_none_timeout(self, mock_serial, mock_logger):
        """Test connect_meshtastic with None timeout value (uses default)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "timeout": None,
            },
            "matrix_rooms": [],
        }

        result = connect_meshtastic(passed_config=config)

        # Should use default timeout and not log warning
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if "timeout" in str(call).lower()
        ]
        # No timeout warnings should be logged
        timeout_warnings = [
            call for call in warning_calls if "timeout" in str(call).lower()
        ]
        self.assertEqual(len(timeout_warnings), 0)


class TestInterfaceNodesFallback(unittest.TestCase):
    """Test cases for interface nodes fallback when database has no names."""

    def setUp(self):
        """Initialize test fixtures for interface fallback tests."""
        import mmrelay.meshtastic_utils

        self.mock_config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "broadcast_enabled": True,
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
            ],
        }

        self.mock_packet = {
            "fromId": 123456789,
            "from": 123456789,
            "to": 4294967295,  # BROADCAST_NUM
            "decoded": {
                "text": "Test message",
                "portnum": "TEXT_MESSAGE_APP",
            },
            "channel": 0,
            "id": 12345,
        }

        # Reset global state
        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.event_loop = None

    @patch("mmrelay.meshtastic_utils.config")
    @patch("mmrelay.meshtastic_utils.matrix_rooms")
    @patch("mmrelay.meshtastic_utils._fire_and_forget")
    @patch("mmrelay.db_utils.get_longname", return_value=None)
    @patch("mmrelay.db_utils.get_shortname", return_value=None)
    @patch("mmrelay.meshtastic_utils.plugin_loader")
    def test_on_meshtastic_message_interface_fallback_longname(
        self,
        mock_plugin_loader,
        mock_get_shortname,
        mock_get_longname,
        mock_fire_and_forget,
        mock_matrix_rooms,
        mock_config,
    ):
        """Test on_meshtastic_message uses interface nodes for longname when DB has none."""
        from mmrelay.meshtastic_utils import on_meshtastic_message

        mock_config.return_value = self.mock_config
        mock_matrix_rooms.return_value = self.mock_config["matrix_rooms"]

        # Mock interface with nodes
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = 987654321
        mock_interface.nodes = {
            123456789: {
                "user": {
                    "longName": "InterfaceLong",
                    "shortName": "InterfaceShort",
                }
            }
        }

        # Mock load_plugins to return empty list
        mock_plugins = MagicMock()
        mock_plugins.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_plugins

        with patch("mmrelay.meshtastic_utils.save_longname") as mock_save_longname:
            with patch(
                "mmrelay.meshtastic_utils.save_shortname"
            ) as mock_save_shortname:
                on_meshtastic_message(self.mock_packet, mock_interface)

                # Should save longname from interface
                mock_save_longname.assert_called_once_with(123456789, "InterfaceLong")

    @patch("mmrelay.meshtastic_utils.config")
    @patch("mmrelay.meshtastic_utils.matrix_rooms")
    @patch("mmrelay.meshtastic_utils._fire_and_forget")
    @patch("mmrelay.db_utils.get_longname", return_value=None)
    @patch("mmrelay.db_utils.get_shortname", return_value=None)
    @patch("mmrelay.meshtastic_utils.plugin_loader")
    def test_on_meshtastic_message_interface_fallback_shortname(
        self,
        mock_plugin_loader,
        mock_get_shortname,
        mock_get_longname,
        mock_fire_and_forget,
        mock_matrix_rooms,
        mock_config,
    ):
        """Test on_meshtastic_message uses interface nodes for shortname when DB has none."""
        from mmrelay.meshtastic_utils import on_meshtastic_message

        mock_config.return_value = self.mock_config
        mock_matrix_rooms.return_value = self.mock_config["matrix_rooms"]

        # Mock interface with nodes
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = 987654321
        mock_interface.nodes = {
            123456789: {
                "user": {
                    "longName": "InterfaceLong",
                    "shortName": "InterfaceShort",
                }
            }
        }

        # Mock load_plugins to return empty list
        mock_plugins = MagicMock()
        mock_plugins.load_plugins.return_value = []
        mock_plugin_loader.return_value = mock_plugins

        with patch("mmrelay.meshtastic_utils.save_longname") as mock_save_longname:
            with patch(
                "mmrelay.meshtastic_utils.save_shortname"
            ) as mock_save_shortname:
                on_meshtastic_message(self.mock_packet, mock_interface)

                # Should save shortname from interface
                mock_save_shortname.assert_called_once_with(123456789, "InterfaceShort")


class TestSendTextReplySystemExit(unittest.TestCase):
    """Test cases for send_text_reply SystemExit handling."""

    def setUp(self):
        """Initialize test fixtures for send_text_reply tests."""
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

    @patch("mmrelay.meshtastic_utils.logger")
    def test_send_text_reply_system_exit_preserved(self, mock_logger):
        """Test send_text_reply preserves SystemExit instead of catching it."""
        from mmrelay.meshtastic_utils import send_text_reply

        # Mock interface that raises SystemExit
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.side_effect = SystemExit("Test exit")

        # Should re-raise SystemExit, not catch it
        with self.assertRaises(SystemExit) as cm:
            send_text_reply(mock_interface, "test", 12345)

        # Verify it's the original SystemExit
        self.assertEqual(str(cm.exception), "Test exit")

        # Should log debug message about SystemExit
        mock_logger.debug.assert_called_once()
        debug_call = mock_logger.debug.call_args[0][0]
        self.assertIn("SystemExit encountered", debug_call)
        self.assertIn("graceful shutdown", debug_call.lower())


if __name__ == "__main__":
    unittest.main()
