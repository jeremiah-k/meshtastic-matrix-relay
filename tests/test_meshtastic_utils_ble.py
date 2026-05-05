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

import contextlib
import sys
import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.constants.network import (
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONNECTION_TYPE_BLE,
)
from mmrelay.meshtastic_utils import (
    _is_ble_duplicate_connect_suppressed_error,
    _reset_ble_connection_gate_state,
    connect_meshtastic,
)
from tests.conftest import cleanup_ble_future_state
from tests.constants import TEST_BLE_MAC

TEST_PACKET_RX_TIME = 1234567890


class _SuppressedBLEInterface:
    """Test double that simulates duplicate connect suppression."""

    def __init__(self, **_kwargs):
        raise RuntimeError("Connection suppressed: recently connected elsewhere")


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
        "mmrelay.meshtastic_utils.shutting_down",
        False,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.reconnecting",
        False,
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
        "mmrelay.meshtastic_utils._ble_executor",
        None,
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
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.event_loop",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils.matrix_rooms",
        [],
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_generation_by_address",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_iface_generation_by_id",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_teardown_unresolved_by_generation",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_lifecycle_lock",
        threading.Lock(),
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_watchdog_secs",
        120.0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_timeout_reset_threshold",
        3,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_scan_timeout_secs",
        4.0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_future_stale_grace_secs",
        2.0,
        raising=False,
    )
    monkeypatch.setattr(
        "mmrelay.meshtastic_utils._ble_interface_create_timeout_secs",
        25.0,
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


class TestBleHelperFunctions(unittest.TestCase):
    """Test BLE helper functions."""

    def test_scan_for_ble_address_find_device_with_timeout(self):
        """Cover BleakScanner.find_device_by_address(timeout=...)."""
        from mmrelay.meshtastic_utils import _scan_for_ble_address

        async def _find_device(_address: str, timeout: float | None = None):
            return object()

        fake_bleak = SimpleNamespace(
            BleakScanner=SimpleNamespace(find_device_by_address=_find_device)
        )

        with (
            patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True),
            patch.dict(sys.modules, {"bleak": fake_bleak}),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                side_effect=RuntimeError(),
            ),
        ):
            self.assertTrue(_scan_for_ble_address("AA:BB", 0.1))

    def test_scan_for_ble_address_find_device_without_timeout(self):
        """Cover BleakScanner.find_device_by_address() without timeout support."""
        from mmrelay.meshtastic_utils import _scan_for_ble_address

        async def _find_device(_address: str):
            return None

        async def _wait_for(coro, timeout=None):
            return await coro

        fake_bleak = SimpleNamespace(
            BleakScanner=SimpleNamespace(find_device_by_address=_find_device)
        )

        with (
            patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True),
            patch.dict(sys.modules, {"bleak": fake_bleak}),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                side_effect=RuntimeError(),
            ),
            patch("mmrelay.meshtastic_utils.asyncio.wait_for", side_effect=_wait_for),
        ):
            result = _scan_for_ble_address("AA:BB", 0.1)
            self.assertFalse(result)

    def test_scan_for_ble_address_discover_fallback(self):
        """Cover BleakScanner.discover() fallback when find_device_by_address is absent."""
        from mmrelay.meshtastic_utils import _scan_for_ble_address

        async def _discover(timeout: float | None = None):
            device = SimpleNamespace(address="AA:BB")
            return [device]

        fake_bleak = SimpleNamespace(
            BleakScanner=SimpleNamespace(
                find_device_by_address=None,
                discover=_discover,
            )
        )

        with (
            patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True),
            patch.dict(sys.modules, {"bleak": fake_bleak}),
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                side_effect=RuntimeError(),
            ),
        ):
            self.assertTrue(_scan_for_ble_address("AA:BB", 0.1))

    def test_is_ble_discovery_error_message_matches(self):
        """Cover message substring checks in _is_ble_discovery_error."""
        from mmrelay.meshtastic_utils import _is_ble_discovery_error

        self.assertTrue(
            _is_ble_discovery_error(
                Exception("No Meshtastic BLE peripheral found during scan")
            )
        )
        self.assertTrue(
            _is_ble_discovery_error(
                Exception("Timed out waiting for connection completion")
            )
        )
        self.assertTrue(_is_ble_discovery_error(KeyError("path")))

    def test_is_ble_discovery_error_type_matches(self):
        """Cover BLEError and MeshInterfaceError type checks."""
        from mmrelay.meshtastic_utils import _is_ble_discovery_error

        class FakeBleInterface:
            class BLEError(Exception):
                pass

        class FakeMeshInterface:
            class MeshInterfaceError(Exception):
                pass

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            FakeBleInterface,
        ):
            self.assertTrue(_is_ble_discovery_error(FakeBleInterface.BLEError("boom")))

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.mesh_interface.MeshInterface",
            FakeMeshInterface,
        ):
            self.assertTrue(
                _is_ble_discovery_error(FakeMeshInterface.MeshInterfaceError("boom"))
            )


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
            error1 = mu.BleakDBusError("Test error")  # type: ignore[call-arg]
        except TypeError:
            # If that fails, try the real bleak constructor
            error1 = mu.BleakDBusError("Test error", ["error_body"])

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


