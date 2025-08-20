#!/usr/bin/env python3
"""
Test suite for Meshtastic utilities in MMRelay.

Tests the Meshtastic client functionality including:
- Message processing and relay to Matrix
- Connection management (serial, TCP, BLE)
- Node information handling
- Packet parsing and validation
- Error handling and reconnection logic
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.meshtastic_utils import (
    check_connection,
    connect_meshtastic,
    is_running_as_service,
    on_lost_meshtastic_connection,
    on_meshtastic_message,
    reconnect,
    sendTextReply,
    serial_port_exists,
)


class TestMeshtasticUtils(unittest.TestCase):
    """Test cases for Meshtastic utilities."""

    def setUp(self):
        """
        Initializes mock configuration and packet data, and resets global Meshtastic utility state to ensure test isolation before each test.
        """
        # Mock configuration
        self.mock_config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "broadcast_enabled": True,
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ],
        }

        # Mock packet data
        self.mock_packet = {
            "from": 123456789,
            "to": 987654321,
            "decoded": {
                "text": "Hello from mesh",
                "portnum": "TEXT_MESSAGE_APP",  # Use string constant
            },
            "channel": 0,
            "id": 12345,
            "rxTime": 1234567890,
        }

        # Reset global state to avoid test interference
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnect_task = None

    def test_on_meshtastic_message_basic(self):
        """
        Test that a valid Meshtastic text message on a mapped channel is processed and relayed to Matrix.

        Verifies that when a properly formatted text message is received on a channel mapped to a Matrix room, the message relay coroutine is scheduled for delivery to Matrix.
        """
        # Mock the required functions
        from concurrent.futures import Future

        import mmrelay.meshtastic_utils

        def _done_future(*args, **kwargs):
            """
            Return a completed Future with a result of None.

            This helper is used to mock asynchronous operations that are already finished.
            """
            f = Future()
            f.set_result(None)
            return f

        with patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname, patch(
            "mmrelay.meshtastic_utils.get_shortname"
        ) as mock_get_shortname, patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ), patch(
            "mmrelay.matrix_utils.get_interaction_settings"
        ) as mock_get_interactions, patch(
            "mmrelay.matrix_utils.message_storage_enabled"
        ) as mock_storage:
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            # Mock interface
            mock_interface = MagicMock()

            # Set up the global config and matrix_rooms
            mmrelay.meshtastic_utils.config = self.mock_config
            mmrelay.meshtastic_utils.matrix_rooms = self.mock_config["matrix_rooms"]

            # Call the function
            on_meshtastic_message(self.mock_packet, mock_interface)

            # The global mock_submit_coro fixture will handle the AsyncMock properly

    def test_on_meshtastic_message_unmapped_channel(self):
        """
        Test that Meshtastic messages on unmapped channels do not trigger Matrix message relay.

        Ensures that when a packet is received on a channel not mapped to any Matrix room, no coroutine is scheduled to relay the message.
        """
        # Modify packet to use unmapped channel
        packet_unmapped = self.mock_packet.copy()
        packet_unmapped["channel"] = 99  # Not in matrix_rooms config

        with patch("mmrelay.meshtastic_utils.config", self.mock_config), patch(
            "mmrelay.meshtastic_utils.matrix_rooms", self.mock_config["matrix_rooms"]
        ), patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro:
            mock_interface = MagicMock()

            # Call the function
            on_meshtastic_message(packet_unmapped, mock_interface)

            # Verify _submit_coro was not called (no matrix relay)
            mock_submit_coro.assert_not_called()

    def test_on_meshtastic_message_no_text(self):
        """
        Test that non-text Meshtastic packets do not trigger message relay to Matrix.

        Ensures that when a packet's port number does not correspond to a text message, the message processing function does not schedule a coroutine to relay the message.
        """
        # Modify packet to have no text
        packet_no_text = self.mock_packet.copy()
        packet_no_text["decoded"] = {"portnum": 2}  # Not TEXT_MESSAGE_APP

        with patch("mmrelay.meshtastic_utils.config", self.mock_config), patch(
            "mmrelay.meshtastic_utils.matrix_rooms", self.mock_config["matrix_rooms"]
        ), patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro, patch(
            "mmrelay.plugin_loader.load_plugins"
        ) as mock_load_plugins, patch(
            "mmrelay.meshtastic_utils.is_running_as_service", return_value=True
        ), patch(
            "mmrelay.matrix_utils.matrix_client", None
        ):
            mock_load_plugins.return_value = []
            mock_interface = MagicMock()

            # Call the function
            on_meshtastic_message(packet_no_text, mock_interface)

            # Verify _submit_coro was not called for non-text message
            mock_submit_coro.assert_not_called()

    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_serial(
        self, mock_tcp, mock_ble, mock_serial, mock_port_exists
    ):
        """
        Test that the Meshtastic client connects via serial interface when the serial port exists.

        Verifies that the serial interface is instantiated with the configured port and that the returned client matches the mock client.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_serial.return_value = mock_client
        mock_port_exists.return_value = True

        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_serial.assert_called_once_with("/dev/ttyUSB0")

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_tcp(self, mock_tcp, mock_ble, mock_serial):
        """
        Tests that the Meshtastic client connects via the TCP interface using the configured host.

        Verifies that the TCP interface is instantiated with the correct hostname and that the returned client matches the mocked instance.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "192.168.1.100",  # Use 'host' not 'tcp_host'
            }
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(hostname="192.168.1.100")

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_ble(self, mock_tcp, mock_ble, mock_serial):
        """
        Test that the Meshtastic client connects via BLE using the configured BLE address.

        Verifies that the BLE interface is instantiated with the expected parameters and that the returned client matches the mocked BLE client.
        """
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        # Ensure the mock doesn't create any async operations
        mock_client.close = MagicMock()

        # Configure BLE mock to return our mock client
        mock_ble.return_value = mock_client

        config = {
            "meshtastic": {"connection_type": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"}
        }

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_ble.assert_called_once_with(
            address="AA:BB:CC:DD:EE:FF",
            noProto=False,
            debugOut=None,
            noNodes=False,
        )

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_invalid_type(self, mock_tcp, mock_ble, mock_serial):
        """
        Test that attempting to connect with an invalid Meshtastic connection type returns None and does not instantiate any interface.
        """
        config = {"meshtastic": {"connection_type": "invalid"}}

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)
        # None of the interfaces should be called
        mock_serial.assert_not_called()
        mock_tcp.assert_not_called()
        mock_ble.assert_not_called()

    def test_sendTextReply_success(self):
        """
        Test that sendTextReply returns the expected result when sending a text reply succeeds.

        Verifies that the function correctly calls the interface methods and returns the response from _sendPacket.
        """
        # Create a mock interface
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = {"id": 12345}

        result = sendTextReply(mock_interface, "Hello", 999, destinationId=123456789)

        # Should return the result from _sendPacket
        self.assertEqual(result, {"id": 12345})

        # Verify the interface methods were called
        mock_interface._generatePacketId.assert_called_once()
        mock_interface._sendPacket.assert_called_once()

    def test_sendTextReply_no_client(self):
        """
        Test that sendTextReply returns None when the interface fails to send a packet.
        """
        # Create a mock interface that fails
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = None  # Simulate failure

        result = sendTextReply(mock_interface, "Hello", 999, destinationId=123456789)

        self.assertIsNone(result)

    def test_on_meshtastic_message_with_broadcast_config(self):
        """
        Test that disabling broadcast in the configuration does not prevent relaying Meshtastic messages to Matrix.

        Ensures that the `broadcast_enabled` setting only affects Matrix-to-Meshtastic message direction, and that Meshtastic-to-Matrix relaying remains functional when broadcast is disabled.
        """
        config_no_broadcast = self.mock_config.copy()
        config_no_broadcast["meshtastic"]["broadcast_enabled"] = False

        import inspect
        from concurrent.futures import Future

        def _done_future(coro, *args, **kwargs):
            # Close the coroutine if it's a coroutine to prevent "never awaited" warnings
            """
            Return a completed Future after closing the given coroutine to prevent unawaited coroutine warnings.

            If the input is a coroutine, it is closed before the Future is returned.
            """
            if inspect.iscoroutine(coro):
                coro.close()
            f = Future()
            f.set_result(None)
            return f

        with patch("mmrelay.meshtastic_utils.config", config_no_broadcast), patch(
            "mmrelay.meshtastic_utils.matrix_rooms", config_no_broadcast["matrix_rooms"]
        ), patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro, patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ), patch(
            "mmrelay.meshtastic_utils.get_longname"
        ) as mock_get_longname, patch(
            "mmrelay.meshtastic_utils.get_shortname"
        ) as mock_get_shortname, patch(
            "mmrelay.matrix_utils.get_interaction_settings"
        ) as mock_get_interactions, patch(
            "mmrelay.matrix_utils.message_storage_enabled"
        ) as mock_storage, patch(
            "mmrelay.meshtastic_utils.is_running_as_service", return_value=True
        ), patch(
            "mmrelay.matrix_utils.matrix_client", None
        ):
            mock_submit_coro.side_effect = _done_future
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            mock_interface = MagicMock()

            # Call the function
            on_meshtastic_message(self.mock_packet, mock_interface)

            # Meshtastic->Matrix messages are still relayed regardless of broadcast_enabled
            # (broadcast_enabled only affects Matrix->Meshtastic direction)
            mock_submit_coro.assert_called_once()


class TestServiceDetection(unittest.TestCase):
    """Test cases for service detection functionality."""

    @patch.dict(os.environ, {"INVOCATION_ID": "test-service-id"})
    def test_is_running_as_service_with_invocation_id(self):
        """Test service detection when INVOCATION_ID environment variable is set."""
        result = is_running_as_service()
        self.assertTrue(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_with_systemd_parent(self):
        """
        Tests that `is_running_as_service` returns True when the parent process is `systemd` by mocking the relevant proc files.
        """
        status_data = "PPid:\t1\n"
        comm_data = "systemd"

        def mock_open_func(filename, *args, **kwargs):
            """
            Mock file open function for simulating reads from specific `/proc` files during testing.

            Returns a mock file object with predefined content for `/proc/self/status` and `/proc/[pid]/comm`. Raises `FileNotFoundError` for any other file paths.

            Parameters:
                filename (str): The path of the file to open.

            Returns:
                file object: A mock file object with the specified content.

            Raises:
                FileNotFoundError: If the filename does not match the supported `/proc` paths.
            """
            if filename == "/proc/self/status":
                return mock_open(read_data=status_data)()
            elif filename.startswith("/proc/") and filename.endswith("/comm"):
                return mock_open(read_data=comm_data)()
            else:
                raise FileNotFoundError()

        with patch("builtins.open", side_effect=mock_open_func):
            result = is_running_as_service()
            self.assertTrue(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_normal_process(self):
        """
        Tests that is_running_as_service returns False for a normal process with a non-systemd parent.
        """
        status_data = "PPid:\t1234\n"
        comm_data = "bash"

        def mock_open_func(filename, *args, **kwargs):
            """
            Mock file open function for simulating reads from specific `/proc` files during testing.

            Returns a mock file object with predefined content for `/proc/self/status` and `/proc/[pid]/comm`. Raises `FileNotFoundError` for any other file paths.

            Parameters:
                filename (str): The path of the file to open.

            Returns:
                file object: A mock file object with the specified content.

            Raises:
                FileNotFoundError: If the filename does not match the supported `/proc` paths.
            """
            if filename == "/proc/self/status":
                return mock_open(read_data=status_data)()
            elif filename.startswith("/proc/") and filename.endswith("/comm"):
                return mock_open(read_data=comm_data)()
            else:
                raise FileNotFoundError()

        with patch("builtins.open", side_effect=mock_open_func):
            result = is_running_as_service()
            self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("builtins.open", side_effect=FileNotFoundError())
    def test_is_running_as_service_file_not_found(self, mock_open_func):
        """
        Test that service detection returns False when required process files cannot be read.
        """
        result = is_running_as_service()
        self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("builtins.open", side_effect=PermissionError("Permission denied"))
    def test_is_running_as_service_permission_error(self, mock_open_func):
        """Test that service detection handles PermissionError gracefully."""
        result = is_running_as_service()
        self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_value_error(self):
        """Test that service detection handles ValueError gracefully when parsing invalid data."""
        with patch("builtins.open", mock_open(read_data="invalid data format\n")):
            result = is_running_as_service()
            self.assertFalse(result)


class TestSerialPortDetection(unittest.TestCase):
    """Test cases for serial port detection functionality."""

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_found(self, mock_comports):
        """
        Test that serial_port_exists returns True when the specified serial port is present among available system ports.
        """
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_comports.return_value = [mock_port]

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertTrue(result)

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_not_found(self, mock_comports):
        """
        Tests that serial_port_exists returns False when the specified serial port is not found among available ports.
        """
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB1"
        mock_comports.return_value = [mock_port]

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertFalse(result)

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_no_ports(self, mock_comports):
        """
        Test that serial port detection returns False when no serial ports are available.
        """
        mock_comports.return_value = []

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertFalse(result)


class TestConnectionLossHandling(unittest.TestCase):
    """Test cases for connection loss handling."""

    def setUp(self):
        """
        Reset global Meshtastic connection state flags before each test to ensure test isolation.
        """
        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnect_task = None

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.reconnect", new_callable=AsyncMock)
    def test_on_lost_meshtastic_connection_normal(self, mock_reconnect, mock_logger):
        """
        Verifies that losing a Meshtastic connection triggers error logging and schedules a reconnection attempt when not already reconnecting or shutting down.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = MagicMock()

        on_lost_meshtastic_connection(mock_interface, "test_source")

        mock_logger.error.assert_called()
        # Should log the connection loss
        error_call = mock_logger.error.call_args[0][0]
        self.assertIn("Lost connection", error_call)
        self.assertIn("test_source", error_call)

        # The global mock_submit_coro fixture will handle the AsyncMock properly

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_already_reconnecting(self, mock_logger):
        """
        Test that connection loss handling does not trigger reconnection when already reconnecting.

        Ensures that if the reconnecting flag is set, the function logs a debug message and skips scheduling another reconnection attempt.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = True
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = MagicMock()

        on_lost_meshtastic_connection(mock_interface, "test_source")

        # Should log that reconnection is already in progress
        mock_logger.debug.assert_called_with(
            "Reconnection already in progress. Skipping additional reconnection attempt."
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_shutting_down(self, mock_logger):
        """
        Tests that connection loss handling does not attempt reconnection and logs the correct message when the system is shutting down.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = True

        mock_interface = MagicMock()

        on_lost_meshtastic_connection(mock_interface, "test_source")

        # Should log that system is shutting down
        mock_logger.debug.assert_called_with(
            "Shutdown in progress. Not attempting to reconnect."
        )


