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
        import asyncio

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

        # Should use default detection source without error
        error_call = mock_logger.error.call_args[0][0]
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

        # Should use default detection source
        error_call = mock_logger.error.call_args[0][0]
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

        # Should use default detection source
        error_call = mock_logger.error.call_args[0][0]
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

        # Should use the topic's getName() method, not __str__
        error_call = mock_logger.error.call_args[0][0]
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

        # Should use str(topic)
        error_call = mock_logger.error.call_args[0][0]
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

        # Should use the BLE disconnect source with 'ble.' prefix stripped
        error_call = mock_logger.error.call_args[0][0]
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
        mock_interface._last_disconnect_source = "   "  # Whitespace only

        on_lost_meshtastic_connection(
            mock_interface, detection_source="unknown", topic=pub.AUTO_TOPIC
        )

        # Should fall back to default detection source
        error_call = mock_logger.error.call_args[0][0]
        self.assertIn("meshtastic.connection.lost", error_call)