class TestClearBleFuture:
    """Test _clear_ble_future clearing all related globals."""

    def test_clear_ble_future_clears_all_globals(self, monkeypatch):
        """Test that when done_future matches _ble_future, all related globals are cleared."""
        mock_future = Mock(spec=Future)
        monkeypatch.setattr(mu, "_ble_future", mock_future, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", TEST_BLE_MAC, raising=False)
        monkeypatch.setattr(
            mu, "_ble_future_started_at", time.monotonic(), raising=False
        )
        monkeypatch.setattr(mu, "_ble_future_timeout_secs", 30.0, raising=False)
        monkeypatch.setattr(mu, "_ble_timeout_counts", {TEST_BLE_MAC: 5}, raising=False)

        mu._clear_ble_future(mock_future)

        assert mu._ble_future is None
        assert mu._ble_future_address is None
        assert mu._ble_future_started_at is None
        assert mu._ble_future_timeout_secs is None
        assert TEST_BLE_MAC not in mu._ble_timeout_counts

    def test_clear_ble_future_no_match_does_not_clear(self, monkeypatch):
        """Test that _clear_ble_future does not clear when future doesn't match."""
        mock_future1 = Mock(spec=Future)
        mock_future2 = Mock(spec=Future)
        monkeypatch.setattr(mu, "_ble_future", mock_future1, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", TEST_BLE_MAC, raising=False)
        monkeypatch.setattr(
            mu, "_ble_future_started_at", time.monotonic(), raising=False
        )
        monkeypatch.setattr(mu, "_ble_future_timeout_secs", 30.0, raising=False)
        monkeypatch.setattr(mu, "_ble_timeout_counts", {TEST_BLE_MAC: 5}, raising=False)

        mu._clear_ble_future(mock_future2)

        assert mu._ble_future is mock_future1
        assert mu._ble_future_address == TEST_BLE_MAC
        assert mu._ble_future_started_at is not None
        assert mu._ble_future_timeout_secs == 30.0
        assert mu._ble_timeout_counts.get(TEST_BLE_MAC) == 5


class TestEnsureBleWorkerAvailableStaleWorker:
    """Test _ensure_ble_worker_available stale worker detection."""

    def test_ensure_ble_worker_stale_detection(self, monkeypatch):
        """Test when elapsed >= stale_after, logs warning and calls _maybe_reset_ble_executor."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        monkeypatch.setattr(mu, "_ble_future", mock_future, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", TEST_BLE_MAC, raising=False)
        monkeypatch.setattr(
            mu, "_ble_future_started_at", time.monotonic() - 100, raising=False
        )
        monkeypatch.setattr(mu, "_ble_future_timeout_secs", 30.0, raising=False)

        def _simulate_reset(*_args, **_kwargs):
            monkeypatch.setattr(mu, "_ble_future", None, raising=False)
            monkeypatch.setattr(mu, "_ble_future_address", None, raising=False)
            monkeypatch.setattr(mu, "_ble_future_started_at", None, raising=False)
            monkeypatch.setattr(mu, "_ble_future_timeout_secs", None, raising=False)

        with patch(
            "mmrelay.meshtastic_utils._maybe_reset_ble_executor",
            side_effect=_simulate_reset,
        ) as mock_reset:
            with patch("mmrelay.meshtastic_utils._record_ble_timeout", return_value=5):
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    with patch(
                        "mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 10.0
                    ):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3
                        ):
                            mu._ensure_ble_worker_available(
                                TEST_BLE_MAC, operation="test"
                            )

                            warning_calls = [
                                str(call) for call in mock_logger.warning.call_args_list
                            ]
                            assert any("stale" in call for call in warning_calls)
                            mock_reset.assert_called_once()

    def test_ensure_ble_worker_busy_raises_timeout(self, monkeypatch):
        """Test when worker is busy (future not done), raises TimeoutError."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        monkeypatch.setattr(mu, "_ble_future", mock_future, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", TEST_BLE_MAC, raising=False)
        monkeypatch.setattr(
            mu, "_ble_future_started_at", time.monotonic(), raising=False
        )
        monkeypatch.setattr(mu, "_ble_future_timeout_secs", 30.0, raising=False)

        with patch("mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 1000.0):
            with pytest.raises(TimeoutError, match="already in progress"):
                mu._ensure_ble_worker_available(TEST_BLE_MAC, operation="test")


class TestMaybeResetBleExecutor:
    """Test _maybe_reset_ble_executor threshold and cleanup."""

    def test_maybe_reset_ble_executor_below_threshold(self, monkeypatch):
        """Test when timeout_count < threshold, returns early without reset."""
        mock_exec = Mock(spec=ThreadPoolExecutor)
        mock_exec._shutdown = False
        monkeypatch.setattr(mu, "_ble_executor", mock_exec, raising=False)

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 10):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=3)

            mu._ble_executor.shutdown.assert_not_called()

    def test_maybe_reset_ble_executor_shutdown_executor(self, monkeypatch):
        """Test when _ble_executor is not None and not shutdown, calls shutdown."""
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        monkeypatch.setattr(mu, "_ble_executor", mock_executor, raising=False)
        monkeypatch.setattr(mu, "_ble_future", None, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", None, raising=False)

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=5)

            mock_executor.shutdown.assert_called_once_with(
                wait=False, cancel_futures=True
            )
            assert mu._ble_executor is not mock_executor

    def test_maybe_reset_ble_executor_handles_type_error(self, monkeypatch):
        """Test handling TypeError during shutdown (older Python compatibility)."""
        mock_executor = Mock(spec=ThreadPoolExecutor)
        mock_executor._shutdown = False
        mock_executor.shutdown.side_effect = [TypeError("cancel_futures"), None]
        monkeypatch.setattr(mu, "_ble_executor", mock_executor, raising=False)
        monkeypatch.setattr(mu, "_ble_future", None, raising=False)
        monkeypatch.setattr(mu, "_ble_future_address", None, raising=False)

        with patch("mmrelay.meshtastic_utils._ble_timeout_reset_threshold", 3):
            mu._maybe_reset_ble_executor(TEST_BLE_MAC, timeout_count=5)

            assert mock_executor.shutdown.call_count == 2
            assert mu._ble_executor is not mock_executor