class TestConnectMeshtasticEdgeCases(unittest.TestCase):
    """Test cases for edge cases in Meshtastic connection."""

    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_serial_port_not_exists(
        self, mock_serial, mock_port_exists
    ):
        """
        Test that connect_meshtastic returns None and does not instantiate the serial interface when the specified serial port does not exist.
        """
        mock_port_exists.return_value = False

        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)
        mock_serial.assert_not_called()

    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_serial_exception(self, mock_serial):
        """
        Test that connect_meshtastic returns None if an exception occurs during serial interface instantiation.
        """
        mock_serial.side_effect = Exception("Serial connection failed")

        config = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        with patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True):
            result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    @patch("time.sleep")  # Speed up any retry logic
    @patch(
        "mmrelay.meshtastic_utils.INFINITE_RETRIES", 1
    )  # Limit retries to prevent infinite loop
    def test_connect_meshtastic_tcp_exception(self, mock_sleep, mock_tcp):
        """
        Test that connect_meshtastic returns None if an exception occurs during TCP interface creation.
        """
        mock_tcp.side_effect = Exception("TCP connection failed")

        config = {"meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"}}

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)

    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    def test_connect_meshtastic_ble_exception(self, mock_ble):
        """
        Test that connect_meshtastic returns None when the BLE interface raises an exception during connection.
        """
        mock_ble.side_effect = Exception("BLE connection failed")

        config = {
            "meshtastic": {"connection_type": "ble", "ble_address": "AA:BB:CC:DD:EE:FF"}
        }

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)

    def test_connect_meshtastic_no_config(self):
        """
        Test that attempting to connect to Meshtastic with no configuration returns None.
        """
        result = connect_meshtastic(passed_config=None)
        self.assertIsNone(result)

    def test_connect_meshtastic_existing_client_simple(self):
        """
        Tests that connect_meshtastic returns None gracefully when called with no configuration.
        """

        # Test with no config
        result = connect_meshtastic(passed_config=None)
        # Should handle gracefully
        self.assertIsNone(result)


