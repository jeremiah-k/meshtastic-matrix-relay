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
import threading
import time
import unittest
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from typing import Any, NoReturn
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest
from meshtastic import BROADCAST_NUM

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.formats import TEXT_MESSAGE_APP
from mmrelay.constants.network import (
    CONFIG_KEY_CONNECTION_TYPE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_PLUGIN_TIMEOUT_SECS,
)
from mmrelay.meshtastic_utils import (
    on_lost_meshtastic_connection,
    on_meshtastic_message,
)
from tests.conftest import cleanup_ble_future_state
from tests.constants import (
    TEST_PACKET_FROM_ID,
    TEST_PACKET_ID,
)

TEST_PACKET_RX_TIME = 1234567890


class _DummyFuture:
    """Helper class to simulate a future that records timeout values and raises an exception.

    Parameters:
        exc (BaseException): The exception instance to store for later inspection or re-raising.
    """

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls: list[float | None] = []

    def result(self, timeout: float | None = None) -> None:
        self.calls.append(timeout)
        raise self.exc


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
        "mmrelay.meshtastic_utils.meshtastic_client",
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
        "mmrelay.meshtastic_utils._callbacks_tearing_down",
        False,
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
        "mmrelay.meshtastic_utils.matrix_rooms",
        [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_iface",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._meshtastic_last_direct_node_id",
        None,
        raising=False,
    )

    yield

    _cancel_startup_drain_timer()
    created_timers.clear()


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


def _done_future(coro, *args, **kwargs):
    """Close coroutine if applicable and return a completed Future."""
    if inspect.iscoroutine(coro):
        coro.close()
    f = Future()
    f.set_result(None)
    return f


class _FakeTimer:
    """Threading.Timer test double for startup-drain expiry timer tests."""

    def __init__(self, interval: float, callback: Callable[[], None]) -> None:
        self.interval: float = interval
        self._callback: Callable[[], None] = callback
        self.daemon: bool = False
        self.cancelled: bool = False
        created_timers.append(self)

    def start(self) -> None:
        return None

    def cancel(self) -> None:
        self.cancelled = True

    def fire(self) -> None:
        self._callback()


created_timers: list[_FakeTimer] = []


