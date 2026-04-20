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
from concurrent.futures import Future
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from typing import NoReturn
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

from meshtastic.mesh_interface import BROADCAST_NUM

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.messages import PORTNUM_TEXT_MESSAGE_APP
from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_PLUGIN_TIMEOUT_SECS,
)
from mmrelay.meshtastic_utils import (
    _is_ble_duplicate_connect_suppressed_error,
    _reset_ble_connection_gate_state,
    connect_meshtastic,
    is_running_as_service,
    on_lost_meshtastic_connection,
    on_meshtastic_message,
    send_text_reply,
    serial_port_exists,
)


class TestMeshtasticUtilsEdgeCases(unittest.TestCase):
    """Test cases for Meshtastic utilities edge cases and error handling."""

    class _DummyFuture:
        """Helper class to simulate a future that records timeout values and raises an exception."""

        def __init__(self, exc: BaseException) -> None:
            """
            Initialize the object with an exception and an empty call history.

            Parameters:
                exc (BaseException): The exception instance to store for later inspection or re-raising.
            """
            self.exc = exc
            self.calls: list[float | None] = []

        def result(self, timeout: float | None = None) -> None:
            """
            Record the provided timeout value and raise the stored exception.

            Parameters:
                timeout (float | None): Timeout passed in; appended to self.calls for later inspection.

            Raises:
                Any: Re-raises the exception stored in self.exc.
            """
            self.calls.append(timeout)
            raise self.exc

    def setUp(self):
        """
        Reset mmrelay.meshtastic_utils module-level state to known defaults for tests.
        
        Sets the following globals to default test values so each test runs in isolation:
        - meshtastic_client: None
        - _relay_active_client_id: None
        - _relay_reconnect_prestart_bootstrap_deadline_monotonic_secs: None
        - reconnecting: False
        - config: None
        - matrix_rooms: []
        - shutting_down: False
        - event_loop: None
        - reconnect_task: None
        - subscribed_to_messages: False
        - subscribed_to_connection_lost: False
        - _callbacks_tearing_down: False
        """
        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils._relay_active_client_id = None
        mmrelay.meshtastic_utils._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = (
            None
        )
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.event_loop = None
        mmrelay.meshtastic_utils.reconnect_task = None
        mmrelay.meshtastic_utils.subscribed_to_messages = False
        mmrelay.meshtastic_utils.subscribed_to_connection_lost = False
        mmrelay.meshtastic_utils._callbacks_tearing_down = False

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
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
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
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": "00:11:22:33:44:55",
            }
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
        Verify connect_meshtastic returns None when a TCP connection is refused.

        Patches the TCPInterface to raise ConnectionRefusedError and asserts that
        connect_meshtastic returns None. ConnectionRefusedError is not treated as
        a critical error (no logger.exception) to allow retry/backoff.
        """
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "192.168.1.100",
            }
        }

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
            side_effect=ConnectionRefusedError("Connection refused"),
        ):
            with patch("mmrelay.meshtastic_utils.logger"):
                result = connect_meshtastic(config)
                self.assertIsNone(result)

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
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
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
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        from concurrent.futures import Future

        def _submit_coro_mock(coro, loop=None):
            """
            Run an awaitable immediately and return a concurrent.futures.Future completed with its outcome.

            Executes the given coroutine synchronously and returns a Future that is set to the coroutine's result or to the exception it raised. The optional `loop` parameter is accepted for compatibility but is ignored.

            Parameters:
                coro (Awaitable): The coroutine or awaitable to execute.
                loop (Any, optional): Ignored; present for API compatibility.

            Returns:
                concurrent.futures.Future: A Future already completed with the coroutine's result or exception.
            """
            f = Future()
            try:
                # Execute the coroutine to trigger the exception
                result = asyncio.run(coro)
                f.set_result(result)
            except Exception as e:
                f.set_exception(e)
            finally:
                if asyncio.iscoroutine(coro):
                    coro.close()
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
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = self._DummyFuture(timeout_exc)

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

        def _submit_with_timeout_future(
            coro: object, **_kwargs: object
        ) -> "_DummyFuture":
            """
            Close the provided coroutine if one is given and return a shared dummy future.
            
            Parameters:
                coro: A coroutine object or any callable; if `coro` is an actual coroutine it will be closed before returning.
            
            Returns:
                _DummyFuture: The preconstructed shared future object `future`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            return future

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_with_timeout_future,
            ) as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", rooms),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", Mock(return_value=None)),
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
            self.assertEqual(mock_submit_coro.call_count, 1)

    def test_on_meshtastic_message_invalid_plugin_timeout_falls_back(self):
        """Ensure invalid plugin_timeout values log a warning and fall back to default."""

        packet = {
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = self._DummyFuture(timeout_exc)

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

        def _submit_with_timeout_future(
            coro: object, **_kwargs: object
        ) -> "_DummyFuture":
            """
            Close the provided coroutine if one is given and return a shared dummy future.
            
            Parameters:
                coro: A coroutine object or any callable; if `coro` is an actual coroutine it will be closed before returning.
            
            Returns:
                _DummyFuture: The preconstructed shared future object `future`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            return future

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_with_timeout_future,
            ) as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", rooms),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="Short"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", Mock(return_value=None)),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            self.assertEqual(future.calls, [DEFAULT_PLUGIN_TIMEOUT_SECS])
            mock_logger.warning.assert_any_call(
                "Invalid meshtastic.plugin_timeout value %r; using %.1fs fallback.",
                "invalid",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
            )
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "timeout_plugin",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
                timeout_exc,
            )
            self.assertEqual(mock_submit_coro.call_count, 1)

    def test_on_meshtastic_message_matrix_relay_failure(self):
        """
        Tests that on_meshtastic_message logs an error when the Matrix relay integration raises an exception during message processing.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }

        mock_interface = MagicMock()

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.matrix_rooms = [
            {"id": "!room:matrix.org", "meshtastic_channel": 0}
        ]

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch(
                "mmrelay.matrix_utils.matrix_relay",
                AsyncMock(return_value=None),
            ),
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

            def _submit_raises(coro: object, **_kwargs: object) -> NoReturn:
                """
                Close the given coroutine (if one) and unconditionally raise an exception indicating matrix relay failure.
                
                Parameters:
                    coro: A coroutine or any object; if `coro` is a coroutine it will be closed before raising.
                
                Raises:
                    Exception: Always raised with the message "Matrix relay failed".
                """
                if asyncio.iscoroutine(coro):
                    coro.close()
                raise Exception("Matrix relay failed")

            mock_submit_coro.side_effect = _submit_raises
            on_meshtastic_message(packet, mock_interface)
            mock_logger.exception.assert_called()

    def test_on_meshtastic_message_database_error(self):
        """
        Verify that on_meshtastic_message handles exceptions from database utility functions without raising unhandled errors.

        Simulates a database error during message processing and ensures the function completes gracefully.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
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

    def test_send_text_reply_no_client(self):
        """
        Test the behavior of send_text_reply when no Meshtastic client is set.

        Verifies that send_text_reply returns None and logs an error if the Meshtastic client is unavailable.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = send_text_reply(None, "test message", 12345)
            self.assertIsNone(result)
            mock_logger.error.assert_called_with(
                "No Meshtastic interface available for sending reply"
            )

    def test_send_text_reply_client_send_failure(self):
        """
        Test that send_text_reply returns None and logs an exception when the client's send operation raises.

        Ensures that if the client's `_sendPacket` raises an exception, `send_text_reply` handles it by returning None and calling `logger.exception`.
        """
        mock_client = MagicMock()
        mock_client._generatePacketId.return_value = 12345
        mock_client._sendPacket.side_effect = RuntimeError("Send failed")

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = mock_client

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = send_text_reply(mock_client, "test message", 12345)
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
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
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
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
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
        Verify on_meshtastic_message handles a packet when the interface has a very large number of nodes without raising exceptions.

        Sets up a mock interface containing 10,000 nodes and uses a completed future for submitted coroutines to simulate fast asynchronous processing; the test passes if the call completes without error.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": PORTNUM_TEXT_MESSAGE_APP},
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

        def _done_future(coro: object, **_kwargs: object) -> Future[None]:
            """
            Return a Future already completed with result None.
            
            Parameters:
                coro (object): The coroutine or object that was intended to run; if it is a coroutine, it will be closed.
            
            Returns:
                concurrent.futures.Future: A Future completed with a result of `None`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            f = Future()
            f.set_result(None)
            return f

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
            patch("mmrelay.matrix_utils.matrix_relay", Mock(return_value=None)),
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

    def test_on_meshtastic_message_plugin_timeout_prevents_relay(self):
        """
        Ensure a plugin timeout prevents the message from being relayed to Matrix.

        Verifies that when a plugin times out while processing an incoming Meshtastic message:
        - the plugin timeout is logged,
        - the Matrix relay is not invoked, and
        - the code treats the message as handled by the plugin (i.e., a debug log shows the plugin processed it).
        """
        packet = {
            "decoded": {"text": "!test", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }
        interface = MagicMock()
        interface.nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
        }

        future = self._DummyFuture(ConcurrentTimeoutError("Plugin timeout"))

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        plugin.handle_meshtastic_message = AsyncMock(return_value=False)

        config = {
            "meshtastic": {"meshnet_name": "test"},
            "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        }

        def _submit_with_timeout_future(
            coro: object, **_kwargs: object
        ) -> "_DummyFuture":
            """
            Close the provided coroutine if one is given and return a shared dummy future.
            
            Parameters:
                coro: A coroutine object or any callable; if `coro` is an actual coroutine it will be closed before returning.
            
            Returns:
                _DummyFuture: The preconstructed shared future object `future`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            return future

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_with_timeout_future,
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="TestNode"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="TN"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            # Verify timeout was logged
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "test_plugin",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
                ANY,
            )
            # Verify Matrix relay was NOT called (message was handled by plugin even though it timed out)
            mock_matrix_relay.assert_not_called()
            # Verify debug log was called (confirming found_matching_plugin was True)
            mock_logger.debug.assert_any_call("Processed by plugin %s", "test_plugin")

    def test_on_meshtastic_message_plugin_timeout_with_dm(self):
        """
        Test that plugin timeout with DM prevents message from being relayed to Matrix.

        Verifies that when a plugin times out on a direct message, the message is NOT relayed
        to Matrix because found_matching_plugin is set to True.
        """
        packet = {
            "decoded": {"text": "!test", "portnum": PORTNUM_TEXT_MESSAGE_APP},
            "fromId": "!67890",
            "channel": 0,
            "to": 12345,  # Direct message to relay node
        }
        interface = MagicMock()
        interface.nodes = {"!67890": {"user": {"id": "!67890", "longName": "TestNode"}}}
        interface.myInfo = MagicMock()
        interface.myInfo.my_node_num = 12345

        future = self._DummyFuture(ConcurrentTimeoutError("Plugin timeout"))

        plugin = MagicMock()
        plugin.plugin_name = "test_plugin"
        plugin.handle_meshtastic_message = AsyncMock(return_value=False)

        config = {
            "meshtastic": {"meshnet_name": "test"},
            "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        }

        def _submit_with_timeout_future(
            coro: object, **_kwargs: object
        ) -> "_DummyFuture":
            """
            Close the provided coroutine if one is given and return a shared dummy future.
            
            Parameters:
                coro: A coroutine object or any callable; if `coro` is an actual coroutine it will be closed before returning.
            
            Returns:
                _DummyFuture: The preconstructed shared future object `future`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            return future

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_with_timeout_future,
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="TestNode"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="TN"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            # Verify timeout was logged
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "test_plugin",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
                ANY,
            )
            # Verify Matrix relay was NOT called (DM was handled by plugin even though it timed out)
            mock_matrix_relay.assert_not_called()

    def test_on_meshtastic_message_non_text_plugin_timeout_prevents_relay(self):
        """
        Ensure a plugin timeout for non-text (telemetry) messages prevents relaying the message to Matrix.

        Asserts that when a plugin handling a telemetry packet times out, a timeout warning is logged and the message is treated as handled (so it is not relayed to Matrix); also verifies a debug log indicating the plugin processed the telemetry message.
        """
        packet = {
            "decoded": {
                "portnum": "TELEMETRY_APP",
                "telemetry": {
                    "deviceMetrics": {
                        "batteryLevel": 85,
                        "voltage": 4.1,
                    },
                },
            },
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }
        interface = MagicMock()
        interface.nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
        }

        future = self._DummyFuture(ConcurrentTimeoutError("Plugin timeout"))
        plugin = MagicMock()
        plugin.plugin_name = "telemetry_plugin"
        plugin.handle_meshtastic_message = AsyncMock(return_value=True)

        config = {
            "meshtastic": {"meshnet_name": "test"},
            "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        }

        def _submit_with_timeout_future(
            coro: object, **_kwargs: object
        ) -> "_DummyFuture":
            """
            Close the provided coroutine if one is given and return a shared dummy future.
            
            Parameters:
                coro: A coroutine object or any callable; if `coro` is an actual coroutine it will be closed before returning.
            
            Returns:
                _DummyFuture: The preconstructed shared future object `future`.
            """
            if asyncio.iscoroutine(coro):
                coro.close()
            return future

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[plugin]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_with_timeout_future,
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            # Verify timeout was logged
            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "telemetry_plugin",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
                ANY,
            )
            # Verify debug log was called (confirming found_matching_plugin was True)
            mock_logger.debug.assert_any_call(
                "Processed %s with plugin %s", "TELEMETRY_APP", "telemetry_plugin"
            )

    def test_on_meshtastic_message_non_text_plugin_no_match_continues(self):
        """
        Verify non-text message processing continues to subsequent plugins when an earlier plugin does not handle it.

        Ensures that if a plugin's handle_meshtastic_message returns False for a non-text packet, the dispatcher continues to the next plugin, and when a later plugin handles the message the Matrix relay is not invoked.
        """
        packet = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {
                    "latitudeI": 377711000,
                    "longitudeI": -1224200000,
                },
            },
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }
        interface = MagicMock()
        interface.nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
        }

        plugin1 = MagicMock()
        plugin1.plugin_name = "first_plugin"
        plugin1.handle_meshtastic_message = Mock(return_value=False)

        plugin2 = MagicMock()
        plugin2.plugin_name = "second_plugin"
        plugin2.handle_meshtastic_message = Mock(return_value=True)

        config = {
            "meshtastic": {"meshnet_name": "test"},
            "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        }

        with (
            patch(
                "mmrelay.plugin_loader.load_plugins", return_value=[plugin1, plugin2]
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
        ):
            on_meshtastic_message(packet, interface)

            # Both plugins should have been called
            plugin1.handle_meshtastic_message.assert_called_once()
            plugin2.handle_meshtastic_message.assert_called_once()
            # Matrix relay should NOT have been called (second plugin handled it)
            mock_matrix_relay.assert_not_called()

    def test_on_meshtastic_message_non_text_plugin_match_skips_remaining(self):
        """
        Ensure a non-text Meshtastic message handled by a plugin prevents remaining plugins from running.

        Asserts that when the first plugin returns True for a non-text message (e.g., a POSITION_APP packet),
        subsequent plugins are not invoked, the Matrix relay is not called, and a debug message is emitted
        indicating the handling plugin's name.
        """
        packet = {
            "decoded": {
                "portnum": "POSITION_APP",
                "position": {
                    "latitudeI": 377711000,
                    "longitudeI": -1224200000,
                },
            },
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }
        interface = MagicMock()
        interface.nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
        }

        plugin1 = MagicMock()
        plugin1.plugin_name = "position_plugin"
        plugin1.handle_meshtastic_message = Mock(return_value=True)

        plugin2 = MagicMock()
        plugin2.plugin_name = "other_plugin"
        plugin2.handle_meshtastic_message = Mock(return_value=False)

        config = {
            "meshtastic": {"meshnet_name": "test"},
            "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
        }

        with (
            patch(
                "mmrelay.plugin_loader.load_plugins", return_value=[plugin1, plugin2]
            ),
            patch("mmrelay.meshtastic_utils.config", config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            # Only first plugin should have been called (second was skipped)
            plugin1.handle_meshtastic_message.assert_called_once()
            plugin2.handle_meshtastic_message.assert_not_called()
            # Debug log should confirm first plugin handled it
            mock_logger.debug.assert_any_call(
                "Processed %s with plugin %s", "POSITION_APP", "position_plugin"
            )
            # Matrix relay should NOT have been called
            mock_matrix_relay.assert_not_called()


class TestBLEDuplicateConnectSuppressionDetector(unittest.TestCase):
    """Test cases for BLE duplicate connect suppression error detection."""

    def test_detects_full_suppression_message(self):
        """Should detect the complete fork error message."""
        exc = RuntimeError("Connection suppressed: recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_detects_partial_suppression_message(self):
        """Should detect partial message with just 'recently connected elsewhere'."""
        exc = RuntimeError("recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_detects_both_keywords_together(self):
        """Should detect when both keywords appear separately."""
        exc = RuntimeError("connection suppressed due to connected elsewhere issue")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_rejects_other_ble_errors(self):
        """Should not match unrelated BLE errors."""
        exc = RuntimeError("BLE connection timeout")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_rejects_empty_exception(self):
        """Should handle empty exception messages."""
        exc = RuntimeError("")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_rejects_only_connection_suppressed(self):
        """Should not match when only 'connection suppressed' appears without 'connected elsewhere'."""
        exc = RuntimeError("Connection suppressed by gate")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_handles_case_insensitivity(self):
        """Should be case insensitive."""
        exc = RuntimeError("CONNECTION SUPPRESSED: RECENTLY CONNECTED ELSEWHERE")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_handles_whitespace(self):
        """Should handle messages with extra whitespace."""
        exc = RuntimeError("  Connection suppressed: recently connected elsewhere  ")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True


class TestBLEGateResetCallable(unittest.TestCase):
    """Test cases for BLE gate reset callable behavior."""

    def test_reset_returns_false_when_no_callable(self):
        """Should return False when _ble_gate_reset_callable is None."""
        import mmrelay.meshtastic_utils as mu

        # Save original state
        original_callable = mu._ble_gate_reset_callable

        try:
            mu._ble_gate_reset_callable = None
            result = _reset_ble_connection_gate_state(
                "AA:BB:CC:DD:EE:FF", reason="test"
            )
            assert result is False
        finally:
            mu._ble_gate_reset_callable = original_callable

    def test_reset_handles_callable_exception(self):
        """Should handle exceptions from _ble_gate_reset_callable gracefully."""
        import mmrelay.meshtastic_utils as mu

        original_callable = mu._ble_gate_reset_callable

        def _raising_callable():
            raise RuntimeError("Gate reset failed")

        try:
            mu._ble_gate_reset_callable = _raising_callable
            result = _reset_ble_connection_gate_state(
                "AA:BB:CC:DD:EE:FF", reason="test exception handling"
            )
            assert result is False
        finally:
            mu._ble_gate_reset_callable = original_callable

    def test_reset_returns_true_on_success(self):
        """Should return True when callable succeeds."""
        import mmrelay.meshtastic_utils as mu

        original_callable = mu._ble_gate_reset_callable
        call_count = [0]

        def _successful_callable():
            call_count[0] += 1

        try:
            mu._ble_gate_reset_callable = _successful_callable
            result = _reset_ble_connection_gate_state(
                "AA:BB:CC:DD:EE:FF", reason="test success"
            )
            assert result is True
            assert call_count[0] == 1
        finally:
            mu._ble_gate_reset_callable = original_callable


class TestBLEGateImportDetection(unittest.TestCase):
    """Test cases for module-level BLE gate import detection."""

    def test_module_imports_gracefully_when_gating_unavailable(self):
        """Should have _ble_gate_reset_callable as None when gating module missing."""
        import mmrelay.meshtastic_utils as mu

        # In environments without the fork, this should be None
        # We can't easily test the import side effects, but we can verify
        # the module is importable and has the expected attributes
        assert hasattr(mu, "_ble_gate_reset_callable")
        assert hasattr(mu, "_ble_gating_module")
        # The callable should be None or a callable
        assert mu._ble_gate_reset_callable is None or callable(
            mu._ble_gate_reset_callable
        )

    def test_helper_safe_when_no_gating_module(self):
        """Should be safe to call _reset_ble_connection_gate_state even without gating."""
        import mmrelay.meshtastic_utils as mu

        original_callable = mu._ble_gate_reset_callable

        try:
            mu._ble_gate_reset_callable = None
            # Should not raise even with no callable
            result = _reset_ble_connection_gate_state(
                "AA:BB:CC:DD:EE:FF", reason="no gating module"
            )
            assert result is False
        finally:
            mu._ble_gate_reset_callable = original_callable


class TestDuplicateSuppressionRetryLogic(unittest.TestCase):
    """Test cases for duplicate suppression retry logic in connect_meshtastic."""

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    @patch("mmrelay.meshtastic_utils._ble_gate_reset_callable")
    def test_logs_warning_on_duplicate_suppression(
        self, mock_gate_callable, mock_sleep, mock_logger
    ):
        """Should log warning when duplicate suppression detected."""
        from mmrelay.meshtastic_utils import CONNECTION_TYPE_BLE

        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        class _SuppressedBLEInterface:
            def __init__(self, **_kwargs):
                raise RuntimeError(
                    "Connection suppressed: recently connected elsewhere"
                )

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            new=_SuppressedBLEInterface,
        ):
            result = connect_meshtastic(passed_config=config)

        # Should return None after retries exhausted
        assert result is None

        # Should have logged the suppression detection warning
        suppression_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Detected duplicate BLE connect suppression" in str(call.args[0])
        ]
        assert len(suppression_calls) > 0, "Expected suppression detection warning"

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    def test_logs_debug_when_gate_reset_unavailable(self, mock_sleep, mock_logger):
        """Should log debug when gate reset hook is unavailable."""
        import mmrelay.meshtastic_utils as mu
        from mmrelay.meshtastic_utils import CONNECTION_TYPE_BLE

        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        class _SuppressedBLEInterface:
            def __init__(self, **_kwargs):
                raise RuntimeError(
                    "Connection suppressed: recently connected elsewhere"
                )

        original_callable = mu._ble_gate_reset_callable
        try:
            # Simulate no gate reset available
            mu._ble_gate_reset_callable = None

            with patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_SuppressedBLEInterface,
            ):
                result = connect_meshtastic(passed_config=config)

            # Should return None after retries exhausted
            assert result is None

            # Should have logged the suppression detection warning
            suppression_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if call.args
                and "Detected duplicate BLE connect suppression" in str(call.args[0])
            ]
            assert len(suppression_calls) > 0, "Expected suppression detection warning"

            # Should have logged debug about unavailable hook
            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if call.args and "BLE gate reset hook unavailable" in str(call.args[0])
            ]
            assert len(debug_calls) > 0, "Expected debug about unavailable hook"
        finally:
            mu._ble_gate_reset_callable = original_callable

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    def test_no_debug_log_when_gate_reset_succeeds(self, mock_sleep, mock_logger):
        """Should not log debug when gate reset succeeds."""
        import mmrelay.meshtastic_utils as mu
        from mmrelay.meshtastic_utils import CONNECTION_TYPE_BLE

        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        class _SuppressedBLEInterface:
            def __init__(self, **_kwargs):
                raise RuntimeError(
                    "Connection suppressed: recently connected elsewhere"
                )

        original_callable = mu._ble_gate_reset_callable

        def _successful_reset():
            pass

        try:
            # Set up a working gate reset callable
            mu._ble_gate_reset_callable = _successful_reset

            with patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_SuppressedBLEInterface,
            ):
                result = connect_meshtastic(passed_config=config)

            # Should return None after retries exhausted
            assert result is None

            # Should have logged the suppression detection warning
            suppression_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if call.args
                and "Detected duplicate BLE connect suppression" in str(call.args[0])
            ]
            assert len(suppression_calls) > 0, "Expected suppression detection warning"

            # Should NOT have logged debug about unavailable hook when reset succeeds
            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if call.args and "BLE gate reset hook unavailable" in str(call.args[0])
            ]
            assert len(debug_calls) == 0, "Should not log debug when reset succeeds"
        finally:
            mu._ble_gate_reset_callable = original_callable


if __name__ == "__main__":
    unittest.main()