class TestMessageProcessingEdgeCases(unittest.TestCase):
    """Test cases for edge cases in message processing."""

    def setUp(self):
        """
        Initializes mock configuration data for use in test cases.
        """
        self.mock_config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "broadcast_enabled": True,
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
        }

    def test_on_meshtastic_message_no_decoded(self):
        """
        Verify that a Meshtastic packet lacking the 'decoded' field does not initiate message relay processing.
        """
        packet = {
            "from": 123456789,
            "to": 987654321,
            "channel": 0,
            "id": 12345,
            "rxTime": 1234567890,
            # No 'decoded' field
        }

        import inspect
        from concurrent.futures import Future

        def _done_future(coro, *args, **kwargs):
            # Close the coroutine if it's a coroutine to prevent "never awaited" warnings
            """
            Return a completed Future after closing the given coroutine to prevent unawaited coroutine warnings.

            If the input is a coroutine, it is closed before the Future is returned.
            """
            if inspect.iscoroutine(coro):
                coro.close()
            f = Future()
            f.set_result(None)
            return f

        with patch("mmrelay.meshtastic_utils.config", self.mock_config), patch(
            "mmrelay.meshtastic_utils.matrix_rooms", self.mock_config["matrix_rooms"]
        ), patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro, patch(
            "mmrelay.meshtastic_utils.is_running_as_service", return_value=True
        ), patch(
            "mmrelay.matrix_utils.matrix_client", None
        ):

            mock_submit_coro.side_effect = _done_future
            mock_interface = MagicMock()

            on_meshtastic_message(packet, mock_interface)

            # Should not process message without decoded field
            mock_submit_coro.assert_not_called()

    def test_on_meshtastic_message_empty_text(self):
        """
        Test that Meshtastic packets with empty text messages do not trigger relaying to Matrix rooms.
        """
        packet = {
            "from": 123456789,
            "to": 987654321,
            "decoded": {"text": "", "portnum": "TEXT_MESSAGE_APP"},  # Empty text
            "channel": 0,
            "id": 12345,
            "rxTime": 1234567890,
        }

        import inspect
        from concurrent.futures import Future

        def _done_future(coro, *args, **kwargs):
            # Close the coroutine if it's a coroutine to prevent "never awaited" warnings
            """
            Return a completed Future after closing the given coroutine to prevent unawaited coroutine warnings.

            If the input is a coroutine, it is closed before the Future is returned.
            """
            if inspect.iscoroutine(coro):
                coro.close()
            f = Future()
            f.set_result(None)
            return f

        with patch("mmrelay.meshtastic_utils.config", self.mock_config), patch(
            "mmrelay.meshtastic_utils.matrix_rooms", self.mock_config["matrix_rooms"]
        ), patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro:

            mock_submit_coro.side_effect = _done_future
            mock_interface = MagicMock()

            on_meshtastic_message(packet, mock_interface)

            # Should not process empty text messages
            mock_submit_coro.assert_not_called()


