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
import logging
import threading
import unittest
from concurrent.futures import Future
from concurrent.futures import TimeoutError as ConcurrentTimeoutError
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock, Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.config import (
    DEFAULT_HEALTH_CHECK_ENABLED,
    DEFAULT_NODEDB_REFRESH_INTERVAL,
)
from mmrelay.constants.network import (
    CONFIG_KEY_CONNECTION_TYPE,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_TCP,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    INITIAL_HEALTH_CHECK_DELAY,
    METADATA_WATCHDOG_SECS,
)
from mmrelay.meshtastic_utils import (
    _get_device_metadata,
    _resolve_plugin_timeout,
    check_connection,
    connect_meshtastic,
    reconnect,
)
from tests.conftest import cleanup_ble_future_state

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
        "mmrelay.meshtastic_utils._metadata_future",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_future_started_at",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._metadata_executor_degraded",
        False,
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
        "mmrelay.meshtastic_utils.RELAY_START_TIME",
        0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_executor",
        None,
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


class TestGetDeviceMetadata(unittest.TestCase):
    """Test cases for _get_device_metadata helper function."""

    def test_get_device_metadata_uses_structured_metadata_first(self):
        """Use existing structured metadata without invoking getMetadata()."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata.side_effect = AssertionError(
            "getMetadata() should not be called when metadata is already available"
        )
        mock_client.localNode.iface.metadata = SimpleNamespace(
            firmware_version="2.7.18"
        )

        result = _get_device_metadata(mock_client)

        self.assertTrue(result["success"])
        self.assertEqual(result["firmware_version"], "2.7.18")
        self.assertEqual(result["raw_output"], "")
        mock_client.localNode.getMetadata.assert_not_called()

    def test_get_device_metadata_force_refresh_ignores_cached_metadata(self):
        """force_refresh=True should invoke getMetadata even with cached metadata."""
        mock_client = MagicMock()
        mock_client.localNode.iface.metadata = SimpleNamespace(
            firmware_version="2.7.18"
        )
        mock_client.localNode.getMetadata = MagicMock()

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "firmware_version: 2.7.19"
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client, force_refresh=True)

        self.assertTrue(result["success"])
        self.assertEqual(result["firmware_version"], "2.7.19")
        mock_client.localNode.getMetadata.assert_called_once()

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

    def test_get_device_metadata_raise_on_error_reraises(self):
        """raise_on_error=True should propagate getMetadata probe failures."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata.side_effect = Exception("Device error")
        mock_client.localNode.iface.metadata = None

        with pytest.raises(Exception, match="Device error"):
            _get_device_metadata(
                mock_client,
                force_refresh=True,
                raise_on_error=True,
            )

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

    def test_get_device_metadata_ignores_unknown_regex_firmware(self):
        """Regex-parsed 'unknown' firmware should not mark metadata retrieval successful."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()
        mock_client.localNode.iface.metadata = None

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "firmware_version: 2.3.15.abc123"
            mock_stringio.return_value = mock_output
            mock_client.localNode.getMetadata.side_effect = Exception("Metadata error")

            result = _get_device_metadata(mock_client)

            # Verify failure on exception
            self.assertFalse(result["success"])
            self.assertEqual(result["firmware_version"], "unknown")

    def test_get_device_metadata_normalizes_refreshed_firmware(self):
        """Refreshed firmware fallback should normalize values before success assignment."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()
        mock_client.localNode.iface.metadata = {
            "firmwareVersion": "unknown",
        }

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "hw_model: HELTEC_V3"
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client, force_refresh=True)

        self.assertFalse(result["success"])
        self.assertEqual(result["firmware_version"], "unknown")
        mock_client.localNode.getMetadata.assert_called_once()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_skips_when_probe_already_running(self, mock_logger):
        """In-flight metadata probes should not raise or start a duplicate request."""
        import mmrelay.meshtastic_utils as mu

        mock_client = MagicMock()
        mock_client.localNode.getMetadata = MagicMock()
        in_flight_future = MagicMock()
        in_flight_future.done.return_value = False
        mu._metadata_future = in_flight_future

        try:
            result = _get_device_metadata(
                mock_client,
                force_refresh=True,
                raise_on_error=True,
            )
        finally:
            mu._metadata_future = None

        self.assertFalse(result["success"])
        self.assertEqual(result["firmware_version"], "unknown")
        self.assertEqual(result["raw_output"], "")
        mock_client.localNode.getMetadata.assert_not_called()
        mock_logger.debug.assert_called_with(
            "getMetadata() already running; skipping new request"
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_submit_metadata_probe_clears_stale_future(self, mock_logger):
        """Stale in-flight metadata futures should be cleared before resubmitting."""
        import mmrelay.meshtastic_utils as mu

        stale_future = MagicMock()
        stale_future.done.return_value = False
        stale_future.add_done_callback = Mock()

        submitted_future = MagicMock()
        submitted_future.add_done_callback = Mock()

        mock_executor = MagicMock()
        mock_executor.submit.return_value = submitted_future
        probe = Mock()

        with (
            patch(
                "mmrelay.meshtastic_utils._get_metadata_executor",
                return_value=mock_executor,
            ),
            patch("mmrelay.meshtastic_utils._schedule_metadata_future_cleanup"),
            patch(
                "mmrelay.meshtastic_utils.time.monotonic",
                return_value=METADATA_WATCHDOG_SECS + 1.0,
            ),
        ):
            mu._metadata_future = stale_future
            mu._metadata_future_started_at = 0.0
            try:
                result = mu._submit_metadata_probe(probe)
            finally:
                mu._metadata_future = None
                mu._metadata_future_started_at = None

        self.assertIs(result, submitted_future)
        mock_executor.submit.assert_called_once_with(probe)
        mock_logger.warning.assert_any_call(
            "Metadata worker still running after %.0fs; clearing stale future (%s)",
            METADATA_WATCHDOG_SECS,
            "submit-retry",
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_submit_metadata_probe_resets_on_submission_failure(self, mock_logger):
        """Submission failures should trigger metadata executor reset for recovery."""
        import mmrelay.meshtastic_utils as mu

        mock_executor = MagicMock()
        mock_executor.submit.side_effect = RuntimeError("executor closed")
        probe = Mock()

        with (
            patch(
                "mmrelay.meshtastic_utils._get_metadata_executor",
                return_value=mock_executor,
            ),
            patch(
                "mmrelay.meshtastic_utils._reset_metadata_executor_for_stale_probe"
            ) as mock_reset,
        ):
            with pytest.raises(RuntimeError, match="executor closed"):
                mu._submit_metadata_probe(probe)

        mock_reset.assert_called_once()
        self.assertTrue(mock_logger.debug.called)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_submit_metadata_probe_rejects_when_degraded(self, mock_logger):
        """Degraded metadata executor should fail fast and skip submission."""
        import mmrelay.meshtastic_utils as mu

        probe = Mock()
        mock_executor = MagicMock()

        with patch(
            "mmrelay.meshtastic_utils._get_metadata_executor",
            return_value=mock_executor,
        ) as mock_get_executor:
            original_degraded = mu._metadata_executor_degraded
            original_future = mu._metadata_future
            original_started_at = mu._metadata_future_started_at
            mu._metadata_executor_degraded = True
            mu._metadata_future = None
            mu._metadata_future_started_at = None
            try:
                with pytest.raises(mu.MetadataExecutorDegradedError):
                    mu._submit_metadata_probe(probe)
            finally:
                mu._metadata_executor_degraded = original_degraded
                mu._metadata_future = original_future
                mu._metadata_future_started_at = original_started_at

        mock_get_executor.assert_not_called()
        mock_executor.submit.assert_not_called()
        mock_logger.error.assert_called()

    def test_reset_metadata_executor_checks_degraded_state_under_lock(self):
        """Degraded metadata reset checks should execute while holding the metadata lock."""
        import mmrelay.meshtastic_utils as mu

        class _TrackingLock:
            def __init__(self) -> None:
                self.entered = False

            def __enter__(self) -> "_TrackingLock":
                self.entered = True
                return self

            def __exit__(self, *_args: Any) -> bool:
                self.entered = False
                return False

        class _DegradedFlag:
            def __init__(self, lock: _TrackingLock) -> None:
                self._lock = lock
                self.check_states: list[bool] = []

            def __bool__(self) -> bool:
                self.check_states.append(self._lock.entered)
                return True

        tracking_lock = _TrackingLock()
        degraded_flag = _DegradedFlag(tracking_lock)
        original_lock = mu._metadata_future_lock
        original_degraded = mu._metadata_executor_degraded
        try:
            setattr(mu, "_metadata_future_lock", tracking_lock)
            setattr(mu, "_metadata_executor_degraded", degraded_flag)
            mu._reset_metadata_executor_for_stale_probe()
        finally:
            mu._metadata_future_lock = original_lock
            mu._metadata_executor_degraded = original_degraded

        assert degraded_flag.check_states
        assert all(degraded_flag.check_states)

    def test_get_device_metadata_raise_on_error_reraises_non_io_value_error(self):
        """Non-I/O ValueError failures from getMetadata() should propagate."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata.side_effect = ValueError("backend failure")

        with pytest.raises(ValueError, match="backend failure"):
            _get_device_metadata(
                mock_client,
                force_refresh=True,
                raise_on_error=True,
            )

    def test_get_device_metadata_structured_fallback_after_getmetadata(self):
        """Fallback to structured metadata when stdout does not include firmware version."""
        mock_client = MagicMock()
        mock_client.localNode.iface.metadata = None

        def _populate_metadata() -> None:
            """Simulate getMetadata() populating structured client metadata."""
            mock_client.localNode.iface.metadata = {
                "firmwareVersion": "2.7.18",
            }

        mock_client.localNode.getMetadata.side_effect = _populate_metadata

        with patch("mmrelay.meshtastic_utils.io.StringIO") as mock_stringio:
            mock_output = MagicMock()
            mock_output.getvalue.return_value = "hw_model: RAK4631"
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client)

        self.assertTrue(result["success"])
        self.assertEqual(result["firmware_version"], "2.7.18")
        self.assertIn("hw_model: RAK4631", result["raw_output"])


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
    def test_resolve_plugin_timeout_zero_logs_warning(self, mock_logger):
        """Non-positive timeout values should fall back and log a warning."""
        from mmrelay.meshtastic_utils import _resolve_plugin_timeout

        result = _resolve_plugin_timeout({"meshtastic": {"plugin_timeout": 0}}, 7.0)
        self.assertEqual(result, 7.0)
        mock_logger.warning.assert_called_once_with(
            "Invalid meshtastic.plugin_timeout value %r; using %.1fs fallback.",
            0,
            7.0,
        )

    @patch("mmrelay.meshtastic_utils.ThreadPoolExecutor")
    def test_maybe_reset_ble_executor_handles_cancel_timeout_and_stale_executor_shutdown(
        self, mock_thread_pool
    ):
        """BLE executor reset should cancel stale futures and shutdown old executors."""
        import mmrelay.meshtastic_utils as mu

        old_executor = mu._ble_executor
        old_future = mu._ble_future
        old_future_address = mu._ble_future_address
        old_future_started_at = mu._ble_future_started_at
        old_future_timeout_secs = mu._ble_future_timeout_secs
        old_threshold = mu._ble_timeout_reset_threshold
        old_timeout_counts = dict(mu._ble_timeout_counts)
        old_orphans = dict(mu._ble_executor_orphaned_workers_by_address)
        old_degraded = set(mu._ble_executor_degraded_addresses)

        stale_executor = Mock()
        stale_executor._shutdown = False
        stale_future = Mock()
        stale_future.done.return_value = False
        stale_future.result.side_effect = ConcurrentTimeoutError()

        replacement_executor = Mock()
        mock_thread_pool.return_value = replacement_executor
        created_executor = None

        try:
            mu._ble_timeout_reset_threshold = 1
            mu._ble_executor = stale_executor
            mu._ble_future = stale_future
            mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
            mu._ble_future_started_at = 1.0
            mu._ble_future_timeout_secs = 1.0
            mu._ble_timeout_counts = {"AA:BB:CC:DD:EE:FF": 4}
            mu._ble_executor_orphaned_workers_by_address = {}
            mu._ble_executor_degraded_addresses = set()

            mu._maybe_reset_ble_executor("AA:BB:CC:DD:EE:FF", timeout_count=1)
            created_executor = mu._ble_executor
        finally:
            mu._ble_executor = old_executor
            mu._ble_future = old_future
            mu._ble_future_address = old_future_address
            mu._ble_future_started_at = old_future_started_at
            mu._ble_future_timeout_secs = old_future_timeout_secs
            mu._ble_timeout_reset_threshold = old_threshold
            mu._ble_timeout_counts = old_timeout_counts
            mu._ble_executor_orphaned_workers_by_address = old_orphans
            mu._ble_executor_degraded_addresses = old_degraded

        stale_future.cancel.assert_called_once()
        stale_executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)
        mock_thread_pool.assert_called_once()
        self.assertIs(created_executor, replacement_executor)

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

        # Create a mock existing client that raises error on close
        mock_existing_client = Mock()
        mock_existing_client.close.side_effect = Exception("Close error")

        # Set up the global meshtastic_client to have an existing client
        import mmrelay.meshtastic_utils

        _orig_client = mmrelay.meshtastic_utils.meshtastic_client
        mmrelay.meshtastic_utils.meshtastic_client = mock_existing_client
        try:
            config = {
                "meshtastic": {
                    "connection_type": CONNECTION_TYPE_TCP,
                    "host": "localhost:4403",
                },
                "matrix_rooms": {},
            }

            # Mock interface creation to avoid actual connection
            with patch("meshtastic.tcp_interface.TCPInterface") as mock_tcp:
                mock_interface = Mock()
                mock_interface.getMyNodeInfo.return_value = {"num": 123}
                mock_tcp.return_value = mock_interface

                connect_meshtastic(config, force_connect=True)

                # Should log warning about close error but continue
                assert mock_logger.warning.called
                args = mock_logger.warning.call_args
                assert args[0][0] == "Error closing previous connection: %s"
                assert isinstance(args[0][1], Exception)
                assert str(args[0][1]) == "Close error"
                assert args[1].get("exc_info") is True
        finally:
            mmrelay.meshtastic_utils.meshtastic_client = _orig_client

    @patch("mmrelay.meshtastic_utils.reconnecting", True)
    @patch(
        "mmrelay.meshtastic_utils.shutting_down", True
    )  # Set to True to exit immediately
    def test_reconnect_function_basic(self):
        """Test reconnect function basic functionality."""

        # Mock the connect_meshtastic function
        with patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect:
            # Run the async function - it should exit immediately due to shutting_down=True
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(reconnect())
            finally:
                with contextlib.suppress(RuntimeError):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with contextlib.suppress(RuntimeError, AttributeError):
                    loop.run_until_complete(loop.shutdown_default_executor())
                loop.close()

            # Should not have attempted connection since shutting_down is True
            mock_connect.assert_not_called()
            # Function should return None when shutting down
            self.assertIsNone(result)

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.config", None)
    def test_check_connection_uncovered_paths(self, mock_logger):
        """Test check_connection function with missing config."""

        # Run the async function with no config
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(check_connection())
        finally:
            with contextlib.suppress(RuntimeError):
                loop.run_until_complete(loop.shutdown_asyncgens())
            with contextlib.suppress(RuntimeError, AttributeError):
                loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

        # Should return None when no config available
        self.assertIsNone(result)
        mock_logger.error.assert_called_with(
            "No configuration available. Cannot check connection."
        )


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


# ---------------------------------------------------------------------------
# Absorbed from tests/test_meshtastic_utils_health.py
# ---------------------------------------------------------------------------


def _make_health_config(
    connection_type: str = CONNECTION_TYPE_TCP,
    enabled: bool = True,
    heartbeat: int = 60,
    initial_delay: float | None = None,
    probe_timeout: float | None = None,
) -> dict[str, Any]:
    """
    Builds a nested configuration dictionary for meshtastic connection health checks.

    Parameters:
        connection_type (str): Connection transport type (e.g., "tcp" or "ble"). Defaults to "tcp".
        enabled (bool): Whether health checks are enabled. Defaults to True.
        heartbeat (int): Heartbeat interval in seconds used for health check scheduling. Defaults to 60.
        initial_delay (float | None): Optional delay before first health check.
        probe_timeout (float | None): Optional timeout per health probe.

    Returns:
        dict: Configuration mapping with keys "meshtastic" -> {"connection_type", "health_check": {"enabled", "heartbeat_interval"}}.
    """
    health_check = {"enabled": enabled, "heartbeat_interval": heartbeat}
    if initial_delay is not None:
        health_check["initial_delay"] = initial_delay
    if probe_timeout is not None:
        health_check["probe_timeout"] = probe_timeout

    return {
        "meshtastic": {
            CONFIG_KEY_CONNECTION_TYPE: connection_type,
            "health_check": health_check,
        }
    }


class SleepAndShutdown:
    """
    Helper to trigger shutdown after a specified number of sleep calls.

    This is used to test the health check loop which has an initial delay
    before the first check, followed by a loop sleep after each check.
    """

    def __init__(self, shutdown_after: int = 1):
        """
        Initialize the helper.

        Parameters:
            shutdown_after (int): Number of sleep calls before triggering shutdown.
                Defaults to 1 (shutdown on first sleep, for tests that don't need initial delay).
        """
        self.sleep_count = 0
        self.shutdown_after = shutdown_after

    def __call__(self, _seconds: float) -> None:
        """
        Increment sleep counter and trigger shutdown after configured count.

        Parameters:
            _seconds (float): Ignored --- present only to match the asyncio.sleep signature.
        """
        self.sleep_count += 1
        if self.sleep_count >= self.shutdown_after:
            mu.shutting_down = True
        return None


def _sleep_and_shutdown(_seconds: float) -> None:
    """
    Mark the application as shutting down; intended as a placeholder to use where asyncio.sleep is expected.

    Parameters:
        _seconds (float): Ignored --- present only to match the asyncio.sleep signature.
    """
    mu.shutting_down = True
    return None


@pytest.mark.asyncio
async def test_check_connection_health_disabled_returns():
    mu.config = _make_health_config(enabled=False)

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        await check_connection()

    mock_logger.info.assert_called_with(
        "Connection health checks are disabled in configuration"
    )


@pytest.mark.asyncio
async def test_check_connection_ble_skips_health_checks():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_BLE)
    mu.meshtastic_client = MagicMock()

    with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
        await check_connection()

    mock_logger.debug.assert_any_call(
        "BLE connection uses real-time disconnection detection; periodic health checks disabled"
    )


@pytest.mark.asyncio
async def test_check_connection_metadata_probe_succeeds():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = MagicMock()
    mu.meshtastic_client.localNode.onAckNak = Mock()

    executor = Mock()

    def _submit(fn, *args, **kwargs):
        probe_future: Future[None] = Future()
        fn(*args, **kwargs)
        probe_future.set_result(None)
        return probe_future

    executor.submit.side_effect = _submit

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch("mmrelay.meshtastic_utils._get_metadata_executor", return_value=executor),
        patch("mmrelay.meshtastic_utils._probe_device_connection") as mock_probe,
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    executor.submit.assert_called_once()
    mock_probe.assert_called_once()
    assert mock_probe.call_args.args[0] is mu.meshtastic_client
    assert mock_probe.call_args.args[1] == DEFAULT_MESHTASTIC_OPERATION_TIMEOUT
    mock_logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_check_connection_uses_configured_initial_delay():
    mu.config = _make_health_config(
        connection_type=CONNECTION_TYPE_TCP,
        heartbeat=5,
        initial_delay=2.5,
    )
    mu.meshtastic_client = None

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with patch(
        "mmrelay.meshtastic_utils.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=sleep_handler,
    ) as mock_sleep:
        await check_connection()

    assert mock_sleep.call_count == 2
    assert mock_sleep.call_args_list[0].args[0] == 2.5
    assert mock_sleep.call_args_list[1].args[0] == 5


@pytest.mark.asyncio
async def test_check_connection_uses_configured_probe_timeout():
    mu.config = _make_health_config(
        connection_type=CONNECTION_TYPE_TCP, probe_timeout=7.5
    )
    mu.meshtastic_client = MagicMock()
    mu.meshtastic_client.localNode.onAckNak = Mock()

    executor = Mock()

    def _submit(fn, *args, **kwargs):
        probe_future: Future[None] = Future()
        fn(*args, **kwargs)
        probe_future.set_result(None)
        return probe_future

    async def _wait_for_passthrough(awaitable, timeout):
        # Intentionally ignore timeout - this is a passthrough for testing
        _ = timeout
        return await awaitable

    executor.submit.side_effect = _submit
    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch("mmrelay.meshtastic_utils._get_metadata_executor", return_value=executor),
        patch("mmrelay.meshtastic_utils._probe_device_connection") as mock_probe,
        patch(
            "mmrelay.meshtastic_utils.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=_wait_for_passthrough,
        ) as mock_wait_for,
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
    ):
        await check_connection()

    mock_probe.assert_called_once()
    assert mock_probe.call_args.args[0] is mu.meshtastic_client
    assert mock_probe.call_args.args[1] == 7.5
    assert mock_wait_for.call_count == 1
    assert mock_wait_for.call_args.kwargs["timeout"] == 7.5


@pytest.mark.asyncio
async def test_check_connection_triggers_reconnect_on_probe_failure():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = MagicMock()
    mu.meshtastic_client.localNode.onAckNak = Mock()

    executor = Mock()
    probe_future: Future[None] = Future()
    probe_future.set_exception(Exception("probe failed"))
    executor.submit.return_value = probe_future

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch("mmrelay.meshtastic_utils._get_metadata_executor", return_value=executor),
        patch("mmrelay.meshtastic_utils.on_lost_meshtastic_connection") as mock_lost,
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_lost.assert_called_once()
    mock_logger.error.assert_any_call(
        "%s connection health check failed: %s",
        "Tcp",
        ANY,
        exc_info=True,
    )


@pytest.mark.asyncio
async def test_check_connection_tracks_timed_out_probe_until_worker_finishes():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = MagicMock()
    mu.meshtastic_client.localNode.onAckNak = Mock()

    executor = Mock()
    probe_future: Future[None] = Future()
    assert probe_future.set_running_or_notify_cancel()
    executor.submit.return_value = probe_future

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch("mmrelay.meshtastic_utils._get_metadata_executor", return_value=executor),
        patch.object(mu, "DEFAULT_MESHTASTIC_OPERATION_TIMEOUT", 0.01),
        patch("mmrelay.meshtastic_utils.on_lost_meshtastic_connection") as mock_lost,
        patch(
            "mmrelay.meshtastic_utils.asyncio.wait_for",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError,
        ),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    assert mu._metadata_future is probe_future
    executor.submit.assert_called_once()
    mock_lost.assert_called_once()
    mock_logger.error.assert_any_call(
        "%s connection health check failed: %s",
        "Tcp",
        ANY,
        exc_info=True,
    )

    probe_future.set_result(None)
    assert mu._metadata_future is None


@pytest.mark.asyncio
async def test_check_connection_skips_when_metadata_probe_active():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = MagicMock()
    mu.meshtastic_client.localNode.onAckNak = Mock()
    mu._metadata_future = Mock()
    mu._metadata_future.done.return_value = False

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils._get_metadata_executor") as mock_executor,
        patch("mmrelay.meshtastic_utils.on_lost_meshtastic_connection") as mock_lost,
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_executor.assert_not_called()
    mock_lost.assert_not_called()
    mock_logger.debug.assert_any_call(
        "Skipping connection check - metadata probe already in progress"
    )


@pytest.mark.asyncio
async def test_check_connection_skips_when_reconnecting():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = MagicMock()
    mu.reconnecting = True

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_logger.debug.assert_any_call(
        "Skipping connection check - reconnection in progress"
    )


@pytest.mark.asyncio
async def test_check_connection_skips_when_no_client():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    mu.meshtastic_client = None

    sleep_handler = SleepAndShutdown(
        shutdown_after=2
    )  # Shutdown after initial delay + loop sleep
    with (
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=sleep_handler,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await check_connection()

    mock_logger.debug.assert_any_call("Skipping connection check - no client available")


@pytest.mark.asyncio
async def test_check_connection_uses_legacy_heartbeat_interval():
    mu.config = _make_health_config(connection_type=CONNECTION_TYPE_TCP)
    del mu.config["meshtastic"]["health_check"]["heartbeat_interval"]
    mu.config["meshtastic"]["heartbeat_interval"] = 5
    mu.meshtastic_client = None

    # We need to survive the first sleep (initial delay) to reach the loop
    # where the heartbeat interval is used.
    sleep_handler = SleepAndShutdown(shutdown_after=2)

    with patch(
        "mmrelay.meshtastic_utils.asyncio.sleep",
        new_callable=AsyncMock,
        side_effect=sleep_handler,
    ) as mock_sleep:
        await check_connection()

    # Should be called twice:
    # 1. Initial delay (INITIAL_HEALTH_CHECK_DELAY)
    # 2. Heartbeat interval (5)
    assert mock_sleep.call_count == 2

    # Check the first call specifically (initial delay)
    assert mock_sleep.call_args_list[0].args[0] == INITIAL_HEALTH_CHECK_DELAY

    # Check the second call specifically (the heartbeat)
    # call_args_list[1] is the second call, args[0] is the first arg
    assert mock_sleep.call_args_list[1].args[0] == 5


def test_probe_device_connection_handles_admin_response_without_routing():
    class AckState:
        def __init__(self):
            self.receivedAck = False
            self.receivedNak = False
            self.receivedImplAck = False
            self.reset = Mock(side_effect=self._reset)

        def _reset(self):
            self.receivedAck = False
            self.receivedNak = False
            self.receivedImplAck = False

    ack_state = AckState()
    local_node = SimpleNamespace(nodeNum=12345)
    local_node.iface = SimpleNamespace(
        _acknowledgment=ack_state,
        localNode=local_node,
    )

    client = SimpleNamespace(
        localNode=local_node,
        _acknowledgment=ack_state,
        waitForAckNak=Mock(),
    )

    def _send_data_side_effect(*_args, **kwargs):
        kwargs["onResponse"]({"from": str(local_node.nodeNum), "decoded": {}})
        return None

    client.sendData = Mock(side_effect=_send_data_side_effect)

    mu._probe_device_connection(client)

    client.sendData.assert_called_once()
    assert ack_state.reset.call_count >= 1
    client.waitForAckNak.assert_not_called()


def test_probe_device_connection_uses_bounded_ack_timeout():
    ack_state = SimpleNamespace(
        receivedAck=False,
        receivedNak=False,
        receivedImplAck=False,
        reset=Mock(),
    )
    local_node = SimpleNamespace(nodeNum=12345)
    local_node.iface = SimpleNamespace(
        _acknowledgment=ack_state,
        localNode=local_node,
    )

    client = SimpleNamespace(
        localNode=local_node,
        _acknowledgment=ack_state,
        sendData=Mock(return_value=None),
        waitForAckNak=Mock(),
    )

    with patch("mmrelay.meshtastic_utils.time.sleep", return_value=None):
        with pytest.raises(TimeoutError):
            mu._probe_device_connection(client, timeout_secs=0.01)

    client.sendData.assert_called_once()
    client.waitForAckNak.assert_not_called()


def test_probe_device_connection_tracks_request_id_for_health_logging():
    local_node = SimpleNamespace(nodeNum=12345)
    client = SimpleNamespace(
        localNode=local_node,
        _acknowledgment=None,
        sendData=Mock(return_value=SimpleNamespace(id=987654321)),
        waitForAckNak=Mock(),
    )

    with patch(
        "mmrelay.meshtastic_utils._track_health_probe_request_id",
        return_value=987654321,
    ) as mock_track:
        mu._probe_device_connection(client, timeout_secs=7.5)

    mock_track.assert_called_once_with(987654321, 7.5)
    client.waitForAckNak.assert_called_once()


# ---------------------------------------------------------------------------
# Absorbed from tests/test_meshtastic_utils_probe_coverage.py
# ---------------------------------------------------------------------------


class TestGetConnectTimeProbeSettings(unittest.TestCase):
    def test_ble_returns_disabled(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"meshtastic": {}}, CONNECTION_TYPE_BLE
        )
        self.assertFalse(enabled)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    def test_none_config_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            None, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    def test_non_dict_config_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            cast(Any, "not_a_dict"),
            CONNECTION_TYPE_TCP,
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    def test_config_without_meshtastic_key_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"other": {}}, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))

    def test_meshtastic_not_dict_returns_defaults(self):
        enabled, timeout = mu._get_connect_time_probe_settings(
            {"meshtastic": "not_a_dict"}, CONNECTION_TYPE_TCP
        )
        self.assertEqual(enabled, DEFAULT_HEALTH_CHECK_ENABLED)
        self.assertEqual(timeout, float(DEFAULT_MESHTASTIC_OPERATION_TIMEOUT))


class TestScheduleConnectTimeCalibrationProbe(unittest.TestCase):
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_client_no_local_node_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock(spec=["sendData"])
        client.sendData = Mock()
        del client.localNode

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_client_no_send_data_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock(spec=["localNode"])
        client.localNode = Mock()
        del client.sendData

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_send_data_not_callable_returns_early(self, mock_settings):
        mock_settings.return_value = (True, 30.0)
        client = Mock()
        client.localNode = Mock()
        client.sendData = "not_callable"

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_degraded_executor_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.side_effect = mu.MetadataExecutorDegradedError("degraded")
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_runtime_error_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.side_effect = RuntimeError("executor broken")
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )

    @patch("mmrelay.meshtastic_utils._submit_metadata_probe")
    @patch("mmrelay.meshtastic_utils._get_connect_time_probe_settings")
    def test_probe_future_none_returns_early(self, mock_settings, mock_submit):
        mock_settings.return_value = (True, 30.0)
        mock_submit.return_value = None
        client = Mock()
        client.localNode = Mock()
        client.sendData = Mock()

        mu._schedule_connect_time_calibration_probe(
            client,
            connection_type=CONNECTION_TYPE_TCP,
            active_config={"meshtastic": {}},
        )


# ---------------------------------------------------------------------------
# Absorbed from tests/test_meshtastic_utils_node_name_refresh.py
# ---------------------------------------------------------------------------


class _OnePassEvent:
    """Event that starts cleared and sets itself when awaited once."""

    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    async def wait(self) -> None:
        self._set = True


class _TimeoutThenSetEvent:
    """Event whose first wait times out and second wait sets the event."""

    def __init__(self) -> None:
        self._set = False
        self._wait_calls = 0
        self.first_wait_cancelled = False

    def is_set(self) -> bool:
        return self._set

    async def wait(self) -> None:
        self._wait_calls += 1
        if self._wait_calls == 1:
            try:
                # Sleep long enough to be cancelled by the refresh loop's timeout
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.first_wait_cancelled = True
                raise
            return
        self._set = True


class _ClientWithoutNodes:
    """Minimal client shape with no nodes attribute."""


class _ClientWithNodes:
    """Minimal client shape with a dict-backed nodes attribute."""

    def __init__(self, nodes: dict[str, Any]) -> None:
        self.nodes = nodes


def test_get_nodedb_refresh_interval_ignores_non_dict_config() -> None:
    """Non-dict config inputs should fall back to default nodedb refresh interval."""
    interval = mu.get_nodedb_refresh_interval_seconds(cast(Any, []))
    assert interval == DEFAULT_NODEDB_REFRESH_INTERVAL


@pytest.mark.asyncio
async def test_refresh_node_name_tables_skips_when_nodes_attribute_unavailable() -> (
    None
):
    """Missing client.nodes should skip sync rather than treat as empty nodedb."""
    with (
        patch.object(mu, "meshtastic_client", _ClientWithoutNodes()),
        patch.object(mu, "sync_name_tables_if_changed") as mock_sync,
    ):
        await mu.refresh_node_name_tables(
            cast(asyncio.Event, _OnePassEvent()),
            refresh_interval_seconds=0.01,
        )
    mock_sync.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_node_name_tables_handles_timeout_then_retries() -> None:
    """Refresh loop should continue after wait timeout and retry sync."""
    event = _TimeoutThenSetEvent()
    client = _ClientWithNodes(
        {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
            }
        }
    )
    state_after_first_refresh = {"state": 1}
    state_after_second_refresh = {"state": 2}
    with (
        patch.object(mu, "meshtastic_client", client),
        patch.object(
            mu,
            "sync_name_tables_if_changed",
            side_effect=[state_after_first_refresh, state_after_second_refresh],
        ) as mock_sync,
    ):
        await mu.refresh_node_name_tables(
            cast(asyncio.Event, event),
            refresh_interval_seconds=0.01,
        )
    assert event.first_wait_cancelled is True
    assert event._wait_calls == 2
    assert mock_sync.call_count == 2
    assert mock_sync.call_args_list[0].args[1] is None
    assert mock_sync.call_args_list[1].args[1] is state_after_first_refresh


@pytest.mark.asyncio
async def test_refresh_node_name_tables_logs_unavailable_only_once_per_state() -> None:
    """Unavailable-client debug noise should be deduplicated across loop iterations."""
    event = _TimeoutThenSetEvent()
    with (
        patch.object(mu, "meshtastic_client", None),
        patch.object(mu, "logger") as mock_logger,
    ):
        await mu.refresh_node_name_tables(
            cast(asyncio.Event, event),
            refresh_interval_seconds=0.01,
        )

    unavailable_calls = [
        c
        for c in mock_logger.debug.call_args_list
        if c.args
        and c.args[0]
        == "Skipping name-cache refresh from NodeDB because Meshtastic client is unavailable"
    ]
    assert len(unavailable_calls) == 1


@pytest.mark.asyncio
async def test_refresh_node_name_tables_uses_reconnecting_unavailable_message() -> None:
    """When reconnecting, refresh logs reconnect-specific unavailability reason."""
    event = _OnePassEvent()
    with (
        patch.object(mu, "meshtastic_client", None),
        patch.object(mu, "reconnecting", True),
        patch.object(mu, "logger") as mock_logger,
    ):
        await mu.refresh_node_name_tables(
            cast(asyncio.Event, event),
            refresh_interval_seconds=0.01,
        )

    mock_logger.debug.assert_any_call(
        "Skipping name-cache refresh from NodeDB while reconnection is in progress"
    )


@pytest.mark.asyncio
async def test_refresh_node_name_tables_non_positive_interval_exits_after_one_pass() -> (
    None
):
    """Zero interval should perform one immediate pass and return."""
    with (
        patch.object(
            mu,
            "meshtastic_client",
            _ClientWithNodes(
                {
                    "node_a": {
                        "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
                    }
                }
            ),
        ),
        patch.object(mu, "sync_name_tables_if_changed", return_value=()) as mock_sync,
    ):
        await mu.refresh_node_name_tables(
            cast(asyncio.Event, _OnePassEvent()),
            refresh_interval_seconds=0.0,
        )
    mock_sync.assert_called_once()


@pytest.mark.asyncio
async def test_refresh_node_name_tables_handles_sync_exceptions(caplog) -> None:
    """Sync errors should be logged and re-raised for supervisor handling."""
    caplog.set_level(logging.ERROR, logger=mu.logger.name)
    original_propagate = mu.logger.propagate
    mu.logger.propagate = True
    client = _ClientWithNodes(
        {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha", "shortName": "A"},
            }
        }
    )
    try:
        with (
            patch.object(mu, "meshtastic_client", client),
            patch.object(
                mu,
                "sync_name_tables_if_changed",
                side_effect=RuntimeError("sync failure"),
            ) as mock_sync,
        ):
            with pytest.raises(RuntimeError, match="sync failure"):
                await mu.refresh_node_name_tables(
                    cast(asyncio.Event, _OnePassEvent()),
                    refresh_interval_seconds=0.0,
                )
    finally:
        mu.logger.propagate = original_propagate
    mock_sync.assert_called_once()
    assert any(
        "Failed to refresh name-cache tables from NodeDB snapshot" in record.message
        for record in caplog.records
    )


class TestFirmwareVersionExtraction:
    """Test firmware version extraction from device metadata."""

    def test_normalize_firmware_version_with_bytes(self):
        """Test normalizing firmware version from bytes."""
        result = mu._normalize_firmware_version(b"2.1.5")
        assert result == "2.1.5"

    def test_normalize_firmware_version_with_string(self):
        """Test normalizing firmware version from string."""
        result = mu._normalize_firmware_version("  2.1.6  ")
        assert result == "2.1.6"

    def test_normalize_firmware_version_with_unknown(self):
        """Test normalizing firmware version with 'unknown' value."""
        result = mu._normalize_firmware_version("unknown")
        assert result is None

    def test_normalize_firmware_version_with_empty(self):
        """Test normalizing firmware version with empty string."""
        result = mu._normalize_firmware_version("")
        assert result is None

    def test_normalize_firmware_version_with_non_string(self):
        """Test normalizing firmware version with non-string type."""
        result = mu._normalize_firmware_version(123)
        assert result is None

    def test_extract_firmware_version_from_metadata_dict(self):
        """Test extracting firmware version from dict metadata."""
        metadata = {"firmware_version": "2.2.0"}
        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.2.0"

    def test_extract_firmware_version_from_metadata_dict_camel_case(self):
        """Test extracting firmware version with camelCase key."""
        metadata = {"firmwareVersion": "2.2.1"}
        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.2.1"

    def test_extract_firmware_version_from_metadata_object(self):
        """Test extracting firmware version from object metadata."""
        metadata = Mock()
        metadata.firmware_version = "2.3.0"

        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.3.0"

    def test_extract_firmware_version_from_metadata_object_camel_case(self):
        """Test extracting firmware version from object with camelCase."""
        metadata = Mock(spec=[])
        metadata.firmwareVersion = "2.3.1"

        result = mu._extract_firmware_version_from_metadata(metadata)
        assert result == "2.3.1"

    def test_extract_firmware_version_from_metadata_none(self):
        """Test extracting firmware version from None metadata."""
        result = mu._extract_firmware_version_from_metadata(None)
        assert result is None

    def test_get_device_metadata_no_local_node(self):
        """Test _get_device_metadata when client has no localNode."""
        client = Mock()
        client.localNode = None

        result = mu._get_device_metadata(client)

        assert result["firmware_version"] == "unknown"
        assert result["success"] is False

    def test_get_device_metadata_no_get_metadata_method(self):
        """Test _get_device_metadata when localNode has no getMetadata."""
        client = Mock()
        client.localNode = Mock(spec=[])

        result = mu._get_device_metadata(client)

        assert result["firmware_version"] == "unknown"
        assert result["success"] is False

    def test_get_device_metadata_raises_on_error(self):
        """Test _get_device_metadata raises error when requested."""
        client = Mock()
        client.localNode = None

        with pytest.raises(RuntimeError, match="no localNode.getMetadata"):
            mu._get_device_metadata(client, raise_on_error=True)

    def test_get_device_metadata_runtime_error_submission(self):
        """Test _get_device_metadata handles RuntimeError during submission."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.side_effect = RuntimeError("Executor shutting down")

            result = mu._get_device_metadata(client)

            assert result["firmware_version"] == "unknown"
            assert result["success"] is False

    def test_get_device_metadata_runtime_error_raises(self):
        """Test _get_device_metadata re-raises RuntimeError when requested."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.side_effect = RuntimeError("Executor shutting down")

            with pytest.raises(RuntimeError, match="Executor shutting down"):
                mu._get_device_metadata(client, raise_on_error=True)

    def test_get_device_metadata_future_none(self):
        """Test _get_device_metadata when probe submission returns None."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = None

            result = mu._get_device_metadata(client)

            assert result["firmware_version"] == "unknown"
            assert result["success"] is False

    def test_get_device_metadata_timeout_with_raise(self):
        """Test _get_device_metadata handles timeout with raise_on_error."""
        client = Mock()
        client.localNode = Mock()
        client.localNode.getMetadata = Mock()
        client.localNode.metadata = None
        client.metadata = None

        mock_future = Mock(spec=Future)
        mock_future.result.side_effect = TimeoutError("Timeout")
        mock_future.done.return_value = True

        with patch("mmrelay.meshtastic_utils._submit_metadata_probe") as mock_submit:
            mock_submit.return_value = mock_future

            with pytest.raises(TimeoutError):
                mu._get_device_metadata(client, raise_on_error=True)
