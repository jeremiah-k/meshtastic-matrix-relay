#!/usr/bin/env python3
"""
Health probe, executor lifecycle, ACK handling, and degraded-state tests.

Covers:
- Executor shutdown paths (BLE and metadata)
- Metadata future cleanup and probe submission
- Health probe tracking and response detection
- Probe ACK handling and device connection probing
- Executor degraded state for both BLE and metadata
- Reconnect path resetting degraded state
- Shutdown clearing degraded state
"""

import contextlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from unittest.mock import Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    METADATA_WATCHDOG_SECS,
    RX_TIME_SKEW_BOOTSTRAP_WINDOW_SECS,
)
from tests.constants import TEST_BLE_MAC


def _cancel_startup_drain_timer() -> None:
    """Best-effort cancellation and join of the startup-drain expiry timer."""
    _timer = getattr(mu, "_relay_startup_drain_expiry_timer", None)
    if _timer is None:
        return
    with contextlib.suppress(AttributeError, RuntimeError, TypeError):
        _timer.cancel()
    _join = getattr(_timer, "join", None)
    if callable(_join):
        with contextlib.suppress(AttributeError, RuntimeError, TypeError):
            _join(0.2)
    with contextlib.suppress(AttributeError):
        mu._relay_startup_drain_expiry_timer = None


def _submit_done_reconnect_future(coro: object, _loop: object) -> Future[None]:
    """
    Create and return an already-completed Future after optionally closing a coroutine-like object.

    If `coro` has a callable `close()` attribute, it will be invoked before creating the completed Future.

    Parameters:
        coro: An object that may represent a coroutine; `close()` will be called if present and callable.
        _loop: Unused placeholder for an event loop (kept for compatibility).

    Returns:
        A Future already completed with the value `None`.
    """
    close = getattr(coro, "close", None)
    if callable(close):
        close()
    done_future: Future[None] = Future()
    done_future.set_result(None)
    return done_future


@pytest.fixture(autouse=True)
def reset_meshtastic_state(monkeypatch):
    """
    Reset mmrelay.meshtastic_utils module state to a clean baseline before each test.

    Clears module-level globals used by the meshtastic relay (executors, futures, BLE state,
    timeouts, health-probe tracking, clock skew/drain timers, and degraded/executor orphan counters)
    so tests run with a deterministic, isolated environment.
    """
    _cancel_startup_drain_timer()

    startup_drain_complete_event = threading.Event()
    startup_drain_complete_event.set()

    monkeypatch.setattr("mmrelay.meshtastic_utils.config", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_client", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.meshtastic_iface", None, raising=False
    )
    monkeypatch.setattr("mmrelay.meshtastic_utils.reconnecting", False, raising=False)
    monkeypatch.setattr("mmrelay.meshtastic_utils.shutting_down", False, raising=False)
    monkeypatch.setattr("mmrelay.meshtastic_utils.reconnect_task", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnect_task_future", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_messages", False, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.subscribed_to_connection_lost", False, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._callbacks_tearing_down", False, raising=False
    )
    monkeypatch.setattr("mmrelay.meshtastic_utils.matrix_rooms", [], raising=False)

    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_future", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_future_started_at", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor_degraded", False, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor_orphaned_workers", 0, raising=False
    )

    monkeypatch.setattr("mmrelay.meshtastic_utils._ble_future", None, raising=False)
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_address", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_started_at", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_timeout_secs", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_timeout_counts", {}, raising=False
    )
    monkeypatch.setattr("mmrelay.meshtastic_utils._ble_executor", None, raising=False)
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
        "mmrelay.meshtastic_utils._health_probe_request_deadlines", {}, raising=False
    )

    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_active_client_id", None, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_rx_time_clock_skew_secs", None, raising=False
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
        "mmrelay.meshtastic_utils._startup_packet_drain_applied", False, raising=False
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._relay_connection_started_monotonic_secs",
        time.monotonic() - (RX_TIME_SKEW_BOOTSTRAP_WINDOW_SECS + 1.0),
        raising=False,
    )
    monkeypatch.setattr("mmrelay.meshtastic_utils.RELAY_START_TIME", 0, raising=False)

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
        "mmrelay.meshtastic_utils._connect_attempt_in_progress", False, raising=False
    )
    monkeypatch.setattr("mmrelay.meshtastic_utils.event_loop", None, raising=False)

    try:
        yield
    finally:
        _cancel_startup_drain_timer()
        for attr in ("_metadata_executor", "_ble_executor"):
            executor = getattr(mu, attr, None)
            if executor is None:
                continue
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)


