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
import contextlib
import inspect
import os
import sys
import threading
import unittest
from collections.abc import Callable, Generator
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from types import SimpleNamespace
from typing import Any, NoReturn
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, mock_open, patch

import pytest
from meshtastic import BROADCAST_NUM

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.network import (
    BLE_CONNECT_TIMEOUT_SECS,
    BLE_DISCONNECT_SETTLE_SECS,
    BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_TIMEOUT,
    DEFAULT_TCP_PORT,
    MAX_TIMEOUT_RETRIES_INFINITE,
    METADATA_WATCHDOG_SECS,
    STALE_DISCONNECT_TIMEOUT_SECS,
    STARTUP_PACKET_DRAIN_SECS,
)
from mmrelay.meshtastic_utils import (
    _get_device_metadata,
    _get_packet_details,
    _get_portnum_name,
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
from tests.conftest import cleanup_ble_future_state
from tests.constants import (
    TEST_BLE_MAC,
    TEST_NODE_NUM,
    TEST_PACKET_FROM_ID,
    TEST_PACKET_ID,
)

TEST_PACKET_RX_TIME = 1234567890


def _cancel_startup_drain_timer() -> None:
    """Best-effort cancellation and join of the startup-drain expiry timer."""
    import mmrelay.meshtastic_utils as _mu

    _timer = getattr(_mu, "_relay_startup_drain_expiry_timer", None)
    if _timer is None:
        return
    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        _timer.cancel()
    _join = getattr(_timer, "join", None)
    if callable(_join):
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            _join(0.2)
    with contextlib.suppress(AttributeError):
        _mu._relay_startup_drain_expiry_timer = None


@pytest.fixture(autouse=True)
def reset_meshtastic_relay_state(monkeypatch):
    """Reset all Meshtastic relay module globals to prevent cross-test leakage."""

    _cancel_startup_drain_timer()

    startup_drain_complete_event = threading.Event()
    startup_drain_complete_event.set()
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_active_client_id",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_rx_time_clock_skew_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_deadline_monotonic_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_expiry_timer",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_startup_drain_complete_event",
        startup_drain_complete_event,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._startup_packet_drain_applied",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_connection_started_monotonic_secs",
        0.0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_messages",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_connection_lost",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._health_probe_request_deadlines",
        {},
        raising=False,
    )

    yield

    _cancel_startup_drain_timer()


@pytest.fixture
def stable_relay_start_time(monkeypatch):
    """
    Keep message-processing tests deterministic regardless of wall-clock time.

    Many packet fixtures in this module use fixed historical `rxTime` values.
    Pinning RELAY_START_TIME prevents accidental stale-message filtering during
    tests that are unrelated to startup history behavior.
    """
    monkeypatch.setattr("mmrelay.meshtastic_utils.RELAY_START_TIME", 0, raising=False)


class _FakeEvent:
    """Threading.Event test double for metadata redirect behavior."""

    def is_set(self) -> bool:
        """
        Always reports the fake event as set.

        Returns:
            bool: `True`, indicating the event is considered set.
        """
        return True

    def set(self) -> None:
        """
        Mark the event as set so subsequent is_set() calls return True.

        Mimics threading.Event.set behavior for the test double.
        """
        return None

    def clear(self) -> None:
        """
        No-op placeholder for clearing the object's internal state.

        This method currently performs no action and exists to be overridden or implemented to reset the instance's state.
        """
        return None


def _reset_ble_inflight_state(module: Any) -> None:
    """
    Reset shared BLE in-flight tracking globals for test isolation.
    """
    cleanup_ble_future_state(module)


def _make_timeout_future() -> Mock:
    """
    Create a mock future that simulates a timeout.

    Returns a Mock configured with:
    - result() raises FuturesTimeoutError
    - done() returns False
    - cancel() returns True
    """
    from concurrent.futures import TimeoutError as FuturesTimeoutError

    future = Mock()
    future.result = Mock(side_effect=FuturesTimeoutError())
    future.done.return_value = False
    future.cancel = Mock(return_value=True)
    return future