class TestBleInterfaceCreationShuttingDown:
    """Test BLE interface creation shutting_down check."""

    def test_connect_meshtastic_returns_none_when_shutting_down(self, monkeypatch):
        """Test when shutting_down is True, connect_meshtastic returns None."""
        monkeypatch.setattr(mu, "shutting_down", True, raising=False)
        monkeypatch.setattr(
            mu,
            "config",
            {
                "meshtastic": {
                    CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                    CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
                }
            },
            raising=False,
        )

        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = mu.connect_meshtastic()

            assert result is None
            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("shutdown" in call.lower() for call in debug_calls)

    def test_ble_interface_creation_calls_ensure_ble_worker(self, monkeypatch):
        """Test _ensure_ble_worker_available is called with correct parameters during BLE connect."""
        monkeypatch.setattr(mu, "shutting_down", False, raising=False)
        monkeypatch.setattr(mu, "reconnecting", False, raising=False)
        monkeypatch.setattr(mu, "meshtastic_client", None, raising=False)
        monkeypatch.setattr(
            mu,
            "config",
            {
                "meshtastic": {
                    CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                    CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
                    "retries": 1,
                }
            },
            raising=False,
        )

        with patch(
            "mmrelay.meshtastic_utils._ensure_ble_worker_available"
        ) as mock_ensure:
            mock_ensure.return_value = None
            with patch("mmrelay.meshtastic_utils._get_ble_executor") as mock_get_exec:
                mock_executor = Mock(spec=ThreadPoolExecutor)
                mock_get_exec.return_value = mock_executor
                mock_future = Mock(spec=Future)
                mock_future.done.return_value = False
                mock_future.result.return_value = None
                mock_executor.submit.return_value = mock_future

                with patch(
                    "mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 30.0
                ):
                    with patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_future_stale_grace_secs",
                            1000.0,
                        ):
                            with patch(
                                "mmrelay.meshtastic_utils._ble_timeout_reset_threshold",
                                3,
                            ):
                                with patch(
                                    "mmrelay.meshtastic_utils._ble_future_watchdog_secs",
                                    60.0,
                                ):
                                    with patch("mmrelay.meshtastic_utils.meshtastic"):
                                        mu.connect_meshtastic()

                                        mock_ensure.assert_any_call(
                                            TEST_BLE_MAC,
                                            operation="interface creation",
                                        )


