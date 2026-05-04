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