class TestExecutorShutdown:
    """Test executor shutdown paths."""

    def test_shutdown_shared_executors_cancels_ble_future(self, monkeypatch):
        """Test BLE future cancellation during shutdown."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mock_future.cancel.return_value = True

        monkeypatch.setattr(mu, "_ble_future", mock_future)
        monkeypatch.setattr(mu, "_ble_future_address", TEST_BLE_MAC)
        monkeypatch.setattr(mu, "_ble_timeout_counts", {TEST_BLE_MAC: 3})

        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        monkeypatch.setattr(mu, "_ble_executor", mock_executor)

        mu._shutdown_shared_executors()

        mock_future.cancel.assert_called_once()
        assert TEST_BLE_MAC not in mu._ble_timeout_counts
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert mu._ble_executor is None

    def test_shutdown_shared_executors_cancels_metadata_future(self, monkeypatch):
        """Test metadata future cancellation during shutdown."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mock_future.cancel.return_value = True

        monkeypatch.setattr(mu, "_metadata_future", mock_future)
        monkeypatch.setattr(mu, "_metadata_future_started_at", time.monotonic())

        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        monkeypatch.setattr(mu, "_metadata_executor", mock_executor)

        mu._shutdown_shared_executors()

        mock_future.cancel.assert_called_once()
        mock_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        assert mu._metadata_executor is None

    def test_shutdown_shared_executors_handles_type_error(self, monkeypatch):
        """Test executor shutdown handles TypeError on older Python."""
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mock_executor.shutdown.side_effect = [TypeError("cancel_futures"), None]
        monkeypatch.setattr(mu, "_ble_executor", mock_executor)
        monkeypatch.setattr(mu, "_ble_future", None)
        monkeypatch.setattr(mu, "_metadata_executor", None)
        monkeypatch.setattr(mu, "_metadata_future", None)

        mu._shutdown_shared_executors()

        assert mock_executor.shutdown.call_count == 2
        assert mu._ble_executor is None