class TestBleConnectShuttingDown:
    """Test BLE connect() shutting_down and busy worker checks."""

    def test_ensure_ble_worker_available_called_for_connect(self, monkeypatch):
        """Test _ensure_ble_worker_available is called with 'connect' operation during BLE connect phase."""
        monkeypatch.setattr(mu, "shutting_down", False, raising=False)
        monkeypatch.setattr(mu, "reconnecting", False, raising=False)
        monkeypatch.setattr(mu, "meshtastic_client", None, raising=False)
        monkeypatch.setattr(
            mu,
            "config",
            {
                "meshtastic": {
                    CONFIG_KEY_CONNECTION_TYPE: CONNECTION_TYPE_BLE,
                    CONFIG_KEY_BLE_ADDRESS: TEST_BLE_MAC,
                }
            },
            raising=False,
        )

        ensure_operations: list[str] = []

        def mock_ensure(address, *, operation):
            ensure_operations.append(operation)
            return None

        mock_interface = Mock()
        mock_interface.auto_reconnect = True
        mock_interface.connect = Mock()
        mock_interface.getMyNodeInfo = Mock(return_value={"user": {"id": "!abc123"}})
        mock_bleak_client = Mock()
        mock_bleak_client.address = TEST_BLE_MAC
        mock_client = Mock()
        mock_client.bleak_client = mock_bleak_client
        mock_interface.client = mock_client

        with patch(
            "mmrelay.meshtastic_utils._ensure_ble_worker_available",
            side_effect=mock_ensure,
        ):
            with patch("mmrelay.meshtastic_utils._get_ble_executor") as mock_get_exec:
                mock_executor = Mock(spec=ThreadPoolExecutor)
                mock_get_exec.return_value = mock_executor

                call_count = [0]

                def create_mock_future(*_args, **_kwargs):
                    mock_future = Mock(spec=Future)
                    mock_future.done.return_value = True
                    mock_future.result.return_value = mock_interface
                    call_count[0] += 1
                    return mock_future

                mock_executor.submit.side_effect = create_mock_future

                with patch(
                    "mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 30.0
                ):
                    with patch("mmrelay.meshtastic_utils.BLE_AVAILABLE", True):
                        with patch(
                            "mmrelay.meshtastic_utils._ble_future_stale_grace_secs",
                            1000.0,
                        ):
                            with patch(
                                "mmrelay.meshtastic_utils._ble_timeout_reset_threshold",
                                3,
                            ):
                                with patch(
                                    "mmrelay.meshtastic_utils._ble_future_watchdog_secs",
                                    60.0,
                                ):
                                    with patch("mmrelay.meshtastic_utils.meshtastic"):
                                        with patch(
                                            "mmrelay.meshtastic_utils._sanitize_ble_address",
                                            return_value=TEST_BLE_MAC,
                                        ):
                                            mu.connect_meshtastic()

                                            assert (
                                                "interface creation"
                                                in ensure_operations
                                            )
                                            assert "connect" in ensure_operations

    def test_ble_connect_busy_worker_raises_timeout(self, monkeypatch):
        """Test when BLE worker is busy during connect phase, raises TimeoutError."""
        mock_future = Mock(spec=Future)
        mock_future.done.return_value = False
        monkeypatch.setattr(mu, "_ble_future", mock_future, raising=False)
        monkeypatch.setattr(
            mu, "_ble_future_started_at", time.monotonic(), raising=False
        )
        monkeypatch.setattr(mu, "_ble_future_timeout_secs", 30.0, raising=False)

        with patch("mmrelay.meshtastic_utils._ble_future_stale_grace_secs", 1000.0):
            with patch("mmrelay.meshtastic_utils.logger"):
                with pytest.raises(TimeoutError, match="already in progress"):
                    mu._ensure_ble_worker_available(TEST_BLE_MAC, operation="connect")