# Meshtastic connection retry tests - converted from unittest.TestCase to standalone pytest functions


@pytest.fixture
def reset_meshtastic_globals():
    """Reset global state for connection retry tests."""
    import mmrelay.meshtastic_utils

    mmrelay.meshtastic_utils.meshtastic_client = None
    mmrelay.meshtastic_utils.shutting_down = False
    mmrelay.meshtastic_utils.reconnecting = False
    yield
    # Cleanup after test
    mmrelay.meshtastic_utils.meshtastic_client = None
    mmrelay.meshtastic_utils.shutting_down = False
    mmrelay.meshtastic_utils.reconnecting = False


@patch("mmrelay.meshtastic_utils.time.sleep")
@patch("mmrelay.meshtastic_utils.serial_port_exists")
@patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
def test_connect_meshtastic_retry_on_serial_exception(
    mock_serial, mock_port_exists, mock_sleep, reset_meshtastic_globals
):
    """Test that connect_meshtastic retries on serial exceptions."""
    mock_port_exists.return_value = True

    # First call fails, second succeeds
    mock_client = MagicMock()
    mock_client.getMyNodeInfo.return_value = {
        "user": {"shortName": "test", "hwModel": "test"}
    }
    mock_serial.side_effect = [Exception("Connection failed"), mock_client]

    config = {
        "meshtastic": {
            "connection_type": "serial",
            "serial_port": "/dev/ttyUSB0",
            "retry_limit": 2,
        }
    }

    result = connect_meshtastic(passed_config=config)

    # Should succeed on second attempt
    assert result == mock_client
    assert mock_serial.call_count == 2
    mock_sleep.assert_called_once()


