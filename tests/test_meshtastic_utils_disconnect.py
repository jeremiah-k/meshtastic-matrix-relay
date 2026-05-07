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
import sys
import threading
import time
import types
import unittest
from concurrent.futures import Future
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import (
    on_lost_meshtastic_connection,
    reconnect,
)
from tests.conftest import cleanup_ble_future_state

TEST_PACKET_RX_TIME = 1234567890


def _submit_done_reconnect_future(coro: object, _loop: object) -> Future[None]:
    """Create and return an already-completed Future after optionally closing a coroutine-like object."""
    close = getattr(coro, "close", None)
    if callable(close):
        close()
    done_future: Future[None] = Future()
    done_future.set_result(None)
    return done_future


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
        "mmrelay.meshtastic_utils.event_loop",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnect_task_future",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnect_task",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future",
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
        "mmrelay.meshtastic_utils._ble_executor_degraded_addresses",
        set(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_executor_orphaned_workers_by_address",
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

    def tearDown(self):
        """Drain any coroutines submitted via run_coroutine_threadsafe."""

        import mmrelay.meshtastic_utils as mu

        loop = mu.event_loop
        if loop and not loop.is_closed():
            if loop.is_running():
                with contextlib.suppress(RuntimeError, ConcurrentTimeoutError):
                    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(
                        timeout=1
                    )
            else:
                with contextlib.suppress(RuntimeError):
                    loop.run_until_complete(asyncio.sleep(0))

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.reconnect")
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
        # Should log the connection loss (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("Lost connection", error_call)
        self.assertIn("test_source", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_interface_none(self, mock_logger):
        """
        Test that the function handles None interface gracefully.

        When interface is None, _last_disconnect_source check should not raise.
        """
        from pubsub import pub

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        on_lost_meshtastic_connection(
            None, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should use default detection source without error (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("meshtastic.connection.lost", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_official_library_compat(self, mock_logger):
        """
        Test compatibility with official meshtastic library (no _last_disconnect_source).

        The official meshtastic library does not have the _last_disconnect_source
        attribute. The code should gracefully fall back to topic/default detection.
        """
        from pubsub import pub

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        # Simulate official library interface (no _last_disconnect_source)
        mock_interface = Mock(spec=[])
        # No _last_disconnect_source attribute on purpose (official lib shape)

        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should use default detection source (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("meshtastic.connection.lost", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_auto_topic_fallback(self, mock_logger):
        """
        Test that pub.AUTO_TOPIC sentinel triggers default detection source and debug logging.

        When the function is called directly (not via pypubsub) with the default AUTO_TOPIC,
        it should use 'meshtastic.connection.lost' as the detection source.
        """
        from pubsub import pub

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = Mock(spec=[])
        # spec=[] prevents auto-creation of _last_disconnect_source

        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should use default detection source (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("meshtastic.connection.lost", error_call)

        # Should log debug about fallback
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        self.assertTrue(
            any("_last_disconnect_source unavailable" in call for call in debug_calls),
            f"Expected debug log about fallback, got: {debug_calls}",
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_real_topic_name_extraction(
        self, mock_logger
    ):
        """
        Test that a real pypubsub topic object's name is extracted as detection_source.

        When called via pypubsub with a real Topic object, the topic's getName() method
        should be used to extract the topic name, not str(topic).
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = Mock(spec=[])
        # spec=[] prevents auto-creation of _last_disconnect_source

        # Create a mock topic object (simulating pypubsub Topic with getName())
        class MockTopic:
            def getName(self):
                """
                Get the canonical name for the Meshtastic connection-lost topic.

                Returns:
                    str: The topic name "meshtastic.connection.lost".
                """
                return "meshtastic.connection.lost"

            def __str__(self):
                """
                Provide a sentinel string indicating this __str__ implementation is not intended for use.

                Returns:
                    str: The sentinel string "should.not.be.used".
                """
                return "should.not.be.used"

        mock_topic = MockTopic()

        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=mock_topic
        )

        # Should use the topic's getName() method, not __str__ (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("meshtastic.connection.lost", error_call)
        self.assertNotIn("should.not.be.used", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_topic_str_fallback(self, mock_logger):
        """
        Test that str(topic) works correctly as a fallback for topic name extraction.

        The production code in on_lost_meshtastic_connection uses:
            detection_source = getattr(topic, "getName", lambda: str(topic))()

        This means getName() is the primary mechanism for extracting the topic name,
        and str(topic) is only used as the lambda fallback when the topic lacks a
        getName method. This test verifies that fallback behavior works correctly.
        """
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = Mock(spec=[])
        # spec=[] prevents auto-creation of _last_disconnect_source

        # Test with a simple object that has __str__
        class SimpleTopic:
            def __str__(self):
                """
                Provide a human-readable string representation of the topic.

                Returns:
                    str: The literal string "custom.topic.name" representing this topic.
                """
                return "custom.topic.name"

        simple_topic = SimpleTopic()

        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=simple_topic
        )

        # Should use str(topic) (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("custom.topic.name", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_already_reconnecting(self, mock_logger):
        """
        Test that connection loss handling does not trigger reconnection when already reconnecting.

        Ensures that if the reconnecting flag is set, the function logs a debug message
        and skips scheduling another reconnection attempt.
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
        Tests that connection loss handling does not attempt reconnection and logs
        the correct message when the system is shutting down.
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

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_ble_disconnect_source(self, mock_logger):
        """
        Test that detection_source is derived from BLE interface _last_disconnect_source when available.

        When a BLE interface has a valid _last_disconnect_source attribute with 'ble.' prefix,
        the prefix is stripped to make the detection source library-agnostic.
        """
        from pubsub import pub

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = MagicMock()
        # BLE interface prefixes with 'ble.' in _last_disconnect_source
        mock_interface._last_disconnect_source = "ble.user_disconnect"

        # Call with unknown detection_source and AUTO_TOPIC (default behavior)
        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should use the BLE disconnect source with 'ble.' prefix stripped (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("user_disconnect", error_call)
        self.assertNotIn("ble.user_disconnect", error_call)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_on_lost_meshtastic_connection_ble_disconnect_source_whitespace(
        self, mock_logger
    ):
        """
        Test that whitespace-only _last_disconnect_source is ignored and fallback is used.
        """
        from pubsub import pub

        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.shutting_down = False

        mock_interface = MagicMock()
        mock_interface._last_disconnect_source = "   "
        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should fall back to default detection source (first error call before reconnect scheduling)
        error_call = mock_logger.error.call_args_list[0][0][0]
        self.assertIn("meshtastic.connection.lost", error_call)


# ---------------------------------------------------------------------------
# Helpers for reconnect tests (absorbed from reconnect + reconnect_paths)
# ---------------------------------------------------------------------------


def _mark_shutdown(*_args, **_kwargs) -> None:
    """Set the shutting_down flag. Suitable for side_effect or direct call."""
    mu.shutting_down = True


class _DummyColumn:
    def __init__(self, *args, **kwargs):
        pass


class _FailedExecutorLoop:
    def __init__(self, future):
        self._future = future

    def run_in_executor(self, *_args, **_kwargs):
        return self._future


# ---------------------------------------------------------------------------
# Reconnect tests absorbed from test_meshtastic_utils_reconnect.py
# ---------------------------------------------------------------------------


def test_on_lost_meshtastic_connection_reconnection_failure():
    """Exercises the reconnect failure path when connect_meshtastic returns None."""
    mock_interface = MagicMock()

    import mmrelay.meshtastic_utils

    mmrelay.meshtastic_utils.reconnecting = False
    mmrelay.meshtastic_utils.shutting_down = False

    _connect_call_count = [0]

    def _connect_side_effect(*_args, **_kwargs):
        """Return None. On second call, signal shutdown to stop the retry loop."""
        _connect_call_count[0] += 1
        if _connect_call_count[0] >= 2:
            mmrelay.meshtastic_utils.shutting_down = True
        return None

    def _schedule_and_run_reconnect():
        """Run the real reconnect() coroutine to exercise its failure branch."""
        import asyncio

        original_backoff = mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME
        mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME = 0
        try:
            reconnect_coro = mmrelay.meshtastic_utils.reconnect()
            asyncio.run(reconnect_coro)
        finally:
            mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME = original_backoff

    with (
        patch(
            "mmrelay.meshtastic_utils.connect_meshtastic",
            side_effect=_connect_side_effect,
        ),
        patch(
            "mmrelay.meshtastic.events._schedule_reconnect_after_disconnect",
            side_effect=_schedule_and_run_reconnect,
        ),
        patch(
            "mmrelay.meshtastic_utils.is_running_as_service",
            return_value=True,
        ),
        patch("mmrelay.meshtastic_utils.config", {}),
        patch("time.sleep"),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        on_lost_meshtastic_connection(mock_interface)

        # Verify reconnect was invoked
        mock_logger.info.assert_any_call(
            "Reconnection attempt starting in 0 seconds..."
        )

        # Verify the reconnect failure branch was exercised
        mock_logger.warning.assert_any_call(
            "Reconnection attempt did not produce a client; backing off"
        )


def test_on_lost_meshtastic_connection_detection_source_edge_cases():
    """Handles unusual detection_source values without raising exceptions."""
    mock_interface = MagicMock()

    import mmrelay.meshtastic_utils

    mmrelay.meshtastic_utils.reconnecting = False

    detection_sources = [
        "unknown_source",
        None,
        123,
        "",
    ]

    for source in detection_sources:
        mmrelay.meshtastic_utils.reconnecting = False
        with (
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                return_value=MagicMock(),
            ),
            patch("mmrelay.meshtastic.events._schedule_reconnect_after_disconnect"),
            patch("time.sleep"),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_lost_meshtastic_connection(mock_interface, detection_source=source)
            assert mock_logger.error.called or mock_logger.debug.called


@pytest.mark.asyncio
class TestReconnectSuccess:
    async def test_reconnect_succeeds_and_clears_future_and_flag(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        def _connect_side_effect(_cfg, _force):
            mu.meshtastic_client = mock_client
            return mock_client

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            await reconnect()

        assert mu.meshtastic_client is mock_client
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    async def test_reconnect_success_does_not_republish_client_global(self):
        mock_client = MagicMock()
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        mu.meshtastic_client = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic", return_value=mock_client
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        ):
            await reconnect()

        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False
        assert mu.meshtastic_client is None


@pytest.mark.asyncio
class TestReconnectCancellation:
    async def test_reconnect_cancellation_logs_and_clears_state(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError,
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        mock_logger.info.assert_any_call("Reconnection task was cancelled.")
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None


class TestConnectionLostHandlerClearingStaleBleFuture:
    """Test connection lost handler clearing stale BLE future."""

    def test_on_lost_meshtastic_connection_clears_ble_future_globals(self):
        """Test that _ble_future, _ble_future_address, _ble_future_started_at, _ble_future_timeout_secs are cleared."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts["AA:BB:CC:DD:EE:FF"] = 5
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False

        with (
            patch("mmrelay.meshtastic_utils.reconnect"),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=_submit_done_reconnect_future,
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            mu.on_lost_meshtastic_connection(None, detection_source="test source")

            assert mu._ble_future is None
            assert mu._ble_future_address is None
            assert mu._ble_future_started_at is None
            assert mu._ble_future_timeout_secs is None
            assert "AA:BB:CC:DD:EE:FF" not in mu._ble_timeout_counts

    def test_on_lost_meshtastic_connection_clears_ble_timeout_counts(self):
        """Test _ble_timeout_counts is popped for the address."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._ble_future = mock_future
        mu._ble_future_address = "11:22:33:44:55:66"
        mu._ble_future_started_at = time.monotonic()
        mu._ble_future_timeout_secs = 30.0
        mu._ble_timeout_counts["11:22:33:44:55:66"] = 10
        mu._ble_timeout_counts["OTHER:ADDRESS"] = 3
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False

        with (
            patch("mmrelay.meshtastic_utils.reconnect"),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=_submit_done_reconnect_future,
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            mu.on_lost_meshtastic_connection(None, detection_source="test source")

            assert "11:22:33:44:55:66" not in mu._ble_timeout_counts
            assert mu._ble_timeout_counts["OTHER:ADDRESS"] == 3


@pytest.mark.asyncio
class TestReconnectShutdownAbort:
    async def test_shutdown_during_backoff_aborts_reconnect(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=_mark_shutdown,
            ),
            patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        mock_connect.assert_not_called()
        mock_logger.debug.assert_any_call(
            "Shutdown in progress. Aborting reconnection attempts."
        )
        assert mu.reconnecting is False


@pytest.mark.asyncio
class TestReconnectFailureBackoff:
    async def test_connect_failure_logs_exception_and_clears_state(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(_cfg, _force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 2:
                mu.shutting_down = True
            raise ConnectionError("connection refused")

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()

        assert attempt_count == 2
        assert any(
            "Reconnection attempt failed" in str(c.args)
            for c in mock_logger.exception.call_args_list
        )
        assert mu.reconnecting is False
        assert mu.reconnect_task_future is None

    async def test_reconnect_task_future_cleared_after_failure(self):
        mu.reconnecting = True
        mu.shutting_down = False
        mu.reconnect_task_future = None
        attempt_count = 0

        def _connect_side_effect(_cfg, _force):
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count >= 2:
                mu.shutting_down = True
            raise RuntimeError("unexpected error")

        with (
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
            patch(
                "mmrelay.meshtastic_utils.connect_meshtastic",
                side_effect=_connect_side_effect,
            ),
            patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            await reconnect()

        assert attempt_count == 2
        assert mu.reconnect_task_future is None
        assert mu.reconnecting is False


# ---------------------------------------------------------------------------
# Reconnect path tests absorbed from test_meshtastic_utils_reconnect_paths.py
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_rich_progress_breaks_on_shutdown():
    mock_progress_instance = MagicMock()
    mock_progress_class = MagicMock()
    mock_progress_class.return_value.__enter__.return_value = mock_progress_instance

    fake_rich = types.ModuleType("rich")
    fake_progress = types.ModuleType("rich.progress")
    fake_progress.Progress = mock_progress_class
    fake_progress.BarColumn = _DummyColumn
    fake_progress.TextColumn = _DummyColumn
    fake_progress.TimeRemainingColumn = _DummyColumn
    fake_rich.progress = fake_progress

    with (
        patch.dict(sys.modules, {"rich": fake_rich, "rich.progress": fake_progress}),
        patch("mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME", 1),
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_mark_shutdown,
        ),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await reconnect()

    mock_connect.assert_not_called()
    mock_progress_instance.update.assert_called_once()
    mock_logger.debug.assert_any_call(
        "Shutdown in progress. Aborting reconnection attempts."
    )


@pytest.mark.asyncio
async def test_reconnect_logs_exception_and_backs_off():
    running_loop = asyncio.get_running_loop()
    failed_future = running_loop.create_future()
    failed_future.set_exception(RuntimeError("boom"))
    loop = _FailedExecutorLoop(failed_future)

    with (
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch(
            "mmrelay.meshtastic_utils.asyncio.get_running_loop",
            return_value=loop,
        ),
        patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        mock_logger.exception.side_effect = _mark_shutdown
        await reconnect()

    mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_logs_cancelled():
    with (
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=asyncio.CancelledError,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await reconnect()

    mock_logger.info.assert_any_call("Reconnection task was cancelled.")