class TestBleInterfaceImport:
    """Test meshtastic.ble_interface import paths."""

    def test_import_failure_sets_none(self):
        """Test that ImportError during ble_interface import sets module to None."""
        import importlib
        import sys

        import mmrelay.meshtastic_utils as mu_module

        original_ble_interface = sys.modules.get("meshtastic.ble_interface")
        saved = {
            attr: getattr(mu_module, attr, None)
            for attr in [
                "config",
                "logger",
                "meshtastic_client",
                "meshtastic_iface",
                "event_loop",
                "reconnecting",
                "shutting_down",
            ]
        }

        try:
            real_import_module = importlib.import_module

            def _raising_import(name, package=None):
                if name == "meshtastic.ble_interface":
                    raise ImportError("no ble interface")
                return real_import_module(name, package)

            with patch.object(importlib, "import_module", side_effect=_raising_import):
                importlib.reload(mu_module)

            assert mu_module._ble_interface_module is None
            assert mu_module.MeshtasticBLEError is None
        finally:
            if original_ble_interface is not None:
                sys.modules["meshtastic.ble_interface"] = original_ble_interface
            importlib.reload(mu_module)
            for attr, value in saved.items():
                setattr(mu_module, attr, value)

    def test_gating_module_import_exception_sets_none(self):
        """Test that a non-ModuleNotFoundError during gating import sets module to None."""
        import importlib

        import mmrelay.meshtastic_utils as mu_module
        from mmrelay.constants.network import (
            MESHTASTIC_BLE_GATING_MODULE_PATH,
        )

        saved = {
            attr: getattr(mu_module, attr, None)
            for attr in [
                "config",
                "logger",
                "meshtastic_client",
                "meshtastic_iface",
                "event_loop",
                "reconnecting",
                "shutting_down",
            ]
        }

        try:
            real_import_module = importlib.import_module

            def _raising_import(name, package=None):
                if name == MESHTASTIC_BLE_GATING_MODULE_PATH:
                    raise RuntimeError("gating boom")
                return real_import_module(name, package)

            with patch.object(importlib, "import_module", side_effect=_raising_import):
                importlib.reload(mu_module)

            assert mu_module._ble_gating_module is None
        finally:
            importlib.reload(mu_module)
            for attr, value in saved.items():
                setattr(mu_module, attr, value)

    def test_gating_module_import_success_sets_callable(self):
        """Test successful gating import sets _ble_gate_reset_callable."""
        import importlib
        import types

        import mmrelay.meshtastic_utils as mu_module
        from mmrelay.constants.network import (
            MESHTASTIC_BLE_GATE_RESET_FUNC,
            MESHTASTIC_BLE_GATING_MODULE_PATH,
        )

        saved = {
            attr: getattr(mu_module, attr, None)
            for attr in [
                "config",
                "logger",
                "meshtastic_client",
                "meshtastic_iface",
                "event_loop",
                "reconnecting",
                "shutting_down",
            ]
        }

        fake_module = types.ModuleType(MESHTASTIC_BLE_GATING_MODULE_PATH)
        setattr(fake_module, MESHTASTIC_BLE_GATE_RESET_FUNC, lambda: None)

        try:
            real_import_module = importlib.import_module

            def _fake_import(name, package=None):
                if name == MESHTASTIC_BLE_GATING_MODULE_PATH:
                    return fake_module
                return real_import_module(name, package)

            with patch.object(importlib, "import_module", side_effect=_fake_import):
                importlib.reload(mu_module)

            assert mu_module._ble_gating_module is fake_module
            assert mu_module._ble_gate_reset_callable is not None
        finally:
            importlib.reload(mu_module)
            for attr, value in saved.items():
                setattr(mu_module, attr, value)

    def test_gating_module_import_success_non_callable(self):
        """Test successful gating import with non-callable reset func."""
        import importlib
        import types

        import mmrelay.meshtastic_utils as mu_module
        from mmrelay.constants.network import (
            MESHTASTIC_BLE_GATE_RESET_FUNC,
            MESHTASTIC_BLE_GATING_MODULE_PATH,
        )

        saved = {
            attr: getattr(mu_module, attr, None)
            for attr in [
                "config",
                "logger",
                "meshtastic_client",
                "meshtastic_iface",
                "event_loop",
                "reconnecting",
                "shutting_down",
            ]
        }

        fake_module = types.ModuleType(MESHTASTIC_BLE_GATING_MODULE_PATH)
        setattr(fake_module, MESHTASTIC_BLE_GATE_RESET_FUNC, "not_callable")

        try:
            real_import_module = importlib.import_module

            def _fake_import(name, package=None):
                if name == MESHTASTIC_BLE_GATING_MODULE_PATH:
                    return fake_module
                return real_import_module(name, package)

            with patch.object(importlib, "import_module", side_effect=_fake_import):
                importlib.reload(mu_module)

            assert mu_module._ble_gating_module is fake_module
            assert mu_module._ble_gate_reset_callable is None
        finally:
            importlib.reload(mu_module)
            for attr, value in saved.items():
                setattr(mu_module, attr, value)


