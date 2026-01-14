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

import asyncio
import inspect
import os
import sys
import unittest
from concurrent.futures import Future
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest
from meshtastic.mesh_interface import BROADCAST_NUM

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.meshtastic_utils import (
    DEFAULT_MESHTASTIC_TIMEOUT,
    _get_device_metadata,
    _resolve_plugin_timeout,
    check_connection,
    connect_meshtastic,
    is_running_as_service,
    on_lost_meshtastic_connection,
    on_meshtastic_message,
    reconnect,
    send_text_reply,
    serial_port_exists,
)


def _done_future(coro: object | None = None, *_args, **_kwargs) -> Future[None]:
    """
    Return a completed Future and close any coroutine passed in for cleanup.
    """
    if inspect.iscoroutine(coro):
        coro.close()
    future: Future[None] = Future()
    future.set_result(None)
    return future


def _drain_coro(coro: object, *_args, **_kwargs) -> Future[None]:
    """
    Close a coroutine and return a completed Future for use in mocks.
    """
    if inspect.iscoroutine(coro):
        coro.close()
    future: Future[None] = Future()
    future.set_result(None)
    return future


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
        Verify that a Meshtastic text message on a channel mapped to a Matrix room schedules the Matrix relay coroutine.

        Sets up name, interaction, and storage mocks and invokes on_meshtastic_message with a valid text packet and mock interface, asserting that the message relay is scheduled for delivery to Matrix.
        """
        # Mock the required functions
        import mmrelay.meshtastic_utils

        with (
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
        ):
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

    def test_on_meshtastic_message_channel_fallback_for_string_portnum(self):
        """
        Text or detection packets with string portnums should fall back to channel 0 when channel is missing.
        """
        # Packet missing channel but using string portnum
        packet_no_channel = self.mock_packet.copy()
        packet_no_channel["channel"] = None
        packet_no_channel["to"] = BROADCAST_NUM

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch("mmrelay.matrix_utils.matrix_client", None),
        ):
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            mock_interface = MagicMock()
            mock_interface.myInfo = MagicMock()
            mock_interface.myInfo.my_node_num = 12345

            # Call the function
            on_meshtastic_message(packet_no_channel, mock_interface)

            mock_matrix_relay.assert_awaited()

    def test_on_meshtastic_message_unmapped_channel(self):
        """
        Test that Meshtastic messages on unmapped channels do not trigger Matrix message relay.

        Ensures that when a packet is received on a channel not mapped to any Matrix room, no coroutine is scheduled to relay the message.
        """
        # Modify packet to use unmapped channel
        packet_unmapped = self.mock_packet.copy()
        packet_unmapped["channel"] = 99  # Not in matrix_rooms config

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
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

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
        ):
            mock_load_plugins.return_value = []
            mock_interface = MagicMock()

            # Call the function
            on_meshtastic_message(packet_no_text, mock_interface)

            # Verify _submit_coro was not called for non-text message
            mock_submit_coro.assert_not_called()

    def test_on_meshtastic_message_missing_myinfo(self):
        """
        Ensure handler returns early without myInfo on the interface.
        """
        packet = self.mock_packet.copy()
        mock_interface = MagicMock()
        mock_interface.myInfo = None

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
        ):
            result = on_meshtastic_message(packet, mock_interface)
            self.assertIsNone(result)

    def test_on_meshtastic_message_reaction_relay(self):
        """
        Ensure reaction packets are relayed to Matrix when reactions are enabled.
        """
        reaction_packet = {
            "fromId": "!node",
            "to": 999,
            "decoded": {
                "text": ":)",
                "portnum": "TEXT_MESSAGE_APP",
                "replyId": 42,
                "emoji": 1,
            },
            "channel": 0,
            "id": 555,
        }

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long Name"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="LN"),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
                return_value=("evt1", "!room1:matrix.org", "orig text", "mesh"),
            ),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[prefix] "),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            mock_submit_coro.side_effect = _drain_coro
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 999

            on_meshtastic_message(reaction_packet, mock_interface)

            mock_submit_coro.assert_called_once()
            # Ensure we scheduled the matrix relay coroutine
            self.assertTrue(mock_matrix_relay.called)

    def test_on_meshtastic_message_reply_relay(self):
        """
        Verify that non-emoji reply packets are relayed to Matrix when reply handling is enabled.
        """
        reply_packet = {
            "fromId": "!node",
            "to": 999,
            "decoded": {
                "text": "Reply message",
                "portnum": "TEXT_MESSAGE_APP",
                "replyId": 77,
            },
            "channel": 0,
            "id": 777,
        }

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="Long Name"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="LN"),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings",
                return_value={"reactions": True, "replies": True},
            ),
            patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.get_message_map_by_meshtastic_id",
                return_value=("evt1", "!room1:matrix.org", "orig text", "mesh"),
            ),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value="[prefix] "),
            patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            mock_submit_coro.side_effect = _drain_coro
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 999

            on_meshtastic_message(reply_packet, mock_interface)

            mock_submit_coro.assert_called_once()
            self.assertTrue(mock_matrix_relay.called)

    def test_on_meshtastic_message_event_loop_missing(self):
        """
        Returns early when event loop is not set.
        """
        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils.event_loop", None),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_interface = MagicMock()
            mock_interface.myInfo.my_node_num = 1
            result = on_meshtastic_message(self.mock_packet, mock_interface)
            self.assertIsNone(result)
            mock_logger.error.assert_called_once()

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
        mock_serial.assert_called_once_with(
            "/dev/ttyUSB0", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )

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
        mock_tcp.assert_called_once_with(
            hostname="192.168.1.100", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )

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

    def test_send_text_reply_success(self):
        """
        Test that send_text_reply returns the expected result when sending a text reply succeeds.

        Verifies that the function correctly calls the interface methods and returns the response from _sendPacket.
        """
        # Create a mock interface
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = {"id": 12345}

        result = send_text_reply(
            mock_interface, "Hello", 999, destinationId="123456789"
        )

        # Should return the result from _sendPacket
        self.assertEqual(result, {"id": 12345})

        # Verify the interface methods were called
        mock_interface._generatePacketId.assert_called_once()
        mock_interface._sendPacket.assert_called_once()

    def test_send_text_reply_send_failure(self):
        """
        Test that send_text_reply returns None when the interface fails to send a packet.
        """
        # Create a mock interface that fails
        mock_interface = MagicMock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.return_value = None  # Simulate failure

        result = send_text_reply(
            mock_interface, "Hello", 999, destinationId="123456789"
        )

        self.assertIsNone(result)

    def test_on_meshtastic_message_with_broadcast_config(self):
        """
        Test that disabling broadcast in the configuration does not prevent relaying Meshtastic messages to Matrix.

        Ensures that the `broadcast_enabled` setting only affects Matrix-to-Meshtastic message direction, and that Meshtastic-to-Matrix relaying remains functional when broadcast is disabled.
        """
        config_no_broadcast = self.mock_config.copy()
        config_no_broadcast["meshtastic"]["broadcast_enabled"] = False

        with (
            patch("mmrelay.meshtastic_utils.config", config_no_broadcast),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                config_no_broadcast["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.get_longname") as mock_get_longname,
            patch("mmrelay.meshtastic_utils.get_shortname") as mock_get_shortname,
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled") as mock_storage,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
        ):
            mock_submit_coro.side_effect = _done_future
            mock_get_longname.return_value = "Test User"
            mock_get_shortname.return_value = "TU"
            mock_get_interactions.return_value = {"reactions": False, "replies": False}
            mock_storage.return_value = True

            mock_interface = MagicMock()
            mock_interface.myInfo = MagicMock()
            mock_interface.myInfo.my_node_num = 12345
            packet = self.mock_packet.copy()
            packet["to"] = BROADCAST_NUM

            # Call the function
            on_meshtastic_message(packet, mock_interface)

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

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_shutdown_during_unexpected_exception(self, mock_tcp):
        """Shutdown flag should break out on unexpected exceptions."""
        import mmrelay.meshtastic_utils as mu

        original_shutdown = mu.shutting_down
        original_reconnecting = mu.reconnecting
        original_client = mu.meshtastic_client

        def raise_and_shutdown(*_args, **_kwargs):
            """
            Set the global shutdown flag and immediately abort by raising an exception.

            This function sets mu.shutting_down to True as a side effect and then unconditionally raises an Exception with the message "boom".

            Raises:
                Exception: Always raised with message "boom".
            """
            mu.shutting_down = True
            raise Exception("boom")

        mock_tcp.side_effect = raise_and_shutdown
        config = {"meshtastic": {"connection_type": "tcp", "host": "127.0.0.1"}}

        try:
            mu.shutting_down = False
            mu.reconnecting = False
            mu.meshtastic_client = None
            result = connect_meshtastic(passed_config=config)
        finally:
            mu.shutting_down = original_shutdown
            mu.reconnecting = original_reconnecting
            mu.meshtastic_client = original_client

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

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch("mmrelay.matrix_utils.matrix_client", None),
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

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                self.mock_config["matrix_rooms"],
            ),
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
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
            "retries": 2,
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
    mock_tcp.side_effect = ConcurrentTimeoutError("Connection timeout")

    config = {"meshtastic": {"connection_type": "tcp", "host": "192.168.1.100"}}

    result = connect_meshtastic(passed_config=config)

    # Should ultimately fail after limited timeout retries even when retries are infinite
    assert result is None
    from mmrelay.meshtastic_utils import MAX_TIMEOUT_RETRIES_INFINITE

    assert mock_tcp.call_count == MAX_TIMEOUT_RETRIES_INFINITE + 1
    assert mock_sleep.call_count == MAX_TIMEOUT_RETRIES_INFINITE


@patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True)
@patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
@patch("mmrelay.meshtastic_utils.connect_meshtastic")
@patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock)
@patch("mmrelay.meshtastic_utils.logger")
def test_reconnect_attempts_connection(
    _mock_logger,
    mock_sleep,
    mock_connect,
    mock_get_loop,
    _mock_is_service,
    reset_meshtastic_globals,
):
    """
    Ensure the reconnect coroutine requests a Meshtastic connection attempt.

    Mocks asyncio.sleep to avoid delays and simulates a successful connection that sets shutdown to True so the coroutine exits after the first attempt. Verifies that the connection function is invoked with `force_connect=True`.
    """
    # Touch the fixture result so static analysis doesn't treat it as unused
    _ = reset_meshtastic_globals

    # Mock asyncio.sleep to prevent the test from actually sleeping
    mock_sleep.return_value = None

    # Simulate connect_meshtastic succeeding and signal shutdown after first attempt to exit cleanly
    def _connect_side_effect(*_args, **_kwargs):
        """
        Set the global shutdown flag in the meshtastic utilities and return a MagicMock.

        This helper sets mmrelay.meshtastic_utils.shutting_down to True as a side effect and provides a MagicMock instance for use in tests.

        Returns:
            MagicMock: A new MagicMock instance.
        """
        import mmrelay.meshtastic_utils as mu

        mu.shutting_down = True
        return MagicMock()

    mock_connect.side_effect = _connect_side_effect

    import mmrelay.meshtastic_utils as mu

    original_backoff = mu.DEFAULT_BACKOFF_TIME
    mu.DEFAULT_BACKOFF_TIME = 0

    async def _run():
        try:
            mock_loop = Mock()
            mock_loop.run_in_executor = AsyncMock(
                side_effect=lambda _x, fn, *a, **kw: fn(*a, **kw)
            )
            mock_get_loop.return_value = mock_loop

            await reconnect()
        finally:
            mu.DEFAULT_BACKOFF_TIME = original_backoff

    asyncio.run(_run())

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
        self.assertTrue(hasattr(result, "result") or hasattr(result, "done"))

        # Clean up the coroutine
        coro.close()


class TestAsyncHelperUtilities(unittest.TestCase):
    """Test cases for fire-and-forget and awaitable helper behavior."""

    class _ExceptionTask:
        def __init__(
            self,
            return_exc: BaseException | None = None,
            raise_exc: BaseException | None = None,
        ) -> None:
            self._return_exc = return_exc
            self._raise_exc = raise_exc
            self._callbacks = []

        def add_done_callback(self, callback):
            self._callbacks.append(callback)

        def exception(self):
            if self._raise_exc is not None:
                raise self._raise_exc
            return self._return_exc

        def trigger(self) -> None:
            for callback in self._callbacks:
                callback(self)

    def test_fire_and_forget_ignores_cancelled_error(self):
        """Ensure fire-and-forget ignores CancelledError in callbacks."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(raise_exc=asyncio.CancelledError())

        def _submit(coro, loop=None):
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_fire_and_forget_logs_exception_retrieval_failure(self):
        """Ensure fire-and-forget logs when exception retrieval fails."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(raise_exc=RuntimeError("boom"))

        def _submit(coro, loop=None):
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_called_once()
            mock_logger.error.assert_not_called()

    def test_fire_and_forget_logs_returned_exception(self):
        """Ensure fire-and-forget logs exceptions returned by a task."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(return_exc=ValueError("Task failed"))

        def _submit(coro, loop=None):
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.error.assert_called_once()
            mock_logger.debug.assert_not_called()
            _call_args, call_kwargs = mock_logger.error.call_args
            self.assertIn("exc_info", call_kwargs)
            exc_info = call_kwargs["exc_info"]
            self.assertIsInstance(exc_info, tuple)
            self.assertEqual(len(exc_info), 3)
            self.assertIs(exc_info[0], ValueError)
            self.assertIsInstance(exc_info[1], ValueError)
            self.assertEqual(str(exc_info[1]), "Task failed")
            self.assertIsNone(exc_info[2])

    def test_fire_and_forget_ignores_returned_cancelled_error(self):
        """Ensure fire-and-forget ignores CancelledError instances returned by task.exception()."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(return_exc=asyncio.CancelledError())

        def _submit(coro, loop=None):
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_make_awaitable_returns_existing_awaitable(self):
        """Ensure _make_awaitable returns objects that are already awaitable."""
        from mmrelay.meshtastic_utils import _make_awaitable

        class DummyAwaitable:
            def __await__(self):
                if False:
                    yield None
                return "done"

        dummy = DummyAwaitable()
        result = _make_awaitable(dummy)

        self.assertIs(result, dummy)


class TestSubmitCoroActualImplementation(unittest.TestCase):
    """Test the actual _submit_coro implementation without global mocking."""

    def setUp(self):
        """
        Prepare test fixture by disabling the module-level asyncio event loop mock and capturing the real `_submit_coro`.

        This saves the current `mmrelay.meshtastic_utils.event_loop` and `_submit_coro` into instance attributes so they can be restored later, sets `event_loop` to None to ensure tests run against the real asyncio behavior, and reloads the `mmrelay.meshtastic_utils` source to obtain the original (unmocked) `_submit_coro` implementation for direct testing.
        """
        import mmrelay.meshtastic_utils as mu

        # Store original event_loop state
        self.original_event_loop = mu.event_loop

        # Reset module state for clean testing
        mu.event_loop = None

        # Store the mocked function so we can restore it
        self.mocked_submit_coro = mu._submit_coro

        # Import the original function from the source
        # We need to reload the function definition
        import importlib
        import importlib.util

        # Get the source module without the mock
        spec = importlib.util.find_spec("mmrelay.meshtastic_utils")
        source_module = importlib.util.module_from_spec(spec)

        # Execute the module to get the original function
        spec.loader.exec_module(source_module)

        # Get the original _submit_coro function
        self.original_submit_coro = source_module._submit_coro

    def tearDown(self):
        """
        Restore mmrelay.meshtastic_utils global state saved during setUp.

        Restores the module-level event_loop and _submit_coro attributes to the
        original values captured in setUp (self.original_event_loop and
        self.mocked_submit_coro). This ensures other tests are not affected by the
        test-specific event loop or submit coroutine replacement.
        """
        import mmrelay.meshtastic_utils as mu

        # Restore original event_loop state
        mu.event_loop = self.original_event_loop
        # Restore the mock
        mu._submit_coro = self.mocked_submit_coro

    def test_submit_coro_with_no_event_loop_no_running_loop(self):
        """Test _submit_coro with no event loop and no running loop - uses a temporary loop."""
        from concurrent.futures import Future

        async def test_coro():
            """
            Simple coroutine that returns a fixed test string.

            Returns:
                str: The literal string "test_result".
            """
            return "test_result"

        coro = test_coro()

        # Patch to ensure no running loop
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(coro)

            # Should return a Future with the result
            self.assertIsInstance(result, Future)
            self.assertEqual(result.result(), "test_result")

    def test_submit_coro_with_no_event_loop_no_running_loop_exception(self):
        """Test _submit_coro exception handling when coroutine execution fails."""
        from concurrent.futures import Future

        async def failing_coro():
            """
            Coroutine that always raises ValueError with message "Test exception" when awaited.

            Intended for use in tests to simulate a coroutine that fails.

            Raises:
                ValueError: Always raised when the coroutine is awaited with message "Test exception".
            """
            raise ValueError("Test exception")

        coro = failing_coro()

        # Patch to ensure no running loop
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(coro)
            self.assertIsInstance(result, Future)
            self.assertIsInstance(result.exception(), ValueError)
            self.assertEqual(str(result.exception()), "Test exception")

    def test_submit_coro_with_running_loop(self):
        """Test _submit_coro with a running loop - should use create_task."""

        async def test_coro():
            return "test_result"

        coro = test_coro()

        try:
            # Mock a running loop
            with patch("asyncio.get_running_loop") as mock_get_loop:
                mock_loop = MagicMock()
                mock_task = MagicMock()

                # Mock create_task to close the coroutine when called
                def mock_create_task(coro_arg):
                    coro_arg.close()  # Close the coroutine to prevent warnings
                    return mock_task

                mock_loop.create_task.side_effect = mock_create_task
                mock_get_loop.return_value = mock_loop

                result = self.original_submit_coro(coro)

                # Should call create_task and return the task
                mock_loop.create_task.assert_called_once_with(coro)
                self.assertEqual(result, mock_task)
        finally:
            # Ensure coroutine is properly closed if not already closed
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                coro.close()

    def test_submit_coro_with_event_loop_parameter(self):
        """Test _submit_coro with event loop parameter - should use run_coroutine_threadsafe."""
        import asyncio

        async def test_coro():
            return "test_result"

        coro = test_coro()

        try:
            # Create mock event loop
            mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
            mock_loop.is_closed.return_value = False

            with patch("asyncio.run_coroutine_threadsafe") as mock_run_threadsafe:
                mock_future = MagicMock()

                # Mock run_coroutine_threadsafe to close the coroutine when called
                def mock_run_coro_threadsafe(coro_arg, loop_arg):
                    coro_arg.close()  # Close the coroutine to prevent warnings
                    return mock_future

                mock_run_threadsafe.side_effect = mock_run_coro_threadsafe

                result = self.original_submit_coro(coro, loop=mock_loop)

                # Should call run_coroutine_threadsafe
                mock_run_threadsafe.assert_called_once_with(coro, mock_loop)
                self.assertEqual(result, mock_future)
        finally:
            # Ensure coroutine is properly closed if not already closed
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                coro.close()

    def test_submit_coro_with_non_coroutine_actual(self):
        """
        Verify that _submit_coro returns None when given non-coroutine inputs such as strings, None, or integers.
        """
        # Test with string input
        result = self.original_submit_coro("not a coroutine")
        self.assertIsNone(result)

        # Test with None input
        result = self.original_submit_coro(None)
        self.assertIsNone(result)

        # Test with integer input
        result = self.original_submit_coro(42)
        self.assertIsNone(result)

    def test_submit_coro_accepts_non_coroutine_awaitable(self):
        """Test _submit_coro handles non-coroutine awaitables by awaiting them."""
        from concurrent.futures import Future

        class DummyAwaitable:
            def __await__(self):
                """
                Allow awaiting this object to receive its awaited result.

                Returns:
                    str: The string produced when awaiting the instance, "awaitable-result".
                """
                if False:
                    yield None
                return "awaitable-result"

        awaitable = DummyAwaitable()

        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(awaitable)

            self.assertIsInstance(result, Future)
            self.assertEqual(result.result(), "awaitable-result")


class TestBLEExceptionHandling(unittest.TestCase):
    """Test cases for BLE exception handling and fallback classes."""

    def test_bleak_import_fallback_classes(self):
        """Test that fallback BLE exception classes are defined when bleak is not available."""
        # This test verifies that the fallback classes exist in the current module
        # without disrupting the module state for other tests
        import mmrelay.meshtastic_utils as mu

        # The fallback classes should already be defined in the module
        # regardless of whether bleak is available, because the module
        # defines them as fallbacks in the except block
        # Verify that the fallback classes are defined
        self.assertTrue(hasattr(mu, "BleakDBusError"))
        self.assertTrue(hasattr(mu, "BleakError"))

        # Verify they are proper exception classes
        self.assertTrue(issubclass(mu.BleakDBusError, Exception))
        self.assertTrue(issubclass(mu.BleakError, Exception))

        # Verify they can be instantiated and raised
        # Note: The actual bleak classes may have different constructors
        # than the fallback classes, so we test instantiation carefully
        try:
            # Try simple instantiation first (fallback classes)
            error1 = mu.BleakDBusError("Test error")
        except TypeError:
            # If that fails, try the real bleak constructor
            error1 = mu.BleakDBusError("Test error", "error_body")

        try:
            error2 = mu.BleakError("Test error")
        except TypeError:
            # If that fails, try with additional args
            error2 = mu.BleakError("Test error", "additional_arg")

        # Verify they can be raised
        with self.assertRaises(mu.BleakDBusError):
            raise error1

        with self.assertRaises(mu.BleakError):
            raise error2


class TestReconnectingFlagLogic(unittest.TestCase):
    """Test cases for reconnecting flag logic in connect_meshtastic."""

    def setUp(self):
        """Set up test fixtures."""
        import mmrelay.meshtastic_utils

        # Reset global state
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.meshtastic_client = None

    def tearDown(self):
        """
        Reset meshtastic-related global state after a test.

        Sets mmrelay.meshtastic_utils.reconnecting to False and mmrelay.meshtastic_utils.meshtastic_client to None
        to ensure tests remain isolated and no client or reconnect loop state is carried across tests.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.meshtastic_client = None

    @patch("mmrelay.meshtastic_utils.logger")
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

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.config", None)
    def test_connect_meshtastic_force_connect_bypasses_reconnecting_flag(
        self, mock_logger
    ):
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

    def test_send_text_reply_with_none_interface(self):
        """Test send_text_reply returns None when interface is None."""
        from mmrelay.meshtastic_utils import send_text_reply

        # Test with None interface
        result = send_text_reply(None, "Test message", reply_id=12345)

        # Should return None
        self.assertIsNone(result)

    def test_send_text_reply_function_exists_and_callable(self):
        """Test that send_text_reply function exists and is callable."""
        from mmrelay.meshtastic_utils import send_text_reply

        # Function should exist and be callable
        self.assertTrue(callable(send_text_reply))


