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
import threading
import time
import unittest
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from typing import Any, NoReturn
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    STARTUP_PACKET_DRAIN_SECS,
)
from mmrelay.meshtastic_utils import (
    _connect_meshtastic_impl,
    _rollback_connect_attempt_state,
    check_connection,
    connect_meshtastic,
    ensure_meshtastic_callbacks_subscribed,
    reconnect,
    unsubscribe_meshtastic_callbacks,
)
from tests.conftest import cleanup_ble_future_state
from tests.constants import (
    TEST_BLE_MAC,
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
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.config",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_iface",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnecting",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.shutting_down",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnect_task",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnect_task_future",
        None,
        raising=False,
    )
    connect_attempt_lock = threading.RLock()
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._connect_attempt_lock",
        connect_attempt_lock,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._connect_attempt_condition",
        threading.Condition(connect_attempt_lock),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._connect_attempt_in_progress",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.matrix_rooms",
        [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_future",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._callbacks_tearing_down",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.RELAY_START_TIME",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_future_started_at",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_address",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_started_at",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_timeout_secs",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_timeout_counts",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor_degraded",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor_orphaned_workers",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_executor_degraded_addresses",
        set(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_executor_orphaned_workers_by_address",
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


def _reset_ble_inflight_state(module) -> None:
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


class _FakeBLEInterfaceCompat:
    """Test double for BLEInterface that records close calls."""

    def __init__(self, **kwargs: object) -> None:
        self.address = kwargs.get("address")
        self.close = MagicMock()

    def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
        return {"user": {"shortName": "Node", "hwModel": "HW"}}


def _ble_config(ble_address: str = "AA:BB:CC:DD:EE:FF", retries: int = 1) -> dict:
    return {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_BLE,
            "ble_address": ble_address,
            "retries": retries,
        }
    }


def _tcp_config(host: str = "127.0.0.1", retries: int = 1) -> dict:
    return {
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_TCP,
            "host": host,
            "retries": retries,
        }
    }


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
    @patch("mmrelay.meshtastic_utils.time.sleep")
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


def test_none_startup_drain_event_is_safe_noop(reset_meshtastic_globals):
    """Code paths that call .set()/.clear() on the startup drain event must handle None gracefully."""
    import mmrelay.meshtastic_utils as mu

    mu._relay_startup_drain_expiry_timer = MagicMock()
    mu._relay_startup_drain_deadline_monotonic_secs = 123.0
    mu._startup_packet_drain_applied = True
    with patch.object(
        mu, "get_startup_drain_complete_event", return_value=None
    ) as mock_get_event:
        _rollback_connect_attempt_state(
            client=None,
            client_assigned_for_this_connect=False,
            startup_drain_armed_for_this_connect=True,
            startup_drain_applied_for_this_connect=True,
            reconnect_bootstrap_armed_for_this_connect=False,
        )
    mock_get_event.assert_called()


# ---------------------------------------------------------------------------
# Tests migrated from test_meshtastic_utils_callback_lifecycle.py
# ---------------------------------------------------------------------------