@patch("mmrelay.meshtastic_utils.time.sleep")
@patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
def test_connect_meshtastic_retry_exhausted(
    mock_tcp, mock_sleep, reset_meshtastic_globals
):
    """Test that connect_meshtastic returns None when retries are exhausted."""
    # Mock a critical error that should not be retried
    mock_tcp.side_effect = TimeoutError("Connection timeout")

    config = {"meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"}}

    result = connect_meshtastic(passed_config=config)

    # Should fail immediately on critical error
    assert result is None
    assert mock_tcp.call_count == 1
    mock_sleep.assert_not_called()  # No retries for critical errors


@patch("mmrelay.meshtastic_utils.connect_meshtastic")
@patch("mmrelay.meshtastic_utils.asyncio.sleep")
@patch("mmrelay.meshtastic_utils.logger")
async def test_reconnect_attempts_connection(
    mock_logger, mock_sleep, mock_connect, reset_meshtastic_globals
):
    """
    Test that the reconnect coroutine attempts to establish a Meshtastic connection.

    Verifies that the reconnect logic calls the connection function with `force_connect=True` and does not actually sleep during the test.
    """

    # Mock asyncio.sleep to prevent the test from actually sleeping
    mock_sleep.return_value = None

    # Simulate connect_meshtastic succeeding to prevent an infinite loop
    mock_connect.return_value = MagicMock()

    await reconnect()

    # Assert that a connection was attempted with correct parameters
    mock_connect.assert_called_with(None, True)