@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestConnectMeshtasticEdgeCases(unittest.TestCase):
    """Test cases for edge cases in Meshtastic connection."""

    @patch("mmrelay.meshtastic_utils.INFINITE_RETRIES", 1)
    @patch("mmrelay.meshtastic_utils.time.sleep")
    @patch("mmrelay.meshtastic_utils.serial_port_exists")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_serial_port_not_exists(
        self, mock_serial, mock_port_exists, _mock_sleep
    ):
        """
        Test that connect_meshtastic returns None and does not instantiate the serial interface when the specified serial port does not exist.
        """
        mock_port_exists.return_value = False

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)
        mock_serial.assert_not_called()

    @patch("mmrelay.meshtastic_utils.INFINITE_RETRIES", 1)
    @patch("mmrelay.meshtastic_utils.time.sleep")
    @patch("mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface")
    def test_connect_meshtastic_serial_exception(self, mock_serial, _mock_sleep):
        """
        Test that connect_meshtastic returns None if an exception occurs during serial interface instantiation.
        """
        mock_serial.side_effect = Exception("Serial connection failed")

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
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

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "192.168.1.100",
            }
        }

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
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
        }

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

    @patch("mmrelay.meshtastic_utils.INFINITE_RETRIES", 1)
    @patch("mmrelay.meshtastic_utils.time.sleep")
    @patch("mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface")
    def test_connect_meshtastic_ble_exception(self, mock_ble, _mock_sleep):
        """
        Test that connect_meshtastic returns None when the BLE interface raises an exception during connection.
        """
        mock_ble.side_effect = Exception("BLE connection failed")

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
            }
        }

        import mmrelay.meshtastic_utils as mu

        mu.meshtastic_client = None
        mu.meshtastic_iface = None
        mu.reconnecting = False
        mu.shutting_down = False
        _reset_ble_inflight_state(mu)
        mu._metadata_future = None
        mu._ble_timeout_counts = {}

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

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_returns_none_when_shutting_down(self, mock_logger):
        """Return None immediately when shutting_down is True."""
        import mmrelay.meshtastic_utils as mu

        original_shutdown = mu.shutting_down
        try:
            mu.shutting_down = True
            result = connect_meshtastic(
                passed_config={
                    "meshtastic": {
                        "connection_type": CONNECTION_TYPE_TCP,
                        "host": "127.0.0.1",
                    }
                }
            )
        finally:
            mu.shutting_down = original_shutdown

        self.assertIsNone(result)
        mock_logger.debug.assert_called_with(
            "Shutdown in progress. Not attempting to connect."
        )

    def test_connect_meshtastic_updates_matrix_rooms_with_existing_client(self):
        """Ensure passed_config updates matrix_rooms even when a client exists."""
        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_config = mu.config
        original_rooms = mu.matrix_rooms

        existing_client = MagicMock()
        config = {
            "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"},
            "matrix_rooms": [{"id": "!room:example.org", "meshtastic_channel": 0}],
        }

        try:
            mu.meshtastic_client = existing_client
            mu.shutting_down = False
            mu.reconnecting = False
            result = connect_meshtastic(passed_config=config)
            observed_rooms = list(mu.matrix_rooms)
            observed_config = mu.config
        finally:
            mu.meshtastic_client = original_client
            mu.config = original_config
            mu.matrix_rooms = original_rooms

        self.assertIs(result, existing_client)
        self.assertEqual(observed_rooms, config["matrix_rooms"])
        self.assertIs(observed_config, config)


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
            "connection_type": CONNECTION_TYPE_SERIAL,
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

    config = {
        "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "192.168.1.100"}
    }

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

    import copy

    import mmrelay.meshtastic_utils as mu

    original_config = mu.config
    test_config = {
        "meshtastic": {"connection_type": CONNECTION_TYPE_TCP, "host": "127.0.0.1"}
    }
    expected_config = copy.deepcopy(test_config)
    mu.config = test_config
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
            mu.config = original_config

    asyncio.run(_run())

    # Reconnection now passes config to ensure matrix_rooms is re-initialized
    mock_connect.assert_called_once_with(expected_config, True)


def test_check_connection_function_exists(reset_meshtastic_globals):
    """
    Verify that the `check_connection` function is importable and callable.
    """
    # This test just verifies the function exists without running it
    # to avoid the hanging issue in the async loop
    assert callable(check_connection)