# ---------------------------------------------------------------------------
# Tests absorbed from test_meshtastic_utils_edge_cases.py (BLE domain)
# ---------------------------------------------------------------------------


class TestConnectMeshtasticBLEDeviceNotFound(unittest.TestCase):
    """Test BLE connection failure when device is not found."""

    def test_connect_meshtastic_ble_device_not_found(self):
        """Returns None and logs error when BLE device is unavailable."""
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": "00:11:22:33:44:55",
            }
        }

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            side_effect=ConnectionRefusedError("Device not found"),
        ):
            with patch("time.sleep"):
                with (
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


class TestBLEDuplicateConnectSuppressionDetector(unittest.TestCase):
    """Test cases for BLE duplicate connect suppression error detection."""

    def test_detects_full_suppression_message(self):
        """Should detect the complete fork error message."""
        exc = RuntimeError("Connection suppressed: recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_detects_partial_suppression_message(self):
        """Should detect partial message with just 'recently connected elsewhere'."""
        exc = RuntimeError("recently connected elsewhere")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_detects_both_keywords_together(self):
        """Should detect when both keywords appear separately."""
        exc = RuntimeError("connection suppressed due to connected elsewhere issue")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_rejects_other_ble_errors(self):
        """Should not match unrelated BLE errors."""
        exc = RuntimeError("BLE connection timeout")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_rejects_empty_exception(self):
        """Should handle empty exception messages."""
        exc = RuntimeError("")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_rejects_only_connection_suppressed(self):
        """Should not match when only 'connection suppressed' appears without 'connected elsewhere'."""
        exc = RuntimeError("Connection suppressed by gate")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is False

    def test_handles_case_insensitivity(self):
        """Should be case insensitive."""
        exc = RuntimeError("CONNECTION SUPPRESSED: RECENTLY CONNECTED ELSEWHERE")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True

    def test_handles_whitespace(self):
        """Should handle messages with extra whitespace."""
        exc = RuntimeError("  Connection suppressed: recently connected elsewhere  ")
        assert _is_ble_duplicate_connect_suppressed_error(exc) is True


class TestBLEGateResetCallable:
    """Test cases for BLE gate reset callable behavior."""

    def test_reset_returns_false_when_no_callable(self, monkeypatch):
        """Should return False when _ble_gate_reset_callable is None."""
        monkeypatch.setattr(mu, "_ble_gate_reset_callable", None, raising=False)
        result = _reset_ble_connection_gate_state("AA:BB:CC:DD:EE:FF", reason="test")
        assert result is False

    def test_reset_handles_callable_exception(self, monkeypatch):
        """Should handle exceptions from _ble_gate_reset_callable gracefully."""

        def _raising_callable():
            raise RuntimeError("Gate reset failed")

        monkeypatch.setattr(
            mu, "_ble_gate_reset_callable", _raising_callable, raising=False
        )
        result = _reset_ble_connection_gate_state(
            "AA:BB:CC:DD:EE:FF", reason="test exception handling"
        )
        assert result is False

    def test_reset_returns_true_on_success(self, monkeypatch):
        """Should return True when callable succeeds."""
        call_count = [0]

        def _successful_callable():
            call_count[0] += 1

        monkeypatch.setattr(
            mu, "_ble_gate_reset_callable", _successful_callable, raising=False
        )
        result = _reset_ble_connection_gate_state(
            "AA:BB:CC:DD:EE:FF", reason="test success"
        )
        assert result is True
        assert call_count[0] == 1


class TestBLEGateImportDetection(unittest.TestCase):
    """Test cases for module-level BLE gate import detection."""

    def test_module_imports_gracefully_when_gating_unavailable(self):
        """Should have _ble_gate_reset_callable as None when gating module missing."""
        assert hasattr(mu, "_ble_gate_reset_callable")
        assert hasattr(mu, "_ble_gating_module")
        assert mu._ble_gate_reset_callable is None or callable(
            mu._ble_gate_reset_callable
        )

    def test_helper_safe_when_no_gating_module(self):
        """Should be safe to call _reset_ble_connection_gate_state even without gating."""
        original_callable = mu._ble_gate_reset_callable

        try:
            mu._ble_gate_reset_callable = None
            result = _reset_ble_connection_gate_state(
                "AA:BB:CC:DD:EE:FF", reason="no gating module"
            )
            assert result is False
        finally:
            mu._ble_gate_reset_callable = original_callable


class TestDuplicateSuppressionRetryLogic(unittest.TestCase):
    """Test cases for duplicate suppression retry logic in connect_meshtastic."""

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    @patch("mmrelay.meshtastic_utils._ble_gate_reset_callable")
    def test_logs_warning_on_duplicate_suppression(
        self, mock_gate_callable, mock_sleep, mock_logger
    ):
        """Should log warning when duplicate suppression detected."""
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        with patch(
            "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
            new=_SuppressedBLEInterface,
        ):
            result = connect_meshtastic(passed_config=config)

        assert result is None

        suppression_calls = [
            call
            for call in mock_logger.warning.call_args_list
            if call.args
            and "Detected duplicate BLE connect suppression" in str(call.args[0])
        ]
        assert len(suppression_calls) > 0, "Expected suppression detection warning"

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    def test_logs_debug_when_gate_reset_unavailable(self, mock_sleep, mock_logger):
        """Should log debug when gate reset hook is unavailable."""
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        original_callable = mu._ble_gate_reset_callable
        try:
            mu._ble_gate_reset_callable = None

            with patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_SuppressedBLEInterface,
            ):
                result = connect_meshtastic(passed_config=config)

            assert result is None

            suppression_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if call.args
                and "Detected duplicate BLE connect suppression" in str(call.args[0])
            ]
            assert len(suppression_calls) > 0, "Expected suppression detection warning"

            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if call.args and "BLE gate reset hook unavailable" in str(call.args[0])
            ]
            assert len(debug_calls) > 0, "Expected debug about unavailable hook"
        finally:
            mu._ble_gate_reset_callable = original_callable

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    def test_no_debug_log_when_gate_reset_succeeds(self, mock_sleep, mock_logger):
        """Should not log debug when gate reset succeeds."""
        ble_address = "AA:BB:CC:DD:EE:FF"
        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": ble_address,
                "retries": 1,
            }
        }

        original_callable = mu._ble_gate_reset_callable

        def _successful_reset():
            pass

        try:
            mu._ble_gate_reset_callable = _successful_reset

            with patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                new=_SuppressedBLEInterface,
            ):
                result = connect_meshtastic(passed_config=config)

            assert result is None

            suppression_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if call.args
                and "Detected duplicate BLE connect suppression" in str(call.args[0])
            ]
            assert len(suppression_calls) > 0, "Expected suppression detection warning"

            debug_calls = [
                call
                for call in mock_logger.debug.call_args_list
                if call.args and "BLE gate reset hook unavailable" in str(call.args[0])
            ]
            assert len(debug_calls) == 0, "Should not log debug when reset succeeds"
        finally:
            mu._ble_gate_reset_callable = original_callable