@pytest.mark.usefixtures("stable_relay_start_time")
class TestMessageProcessingEdgeCases(unittest.TestCase):
    """Test cases for edge cases in message processing."""

    def setUp(self):
        """
        Initializes mock configuration data for use in test cases.
        """
        import mmrelay.meshtastic_utils as mu

        self.mock_config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_SERIAL,
                "serial_port": "/dev/ttyUSB0",
                "broadcast_enabled": True,
                "meshnet_name": "test_mesh",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
        }
        mu.meshtastic_client = None
        mu.config = self.mock_config
        mu.matrix_rooms = self.mock_config["matrix_rooms"]
        mu.reconnecting = False
        mu.shutting_down = False
        mu.reconnect_task = None

    def test_on_meshtastic_message_no_decoded(self):
        """
        Verify that a Meshtastic packet lacking the 'decoded' field does not initiate message relay processing.
        """
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": 987654321,
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": TEST_PACKET_RX_TIME,
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

            with self.assertLogs("Meshtastic", level="DEBUG") as cm:
                on_meshtastic_message(packet, mock_interface)

            # Should not process message without decoded field
            mock_submit_coro.assert_not_called()

            # Verify debug log was called with packet type information (portnum None)
            log_output = "\n".join(cm.output)
            self.assertIn("UNKNOWN (None)", log_output)
            self.assertIn(f"from={TEST_PACKET_FROM_ID}", log_output)
            self.assertIn("channel=0", log_output)
            self.assertIn(f"id={TEST_PACKET_ID}", log_output)

    def test_on_meshtastic_message_empty_text(self):
        """
        Test that Meshtastic packets with empty text messages do not trigger relaying to Matrix rooms.
        """
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": TEST_PACKET_RX_TIME,
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

            with self.assertLogs("Meshtastic", level="DEBUG") as cm:
                on_meshtastic_message(packet, mock_interface)

            # Should not process empty text messages
            mock_submit_coro.assert_not_called()

            # Verify debug log was called with packet type information
            log_output = "\n".join(cm.output)
            self.assertIn(TEXT_MESSAGE_APP, log_output)
            self.assertIn(f"from={TEST_PACKET_FROM_ID}", log_output)
            self.assertIn("channel=0", log_output)
            self.assertIn(f"id={TEST_PACKET_ID}", log_output)

    def test_on_meshtastic_message_health_probe_response_logged_separately(self):
        """
        Health probe responses should be logged with HEALTH_CHECK prefix and not
        processed as regular ADMIN_APP traffic.
        """
        import mmrelay.meshtastic_utils as mu

        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"portnum": "ADMIN_APP", "requestId": 4242},
            "channel": 0,
            "id": 22222,
            "rxTime": 95_000.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch.dict(
                "mmrelay.meshtastic_utils._health_probe_request_deadlines",
                {4242: 9999999999.0},
                clear=True,
            ),
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils._run_meshtastic_plugins") as mock_plugins,
        ):
            with self.assertLogs("Meshtastic", level="DEBUG") as cm:
                on_meshtastic_message(packet, mock_interface)

        mock_plugins.assert_not_called()
        assert (mu._relay_rx_time_clock_skew_secs or 0.0) == pytest.approx(5_000.0)
        log_output = "\n".join(cm.output)
        assert "[HEALTH_CHECK] Metadata probe response requestId=4242" in log_output
        assert "port=ADMIN_APP" in log_output

    def test_on_meshtastic_message_health_probe_calibration_ignores_extreme_skew(self):
        """Health-probe calibration should ignore implausibly large skew values."""
        import mmrelay.meshtastic_utils as mu

        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"portnum": "ADMIN_APP", "requestId": 4343},
            "channel": 0,
            "id": 33333,
            "rxTime": 1.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID
        mu._relay_rx_time_clock_skew_secs = None

        with (
            patch.dict(
                "mmrelay.meshtastic_utils._health_probe_request_deadlines",
                {4343: 9999999999.0},
                clear=True,
            ),
            patch("mmrelay.meshtastic_utils.time.time", return_value=200_000.0),
        ):
            on_meshtastic_message(packet, mock_interface)

        assert mu._relay_rx_time_clock_skew_secs is None

    def test_on_meshtastic_message_ignores_stale_interface_packet(self):
        """Packets emitted by stale interfaces should not seed skew or be processed."""
        import mmrelay.meshtastic_utils as mu

        active_interface = MagicMock()
        stale_interface = MagicMock()
        stale_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID
        mu.meshtastic_client = active_interface
        mu._relay_active_client_id = id(active_interface)
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = 1_005.0
        mu.RELAY_START_TIME = 100_000.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "stale iface packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 94_900.0,
        }

        with (
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_001.0),
        ):
            on_meshtastic_message(packet, stale_interface)

        assert mu._relay_rx_time_clock_skew_secs is None
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs == 1_005.0
        mock_submit_coro.assert_not_called()
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("stale Meshtastic interface" in call for call in log_calls)

    def test_on_meshtastic_message_filters_old_packets_using_calibrated_skew(self):
        """Old packet filtering should use the calibrated rxTime skew."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = 5_000.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "old message", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 94_900.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Ignoring old packet" in call for call in log_calls)

    def test_on_meshtastic_message_filters_old_packets_with_negative_skew(self):
        """Negative skew (node ahead) should shift cutoff forward."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = -120.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "old message", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Ignoring old packet" in call for call in log_calls)

    def test_on_meshtastic_message_bootstraps_prestart_skew_during_startup_window(self):
        """Startup packets before relay start can bootstrap skew once, then are dropped."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "startup packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 94_900.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, mock_interface)

        assert (mu._relay_rx_time_clock_skew_secs or 0.0) == pytest.approx(5_100.0)
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any(
            "Bootstrapped rxTime clock skew from startup packet" in c for c in log_calls
        )
        assert any("Consumed startup bootstrap packet" in c for c in log_calls)

    def test_on_meshtastic_message_does_not_bootstrap_prestart_skew_after_window(self):
        """Pre-start packets should not bootstrap skew once startup window has passed."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "stale packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 94_900.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_500.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, mock_interface)

        assert mu._relay_rx_time_clock_skew_secs is None
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Ignoring old packet" in call for call in log_calls)

    def test_on_meshtastic_message_drains_packets_during_startup_window(self):
        """Packets should be consumed during the startup drain window."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "startup packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_005.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            on_meshtastic_message(packet, mock_interface)

        mock_submit_coro.assert_not_called()
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any(
            "Dropping inbound packet during startup drain window" in c
            for c in log_calls
        )

    def test_on_meshtastic_message_clears_expired_startup_drain_deadline(self):
        """Expired startup drain deadline should be cleared on next packet."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 200_000.0
        mu._relay_startup_drain_deadline_monotonic_secs = 1_000.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_complete_event.clear()
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "packet after drain", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_005.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_010.0),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            on_meshtastic_message(packet, mock_interface)

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_complete_event.is_set() is True

    def test_startup_drain_expiry_timer_clears_deadline_and_logs(self):
        """Drain deadline should clear and log even when no packet arrives."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        mu._relay_startup_drain_complete_event.clear()
        created_timers.clear()

        with (
            patch("mmrelay.meshtastic.events.threading.Timer", new=_FakeTimer),
            patch(
                "mmrelay.meshtastic_utils.time.monotonic",
                side_effect=[1_005.0, 1_011.0],
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mu._schedule_startup_drain_deadline_cleanup(1_010.0)
            assert len(created_timers) == 1
            assert created_timers[0].interval == pytest.approx(5.0)
            created_timers[0].fire()

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_expiry_timer is None
        assert mu._relay_startup_drain_complete_event.is_set() is True
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Startup drain window has ended" in c for c in log_calls)

    def test_startup_drain_expiry_timer_ignores_stale_deadline(self):
        """Timer for an old deadline should not clear a newer drain deadline."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        mu._relay_startup_drain_complete_event.clear()
        created_timers.clear()

        with (
            patch("mmrelay.meshtastic.events.threading.Timer", new=_FakeTimer),
            patch(
                "mmrelay.meshtastic_utils.time.monotonic",
                side_effect=[1_005.0, 1_011.0],
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mu._schedule_startup_drain_deadline_cleanup(1_010.0)
            mu._relay_startup_drain_deadline_monotonic_secs = 1_020.0
            created_timers[0].fire()

        assert mu._relay_startup_drain_deadline_monotonic_secs == 1_020.0
        assert mu._relay_startup_drain_expiry_timer is None
        assert mu._relay_startup_drain_complete_event.is_set() is False
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert not any("Startup drain window has ended" in c for c in log_calls)

    def test_startup_drain_expiry_timer_reschedules_when_triggered_early(self):
        """Early timer wakeups should reschedule and still clear on the deadline."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        mu._relay_startup_drain_complete_event.clear()
        created_timers.clear()

        with (
            patch("mmrelay.meshtastic.events.threading.Timer", new=_FakeTimer),
            patch(
                "mmrelay.meshtastic_utils.time.monotonic",
                side_effect=[1_005.0, 1_009.0, 1_009.5, 1_011.0],
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mu._schedule_startup_drain_deadline_cleanup(1_010.0)
            created_timers[0].fire()
            assert len(created_timers) == 2
            assert created_timers[1].interval == pytest.approx(0.5)
            created_timers[1].fire()

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_expiry_timer is None
        assert mu._relay_startup_drain_complete_event.is_set() is True
        log_calls = [str(call) for call in mock_logger.debug.call_args_list]
        assert any("Startup drain window has ended" in c for c in log_calls)

    def test_is_health_probe_response_packet_handles_zero_sender_id(self):
        """Sender 0 should not match a non-zero local node id."""
        import mmrelay.meshtastic_utils as mu

        packet = {"from": 0, "decoded": {"requestId": 4242}}
        interface = MagicMock()
        interface.myInfo.my_node_num = 123

        with patch.dict(
            "mmrelay.meshtastic_utils._health_probe_request_deadlines",
            {4242: 9999999999.0},
            clear=True,
        ):
            self.assertFalse(mu._is_health_probe_response_packet(packet, interface))

    def test_is_health_probe_response_packet_accepts_zero_local_node_id(self):
        """Sender 0 should match when the local node id is also 0."""
        import mmrelay.meshtastic_utils as mu

        packet = {"from": 0, "decoded": {"requestId": 4242}}
        interface = MagicMock()
        interface.myInfo.my_node_num = 0

        with patch.dict(
            "mmrelay.meshtastic_utils._health_probe_request_deadlines",
            {4242: 9999999999.0},
            clear=True,
        ):
            self.assertTrue(mu._is_health_probe_response_packet(packet, interface))

    def test_seed_connect_time_skew_rejects_non_positive_rx_time(self):
        """_seed_connect_time_skew should return False for rx_time <= 0."""
        import mmrelay.meshtastic_utils as mu

        self.assertFalse(mu._seed_connect_time_skew(0.0))
        self.assertFalse(mu._seed_connect_time_skew(-1.0))
        self.assertIsNone(mu._relay_rx_time_clock_skew_secs)

    def test_seed_connect_time_skew_logs_post_start_calibration(self):
        """When rx_time >= RELAY_START_TIME, _seed_connect_time_skew should log the post-start message."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu.RELAY_START_TIME = 50_000.0
        mu._relay_startup_drain_deadline_monotonic_secs = 1_180.0
        rx_time = 51_000.0  # post-start

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=52_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            result = mu._seed_connect_time_skew(rx_time)

        self.assertTrue(result)
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Calibrated rxTime clock skew from connect-time packet" in c
            for c in log_calls
        )

    def test_seed_connect_time_skew_allows_one_prestart_bootstrap_on_reconnect(self):
        """Reconnect path should allow one bounded pre-start bootstrap without startup drain."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu.RELAY_START_TIME = 100_000.0
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = 1_005.0

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_001.0),
        ):
            first_result = mu._seed_connect_time_skew(94_900.0)

        assert first_result
        assert mu._relay_rx_time_clock_skew_secs == 5_100.0
        assert mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs is None

        # Clearing calibrated skew should not re-enable bootstrap once the reconnect
        # one-shot allowance has been consumed.
        mu._relay_rx_time_clock_skew_secs = None
        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_002.0),
        ):
            second_result = mu._seed_connect_time_skew(94_850.0)

        assert not second_result
        assert mu._relay_rx_time_clock_skew_secs is None

    def test_seed_connect_time_skew_accepts_day_scale_future_offset(self):
        """Day-scale node clock offsets should still be calibratable."""
        import mmrelay.meshtastic_utils as mu

        now_wall = 200_000.0
        now_mono = 1_005.0
        rx_time = now_wall + 100_654.84

        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu.RELAY_START_TIME = 190_000.0
        mu._relay_startup_drain_deadline_monotonic_secs = None
        mu._relay_reconnect_prestart_bootstrap_deadline_monotonic_secs = None

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=now_wall),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=now_mono),
        ):
            result = mu._seed_connect_time_skew(rx_time)

        assert result
        skew = mu._relay_rx_time_clock_skew_secs
        assert skew is not None
        assert abs(skew + 100_654.84) < 1e-6

    def test_claim_health_probe_uses_localnode_fallback(self):
        """_claim_health_probe_response_and_maybe_calibrate should fall back to localNode when myInfo is absent."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = None
        packet = {
            "from": 100,
            "decoded": {"requestId": 5555},
            "rxTime": 0,
        }
        interface = MagicMock()
        # No myInfo — must fall back to localNode
        interface.myInfo = None
        interface.localNode.nodeNum = 100

        with patch.dict(
            "mmrelay.meshtastic_utils._health_probe_request_deadlines",
            {5555: 9999999999.0},
            clear=True,
        ):
            result = mu._claim_health_probe_response_and_maybe_calibrate(
                packet, interface, rx_time=0.0
            )

        self.assertTrue(result)

    def test_claim_health_probe_rejects_sender_mismatch(self):
        """Should return False when sender != local node."""
        import mmrelay.meshtastic_utils as mu

        packet = {
            "from": 200,
            "decoded": {"requestId": 5556},
        }
        interface = MagicMock()
        interface.myInfo.my_node_num = 100  # local node is 100, sender is 200

        result = mu._claim_health_probe_response_and_maybe_calibrate(
            packet, interface, rx_time=0.0
        )

        self.assertFalse(result)

    def test_claim_health_probe_unknown_request_id_returns_false(self):
        """Should return False when request_id is not in tracked deadlines."""
        import mmrelay.meshtastic_utils as mu

        packet = {
            "from": 100,
            "decoded": {"requestId": 9999},
        }
        interface = MagicMock()
        interface.myInfo.my_node_num = 100

        # Ensure the request_id is not tracked
        with patch.dict(
            "mmrelay.meshtastic_utils._health_probe_request_deadlines",
            {},
            clear=True,
        ):
            result = mu._claim_health_probe_response_and_maybe_calibrate(
                packet, interface, rx_time=0.0
            )

        self.assertFalse(result)

    def test_claim_health_probe_calibrates_skew(self):
        """Should calibrate skew from a valid health-probe response."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = None
        packet = {
            "from": 100,
            "decoded": {"requestId": 5557},
        }
        interface = MagicMock()
        interface.myInfo.my_node_num = 100

        with (
            patch.dict(
                "mmrelay.meshtastic_utils._health_probe_request_deadlines",
                {5557: 9999999999.0},
                clear=True,
            ),
            patch("mmrelay.meshtastic_utils.time.time", return_value=50_100.0),
        ):
            result = mu._claim_health_probe_response_and_maybe_calibrate(
                packet, interface, rx_time=50_000.0
            )

        self.assertTrue(result)
        assert mu._relay_rx_time_clock_skew_secs == pytest.approx(100.0)

    def test_claim_health_probe_skips_extreme_skew(self):
        """Should not calibrate when observed skew is implausibly large."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = None
        packet = {
            "from": 100,
            "decoded": {"requestId": 5558},
        }
        interface = MagicMock()
        interface.myInfo.my_node_num = 100

        with (
            patch.dict(
                "mmrelay.meshtastic_utils._health_probe_request_deadlines",
                {5558: 9999999999.0},
                clear=True,
            ),
            patch("mmrelay.meshtastic_utils.time.time", return_value=200_000.0),
        ):
            result = mu._claim_health_probe_response_and_maybe_calibrate(
                packet, interface, rx_time=1.0
            )

        self.assertTrue(result)
        self.assertIsNone(mu._relay_rx_time_clock_skew_secs)

    def test_claim_health_probe_skips_when_already_calibrated(self):
        """Should not recalibrate if skew is already set."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_rx_time_clock_skew_secs = 50.0
        packet = {
            "from": 100,
            "decoded": {"requestId": 5559},
        }
        interface = MagicMock()
        interface.myInfo.my_node_num = 100

        with patch.dict(
            "mmrelay.meshtastic_utils._health_probe_request_deadlines",
            {5559: 9999999999.0},
            clear=True,
        ):
            result = mu._claim_health_probe_response_and_maybe_calibrate(
                packet, interface, rx_time=50_000.0
            )

        self.assertTrue(result)
        # Should not have changed from the original value
        self.assertEqual(mu._relay_rx_time_clock_skew_secs, 50.0)

    def test_on_meshtastic_message_drains_with_prestart_bootstrap(self):
        """During startup drain, a pre-start rxTime packet should bootstrap and be consumed."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_180.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "drain bootstrap", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 94_900.0,  # pre-start
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_000.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            on_meshtastic_message(packet, mock_interface)

        mock_submit_coro.assert_not_called()
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("Consumed startup bootstrap packet" in c for c in log_calls)
        assert any(
            "Dropping inbound packet during startup drain window" in c
            for c in log_calls
        )

    def test_on_meshtastic_message_drains_without_calibration(self):
        """During startup drain, rx_time=0 should skip calibration and still drain."""
        import mmrelay.meshtastic_utils as mu

        mu._relay_startup_drain_deadline_monotonic_secs = 1_010.0
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "drain no cal", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 0,  # no rxTime
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            on_meshtastic_message(packet, mock_interface)

        mock_submit_coro.assert_not_called()
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Dropping inbound packet during startup drain window" in c
            for c in log_calls
        )

    def test_on_meshtastic_message_consumes_bootstrap_outside_drain(self):
        """Outside drain window, a post-start rxTime that calibrates skew should be consumed (calibrated but not relayed)."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_connection_started_monotonic_secs = 1_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = None
        # Use a post-start rxTime so calibration succeeds but packet is not consumed
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "bootstrap consume", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,  # post-start, will calibrate
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_100.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
        ):
            on_meshtastic_message(packet, mock_interface)

        # Calibration should have occurred with post-start message
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Calibrated rxTime clock skew from connect-time packet" in c
            for c in log_calls
        )
        # Skew should be calibrated
        assert mu._relay_rx_time_clock_skew_secs == pytest.approx(50.0)
        # Bootstrap packet should be consumed (not relayed)
        mock_submit_coro.assert_not_called()

    def test_on_meshtastic_message_packets_continue_after_drain_window_expires(self):
        """After drain window expires, processing continues and submits for relay."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_000.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_complete_event.clear()
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": BROADCAST_NUM,
            "decoded": {"text": "post-drain packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID
        relay_coro = object()
        mock_matrix_relay = Mock(return_value=relay_coro)

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_100.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.matrix_utils.matrix_relay", new=mock_matrix_relay),
            patch("mmrelay.meshtastic_utils._fire_and_forget") as mock_fire_and_forget,
        ):
            on_meshtastic_message(packet, mock_interface)

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_complete_event.is_set() is True
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("Startup drain window has ended" in c for c in log_calls)
        # Post-drain packet should have been submitted for Matrix relay.
        mock_matrix_relay.assert_called_once()
        mock_fire_and_forget.assert_called_once_with(relay_coro, loop=mu.event_loop)

    def test_on_meshtastic_message_expired_drain_uses_timer_for_end_log(self):
        """
        With an active expiry timer, packet handling should not emit drain-end log.
        """
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_000.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_complete_event.clear()
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "post-drain packet", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with patch.object(mu, "_relay_startup_drain_expiry_timer", MagicMock()):
            with (
                patch("mmrelay.meshtastic_utils.config", self.mock_config),
                patch("mmrelay.meshtastic_utils.time.time", return_value=100_100.0),
                patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
                patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            ):
                on_meshtastic_message(packet, mock_interface)

        assert mu._relay_startup_drain_deadline_monotonic_secs == 1_000.0
        assert mu._relay_startup_drain_complete_event.is_set() is False
        log_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert not any("Startup drain window has ended" in c for c in log_calls)

    def test_on_meshtastic_message_clears_drain_at_exact_deadline(self):
        """Drain window should clear when packet arrives exactly at the deadline."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_000.0
        mu._relay_startup_drain_complete_event.clear()
        packet = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "exact deadline", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_100.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_000.0),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            on_meshtastic_message(packet, mock_interface)

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_complete_event.is_set() is True

    def test_on_meshtastic_message_drain_cleared_only_once_on_first_packet(self):
        """After drain window expires, subsequent packets should not re-clear the deadline."""
        import mmrelay.meshtastic_utils as mu

        mu.RELAY_START_TIME = 100_000.0
        mu._relay_rx_time_clock_skew_secs = None
        mu._relay_startup_drain_deadline_monotonic_secs = 1_000.0
        mu._relay_startup_drain_expiry_timer = None
        mu._relay_startup_drain_complete_event.clear()

        packet1 = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "packet 1", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID,
            "rxTime": 100_050.0,
        }
        packet2 = {
            "from": TEST_PACKET_FROM_ID,
            "to": TEST_PACKET_FROM_ID,
            "decoded": {"text": "packet 2", "portnum": TEXT_MESSAGE_APP},
            "channel": 0,
            "id": TEST_PACKET_ID + 1,
            "rxTime": 100_060.0,
        }
        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = TEST_PACKET_FROM_ID

        debug_log_messages = []

        def capture_debug(*args, **kwargs):
            debug_log_messages.append(args[0] if args else "")

        with (
            patch("mmrelay.meshtastic_utils.config", self.mock_config),
            patch("mmrelay.meshtastic_utils.time.time", return_value=100_100.0),
            patch("mmrelay.meshtastic_utils.time.monotonic", return_value=1_005.0),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_logger.debug.side_effect = capture_debug
            on_meshtastic_message(packet1, mock_interface)
            on_meshtastic_message(packet2, mock_interface)

        assert mu._relay_startup_drain_deadline_monotonic_secs is None
        assert mu._relay_startup_drain_complete_event.is_set() is True
        drain_end_count = sum(
            1 for msg in debug_log_messages if "Startup drain window has ended" in msg
        )
        assert drain_end_count == 1


class TestOnLostConnectionNoActiveClient:
    def test_ignores_when_no_active_client_and_subscribed(self):
        mu.meshtastic_client = None
        mu.subscribed_to_connection_lost = True
        mu.shutting_down = False

        mock_interface = MagicMock()

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(interface=mock_interface)

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring connection-lost event because no Meshtastic interface is currently active"
            in c
            for c in debug_calls
        )

    def test_ignores_when_no_active_client_and_callbacks_tearing_down(self):
        mu.meshtastic_client = None
        mu.subscribed_to_connection_lost = False
        mu._callbacks_tearing_down = True
        mu.shutting_down = False

        mock_interface = MagicMock()

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(interface=mock_interface)

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring connection-lost event because no Meshtastic interface is currently active"
            in c
            for c in debug_calls
        )


class TestOnLostConnectionStaleInterface:
    def test_ignores_stale_interface_with_relay_active_client_id(self):
        active_client = MagicMock()
        stale_interface = MagicMock()
        mu.meshtastic_client = active_client
        mu._relay_active_client_id = id(active_client)
        mu.reconnecting = False
        mu.shutting_down = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(
                interface=stale_interface, detection_source="test"
            )

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("stale Meshtastic interface" in c for c in debug_calls)

    def test_ignores_stale_interface_without_relay_active_client_id(self):
        active_client = MagicMock()
        stale_interface = MagicMock()
        mu.meshtastic_client = active_client
        mu._relay_active_client_id = None
        mu.reconnecting = False
        mu.shutting_down = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_lost_meshtastic_connection(
                interface=stale_interface, detection_source="test"
            )

        assert mu.reconnecting is False
        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any("stale Meshtastic interface" in c for c in debug_calls)


class TestOnMeshtasticMessageNoActiveClient:
    def test_ignores_packet_when_subscribed_to_messages(self):
        mu.meshtastic_client = None
        mu.subscribed_to_messages = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_reconnecting(self):
        mu.meshtastic_client = None
        mu.reconnecting = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_callbacks_tearing_down(self):
        mu.meshtastic_client = None
        mu._callbacks_tearing_down = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_shutting_down(self):
        mu.meshtastic_client = None
        mu.shutting_down = True

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        debug_calls = [str(c) for c in mock_logger.debug.call_args_list]
        assert any(
            "Shutdown in progress. Ignoring incoming messages." in c
            for c in debug_calls
        )
        assert not any(
            "Ignoring packet because no Meshtastic interface is currently active" in c
            for c in debug_calls
        )

    def test_ignores_packet_when_active_client_id_set(self):
        mu.meshtastic_client = None
        mu._relay_active_client_id = 12345

        mock_interface = MagicMock()
        packet = {"decoded": {"text": "hello"}, "fromId": "!abc", "to": 4294967295}

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            on_meshtastic_message(packet, mock_interface)

        error_calls = [str(c) for c in mock_logger.error.call_args_list]
        assert any(
            "Inconsistent relay state: active_client is None but active_client_id=" in c
            for c in error_calls
        )


class TestMessageHandlerEdgeCases:
    """Test edge cases in message handler and check_connection."""

    def test_on_meshtastic_message_invalid_rx_time(self):
        """Test message handler with invalid rxTime."""
        packet = {
            "rxTime": "not-a-number",
            "decoded": {"text": "test"},
            "channel": 0,
            "to": 4294967295,
        }

        mock_interface = Mock()
        mock_interface.myInfo = Mock()
        mock_interface.myInfo.my_node_num = 12345

        mu.config = {"meshtastic": {"meshnet_name": "test"}}
        mu.matrix_rooms = []

        mu.on_meshtastic_message(packet, mock_interface)

    @pytest.mark.asyncio
    async def test_check_connection_non_dict_health_config(self):
        """Test check_connection with non-dict health_check config exits early via requires_continuous_health_monitor."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": "invalid",
            }
        }
        mu.meshtastic_client = None
        mu.shutting_down = True

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            await mu.check_connection()

            mock_logger.warning.assert_not_called()
            mock_logger.info.assert_called()
            call_args = mock_logger.info.call_args[0]
            assert "disabled" in call_args[0]

    @pytest.mark.asyncio
    async def test_check_connection_probe_submission_fails(self):
        """Test check_connection when probe submission raises RuntimeError."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        sleep_count = [0]

        async def sleep_side_effect(delay):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                mu.shutting_down = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.side_effect = RuntimeError("Executor closed")

            with patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                side_effect=sleep_side_effect,
            ):
                await mu.check_connection()

                assert mock_submit.call_count >= 1

    @pytest.mark.asyncio
    async def test_check_connection_probe_future_none(self):
        """Test check_connection when probe submission returns None."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        sleep_count = [0]

        async def sleep_side_effect(delay):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                mu.shutting_down = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = None

            with patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                side_effect=sleep_side_effect,
            ):
                await mu.check_connection()

                assert mock_submit.call_count >= 1

    @pytest.mark.asyncio
    async def test_check_connection_probe_fails_not_reconnecting(self):
        """Test check_connection when probe fails and not reconnecting."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = False
        mu.shutting_down = False

        sleep_count = [0]

        async def sleep_side_effect(delay):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                mu.shutting_down = True

        mock_future = Mock(spec=Future)
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            with patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                side_effect=sleep_side_effect,
            ):
                with patch("mmrelay.meshtastic_utils.asyncio.wait_for") as mock_wait:
                    mock_wait.side_effect = Exception("Connection lost")

                    with patch(
                        "mmrelay.meshtastic_utils.on_lost_meshtastic_connection"
                    ) as mock_lost:
                        await mu.check_connection()

                        mock_lost.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_connection_probe_fails_already_reconnecting(self):
        """Test check_connection when probe fails but already reconnecting."""
        mu.config = {
            "meshtastic": {
                CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_TCP,
                "health_check": {"enabled": True, "initial_delay": 0},
            }
        }
        mu.meshtastic_client = Mock()
        mu.reconnecting = True
        mu.shutting_down = False

        sleep_count = [0]

        async def sleep_side_effect(delay):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                mu.shutting_down = True

        mock_future = Mock(spec=Future)
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            with patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                side_effect=sleep_side_effect,
            ):
                with patch("mmrelay.meshtastic_utils.asyncio.wait_for") as mock_wait:
                    mock_wait.side_effect = Exception("Connection lost")

                    with patch(
                        "mmrelay.meshtastic_utils.on_lost_meshtastic_connection"
                    ) as mock_lost:
                        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                            await mu.check_connection()

                            mock_lost.assert_not_called()
                            debug_calls = [
                                str(call) for call in mock_logger.debug.call_args_list
                            ]
                            assert any(
                                "reconnection in progress" in call.lower()
                                for call in debug_calls
                            ), f"Expected 'reconnection in progress' debug message, got: {debug_calls}"


class TestOnMeshtasticMessageOldPacketFiltering:
    """Test old message filtering in on_meshtastic_message."""

    def test_on_meshtastic_message_filters_old_packets(self):
        """Test that packets with rx_time < RELAY_START_TIME are filtered out."""
        import mmrelay.meshtastic_utils as mu_module

        mu_module.RELAY_START_TIME = time.time()
        mu_module._relay_rx_time_clock_skew_secs = None

        old_packet = {
            "from": 12345,
            "to": 4294967295,
            "decoded": {"text": "old message", "portnum": "TEXT_MESSAGE_APP"},
            "channel": 0,
            "id": 12345,
            "rxTime": mu_module.RELAY_START_TIME - 100,
        }

        mock_interface = Mock()
        mock_interface.myInfo = Mock()
        mock_interface.myInfo.my_node_num = 12345

        mu_module.config = {"meshtastic": {"meshnet_name": "test"}}
        mu_module.matrix_rooms = []

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu_module.on_meshtastic_message(old_packet, mock_interface)

            log_calls = [str(call).lower() for call in mock_logger.debug.call_args_list]
            assert any(
                ("ignore" in call or "ignoring" in call)
                and ("old" in call or "stale" in call or "filtered" in call)
                for call in log_calls
            )


# ---------------------------------------------------------------------------
# Tests absorbed from test_meshtastic_utils_edge_cases.py (message edge domain)
# ---------------------------------------------------------------------------


class TestOnMeshtasticMessageEdgeCases(unittest.TestCase):
    """Edge case tests for on_meshtastic_message."""

    def test_on_meshtastic_message_malformed_packet(self):
        """Handles various malformed packet inputs without raising exceptions."""
        malformed_packets = [
            {},
            {"decoded": None},
            {"decoded": {"text": None}},
            {"fromId": None},
            {"channel": "invalid"},
        ]

        for packet in malformed_packets:
            with self.subTest(packet=packet):
                mock_interface = MagicMock()
                with patch("mmrelay.meshtastic_utils.logger"):
                    on_meshtastic_message(packet, mock_interface)

    def test_on_meshtastic_message_plugin_processing_failure(self):
        """Logs error when a plugin raises an exception during message processing."""
        packet = {
            "decoded": {"text": "test message", "portnum": TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        class _PluginFailure(RuntimeError):
            """Test-specific plugin failure."""

        def _submit_coro_mock(coro: Any, loop: Any = None) -> Future[Any]:
            f = Future()
            try:
                result = asyncio.run(coro)
                f.set_result(result)
            except _PluginFailure as e:
                f.set_exception(e)
            finally:
                if asyncio.iscoroutine(coro):
                    coro.close()
            return f

        with (
            patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins,
            patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        ):
            mock_plugin = MagicMock()
            mock_plugin.plugin_name = "test_plugin"
            mock_plugin.handle_meshtastic_message = AsyncMock(
                side_effect=_PluginFailure("Plugin failed")
            )
            mock_load_plugins.return_value = [mock_plugin]
            mock_submit_coro.side_effect = _submit_coro_mock

            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.config = {
                "matrix": {"homeserver": "test"},
                "meshtastic": {
                    "meshnet_name": "test_meshnet",
                    "message_interactions": {"reactions": True, "replies": True},
                },
            }
            mmrelay.meshtastic_utils.matrix_rooms = [
                {"meshtastic_channel": 0, "matrix_room_id": "!test:example.com"}
            ]
            mock_interface.myInfo.my_node_num = 999999

            on_meshtastic_message(packet, mock_interface)
            mock_logger.exception.assert_called()

    def test_on_meshtastic_message_plugin_timeout_uses_config(self):
        """Plugin timeout honors meshtastic.plugin_timeout configuration."""
        packet = {
            "decoded": {"text": "test message", "portnum": TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = _DummyFuture(timeout_exc)

        with (
            patch(
                "mmrelay.plugin_loader.load_plugins",
                return_value=[_make_plugin("timeout_plugin")],
            ),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_make_submit_side_effect(future),
            ) as mock_submit_coro,
            patch(
                "mmrelay.meshtastic_utils.config",
                {
                    "meshtastic": {
                        "meshnet_name": "meshnet",
                        "plugin_timeout": 7.5,
                        "message_interactions": {"reactions": False, "replies": False},
                    }
                },
            ),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            ),
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
        """Invalid plugin_timeout logs warning and falls back to default."""
        packet = {
            "decoded": {"text": "test message", "portnum": TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
        }
        interface = MagicMock()
        interface.nodes = {}

        timeout_exc = ConcurrentTimeoutError("Plugin timeout")
        future = _DummyFuture(timeout_exc)

        with (
            patch(
                "mmrelay.plugin_loader.load_plugins",
                return_value=[_make_plugin("timeout_plugin")],
            ),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_make_submit_side_effect(future),
            ) as mock_submit_coro,
            patch(
                "mmrelay.meshtastic_utils.config",
                {
                    "meshtastic": {
                        "meshnet_name": "meshnet",
                        "plugin_timeout": "invalid",
                        "message_interactions": {"reactions": False, "replies": False},
                    }
                },
            ),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            ),
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
        """Logs error when Matrix relay raises exception during message processing."""
        packet = {
            "decoded": {"text": "test message", "portnum": TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }

        mock_interface = MagicMock()

        class _MatrixRelayFailure(RuntimeError):
            """Test-specific Matrix relay failure."""

        def _submit_raises(coro: Any, **_kwargs: Any) -> NoReturn:
            if asyncio.iscoroutine(coro):
                coro.close()
            raise _MatrixRelayFailure

        with (
            patch("mmrelay.plugin_loader.load_plugins", return_value=[]),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_submit_raises,
            ),
            patch(
                "mmrelay.matrix_utils.matrix_relay",
                AsyncMock(return_value=None),
            ),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
        ):
            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.matrix_rooms = [
                {"id": "!room:matrix.org", "meshtastic_channel": 0}
            ]
            mmrelay.meshtastic_utils.config = {
                "matrix": {"homeserver": "test"},
                "meshtastic": {
                    "meshnet_name": "test_meshnet",
                    "message_interactions": {"reactions": True, "replies": True},
                },
            }

            on_meshtastic_message(packet, mock_interface)
            mock_logger.exception.assert_called()

    def test_on_meshtastic_message_plugin_timeout_prevents_relay(self):
        """Plugin timeout prevents message from being relayed to Matrix."""
        packet = {
            "decoded": {"text": "!test", "portnum": TEXT_MESSAGE_APP},
            "fromId": "!12345678",
            "channel": 0,
            "to": BROADCAST_NUM,
        }
        interface = MagicMock()
        interface.nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "TestNode"}}
        }

        future = _DummyFuture(ConcurrentTimeoutError("Plugin timeout"))

        with (
            patch(
                "mmrelay.plugin_loader.load_plugins",
                return_value=[_make_plugin("test_plugin")],
            ),
            patch(
                "mmrelay.meshtastic_utils._submit_coro",
                side_effect=_make_submit_side_effect(future),
            ),
            patch(
                "mmrelay.meshtastic_utils.config",
                {
                    "meshtastic": {"meshnet_name": "test"},
                    "matrix_rooms": [{"meshtastic_channel": 0, "id": "!room:matrix"}],
                },
            ),
            patch(
                "mmrelay.meshtastic_utils.matrix_rooms",
                [{"meshtastic_channel": 0, "id": "!room:matrix"}],
            ),
            patch("mmrelay.meshtastic_utils.get_longname", return_value="TestNode"),
            patch("mmrelay.meshtastic_utils.get_shortname", return_value="TN"),
            patch("mmrelay.matrix_utils.get_matrix_prefix", return_value=""),
            patch("mmrelay.matrix_utils.matrix_relay", Mock()) as mock_matrix_relay,
            patch("mmrelay.meshtastic_utils.event_loop", MagicMock()),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            on_meshtastic_message(packet, interface)

            mock_logger.warning.assert_any_call(
                "Plugin %s did not respond within %ss: %s",
                "test_plugin",
                DEFAULT_PLUGIN_TIMEOUT_SECS,
                ANY,
            )
            mock_matrix_relay.assert_not_called()
            mock_logger.debug.assert_any_call("Processed by plugin %s", "test_plugin")


# ---------------------------------------------------------------------------
# Helpers for tests absorbed from edge_cases
# ---------------------------------------------------------------------------


def _make_plugin(name: str) -> MagicMock:
    """Create a MagicMock plugin with the given name and async handle_meshtastic_message."""
    plugin = MagicMock()
    plugin.plugin_name = name
    plugin.handle_meshtastic_message = AsyncMock(return_value=False)
    return plugin


def _make_submit_side_effect(future: Any) -> Callable[[Any], Any]:

    def _submit(coro: Any, **_kwargs: Any) -> Any:
        if asyncio.iscoroutine(coro):
            coro.close()
        return future

    return _submit