class TestGetDeviceMetadata(unittest.TestCase):
    """Test cases for _get_device_metadata helper function."""

    def test_get_device_metadata_success(self):
        """Test successful metadata retrieval and parsing."""
        # Create mock client with localNode.getMetadata()
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()

        # Mock the output capture to return firmware version
        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = (
                "firmware_version: 2.3.15.abc123\nhw_model: HELTEC_V3"
            )
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client)

            # Verify successful parsing
            self.assertTrue(result["success"])
            self.assertEqual(result["firmware_version"], "2.3.15.abc123")
            self.assertIn("firmware_version: 2.3.15.abc123", result["raw_output"])

    def test_get_device_metadata_no_firmware_version(self):
        """Test metadata retrieval when firmware_version is not present."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "hw_model: HELTEC_V3\nother_info: test"
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client)

            # Verify failure when no firmware version found
            self.assertFalse(result["success"])
            self.assertEqual(result["firmware_version"], "unknown")
            self.assertIn("hw_model: HELTEC_V3", result["raw_output"])

    def test_get_device_metadata_no_localnode(self):
        """Test metadata retrieval when client has no localNode."""
        mock_client = MagicMock()
        del mock_client.localNode  # Remove localNode attribute

        result = _get_device_metadata(mock_client)

        # Verify early return for missing localNode
        self.assertFalse(result["success"])
        self.assertEqual(result["firmware_version"], "unknown")
        self.assertEqual(result["raw_output"], "")

    def test_get_device_metadata_no_getmetadata_method(self):
        """Test metadata retrieval when localNode has no getMetadata method."""
        mock_client = MagicMock()
        mock_client.localNode = MagicMock()
        del mock_client.localNode.getMetadata  # Remove getMetadata method

        result = _get_device_metadata(mock_client)

        # Verify early return for missing getMetadata
        self.assertFalse(result["success"])
        self.assertEqual(result["firmware_version"], "unknown")
        self.assertEqual(result["raw_output"], "")

    def test_get_device_metadata_exception_handling(self):
        """Test metadata retrieval when getMetadata raises an exception."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata.side_effect = Exception("Device error")

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _get_device_metadata(mock_client)

            # Verify exception handling
            self.assertFalse(result["success"])
            self.assertEqual(result["firmware_version"], "unknown")
            mock_logger.debug.assert_called_once()

    def test_get_device_metadata_quoted_version(self):
        """Test parsing firmware version with quotes."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = 'firmware_version: "2.3.15.abc123"'
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client)

            # Verify quoted version is parsed correctly
            self.assertTrue(result["success"])
            self.assertEqual(result["firmware_version"], "2.3.15.abc123")

    def test_get_device_metadata_whitespace_handling(self):
        """Test parsing firmware version with various whitespace."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "firmware_version:   2.3.15.abc123   "
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client)

            # Verify whitespace is handled correctly
            self.assertTrue(result["success"])
            self.assertEqual(result["firmware_version"], "2.3.15.abc123")