class TestMetadataFutureCleanup:
    """Test metadata future cleanup functions."""

    def test_clear_metadata_future_if_current_match(self):
        """Test clearing metadata future when it matches."""
        mock_future = Mock(spec=Future)
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic()

        mu._clear_metadata_future_if_current(mock_future)

        assert mu._metadata_future is None
        assert mu._metadata_future_started_at is None

    def test_clear_metadata_future_if_current_no_match(self):
        """Test metadata future not cleared when different."""
        mock_future1 = Mock(spec=Future)
        mock_future2 = Mock(spec=Future)
        mu._metadata_future = mock_future1
        mu._metadata_future_started_at = time.monotonic()

        mu._clear_metadata_future_if_current(mock_future2)

        assert mu._metadata_future is mock_future1
        assert mu._metadata_future_started_at is not None

    def test_reset_metadata_executor_for_stale_probe(self):
        """Test resetting metadata executor after stale probe."""
        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()

        mu._reset_metadata_executor_for_stale_probe()

        assert mu._metadata_executor is not old_executor
        assert isinstance(mu._metadata_executor, ThreadPoolExecutor)
        assert mu._metadata_future is None
        assert mu._metadata_future_started_at is None
        old_executor.shutdown.assert_called_once()

        # Clean up the newly created executor
        mu._metadata_executor.shutdown(wait=False)

    def test_schedule_metadata_future_cleanup(self):
        """Test metadata future cleanup timer is scheduled."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future

        with patch("mmrelay.meshtastic.executors.threading.Timer") as mock_timer_class:
            mock_timer = Mock()
            mock_timer.daemon = False
            mock_timer_class.return_value = mock_timer

            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")

            assert mock_timer_class.call_count == 1
            call_args = mock_timer_class.call_args
            assert call_args[0][0] == METADATA_WATCHDOG_SECS
            mock_timer.start.assert_called_once()
            mock_future.add_done_callback.assert_called_once()

    def test_schedule_metadata_future_cleanup_exception(self):
        """Test metadata future cleanup handles exceptions during timer setup."""
        mock_future = Mock(spec=Future)
        mock_future.add_done_callback.side_effect = RuntimeError("Test error")

        with (
            patch("mmrelay.meshtastic.executors.threading.Timer") as mock_timer_class,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mock_timer_class.return_value = Mock()
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            mock_logger.debug.assert_called()


class TestSubmitMetadataProbe:
    """Test metadata probe submission."""

    def test_submit_metadata_probe_rejects_duplicate(self):
        """Test that duplicate metadata probe is rejected."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic()

        result = mu._submit_metadata_probe(lambda: None)

        assert result is None

    def test_submit_metadata_probe_accepts_after_stale(self):
        """Test that probe is accepted after previous one becomes stale."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        mu._metadata_future = mock_future
        mu._metadata_future_started_at = time.monotonic() - (
            METADATA_WATCHDOG_SECS + 10
        )

        with patch("mmrelay.meshtastic_utils._get_metadata_executor") as mock_get_exec:
            mock_executor = Mock(spec=ThreadPoolExecutor)
            mock_get_exec.return_value = mock_executor
            mock_executor.submit.return_value = Mock(spec=Future)

            result = mu._submit_metadata_probe(lambda: None)

            assert result is not None
            mock_executor.submit.assert_called_once()


class TestHealthProbeTracking:
    """Test health probe request ID tracking."""

    def test_track_health_probe_request_id_prunes_expired(self):
        """Test that tracking prunes expired request IDs."""
        mu._health_probe_request_deadlines[123] = time.monotonic() - 100

        request_id = mu._track_health_probe_request_id(456, 10.0)

        assert request_id == 456
        assert 123 not in mu._health_probe_request_deadlines
        assert 456 in mu._health_probe_request_deadlines

    def test_track_health_probe_request_id_with_invalid_id(self):
        """Test tracking with invalid request ID."""
        result = mu._track_health_probe_request_id(-1, 10.0)
        assert result is None

    def test_is_health_probe_response_packet_valid(self):
        """Test detecting valid health probe response."""
        mu._track_health_probe_request_id(789, 10.0)

        packet = {
            "requestId": 789,
            "from": 12345,
        }

        mock_interface = Mock()
        mock_my_info = Mock()
        mock_my_info.my_node_num = 12345
        mock_interface.myInfo = mock_my_info

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is True

    def test_is_health_probe_response_packet_wrong_sender(self):
        """Test health probe response from different sender."""
        mu._track_health_probe_request_id(789, 10.0)

        packet = {
            "requestId": 789,
            "from": 99999,
        }

        mock_interface = Mock()
        mock_my_info = Mock()
        mock_my_info.my_node_num = 12345
        mock_interface.myInfo = mock_my_info

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is False

    def test_is_health_probe_response_packet_no_request_id(self):
        """Test health probe response without request ID."""
        packet = {"from": 12345}
        mock_interface = Mock()

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is False


class TestProbeAckHandling:
    """Test ACK flag handling for health probes."""

    def test_set_probe_ack_flag_no_ack_state(self):
        """Test ACK flag setting when no ack state exists."""
        local_node = Mock()
        local_node.iface = None
        packet = {"from": 12345}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is False

    def test_set_probe_ack_flag_sender_matches_local(self):
        """Test ACK flag setting when sender matches local node."""
        ack_state = Mock()
        ack_state.receivedImplAck = False

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        localNode = Mock()
        localNode.nodeNum = 12345
        iface.localNode = localNode

        packet = {"from": 12345}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is True
        assert ack_state.receivedImplAck is True

    def test_set_probe_ack_flag_sender_different_fallback(self):
        """Test ACK flag fallback when sender doesn't match."""
        ack_state = Mock(spec=[])
        ack_state.receivedAck = False

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        packet = {"from": 99999}

        result = mu._set_probe_ack_flag_from_packet(local_node, packet)
        assert result is True
        assert ack_state.receivedAck is True

    def test_handle_probe_ack_callback_no_ack_state(self):
        """Test probe ACK callback with no ack state."""
        local_node = Mock()
        local_node.iface = None
        packet = {}

        with pytest.raises(RuntimeError, match="missing acknowledgment state"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_handle_probe_ack_callback_with_nak(self):
        """Test probe ACK callback with NAK response."""
        ack_state = Mock()
        ack_state.receivedNak = False

        iface = Mock()
        iface._acknowledgment = ack_state
        iface.localNode = None

        local_node = Mock()
        local_node.iface = iface

        packet = {"decoded": {"routing": {"errorReason": "NO_ROUTE"}}}

        mu._handle_probe_ack_callback(local_node, packet)

        assert ack_state.receivedNak is True

    def test_handle_probe_ack_callback_nak_no_received_nak_attr(self):
        """Test probe ACK callback with NAK but no receivedNak attribute."""
        ack_state = Mock(spec=[])

        iface = Mock()
        iface._acknowledgment = ack_state

        local_node = Mock()
        local_node.iface = iface

        packet = {"decoded": {"routing": {"errorReason": "TIMEOUT"}}}

        with pytest.raises(RuntimeError, match="missing receivedNak"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_handle_probe_ack_callback_failed_to_set_state(self):
        """Test probe ACK callback when ACK state cannot be set."""
        ack_state = Mock(spec=[])

        iface = Mock()
        iface._acknowledgment = ack_state
        iface.localNode = None

        local_node = Mock()
        local_node.iface = iface

        packet = {}

        with pytest.raises(RuntimeError, match="Failed to set ACK state"):
            mu._handle_probe_ack_callback(local_node, packet)

    def test_wait_for_probe_ack_no_ack_state(self):
        """Test waiting for probe ACK with no ack state."""
        with pytest.raises(RuntimeError, match="missing acknowledgment state"):
            mu._wait_for_probe_ack(None, 1.0)

    def test_wait_for_probe_ack_with_reset(self):
        """Test waiting for probe ACK calls reset when available."""
        ack_state = Mock()
        ack_state.receivedAck = True
        ack_state.receivedNak = False
        ack_state.receivedImplAck = False
        ack_state.reset = Mock()

        mu._wait_for_probe_ack(ack_state, 1.0)

        ack_state.reset.assert_called_once()

    def test_probe_device_connection_no_local_node(self):
        """Test probe connection when client has no localNode."""
        client = Mock()
        client.localNode = None

        with pytest.raises(
            RuntimeError, match="cannot perform metadata liveness probe"
        ):
            mu._probe_device_connection(client)

    def test_probe_device_connection_no_send_data(self):
        """Test probe connection when client has no sendData."""
        client = Mock()
        client.localNode = Mock()
        delattr(client, "sendData")

        with pytest.raises(
            RuntimeError, match="cannot perform metadata liveness probe"
        ):
            mu._probe_device_connection(client)

    def test_probe_device_connection_no_wait_method(self):
        """Test probe connection when client cannot wait for ACK."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.iface._acknowledgment = None
        client.localNode.nodeNum = 12345
        client.sendData = Mock(return_value=Mock(id=999))
        client._acknowledgment = None
        delattr(client, "waitForAckNak")

        with pytest.raises(RuntimeError, match="cannot wait for metadata probe ACK"):
            mu._probe_device_connection(client, 1.0)


class TestErrorBuilders:
    """Test error builder functions."""

    def test_missing_local_node_ack_state_error(self):
        """Test error builder for missing local node ack state."""
        err = mu._missing_local_node_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "local node missing acknowledgment state" in str(err)

    def test_missing_received_nak_error(self):
        """Test error builder for missing receivedNak."""
        err = mu._missing_received_nak_error()
        assert isinstance(err, RuntimeError)
        assert "missing receivedNak" in str(err)

    def test_failed_probe_ack_state_error(self):
        """Test error builder for failed probe ack state."""
        err = mu._failed_probe_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "Failed to set ACK state" in str(err)

    def test_missing_ack_state_error(self):
        """Test error builder for missing ack state."""
        err = mu._missing_ack_state_error()
        assert isinstance(err, RuntimeError)
        assert "client missing acknowledgment state" in str(err)

    def test_metadata_probe_ack_timeout_error(self):
        """Test error builder for probe timeout."""
        err = mu._metadata_probe_ack_timeout_error(5.5)
        assert isinstance(err, TimeoutError)
        assert "5.5 seconds" in str(err)

    def test_missing_probe_transport_error(self):
        """Test error builder for missing probe transport."""
        err = mu._missing_probe_transport_error()
        assert isinstance(err, RuntimeError)
        assert "cannot perform metadata liveness probe" in str(err)

    def test_missing_probe_wait_error(self):
        """Test error builder for missing probe wait."""
        err = mu._missing_probe_wait_error()
        assert isinstance(err, RuntimeError)
        assert "cannot wait for metadata probe ACK" in str(err)


class TestHealthProbeResponsePacketLocalNodeFallback:
    """Test _is_health_probe_response_packet fallback to localNode."""

    def test_is_health_probe_response_packet_uses_localnode_when_myinfo_none(self):
        """Test that packet detection falls back to localNode.nodeNum when myInfo is None."""
        mu._track_health_probe_request_id(789, 10.0)

        packet = {
            "from": 12345,
            "decoded": {"requestId": 789},
        }

        mock_interface = Mock()
        mock_interface.myInfo = None
        mock_local_node = Mock()
        mock_local_node.nodeNum = 12345
        mock_interface.localNode = mock_local_node

        result = mu._is_health_probe_response_packet(packet, mock_interface)
        assert result is True


class TestWaitForProbeAckManualReset:
    """Test _wait_for_probe_ack with non-callable reset."""

    def test_wait_for_probe_ack_with_non_callable_reset(self):
        """Test that _wait_for_probe_ack manually resets flags when reset is not callable."""
        ack_state = Mock()
        ack_state.receivedAck = True
        ack_state.receivedNak = False
        ack_state.receivedImplAck = False
        ack_state.reset = "not_callable"

        mu._wait_for_probe_ack(ack_state, 0.1)

        assert ack_state.receivedAck is False
        assert ack_state.receivedNak is False
        assert ack_state.receivedImplAck is False


class TestProbeDeviceConnectionManualReset:
    """Test _probe_device_connection with non-callable reset."""

    def test_probe_device_connection_with_non_callable_reset(self):
        """Test that _probe_device_connection manually resets flags when reset is not callable."""
        ack_state = Mock()
        ack_state.reset = "not_callable"
        ack_state.receivedAck = True
        ack_state.receivedNak = True
        ack_state.receivedImplAck = True

        client = Mock()
        client.localNode = Mock()
        client.localNode.nodeNum = 12345
        client.sendData = Mock(return_value=Mock(id=999))
        client._acknowledgment = ack_state
        client.waitForAckNak = Mock()

        with patch("mmrelay.meshtastic_utils._wait_for_probe_ack"):
            mu._probe_device_connection(client, 0.1)

        assert ack_state.receivedAck is False
        assert ack_state.receivedNak is False
        assert ack_state.receivedImplAck is False


class TestMetadataExecutorResetTypeError:
    """Test TypeError handling in _reset_metadata_executor_for_stale_probe."""

    def test_reset_metadata_executor_handles_type_error(self):
        """Test that _reset_metadata_executor_for_stale_probe handles TypeError on shutdown."""
        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        old_executor.shutdown.side_effect = [
            TypeError("cancel_futures not supported"),
            None,
        ]
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()

        mu._reset_metadata_executor_for_stale_probe()

        assert old_executor.shutdown.call_count == 2
        assert mu._metadata_executor is not old_executor

        # Clean up the newly created executor
        mu._metadata_executor.shutdown(wait=False)


class TestMetadataFutureCleanupPaths:
    """Test all paths in _schedule_metadata_future_cleanup."""

    def test_cleanup_early_return_when_future_done(self):
        """Test that cleanup returns early when future is already done.

        The _cleanup function is called by Timer after METADATA_WATCHDOG_SECS.
        When future.done() is True, _cleanup should return early without
        calling _reset_metadata_executor_for_stale_probe.
        """
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = True
        mu._metadata_future = mock_future

        with (
            patch(
                "mmrelay.meshtastic_utils._reset_metadata_executor_for_stale_probe"
            ) as mock_reset,
            patch("mmrelay.meshtastic.executors.threading.Timer") as mock_timer,
        ):
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            mock_future.add_done_callback.assert_called_once()
            mock_timer.assert_called_once()
            cleanup_callback = mock_timer.call_args[0][1]
            cleanup_callback()
            mock_reset.assert_not_called()

    def test_cleanup_early_return_when_should_clear_false(self):
        """Test that cleanup returns early when _metadata_future is different."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        different_future = Mock(spec=Future)
        mu._metadata_future = different_future

        with (
            patch("mmrelay.meshtastic.executors.threading.Timer") as mock_timer_class,
            patch(
                "mmrelay.meshtastic_utils._reset_metadata_executor_for_stale_probe"
            ) as mock_reset,
        ):
            mock_timer = Mock()
            mock_timer_class.return_value = mock_timer
            mu._schedule_metadata_future_cleanup(mock_future, "test-reason")
            mock_timer_class.assert_called_once()
            cleanup_callback = mock_timer_class.call_args[0][1]
            cleanup_callback()
            mock_reset.assert_not_called()


class TestGetDeviceMetadataIoError:
    """Test _get_device_metadata I/O error handling."""

    def test_get_device_metadata_handles_io_error_on_closed_file(self):
        """Test handling of I/O operation on closed file."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        mock_future = Mock(spec=Future)
        mock_future.result.side_effect = ValueError("I/O operation on closed file")
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            result = mu._get_device_metadata(client)

            assert result["firmware_version"] == "unknown"
            assert result["success"] is False


class TestMetadataExecutorDegradedState:
    """Test metadata executor degraded state when orphan threshold is exceeded."""

    def test_metadata_executor_enters_degraded_state_at_threshold(self):
        """Test that metadata executor enters degraded state when orphan count reaches threshold."""
        from mmrelay.constants.network import EXECUTOR_ORPHAN_THRESHOLD

        old_executor = Mock(spec=ThreadPoolExecutor)
        old_executor._shutdown = False
        mu._metadata_executor = old_executor
        mu._metadata_future = Mock(spec=Future)
        mu._metadata_future_started_at = time.monotonic()
        mu._metadata_executor_orphaned_workers = EXECUTOR_ORPHAN_THRESHOLD - 1
        mu._metadata_executor_degraded = False

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._reset_metadata_executor_for_stale_probe()

            assert mu._metadata_executor_degraded is True
            assert mu._metadata_future is None
            assert mu._metadata_future_started_at is None
            assert mock_logger.error.called
            error_msg = str(mock_logger.error.call_args)
            assert "DEGRADED" in error_msg

    def test_metadata_executor_refuses_reset_when_degraded(self):
        """Test that degraded metadata executor refuses to reset."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor = Mock(spec=ThreadPoolExecutor)
        mu._metadata_future = Mock(spec=Future)

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._reset_metadata_executor_for_stale_probe()

            assert mu._metadata_executor_degraded is True
            assert mock_logger.debug.called
            debug_msg = str(mock_logger.debug.call_args)
            assert "degraded state" in debug_msg.lower()

    def test_metadata_executor_allows_recovery_from_degraded(self):
        """Test that reset_executor_degraded_state clears metadata degraded state."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 10

        result = mu.reset_executor_degraded_state()

        assert result is True
        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_metadata_degraded_state_blocks_new_probes(self):
        """Test that metadata degraded state raises MetadataExecutorDegradedError."""
        from mmrelay.meshtastic_utils import MetadataExecutorDegradedError

        mu._metadata_executor_degraded = True
        mu._metadata_future = None
        mu._metadata_future_started_at = None

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            with pytest.raises(MetadataExecutorDegradedError):
                mu._submit_metadata_probe(lambda: None)

            mock_logger.error.assert_called_once()
            error_msg = str(mock_logger.error.call_args)
            assert "degraded" in error_msg.lower()


class TestBleExecutorDegradedState:
    """Test BLE executor degraded state when orphan threshold is exceeded."""

    def test_ble_executor_enters_degraded_state_at_threshold(self):
        """Test that BLE executor enters degraded state when orphan count reaches threshold per address."""
        from mmrelay.constants.network import EXECUTOR_ORPHAN_THRESHOLD

        ble_address = TEST_BLE_MAC
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mu._ble_executor = mock_executor
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_orphaned_workers_by_address = {
            ble_address: EXECUTOR_ORPHAN_THRESHOLD - 1
        }
        mu._ble_executor_degraded_addresses = set()

        with (
            patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            mu._maybe_reset_ble_executor(ble_address, timeout_count=5)

            assert ble_address in mu._ble_executor_degraded_addresses
            assert mock_logger.error.called
            error_msg = str(mock_logger.error.call_args)
            assert "DEGRADED" in error_msg
            assert ble_address in error_msg

    def test_ble_executor_refuses_reset_when_degraded(self):
        """Test that degraded BLE executor refuses to reset for that address."""
        ble_address = TEST_BLE_MAC
        mu._ble_executor_degraded_addresses = {ble_address}
        mu._ble_executor = Mock(spec=ThreadPoolExecutor)
        mu._ble_future = Mock(spec=Future)

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            mu._maybe_reset_ble_executor(ble_address, timeout_count=10)

            assert ble_address in mu._ble_executor_degraded_addresses
            assert mock_logger.debug.called
            debug_msg = str(mock_logger.debug.call_args)
            assert "degraded state" in debug_msg.lower()

    def test_ble_executor_allows_recovery_for_specific_address(self):
        """Test that reset_executor_degraded_state clears degraded state for specific BLE address."""
        ble_address = TEST_BLE_MAC
        mu._ble_executor_degraded_addresses = {ble_address}
        mu._ble_executor_orphaned_workers_by_address = {ble_address: 10}

        result = mu.reset_executor_degraded_state(ble_address=ble_address)

        assert result is True
        assert ble_address not in mu._ble_executor_degraded_addresses
        assert ble_address not in mu._ble_executor_orphaned_workers_by_address

    def test_ble_executor_allows_recovery_for_all_addresses(self):
        """Test that reset_executor_degraded_state with reset_all clears all degraded state."""
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC, "11:22:33:44:55:66"}
        mu._ble_executor_orphaned_workers_by_address = {
            TEST_BLE_MAC: 10,
            "11:22:33:44:55:66": 5,
        }
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 8

        result = mu.reset_executor_degraded_state(reset_all=True)

        assert result is True
        assert len(mu._ble_executor_degraded_addresses) == 0
        assert len(mu._ble_executor_orphaned_workers_by_address) == 0
        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_ble_executor_degraded_state_per_address(self):
        """Test that BLE degraded state is tracked per address."""

        degraded_address = TEST_BLE_MAC
        healthy_address = "11:22:33:44:55:66"

        mu._ble_executor_degraded_addresses = {degraded_address}
        mu._ble_executor = Mock(spec=ThreadPoolExecutor)
        mu._ble_executor._shutdown = False
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_orphaned_workers_by_address = {healthy_address: 0}

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            with patch("mmrelay.meshtastic_utils.logger"):
                mu._maybe_reset_ble_executor(healthy_address, timeout_count=5)

                assert degraded_address in mu._ble_executor_degraded_addresses
                assert healthy_address not in mu._ble_executor_degraded_addresses


class TestReconnectResetsDegradedStateWithoutDeadlock:
    """Test reconnect path resets degraded BLE state without deadlock."""

    def test_on_lost_connection_resets_degraded_state_no_stale_address(self):
        """
        Test that on_lost_meshtastic_connection resets degraded state when
        there is no stale_ble_address but degraded addresses exist.

        This tests the fix for a deadlock bug where reset_executor_degraded_state(reset_all=True)
        was called while holding _ble_executor_lock, causing a self-deadlock since
        threading.Lock is not reentrant.
        """
        mu._ble_future = None
        mu._ble_future_address = None
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC, "11:22:33:44:55:66"}
        mu._ble_executor_orphaned_workers_by_address = {
            TEST_BLE_MAC: 10,
            "11:22:33:44:55:66": 5,
        }
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 8
        mu.meshtastic_client = Mock()
        mu.event_loop = Mock()
        mu.event_loop.is_closed.return_value = False
        mu.reconnecting = False
        mu.shutting_down = False

        with (
            patch("mmrelay.meshtastic_utils.reconnect"),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=_submit_done_reconnect_future,
            ),
            patch("mmrelay.meshtastic_utils.logger"),
        ):
            mu.on_lost_meshtastic_connection(detection_source="test source")

        assert len(mu._ble_executor_degraded_addresses) == 0
        assert len(mu._ble_executor_orphaned_workers_by_address) == 0


class TestShutdownClearsDegradedState:
    """Test that shutdown_shared_executors clears degraded state."""

    def test_shutdown_clears_metadata_degraded_state(self):
        """Test that shutdown clears metadata executor degraded state."""
        mu._metadata_executor_degraded = True
        mu._metadata_executor_orphaned_workers = 10

        mu.shutdown_shared_executors()

        assert mu._metadata_executor_degraded is False
        assert mu._metadata_executor_orphaned_workers == 0

    def test_shutdown_clears_ble_degraded_state(self):
        """Test that shutdown clears BLE executor degraded state."""
        mu._ble_executor_degraded_addresses = {TEST_BLE_MAC}
        mu._ble_executor_orphaned_workers_by_address = {TEST_BLE_MAC: 10}

        mu.shutdown_shared_executors()

        assert len(mu._ble_executor_degraded_addresses) == 0
        assert len(mu._ble_executor_orphaned_workers_by_address) == 0