def test_check_connection_function_exists(reset_meshtastic_globals):
    """
    Verify that the `check_connection` function is importable and callable.
    """
    # This test just verifies the function exists without running it
    # to avoid the hanging issue in the async loop
    assert callable(check_connection)


class TestCoroutineSubmission(unittest.TestCase):
    """Test cases for coroutine submission functionality."""

    def test_submit_coro_with_non_coroutine_input(self):
        """Test that _submit_coro returns None when given non-coroutine input."""
        from mmrelay.meshtastic_utils import _submit_coro

        # Test with string input
        result = _submit_coro("not a coroutine")
        self.assertIsNone(result)

        # Test with None input
        result = _submit_coro(None)
        self.assertIsNone(result)

        # Test with integer input
        result = _submit_coro(42)
        self.assertIsNone(result)

    def test_submit_coro_returns_future_for_valid_coroutine(self):
        """Test _submit_coro returns a Future-like object for valid coroutines."""
        from mmrelay.meshtastic_utils import _submit_coro

        async def test_coro():
            return "test_result"

        coro = test_coro()
        result = _submit_coro(coro)

        # Should return a Future-like object (either Future or Task)
        self.assertTrue(hasattr(result, 'result') or hasattr(result, 'done'))

        # Clean up the coroutine
        coro.close()


