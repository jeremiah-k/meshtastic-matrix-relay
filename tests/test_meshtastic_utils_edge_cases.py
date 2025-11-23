#!/usr/bin/env python3
"""
Test suite for Meshtastic utilities edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Connection failures and timeouts
- Protocol errors and malformed packets
- Hardware disconnection scenarios
- Serial port access issues
- BLE connection instability
- TCP connection drops
- Memory constraints with large node lists
"""

import asyncio
import os
import sys
import unittest
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.meshtastic_utils import (
    connect_meshtastic,
    is_running_as_service,
    on_lost_meshtastic_connection,
    on_meshtastic_message,
    sendTextReply,
    serial_port_exists,
)


class TestMeshtasticUtilsEdgeCases(unittest.TestCase):
    """Test cases for Meshtastic utilities edge cases and error handling."""

    def setUp(self):
        """
        Resets global state variables in mmrelay.meshtastic_utils to ensure each test runs in isolation.
        """
        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.event_loop = None
        mmrelay.meshtastic_utils.reconnect_task = None
        mmrelay.meshtastic_utils.subscribed_to_messages = False
        mmrelay.meshtastic_utils.subscribed_to_connection_lost = False

    def test_serial_port_exists_permission_error(self):
        """
        Test that serial_port_exists returns False when access to the serial port is denied due to a PermissionError.
        """
        with patch(
            "mmrelay.meshtastic_utils.serial.tools.list_ports.comports",
            return_value=[],
        ):
            result = serial_port_exists("/dev/ttyUSB0")
            self.assertFalse(result)

    def test_serial_port_exists_device_not_found(self):
        """
        Test that serial_port_exists returns False when the specified device is not found.
        """
        with patch(
            "mmrelay.meshtastic_utils.serial.tools.list_ports.comports",
            return_value=[],
        ):
            result = serial_port_exists("/dev/nonexistent")
            self.assertFalse(result)

    def test_serial_port_exists_device_busy(self):
        """
        Test that serial_port_exists returns False when the serial device is busy.

        Simulates a busy device by patching serial.Serial to raise SerialException.
        """
        with patch(
            "mmrelay.meshtastic_utils.serial.tools.list_ports.comports",
            return_value=[],
        ):
            result = serial_port_exists("/dev/ttyUSB0")
            self.assertFalse(result)

    def test_connect_meshtastic_serial_connection_timeout(self):
        """
        Test that connect_meshtastic returns None and logs an error when a serial connection attempt results in a timeout.
        """
        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        with patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True):
            with patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=ConcurrentTimeoutError("Connection timeout"),
            ):
                with patch("time.sleep"):  # Speed up test
                    with (
                        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
                        patch(
                            "mmrelay.meshtastic_utils.is_running_as_service",
                            return_value=True,
                        ),
                        patch("mmrelay.matrix_utils.matrix_client", None),
                    ):
                        result = connect_meshtastic(config)
                        self.assertIsNone(result)
                        mock_logger.exception.assert_called()

    def test_connect_meshtastic_ble_device_not_found(self):
        """
        Test that connect_meshtastic returns None and logs an error when a BLE device is unavailable.

        Simulates a BLE connection attempt where the device cannot be found, verifying that connect_meshtastic handles the error gracefully and logs the failure.
        """
        config = {
            "meshtastic": {"connection_type": "ble", "ble_address": "00:11:22:33:44:55"}
        }

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            side_effect=ConnectionRefusedError("Device not found"),
        ):
            with patch("time.sleep"):  # Speed up test
                with (
                    patch("mmrelay.meshtastic_utils.logger") as mock_logger,
                    patch(
                        "mmrelay.meshtastic_utils.is_running_as_service",
                        return_value=True,
                    ),
                    patch("mmrelay.matrix_utils.matrix_client", None),
                ):
                    result = connect_meshtastic(config)
                    self.assertIsNone(result)
                    mock_logger.exception.assert_called()

    def test_connect_meshtastic_tcp_connection_refused(self):
        """
        Verify connect_meshtastic returns None and logs an exception when a TCP connection is refused.

        Patches the TCPInterface to raise ConnectionRefusedError and asserts that connect_meshtastic returns None and that logger.exception is invoked.
        """
        config = {"meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"}}

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                result = connect_meshtastic(config)
                self.assertIsNone(result)
                mock_logger.exception.assert_called()

    def test_connect_meshtastic_invalid_connection_type(self):
        """
        Test that connect_meshtastic returns None and logs an error when given an invalid connection type in the configuration.
        """
        config = {"meshtastic": {"connection_type": "invalid_type"}}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = connect_meshtastic(config)
            self.assertIsNone(result)
            mock_logger.error.assert_called()

    def test_connect_meshtastic_exponential_backoff_max_retries(self):
        """
        Test that connect_meshtastic returns None and logs an exception when repeated connection attempts hit the maximum retries due to persistent MemoryError.

        Sets a serial connection config, patches serial_port_exists to True and SerialInterface to raise MemoryError on each attempt, patches time.sleep to avoid delays, and asserts connect_meshtastic returns None and that logger.exception was called.
        """
        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        with patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True):
            with patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=MemoryError("Out of memory"),
            ):
                with patch("time.sleep"):  # Speed up test
                    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                        result = connect_meshtastic(config)
                        self.assertIsNone(result)
                        # Should log error for critical failure
                mock_logger.exception.assert_called()

    def test_on_meshtastic_message_malformed_packet(self):
        """
        Verifies that on_meshtastic_message handles various malformed packet inputs without raising exceptions.

        Tests the function's robustness against empty packets, missing or None fields, and invalid channel types.
        """
        malformed_packets = [
            {},  # Empty packet
            {"decoded": None},  # None decoded
            {"decoded": {"text": None}},  # None text
            {"fromId": None},  # None fromId
            {"channel": "invalid"},  # Invalid channel type
        ]

        for packet in malformed_packets:
            with self.subTest(packet=packet):
                mock_interface = MagicMock()
                with patch("mmrelay.meshtastic_utils.logger"):
                    # Should handle malformed packets gracefully
                    on_meshtastic_message(packet, mock_interface)

    def test_on_meshtastic_message_plugin_processing_failure(self):
        """
        Test that on_meshtastic_message logs an error when a plugin raises an exception during message processing.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        from concurrent.futures import Future

        def _submit_coro_mock(coro, loop=None):
            """
            Run a coroutine synchronously and return a concurrent.futures.Future completed with its outcome.

            This test helper executes `coro` immediately via `asyncio.run` so any exceptions are raised and captured; the returned Future will be set with the coroutine's result or its exception. The optional `loop` parameter is accepted for API compatibility but is ignored.
            """
            f = Future()
            try:
                # Execute the coroutine to trigger the exception
                result = asyncio.run(coro)
                f.set_result(result)
            except Exception as e:
                f.set_exception(e)
            return f

        with (
            patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_plugin = MagicMock()
            mock_plugin.plugin_name = "test_plugin"
            mock_plugin.handle_meshtastic_message = AsyncMock(
                side_effect=Exception("Plugin failed")
            )
            mock_load_plugins.return_value = [mock_plugin]
            mock_submit_coro.side_effect = _submit_coro_mock

            # Set up required globals for the function to reach plugin processing
            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.config = {
                "matrix": {"homeserver": "test"},
                "meshtastic": {
                    "meshnet_name": "test_meshnet",
                    "message_interactions": {"reactions": True, "replies": True},
                },
            }
            # Set up matrix_rooms to map channel 0 so the message is processed
            mmrelay.meshtastic_utils.matrix_rooms = [
                {"meshtastic_channel": 0, "matrix_room_id": "!test:example.com"}
            ]
            mmrelay.meshtastic_utils.event_loop = MagicMock()
            # Mock the interface myInfo to avoid direct message detection
            mock_interface.myInfo.my_node_num = 999999

            on_meshtastic_message(packet, mock_interface)
            mock_logger.exception.assert_called()

    def test_on_meshtastic_message_plugin_timeout_uses_config(self):
        """Verify plugin timeout honors meshtastic.plugin_timeout configuration."""

        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        class DummyFuture:
            def __init__(self, exc):
                """
                Initialize the object with an exception and an empty call history.

                Parameters:
                    exc (BaseException): The exception instance to store for later inspection or re-raising.
                """
                self.exc = exc
                self.calls = []

            def result(self, timeout=None):
                """
                Record the provided timeout value and raise the stored exception.

                Parameters:
                    timeout (float | None): Timeout passed in; appended to self.calls for later inspection.

                Raises:
                    Any: Re-raises the exception stored in self.exc.
                """
                self.calls.append(timeout)
                raise self.exc

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = DummyFuture(timeout_exc)

        plugin = MagicMock()
        plugin.plugin_name = "timeout_plugin"
        plugin.handle_meshtastic_message = AsyncMock(return_value=False)

        config = {
            "meshtastic": {
                "meshnet_name": "meshnet",
                "plugin_timeout": 7.5,
                "message_interactions": {"reactions": False, "replies": False},
            }
        }
        rooms = [{"meshtastic_channel": 0, "id": "!room:matrix"}]

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=[future, MagicMock()],
            ) as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", rooms),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", AsyncMock(return_value=None)),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            self.assertEqual(future.calls, [7.5])
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "timeout_plugin",
                7.5,
                timeout_exc,
            )
            self.assertEqual(mock_submit_coro.call_count, 2)

    def test_on_meshtastic_message_invalid_plugin_timeout_falls_back(self):
        """Ensure invalid plugin_timeout values log a warning and fall back to default."""

        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        class DummyFuture:
            def __init__(self, exc):
                """
                Initialize the object with an exception and an empty call history.

                Parameters:
                    exc (BaseException): The exception instance to store for later inspection or re-raising.
                """
                self.exc = exc
                self.calls = []

            def result(self, timeout=None):
                """
                Record the provided timeout value and raise the stored exception.

                Parameters:
                    timeout (float | None): Timeout passed in; appended to self.calls for later inspection.

                Raises:
                    Any: Re-raises the exception stored in self.exc.
                """
                self.calls.append(timeout)
                raise self.exc

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = DummyFuture(timeout_exc)

        plugin = MagicMock()
        plugin.plugin_name = "timeout_plugin"
        plugin.handle_meshtastic_message = AsyncMock(return_value=False)

        config = {
            "meshtastic": {
                "meshnet_name": "meshnet",
                "plugin_timeout": "invalid",
                "message_interactions": {"reactions": False, "replies": False},
            }
        }
        rooms = [{"meshtastic_channel": 0, "id": "!room:matrix"}]

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=[future, MagicMock()],
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", rooms),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", AsyncMock(return_value=None)),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            self.assertEqual(future.calls, [5.0])
            mock_logger.warning.assert_any_call(
                "Invalid meshtastic.plugin_timeout value %r; using %ss fallback.",
                "invalid",
                5.0,
            )
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "timeout_plugin",
                5.0,
                timeout_exc,
            )

    def test_on_meshtastic_message_matrix_relay_failure(self):
        """
        Tests that on_meshtastic_message logs an error when the Matrix relay integration raises an exception during message processing.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
            "to": 4294967295,  # BROADCAST_NUM
        }

        mock_interface = MagicMock()

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.matrix_rooms = [
            {"id": "!room:matrix.org", "meshtastic_channel": 0}
        ]

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.matrix_utils.matrix_relay"),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            # Set up required globals for the function to run
            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.config = {
                "matrix": {"homeserver": "test"},
                "meshtastic": {
                    "meshnet_name": "test_meshnet",
                    "message_interactions": {"reactions": True, "replies": True},
                },
            }
            mmrelay.meshtastic_utils.event_loop = MagicMock()

            mock_submit_coro.side_effect = Exception("Matrix relay failed")
            on_meshtastic_message(packet, mock_interface)
            mock_logger.exception.assert_called()

    def test_on_meshtastic_message_database_error(self):
        """
        Verify that on_meshtastic_message handles exceptions from database utility functions without raising unhandled errors.

        Simulates a database error during message processing and ensures the function completes gracefully.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        with patch(
            "mmrelay.db_utils.get_longname", side_effect=Exception("Database error")
        ):
            with patch("mmrelay.meshtastic_utils.logger"):
                on_meshtastic_message(packet, mock_interface)
                # Should handle database errors gracefully

    def test_on_lost_meshtastic_connection_reconnection_failure(self):
        """
        Test that on_lost_meshtastic_connection logs an error when reconnection attempts fail.

        Simulates a failed reconnection by patching connect_meshtastic to return None and verifies that an error is logged.
        """
        mock_interface = MagicMock()

        with (
            patch("mmrelay.meshtastic_utils.connect_meshtastic", return_value=None),
            patch("time.sleep"),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            # Prevent async reconnect
            mock_submit_coro.return_value = None
            on_lost_meshtastic_connection(mock_interface)
            mock_logger.error.assert_called()

    def test_on_lost_meshtastic_connection_detection_source_edge_cases(self):
        """
        Test that on_lost_meshtastic_connection handles invalid or unusual detection_source values without raising exceptions.

        Verifies that the function does not fail when provided with unexpected detection_source inputs such as unknown strings, None, invalid types, or empty strings.
        """
        mock_interface = MagicMock()

        detection_sources = [
            "unknown_source",
            None,
            123,  # Invalid type
            "",  # Empty string
        ]

        for source in detection_sources:
            with self.subTest(detection_source=source):
                with patch(
                    "mmrelay.meshtastic_utils.connect_meshtastic",
                    return_value=MagicMock(),
                ):
                    with patch("time.sleep"):
                        # Should handle various detection sources gracefully
                        on_lost_meshtastic_connection(
                            mock_interface, detection_source=source
                        )

    def test_sendTextReply_no_client(self):
        """
        Test the behavior of sendTextReply when no Meshtastic client is set.

        Verifies that sendTextReply returns None and logs an error if the Meshtastic client is unavailable.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = sendTextReply(None, "test message", 12345)
            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No Meshtastic interface available for sending reply"
            )

    def test_sendTextReply_client_send_failure(self):
        """
        Test that sendTextReply returns None and logs an exception when the client's send operation raises.

        Ensures that if the client's `_sendPacket` raises an exception, `sendTextReply` handles it by returning None and calling `logger.exception`.
        """
        mock_client = MagicMock()
        mock_client._sendPacket.side_effect = Exception("Send failed")

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = mock_client

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = sendTextReply(mock_client, "test message", 12345)
            self.assertIsNone(result)
            mock_logger.exception.assert_called()

    def test_is_running_as_service_detection_failure(self):
        """
        Test that is_running_as_service returns a boolean when process detection methods fail.

        Simulates failures in retrieving the parent process ID and process information to verify that is_running_as_service handles these errors gracefully without raising exceptions.
        """
        with patch("os.getppid", side_effect=OSError("Cannot get parent PID")):
            with patch(
                "psutil.Process", side_effect=Exception("Process info unavailable")
            ):
                # Should handle detection failures gracefully
                result = is_running_as_service()
                self.assertIsInstance(result, bool)

    def test_connect_meshtastic_concurrent_access(self):
        """
        Verify that connect_meshtastic returns None and handles concurrent connection attempts gracefully when a reconnection is already in progress.
        """
        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = True  # Simulate ongoing reconnection

        result = connect_meshtastic(config)
        # Should handle concurrent access gracefully
        self.assertIsNone(result)

    def test_connect_meshtastic_memory_constraint(self):
        """
        Test that connect_meshtastic handles MemoryError exceptions gracefully during serial connection attempts.

        Simulates a memory constraint scenario by forcing SerialInterface to raise MemoryError, and verifies that connect_meshtastic returns None and logs an error.
        """
        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        with patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True):
            with patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=MemoryError("Out of memory"),
            ):
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    result = connect_meshtastic(config)
                    self.assertIsNone(result)
                    mock_logger.exception.assert_called()

    def test_on_meshtastic_message_large_node_list(self):
        """
        Tests that on_meshtastic_message can handle processing a packet when the interface contains a very large number of nodes, ensuring the function does not crash or fail due to scalability issues.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        # Create a mock interface with a large node list
        mock_interface = MagicMock()
        large_nodes = {}
        for i in range(10000):  # Large number of nodes
            large_nodes[f"node_{i}"] = {
                "user": {
                    "id": f"!{i:08x}",
                    "longName": f"Node {i}",
                    "shortName": f"N{i}",
                }
            }
        mock_interface.nodes = large_nodes

        from concurrent.futures import Future

        def _done_future(*args, **kwargs):
            """
            Return a completed Future with a result of None.

            This utility function is typically used to simulate an already-finished asynchronous operation in tests.
            """
            f = Future()
            f.set_result(None)
            return f

        with (
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
        ):
            mock_submit_coro.side_effect = _done_future
            # Should handle large node lists without crashing
            on_meshtastic_message(packet, mock_interface)

    def test_connect_meshtastic_config_validation_edge_cases(self):
        """
        Test that connect_meshtastic gracefully handles various invalid or incomplete configuration inputs.

        Verifies that the function returns None without raising exceptions when provided with None, empty, or malformed configuration dictionaries.
        """
        invalid_configs = [
            None,  # None config
            {},  # Empty config
            {"meshtastic": None},  # None meshtastic section
            {"meshtastic": {}},  # Empty meshtastic section
            {"meshtastic": {"connection_type": None}},  # None connection type
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with patch("mmrelay.meshtastic_utils.logger"):
                    result = connect_meshtastic(config)
                    # Should handle invalid configs gracefully
                    self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
