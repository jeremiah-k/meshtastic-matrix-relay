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

    def test_get_device_metadata_raise_on_error_reraises(self):
        """raise_on_error=True should propagate getMetadata probe failures."""
        mock_client = MagicMock()
        mock_client.localNode.getMetadata.side_effect = Exception("Device error")
        mock_client.localNode.iface.metadata = None

        with self.assertRaisesRegex(Exception, "Device error"):
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
            mock_output.getvalue.return_value = "firmware_version: unknown"
            mock_stringio.return_value = mock_output

            result = _get_device_metadata(mock_client, force_refresh=True)

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
            with self.assertRaisesRegex(RuntimeError, "executor closed"):
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
                with self.assertRaises(mu.MetadataExecutorDegradedError):
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
            mu._metadata_future_lock = tracking_lock  # type: ignore[assignment]
            mu._metadata_executor_degraded = degraded_flag  # type: ignore[assignment]
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

        with self.assertRaisesRegex(ValueError, "backend failure"):
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
        from mmrelay.meshtastic_utils import connect_meshtastic

        # Create a mock existing client that raises error on close
        mock_existing_client = Mock()
        mock_existing_client.close.side_effect = Exception("Close error")

        # Set up the global meshtastic_client to have an existing client
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = mock_existing_client

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
        import asyncio

        from mmrelay.meshtastic_utils import check_connection

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