class TestEnsureCallbacksSubscribed:
    def test_subscribes_to_both_topics(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False
        mu._callbacks_tearing_down = True

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 2
        mock_subscribe.assert_any_call(mu.on_meshtastic_message, "meshtastic.receive")
        mock_subscribe.assert_any_call(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True
        assert mu._callbacks_tearing_down is False

    def test_idempotent_does_not_double_subscribe(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 2

    def test_skips_already_subscribed_topics(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        mock_subscribe.assert_not_called()

    def test_partial_subscription_only_subscribes_missing(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.subscribe") as mock_subscribe:
            ensure_meshtastic_callbacks_subscribed()

        assert mock_subscribe.call_count == 1
        mock_subscribe.assert_called_once_with(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True


class TestUnsubscribeCallbacks:
    def test_unsubscribes_from_both_topics_when_subscribed(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True
        mu._callbacks_tearing_down = False

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        assert mock_unsubscribe.call_count == 2
        mock_unsubscribe.assert_any_call(mu.on_meshtastic_message, "meshtastic.receive")
        mock_unsubscribe.assert_any_call(
            mu.on_lost_meshtastic_connection, "meshtastic.connection.lost"
        )
        assert mu.subscribed_to_messages is False
        assert mu.subscribed_to_connection_lost is False
        assert mu._callbacks_tearing_down is True

    def test_suppresses_exception_from_unsubscribe_messages(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_messages is True

    def test_suppresses_exception_from_unsubscribe_connection_lost(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = True

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_connection_lost is True

    def test_suppresses_exception_from_both_unsubscribes(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with patch(
            "mmrelay.meshtastic_utils.pub.unsubscribe",
            side_effect=RuntimeError("boom"),
        ):
            unsubscribe_meshtastic_callbacks()

        assert mu.subscribed_to_messages is True
        assert mu.subscribed_to_connection_lost is True

    def test_idempotent_when_already_unsubscribed(self):
        mu.subscribed_to_messages = False
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        mock_unsubscribe.assert_not_called()

    def test_unsubscribes_only_subscribed_topics(self):
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = False

        with patch("mmrelay.meshtastic_utils.pub.unsubscribe") as mock_unsubscribe:
            unsubscribe_meshtastic_callbacks()

        assert mock_unsubscribe.call_count == 1
        mock_unsubscribe.assert_called_once_with(
            mu.on_meshtastic_message, "meshtastic.receive"
        )


class TestConnectMeshtasticShutdownGuard:
    def test_returns_none_when_shutting_down_while_waiting_for_connect(self):
        mu._connect_attempt_in_progress = True
        mu.shutting_down = True

        start = time.monotonic()
        result = connect_meshtastic(passed_config=None)
        elapsed = time.monotonic() - start

        assert result is None
        # NOTE: Timing-based assertion — may be flaky under extreme CI load.
        # The 0.2s threshold is generous; if this becomes flaky, consider removing
        # the timing check and relying only on the `result is None` assertion.
        assert elapsed < 0.2


class TestConnectMeshtasticImplGuards:
    def test_returns_none_when_shutting_down(self):
        mu.shutting_down = True

        result = _connect_meshtastic_impl(passed_config=None, force_connect=False)

        assert result is None

    def test_returns_none_when_reconnecting_and_not_force_connect(self):
        mu.reconnecting = True
        mu.shutting_down = False

        result = _connect_meshtastic_impl(passed_config=None, force_connect=False)

        assert result is None

    def test_proceeds_when_reconnecting_but_force_connect_is_true(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.meshtastic_client = None
        mu.config = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _connect_meshtastic_impl(passed_config=None, force_connect=True)

        assert result is None
        assert not any(
            "Reconnection already in progress" in str(c.args)
            for c in mock_logger.debug.call_args_list
        )
        assert any(
            "No configuration available" in str(c.args)
            for c in mock_logger.error.call_args_list
        )


# ---------------------------------------------------------------------------
# Tests migrated from test_meshtastic_utils_client_cleanup_coverage.py
# ---------------------------------------------------------------------------


class TestBleValidationFailure:
    def test_ble_validation_failure_disconnects_and_returns_none(self):
        config = _ble_config()

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface") as mock_disc,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        mock_disc.assert_called_once()
        call_args = mock_disc.call_args
        assert call_args.kwargs.get("reason") == "address validation failed"
        assert call_args.args[0] is not None
        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "BLE connection validation failed" in str(call.args[0])
        ]
        assert error_calls

    def test_ble_validation_failure_non_iface_client_closes(self):
        config = _ble_config()

        class _FakeBLEWithSideEffect(_FakeBLEInterfaceCompat):
            pass

        orig_init = _FakeBLEWithSideEffect.__init__

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEWithSideEffect,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_by_address"
            ) as mock_disconnect_by_addr,
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface"
            ) as mock_disconnect_iface,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            saved_iface = None

            def _capture_iface(
                self: "_FakeBLEWithSideEffect", **kwargs: object
            ) -> None:
                nonlocal saved_iface
                orig_init(self, **kwargs)
                saved_iface = mu.meshtastic_iface

            _FakeBLEWithSideEffect.__init__ = _capture_iface

            mu.meshtastic_iface = None

            try:
                result = connect_meshtastic(passed_config=config)
            finally:
                _FakeBLEWithSideEffect.__init__ = orig_init

        assert result is None

        # Verify _disconnect_ble_interface was called during the validation failure cleanup
        assert mock_disconnect_iface.call_count >= 1
        # Verify _disconnect_ble_by_address was called during retry (meshtastic_iface was None)
        assert mock_disconnect_by_addr.call_count >= 1

    def test_ble_validation_failure_client_not_iface_uses_close(self):
        config = _ble_config()
        captured_client = None

        def _validate_and_clear_iface(_client: object, _addr: str) -> bool:
            nonlocal captured_client
            captured_client = _client
            mu.meshtastic_iface = None
            return False

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                side_effect=_validate_and_clear_iface,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface"
            ) as mock_disconnect_iface,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        mock_disconnect_iface.assert_not_called()
        assert captured_client is not None
        captured_client.close.assert_called_once()

    def test_ble_validation_disconnect_exception_handled(self):
        config = _ble_config()

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEInterfaceCompat,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=False,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface",
                side_effect=RuntimeError("disconnect failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args and "Error closing invalid BLE connection" in str(call.args[0])
        ]
        assert warning_calls


class TestCleanupFailedAssignedClient:
    def test_tcp_getMyNodeInfo_failure_triggers_close_cleanup(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.side_effect = RuntimeError("node info failed")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_called()
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_ble_getMyNodeInfo_failure_triggers_ble_disconnect_cleanup(self):
        config = _ble_config()

        class _FakeBLEFailsNodeInfo(_FakeBLEInterfaceCompat):
            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                raise RuntimeError("node info failed")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEFailsNodeInfo,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_interface") as mock_disc,
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mock_disc.call_count >= 1
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_cleanup_early_return_when_client_changed(self):
        mock_client = MagicMock()

        other_client = MagicMock()

        def _change_client_side_effect() -> dict[str, dict[str, str]]:
            mu.meshtastic_client = other_client
            mu._relay_active_client_id = id(other_client)
            raise RuntimeError("trigger cleanup")

        mock_client.getMyNodeInfo.side_effect = _change_client_side_effect

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_not_called()
        assert mu.meshtastic_client is other_client

    def test_cleanup_close_exception_logs_warning_and_clears_globals(self):
        mock_client = MagicMock()
        mock_client.close.side_effect = RuntimeError("close failed")
        mock_client.getMyNodeInfo.side_effect = RuntimeError("trigger cleanup")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Error closing Meshtastic client after setup failure"
            in str(call.args[0])
        ]
        assert warning_calls
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_ble_disconnect_cleanup_exception_logs_warning_and_clears_globals(self):
        config = _ble_config()

        class _FakeBLEFailsNodeInfo(_FakeBLEInterfaceCompat):
            def getMyNodeInfo(self) -> dict[str, dict[str, str]]:
                raise RuntimeError("node info failed")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_FakeBLEFailsNodeInfo,
            ),
            patch("mmrelay.meshtastic_utils._disconnect_ble_by_address"),
            patch(
                "mmrelay.meshtastic_utils._validate_ble_connection_address",
                return_value=True,
            ),
            patch(
                "mmrelay.meshtastic_utils._disconnect_ble_interface",
                side_effect=RuntimeError("disconnect cleanup failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None
        warning_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Error closing Meshtastic client after setup failure"
            in str(call.args[0])
        ]
        assert warning_calls
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None

    def test_tcp_metadata_failure_triggers_close_cleanup(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                side_effect=RuntimeError("metadata failed"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(passed_config=_tcp_config())

        assert result is None
        mock_client.close.assert_called()
        assert mu.meshtastic_client is None
        assert mu._relay_active_client_id is None


class TestInconsistentRelayState:
    def test_on_meshtastic_message_logs_error_when_client_none_but_id_set(self):
        mu.meshtastic_client = None
        mu._relay_active_client_id = 12345

        mock_interface = MagicMock()
        packet = {
            "decoded": {"text": "hello", "portnum": "TEXT_MESSAGE_APP"},
            "from": "!abc12345",
            "to": 4294967295,
            "id": 0x12345678,
        }

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu.on_meshtastic_message(packet, mock_interface)

        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if call.args and "Inconsistent relay state" in str(call.args[0])
        ]
        assert error_calls


# ---------------------------------------------------------------------------
# Tests migrated from test_meshtastic_utils_reconnect_bootstrap_coverage.py
# ---------------------------------------------------------------------------


class TestConnectionRefusedReconnectBootstrapCleanup:
    def test_connection_refused_reconnect_bootstrap_cleared(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=ConnectionRefusedError("test refused"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
        assert mu.meshtastic_client is None


class TestTimeoutReconnectBootstrapCleanup:
    def test_timeout_reconnect_bootstrap_cleared(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=[first_client, TimeoutError("retry timeout")],
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=TimeoutError("probe timeout"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None


class TestGenericExceptionReconnectBootstrapCleanup:
    def test_generic_exception_reconnect_bootstrap_cleared(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        mu._startup_packet_drain_applied = True
        mu.subscribed_to_messages = True
        mu.subscribed_to_connection_lost = True

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=[first_client, RuntimeError("retry boom")],
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=RuntimeError("probe boom"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None


# ---------------------------------------------------------------------------
# Tests migrated from test_meshtastic_utils_skew_drain_coverage.py
# ---------------------------------------------------------------------------


class TestSeedConnectTimeSkewExpiredDeadline:
    def test_expired_reconnect_bootstrap_deadline_is_cleared(self):
        now_wall = 100_000.0
        now_mono = 1_000.0

        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = now_mono - 5.0
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = now_mono - 10.0
        mu.RELAY_START_TIME = now_wall - 100.0
        rx_time = now_wall - 50.0

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=now_wall),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            result = mu._seed_connect_time_skew(rx_time)

        assert result is True
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None
        assert mu._relay_rx_time_clock_skew_secs == now_wall - rx_time


class TestConnectMeshtasticDrainArming:
    def test_arms_startup_drain_on_first_connect(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is mock_client
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs == pytest.approx(
            now_mono + STARTUP_PACKET_DRAIN_SECS
        )


class TestConnectionRefusedExceptionHandler:
    def test_connection_refused_after_drain_armed_cleans_up(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }
        now_mono = 1_000.0

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            # Defensive coverage: _schedule_connect_time_calibration_probe catches exceptions
            # internally, so ConnectionRefusedError cannot propagate from it in production.
            # This exercises connect_meshtastic's outer exception handler directly.
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=ConnectionRefusedError("test refused"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._startup_packet_drain_applied is False
        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Tests absorbed from test_meshtastic_utils_edge_cases.py (connect domain)
# ---------------------------------------------------------------------------


class TestConnectMeshtasticConfigAndRetryEdgeCases(unittest.TestCase):
    """Edge case tests for connect_meshtastic config validation and retry."""

    def test_connect_meshtastic_serial_connection_timeout(self):
        """Returns None and logs exception on serial connection timeout."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        with (
            patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=ConcurrentTimeoutError("Connection timeout"),
            ),
            patch("mmrelay.meshtastic_utils.time.sleep"),
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
        """Returns None when TCP connection is refused (not a critical error)."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_TCP,
                "host": "192.168.1.100",
            }
        }

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=ConnectionRefusedError("Connection refused"),
            ),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            result = connect_meshtastic(config)
            self.assertIsNone(result)

    def test_connect_meshtastic_invalid_connection_type(self):
        """Returns None and logs error for invalid connection type."""
        config = {"meshtastic": {"connection_type": "invalid_type"}}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = connect_meshtastic(config)
            self.assertIsNone(result)
            mock_logger.error.assert_called()

    def test_connect_meshtastic_exponential_backoff_max_retries(self):
        """Returns None after max retries on persistent MemoryError."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        with (
            patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=MemoryError("Out of memory"),
            ),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(config)
            self.assertIsNone(result)
            mock_logger.exception.assert_called()

    def test_connect_meshtastic_concurrent_access(self):
        """Returns None when a reconnection is already in progress."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = True
        result = connect_meshtastic(config)
        self.assertIsNone(result)

    def test_connect_meshtastic_memory_constraint(self):
        """Handles MemoryError during serial connection gracefully."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
            }
        }

        with (
            patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.serial_interface.SerialInterface",
                side_effect=MemoryError("Out of memory"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = connect_meshtastic(config)
            self.assertIsNone(result)
            mock_logger.exception.assert_called()

    def test_connect_meshtastic_config_validation_edge_cases(self):
        """Returns None for various invalid configs without raising."""
        invalid_configs = [
            None,
            {},
            {"meshtastic": None},
            {"meshtastic": {}},
            {"meshtastic": {"connection_type": None}},
        ]

        for config in invalid_configs:
            with self.subTest(config=config):
                with patch("mmrelay.meshtastic_utils.logger"):
                    result = connect_meshtastic(config)
                    self.assertIsNone(result)

    def test_timeout_breaks_on_shutdown(self):
        def _timeout_then_shutdown(*_args, **_kwargs) -> NoReturn:
            mu.shutting_down = True
            raise TimeoutError("timeout")

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=_timeout_then_shutdown,
            ),
            patch("mmrelay.meshtastic_utils.time.sleep") as mock_sleep,
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        mock_sleep.assert_not_called()


class TestInfiniteRetriesAbort:
    def test_aborts_after_max_consecutive_timeouts(self):
        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=TimeoutError("timeout"),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils.time.sleep"),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        mock_logger.exception.assert_called_with(
            "Connection timed out after %s attempts (unlimited retries); aborting",
            6,
        )


class TestStartupDrainRaceCondition:
    def test_drain_race_skips_arm_when_already_applied(self):
        mock_client = MagicMock()
        mock_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        def _metadata_side_effect(_client) -> dict[str, Any]:
            mu._startup_packet_drain_applied = True
            return {"firmware_version": "unknown", "success": False}

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                return_value=mock_client,
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                side_effect=_metadata_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is mock_client
        assert mu._startup_packet_drain_applied is True
        assert mu._relay_startup_drain_deadline_monotonic_secs is None


class TestGenericExceptionHandler:
    def test_generic_exception_after_drain_armed_cleans_up(self):
        first_client = MagicMock()
        first_client.getMyNodeInfo.return_value = {
            "user": {"shortName": "Node", "hwModel": "HW"}
        }

        with (
            patch(
                "mmrelay.meshtastic_utils.meshtastic.tcp_interface.TCPInterface",
                side_effect=[first_client, RuntimeError("retry boom")],
            ),
            patch(
                "mmrelay.meshtastic_utils._get_device_metadata",
                return_value={"firmware_version": "unknown", "success": False},
            ),
            # Defensive coverage: _schedule_connect_time_calibration_probe catches RuntimeError
            # internally and returns normally. The patch exercises connect_meshtastic's outer
            # generic exception handler and retry logic directly.
            patch(
                "mmrelay.meshtastic_utils._schedule_connect_time_calibration_probe",
                side_effect=RuntimeError("probe boom"),
            ),
            patch("mmrelay.meshtastic_utils.logger"),
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
        ):
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "127.0.0.1",
                    "retries": 1,
                }
            }
            result = connect_meshtastic(passed_config=config)

        assert result is None
        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._startup_packet_drain_applied is False
        first_client.close.assert_called_once()