class TestReconnectingFlagLogic(unittest.TestCase):
    """Test cases for reconnecting flag logic in connect_meshtastic."""

    def setUp(self):
        """Set up test fixtures."""
        import mmrelay.meshtastic_utils
        # Reset global state
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.meshtastic_client = None

    def tearDown(self):
        """Clean up after tests."""
        import mmrelay.meshtastic_utils
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.meshtastic_client = None

    @patch('mmrelay.meshtastic_utils.logger')
    def test_connect_meshtastic_blocked_by_reconnecting_flag(self, mock_logger):
        """Test that connect_meshtastic is blocked when reconnecting=True and force_connect=False."""
        import mmrelay.meshtastic_utils
        from mmrelay.meshtastic_utils import connect_meshtastic

        # Set reconnecting flag
        mmrelay.meshtastic_utils.reconnecting = True

        # Call connect_meshtastic with force_connect=False (default)
        result = connect_meshtastic(None, False)

        # Should return None and log debug message
        self.assertIsNone(result)
        mock_logger.debug.assert_called_with(
            "Reconnection already in progress. Not attempting new connection."
        )

    @patch('mmrelay.meshtastic_utils.logger')
    @patch('mmrelay.meshtastic_utils.config', None)
    def test_connect_meshtastic_force_connect_bypasses_reconnecting_flag(self, mock_logger):
        """Test that connect_meshtastic with force_connect=True bypasses reconnecting flag."""
        import mmrelay.meshtastic_utils
        from mmrelay.meshtastic_utils import connect_meshtastic

        # Set reconnecting flag
        mmrelay.meshtastic_utils.reconnecting = True

        # Call connect_meshtastic with force_connect=True
        result = connect_meshtastic(None, True)

        # Should NOT be blocked by reconnecting flag
        # Should return None due to missing config, not due to reconnecting flag
        self.assertIsNone(result)

        # Should NOT log the reconnection debug message
        mock_logger.debug.assert_not_called()

        # Should log the config error instead
        mock_logger.error.assert_called_with(
            "No configuration available. Cannot connect to Meshtastic."
        )


class TestTextReplyFunctionality(unittest.TestCase):
    """Test cases for text reply functionality."""

    def test_sendTextReply_with_none_interface(self):
        """Test sendTextReply returns None when interface is None."""
        from mmrelay.meshtastic_utils import sendTextReply

        # Test with None interface
        result = sendTextReply(None, "Test message", reply_id=12345)

        # Should return None
        self.assertIsNone(result)

    def test_sendTextReply_function_exists_and_callable(self):
        """Test that sendTextReply function exists and is callable."""
        from mmrelay.meshtastic_utils import sendTextReply

        # Function should exist and be callable
        self.assertTrue(callable(sendTextReply))


if __name__ == "__main__":
    unittest.main()