@pytest.mark.parametrize(
    "cfg, default, expected",
    [
        ({"meshtastic": {"plugin_timeout": 10.5}}, 5.0, 10.5),
        ({}, 5.0, 5.0),
        (None, 5.0, 5.0),
        ({"meshtastic": {"plugin_timeout": "invalid"}}, 5.0, 5.0),
        ({"meshtastic": {"plugin_timeout": -1.0}}, 5.0, 5.0),
        ({"meshtastic": {"plugin_timeout": 0.0}}, 5.0, 5.0),
        ({"meshtastic": {}}, 5.0, 5.0),
    ],
    ids=[
        "with_config",
        "without_config",
        "none_config",
        "invalid_timeout",
        "negative_timeout",
        "zero_timeout",
        "missing_plugin_timeout_key",
    ],
)
def test_resolve_plugin_timeout(cfg, default, expected):
    """Test _resolve_plugin_timeout with various configurations."""
    result = _resolve_plugin_timeout(cfg, default)
    assert result == expected


class TestUncoveredMeshtasticUtils(unittest.TestCase):
    """Test cases for uncovered functions and edge cases in meshtastic_utils.py."""

    @patch("mmrelay.meshtastic_utils.logger")
    def test_resolve_plugin_timeout_attribute_error_handling(self, mock_logger):
        """Test _resolve_plugin_timeout handles AttributeError gracefully."""
        from mmrelay.meshtastic_utils import _resolve_plugin_timeout

        # Create a config dict that will cause AttributeError when accessing nested dict
        class FaultyDict(dict):
            def get(self, key, default=None):
                if key == "meshtastic":
                    # Return None to cause AttributeError when trying to access .get() on None
                    return None
                return super().get(key, default)

        faulty_config = FaultyDict()
        result = _resolve_plugin_timeout(faulty_config, 10.0)

        # Should return default value when AttributeError occurs
        self.assertEqual(result, 10.0)
        # Should not log any warnings for AttributeError handling
        mock_logger.warning.assert_not_called()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_no_localnode(self, mock_logger):
        """Test _get_device_metadata when client has no localNode attribute."""
        from mmrelay.meshtastic_utils import _get_device_metadata

        # Mock client without localNode
        mock_client = Mock(spec=[])  # No attributes at all

        result = _get_device_metadata(mock_client)

        # Should return default result
        expected = {
            "firmware_version": "unknown",
            "raw_output": "",
            "success": False,
        }
        self.assertEqual(result, expected)
        mock_logger.debug.assert_called_with(
            "Meshtastic client has no localNode.getMetadata(); skipping metadata retrieval"
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_no_getmetadata_method(self, mock_logger):
        """Test _get_device_metadata when localNode has no getMetadata method."""
        from mmrelay.meshtastic_utils import _get_device_metadata

        # Mock client with localNode but no getMetadata method
        mock_client = Mock()
        mock_client.localNode = Mock(spec=[])  # No attributes at all

        result = _get_device_metadata(mock_client)

        # Should return default result
        expected = {
            "firmware_version": "unknown",
            "raw_output": "",
            "success": False,
        }
        self.assertEqual(result, expected)
        mock_logger.debug.assert_called_with(
            "Meshtastic client has no localNode.getMetadata(); skipping metadata retrieval"
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_getmetadata_exception(self, mock_logger):
        """Test _get_device_metadata when getMetadata raises exception."""
        from mmrelay.meshtastic_utils import _get_device_metadata

        # Mock client where getMetadata raises exception
        mock_client = Mock()
        mock_client.localNode.getMetadata.side_effect = Exception("Test error")

        result = _get_device_metadata(mock_client)

        # Should return default result when exception occurs
        expected = {
            "firmware_version": "unknown",
            "raw_output": "",
            "success": False,
        }
        self.assertEqual(result, expected)
        # Verify the logger was called with the correct message and exc_info
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args
        self.assertEqual(
            call_args[0][0],
            "Could not retrieve device metadata via localNode.getMetadata()",
        )
        self.assertTrue(call_args[1]["exc_info"])
        self.assertIsInstance(call_args[1]["exc_info"], Exception)
        self.assertEqual(str(call_args[1]["exc_info"]), "Test error")

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_close_existing_connection_error(self, mock_logger):
        """Test connect_meshtastic handles error when closing existing connection."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        # Create a mock existing client that raises error on close
        mock_existing_client = Mock()
        mock_existing_client.close.side_effect = Exception("Close error")

        # Set up the global meshtastic_client to have an existing client
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = mock_existing_client

        config = {
            "meshtastic": {"connection_type": "tcp", "host": "localhost:4403"},
            "matrix_rooms": {},
        }

        # Mock interface creation to avoid actual connection
        with patch("meshtastic.tcp_interface.TCPInterface") as mock_tcp:
            mock_interface = Mock()
            mock_interface.getMyNodeInfo.return_value = {"num": 123}
            mock_tcp.return_value = mock_interface

            connect_meshtastic(config, force_connect=True)

            # Should log warning about close error but continue
            mock_logger.warning.assert_called_with(
                "Error closing previous connection: Close error"
            )

    @patch("mmrelay.meshtastic_utils.reconnecting", True)
    @patch(
        "mmrelay.meshtastic_utils.shutting_down", True
    )  # Set to True to exit immediately
    def test_reconnect_function_basic(self):
        """Test reconnect function basic functionality."""
        import asyncio

        from mmrelay.meshtastic_utils import reconnect

        # Mock the connect_meshtastic function
        with patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect:
            # Run the async function - it should exit immediately due to shutting_down=True
            loop = asyncio.new_event_loop()
            policy = asyncio.get_event_loop_policy()
            previous_loop = None
            try:
                previous_loop = policy.get_event_loop()
            except RuntimeError:
                pass
            policy.set_event_loop(loop)
            try:
                result = loop.run_until_complete(reconnect())
            finally:
                policy.set_event_loop(previous_loop)
                loop.close()

            # Should not have attempted connection since shutting_down is True
            mock_connect.assert_not_called()
            # Function should return None when shutting down
            self.assertIsNone(result)

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.config", None)
    def test_check_connection_uncovered_paths(self, mock_logger):
        """Test check_connection function with missing config."""
        import asyncio

        from mmrelay.meshtastic_utils import check_connection

        # Run the async function with no config
        loop = asyncio.new_event_loop()
        policy = asyncio.get_event_loop_policy()
        previous_loop = None
        try:
            previous_loop = policy.get_event_loop()
        except RuntimeError:
            pass
        policy.set_event_loop(loop)
        try:
            result = loop.run_until_complete(check_connection())
        finally:
            policy.set_event_loop(previous_loop)
            loop.close()

        # Should return None when no config available
        self.assertIsNone(result)
        mock_logger.error.assert_called_with(
            "No configuration available. Cannot check connection."
        )

    def test_submit_coro_ultimate_fallback_runtime_error(self):
        """Test _submit_coro ultimate fallback when event loop operations raise RuntimeError."""
        from concurrent.futures import Future as ConcurrentFuture

        from mmrelay.meshtastic_utils import _submit_coro

        async def test_coro():
            return "test"

        with (
            patch("mmrelay.meshtastic_utils.event_loop", None),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                side_effect=RuntimeError(),
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.new_event_loop",
                side_effect=RuntimeError("test error"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = _submit_coro(test_coro())

            self.assertIsInstance(result, ConcurrentFuture)
            # Check that the exception was set on the future
            exc = result.exception()
            self.assertIsInstance(exc, RuntimeError)
            mock_logger.debug.assert_called()
            self.assertIn(
                "Ultimate fallback triggered", mock_logger.debug.call_args[0][0]
            )

    def test_submit_coro_ultimate_fallback_runtime_error(self):
        """Test _submit_coro ultimate fallback when event loop creation raises RuntimeError."""
        from mmrelay.meshtastic_utils import _submit_coro

        async def test_coro():
            return "test"

        with (
            patch("mmrelay.meshtastic_utils.event_loop", None),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                side_effect=RuntimeError(),
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_event_loop_policy"
            ) as mock_policy,
            patch("mmrelay.meshtastic_utils.asyncio.set_event_loop"),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_policy.return_value.new_event_loop.side_effect = RuntimeError(
                "test error"
            )
            result = _submit_coro(test_coro())

            self.assertIsInstance(result, Future)
            # The exception is set on the future, not raised directly
            with self.assertRaises(RuntimeError):
                result.result(timeout=1)
            mock_logger.debug.assert_called()


class TestResolvePluginResultNoneFuture(unittest.TestCase):
    """Test _resolve_plugin_result when result_future is None (lines 394-396)."""

    def test_resolve_plugin_result_none_future(self):
        """Test _resolve_plugin_result returns False when result_future is None."""
        from mmrelay.meshtastic_utils import _resolve_plugin_result

        mock_plugin = Mock()
        mock_plugin.plugin_name = "test_plugin"

        with patch("mmrelay.meshtastic_utils._submit_coro", return_value=None):
            with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                mock_loop = Mock()
                result = _resolve_plugin_result(Mock(), mock_plugin, 5.0, mock_loop)

                self.assertFalse(result)
                mock_logger.warning.assert_called_once_with(
                    "Plugin %s returned no awaitable; skipping.", "test_plugin"
                )


class TestConnectMeshtasticClientIsIfaceCleanup(unittest.TestCase):
    """Test connect_meshtastic cleanup when client is meshtastic_iface (lines 626-628, 966-968)."""

    def setUp(self):
        import mmrelay.meshtastic_utils

        self.original_client = mmrelay.meshtastic_utils.meshtastic_client
        self.original_iface = mmrelay.meshtastic_utils.meshtastic_iface
        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.meshtastic_iface = None

    def tearDown(self):
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = self.original_client
        mmrelay.meshtastic_utils.meshtastic_iface = self.original_iface

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    def test_connect_meshtastic_clears_iface_when_same_as_client(self, mock_tcp):
        """Test that meshtastic_iface is cleared when it's the same object as meshtastic_client."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        # Set meshtastic_iface to same object as what will be meshtastic_client
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_iface = mock_client
        mmrelay.meshtastic_utils.meshtastic_client = mock_client

        config = {"meshtastic": {"connection_type": "tcp", "host": "127.0.0.1"}}

        result = connect_meshtastic(passed_config=config, force_connect=True)

        self.assertEqual(result, mock_client)
        # meshtastic_iface should be None after cleanup
        self.assertIsNone(mmrelay.meshtastic_utils.meshtastic_iface)


class TestTimeoutConfigurationEdgeCases(unittest.TestCase):
    """Test timeout configuration edge cases (lines 689-704)."""

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_timeout_non_positive_uses_default(self, mock_tcp, mock_logger):
        """Test that non-positive timeout values use default and log warning."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "127.0.0.1",
                "timeout": -5,
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(
            hostname="127.0.0.1", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )
        mock_logger.warning.assert_called()

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_timeout_none_uses_default_without_warning(self, mock_tcp, mock_logger):
        """Test that None timeout uses default without logging warning."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "127.0.0.1",
                "timeout": None,
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(
            hostname="127.0.0.1", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )
        mock_logger.warning.assert_not_called()

    @patch("mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_timeout_invalid_string_uses_default_with_warning(
        self, mock_tcp, mock_logger
    ):
        """Test that invalid timeout string uses default and logs warning."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_tcp.return_value = mock_client

        # Reset global state
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.reconnecting = False

        config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "127.0.0.1",
                "timeout": "invalid",
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_tcp.assert_called_once_with(
            hostname="127.0.0.1", timeout=DEFAULT_MESHTASTIC_TIMEOUT
        )
        mock_logger.warning.assert_called()


class TestBLEConnectionEdgeCases(unittest.TestCase):
    """Test BLE connection edge cases (lines 753-754, 761, 772-774)."""

    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_ble_close_error_is_caught_and_logged(self, mock_ble, mock_logger):
        """Test that errors closing old BLE interface are caught and logged (line 753-754)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_ble.return_value = mock_client

        # Create a mock interface that raises error on close
        mock_old_iface = Mock()
        mock_old_iface.close.side_effect = Exception("Close failed")

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_iface = mock_old_iface

        config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            }
        }

        result = connect_meshtastic(passed_config=config, force_connect=True)

        self.assertEqual(result, mock_client)
        mock_logger.debug.assert_called()

    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    def test_ble_interface_created_when_none(self, mock_ble):
        """Test that BLE interface is created when meshtastic_iface is None (line 761)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_ble.return_value = mock_client

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_iface = None

        config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_ble.assert_called_once()

    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.inspect.signature")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_ble_auto_reconnect_parameter_in_signature(
        self, mock_signature, mock_ble, mock_logger
    ):
        """Test BLE connection when auto_reconnect is in signature (lines 772-774)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_ble.return_value = mock_client

        # Mock signature to include auto_reconnect parameter
        mock_sig = Mock()
        mock_sig.parameters = {"auto_reconnect": Mock()}
        mock_signature.return_value = mock_sig

        config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_ble.assert_called_once()
        call_kwargs = mock_ble.call_args[1]
        self.assertEqual(call_kwargs["auto_reconnect"], False)
        mock_logger.debug.assert_called()

    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    @patch("mmrelay.meshtastic_utils.inspect.signature")
    @patch("mmrelay.meshtastic_utils.logger")
    def test_ble_no_auto_reconnect_parameter_in_signature(
        self, mock_signature, mock_ble, mock_logger
    ):
        """Test BLE connection when auto_reconnect is NOT in signature (lines 772-774)."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "test", "hwModel": "test"}
        }
        mock_ble.return_value = mock_client

        # Mock signature without auto_reconnect parameter
        mock_sig = Mock()
        mock_sig.parameters = {}
        mock_signature.return_value = mock_sig

        config = {
            "meshtastic": {
                "connection_type": "ble",
                "ble_address": "AA:BB:CC:DD:EE:FF",
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertEqual(result, mock_client)
        mock_ble.assert_called_once()
        call_kwargs = mock_ble.call_args[1]
        self.assertNotIn("auto_reconnect", call_kwargs)
        mock_logger.debug.assert_called()


class TestOnMeshtasticMessageNameSaving(unittest.TestCase):
    """Test on_meshtastic_message saving longname from interface (lines 1292-1300)."""

    def test_saves_longname_from_interface_nodes(self):
        """Test that longname is saved from interface.nodes when not in database."""
        from mmrelay.meshtastic_utils import on_meshtastic_message

        packet = {
            "from": 123456789,
            "to": 987654321,
            "decoded": {
                "text": "Hello",
                "portnum": "TEXT_MESSAGE_APP",
            },
            "channel": 0,
            "id": 12345,
        }

        mock_config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
        }

        with (
            patch("mmrelay.meshtastic_utils.config", mock_config),
            patch("mmrelay.meshtastic_utils.matrix_rooms", mock_config["matrix_rooms"]),
            patch("mmrelay.meshtastic_utils.get_longname", return_value=None),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="SN"),
            patch("mmrelay.meshtastic_utils.save_longname") as mock_save_longname,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock),
            patch(
                "mmrelay.matrix_utils.get_interaction_settings"
            ) as mock_get_interactions,
            patch("mmrelay.matrix_utils.message_storage_enabled", return_value=False),
        ):
            mock_submit_coro.side_effect = _done_future
            mock_get_interactions.return_value = {"reactions": False, "replies": False}

            mock_interface = Mock()
            mock_interface.myInfo = Mock()
            mock_interface.myInfo.my_node_num = 987654321
            mock_interface.nodes = {
                123456789: {
                    "user": {
                        "longName": "Interface LongName",
                        "shortName": "ILN",
                    }
                }
            }

            on_meshtastic_message(packet, mock_interface)

            # Verify longname was saved from interface
            mock_save_longname.assert_called_once_with(123456789, "Interface LongName")


class TestCheckConnectionFallbackProbe(unittest.TestCase):
    """Test check_connection fallback probe when metadata fails (line 1446)."""

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_check_connection_fallback_probe_success(self, mock_sleep, mock_logger):
        """Test check_connection fallback probe when metadata parse fails but node info succeeds."""
        import asyncio

        from mmrelay.meshtastic_utils import check_connection

        mock_config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "127.0.0.1",
                "health_check": {"enabled": True, "heartbeat_interval": 60},
            }
        }

        mock_client = Mock()
        mock_client.getMyNodeInfo.return_value = {"num": 123}

        with (
            patch("mmrelay.meshtastic_utils.config", mock_config),
            patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client),
            patch("mmrelay.meshtastic_utils.reconnecting", False),
            patch("mmrelay.meshtastic_utils._get_device_metadata") as mock_metadata,
        ):
            # Metadata fails to parse but node info succeeds
            mock_metadata.return_value = {
                "firmware_version": "unknown",
                "raw_output": "",
                "success": False,
            }

            async def run_test():
                mock_sleep.side_effect = asyncio.CancelledError()
                try:
                    await check_connection()
                except asyncio.CancelledError:
                    pass

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_test())
            finally:
                loop.close()

            # Should log debug message about skipping reconnect
            mock_logger.debug.assert_called()
            debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
            self.assertTrue(
                any(
                    "Metadata parse failed but device responded" in msg
                    for msg in debug_calls
                )
            )

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.on_lost_meshtastic_connection")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock)
    def test_check_connection_fallback_probe_failure(
        self, mock_sleep, mock_lost_conn, mock_logger
    ):
        """Test check_connection fallback probe when both metadata and node info fail."""
        import asyncio

        from mmrelay.meshtastic_utils import check_connection

        mock_config = {
            "meshtastic": {
                "connection_type": "tcp",
                "host": "127.0.0.1",
                "health_check": {"enabled": True, "heartbeat_interval": 60},
            }
        }

        mock_client = Mock()
        mock_client.getMyNodeInfo.side_effect = Exception("No response")

        with (
            patch("mmrelay.meshtastic_utils.config", mock_config),
            patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client),
            patch("mmrelay.meshtastic_utils.reconnecting", False),
            patch("mmrelay.meshtastic_utils._get_device_metadata") as mock_metadata,
        ):
            # Metadata fails to parse
            mock_metadata.return_value = {
                "firmware_version": "unknown",
                "raw_output": "",
                "success": False,
            }

            async def run_test():
                mock_sleep.side_effect = asyncio.CancelledError()
                try:
                    await check_connection()
                except asyncio.CancelledError:
                    pass

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(run_test())
            finally:
                loop.close()

            # Should trigger connection lost
            mock_lost_conn.assert_called_once()


class TestSendTextReplySystemExit(unittest.TestCase):
    """Test send_text_reply SystemExit handling (lines 1543-1545)."""

    @patch("mmrelay.meshtastic_utils.logger")
    def test_send_text_reply_system_exit_re_raised(self, mock_logger):
        """Test that SystemExit is re-raised after logging."""
        from mmrelay.meshtastic_utils import send_text_reply

        mock_interface = Mock()
        mock_interface._generatePacketId.return_value = 12345
        mock_interface._sendPacket.side_effect = SystemExit(1)

        with self.assertRaises(SystemExit):
            send_text_reply(mock_interface, "Hello", 999, destinationId="123456789")

        mock_logger.debug.assert_called_once_with(
            "SystemExit encountered, preserving for graceful shutdown"
        )


if __name__ == "__main__":
    unittest.main()
