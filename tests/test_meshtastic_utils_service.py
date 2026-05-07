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
import os
import threading
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, mock_open, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import (
    _coerce_bool,
    _coerce_int_id,
    _coerce_positive_float,
    is_running_as_service,
    serial_port_exists,
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


class TestServiceDetection(unittest.TestCase):
    """Test cases for service detection functionality."""

    @patch.dict(os.environ, {"INVOCATION_ID": "test-service-id"})
    def test_is_running_as_service_with_invocation_id(self):
        """Test service detection when INVOCATION_ID environment variable is set."""
        result = is_running_as_service()
        self.assertTrue(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_with_systemd_parent(self):
        """
        Tests that `is_running_as_service` returns True when the parent process is `systemd` by mocking the relevant proc files.
        """
        status_data = "PPid:\t1\n"
        comm_data = "systemd"

        def mock_open_func(filename, *args, **kwargs):
            """
            Mock file open function for simulating reads from specific `/proc` files during testing.

            Returns a mock file object with predefined content for `/proc/self/status` and `/proc/[pid]/comm`. Raises `FileNotFoundError` for any other file paths.

            Parameters:
                filename (str): The path of the file to open.

            Returns:
                file object: A mock file object with the specified content.

            Raises:
                FileNotFoundError: If the filename does not match the supported `/proc` paths.
            """
            if filename == "/proc/self/status":
                return mock_open(read_data=status_data)()
            elif filename.startswith("/proc/") and filename.endswith("/comm"):
                return mock_open(read_data=comm_data)()
            else:
                raise FileNotFoundError()

        with patch("builtins.open", side_effect=mock_open_func):
            result = is_running_as_service()
            self.assertTrue(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_normal_process(self):
        """
        Tests that is_running_as_service returns False for a normal process with a non-systemd parent.
        """
        status_data = "PPid:\t1234\n"
        comm_data = "bash"

        def mock_open_func(filename, *args, **kwargs):
            """
            Mock file open function for simulating reads from specific `/proc` files during testing.

            Returns a mock file object with predefined content for `/proc/self/status` and `/proc/[pid]/comm`. Raises `FileNotFoundError` for any other file paths.

            Parameters:
                filename (str): The path of the file to open.

            Returns:
                file object: A mock file object with the specified content.

            Raises:
                FileNotFoundError: If the filename does not match the supported `/proc` paths.
            """
            if filename == "/proc/self/status":
                return mock_open(read_data=status_data)()
            elif filename.startswith("/proc/") and filename.endswith("/comm"):
                return mock_open(read_data=comm_data)()
            else:
                raise FileNotFoundError()

        with patch("builtins.open", side_effect=mock_open_func):
            result = is_running_as_service()
            self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("builtins.open", side_effect=FileNotFoundError())
    def test_is_running_as_service_file_not_found(self, mock_open_func):
        """
        Test that service detection returns False when required process files cannot be read.
        """
        result = is_running_as_service()
        self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("builtins.open", side_effect=PermissionError("Permission denied"))
    def test_is_running_as_service_permission_error(self, mock_open_func):
        """Test that service detection handles PermissionError gracefully."""
        result = is_running_as_service()
        self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    def test_is_running_as_service_value_error(self):
        """Test that service detection handles ValueError gracefully when parsing invalid data."""
        with patch("builtins.open", mock_open(read_data="invalid data format\n")):
            result = is_running_as_service()
            self.assertFalse(result)


class TestSerialPortDetection(unittest.TestCase):
    """Test cases for serial port detection functionality."""

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_found(self, mock_comports):
        """
        Test that serial_port_exists returns True when the specified serial port is present among available system ports.
        """
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB0"
        mock_comports.return_value = [mock_port]

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertTrue(result)

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_not_found(self, mock_comports):
        """
        Tests that serial_port_exists returns False when the specified serial port is not found among available ports.
        """
        mock_port = MagicMock()
        mock_port.device = "/dev/ttyUSB1"
        mock_comports.return_value = [mock_port]

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertFalse(result)

    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_no_ports(self, mock_comports):
        """
        Test that serial port detection returns False when no serial ports are available.
        """
        mock_comports.return_value = []

        result = serial_port_exists("/dev/ttyUSB0")
        self.assertFalse(result)

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.serial.tools.list_ports.comports")
    def test_serial_port_exists_permission_error(self, mock_comports, mock_logger):
        """
        Test that serial_port_exists catches PermissionError when enumerating ports,
        logs a warning mentioning the port name, and returns False.
        """
        mock_comports.side_effect = PermissionError("Permission denied")

        result = serial_port_exists("/dev/ttyUSB0")

        self.assertFalse(result)
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args
        self.assertIn("/dev/ttyUSB0", str(warning_args))
        self.assertIn("PermissionError", str(warning_args))


class TestCoercionFunctions:
    """Test coercion utility functions."""

    def test_coerce_int_id_with_valid_int(self):
        """Test _coerce_int_id with valid integer."""
        assert _coerce_int_id(123) == 123

    def test_coerce_int_id_with_string(self):
        """Test _coerce_int_id with string number."""
        assert _coerce_int_id("456") == 456

    def test_coerce_int_id_with_invalid_string(self):
        """Test _coerce_int_id with non-numeric string."""
        assert _coerce_int_id("not-a-number") is None

    def test_coerce_int_id_with_none(self):
        """Test _coerce_int_id with None."""
        assert _coerce_int_id(None) is None

    def test_coerce_positive_float_with_valid(self):
        """Test _coerce_positive_float with valid positive float."""

        assert _coerce_positive_float(5.5, 1.0, "test") == 5.5

    def test_coerce_positive_float_with_zero(self):
        """Test _coerce_positive_float with zero (invalid)."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _coerce_positive_float(0, 2.0, "test_setting")
            assert result == 2.0
            mock_logger.warning.assert_called_once()

    def test_coerce_positive_float_with_negative(self):
        """Test _coerce_positive_float with negative (invalid)."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _coerce_positive_float(-5.0, 3.0, "test_setting")
            assert result == 3.0
            mock_logger.warning.assert_called_once()

    def test_coerce_positive_float_with_invalid_type(self):
        """Test _coerce_positive_float with invalid type."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _coerce_positive_float("not-a-number", 4.0, "test_setting")
            assert result == 4.0
            mock_logger.warning.assert_called_once()


class TestCoerceBoolEdgeCases:
    """Test _coerce_bool edge cases."""

    def test_coerce_bool_with_true_bool(self):
        """Test _coerce_bool with True boolean."""
        result = _coerce_bool(True, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_false_bool(self):
        """Test _coerce_bool with False boolean."""
        result = _coerce_bool(False, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_positive_int(self):
        """Test _coerce_bool with positive integer."""
        result = _coerce_bool(1, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_zero_int(self):
        """Test _coerce_bool with zero integer."""
        result = _coerce_bool(0, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_positive_float(self):
        """Test _coerce_bool with positive float."""
        result = _coerce_bool(1.5, False, "test_setting")
        assert result is True

    def test_coerce_bool_with_zero_float(self):
        """Test _coerce_bool with zero float."""
        result = _coerce_bool(0.0, True, "test_setting")
        assert result is False

    def test_coerce_bool_with_whitespace_string(self):
        """Test _coerce_bool with whitespace-only string coerces to False (independent of default)."""
        result = _coerce_bool("   ", True, "test_setting")
        assert result is False

    def test_coerce_bool_with_empty_string(self):
        """Test _coerce_bool with empty string returns False."""
        result = _coerce_bool("", True, "test_setting")
        assert result is False

    def test_coerce_bool_with_invalid_type(self):
        """Test _coerce_bool with invalid type logs warning and returns default."""
        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
            result = _coerce_bool(["list"], True, "test_setting")
            assert result is True
            mock_logger.warning.assert_called_once()


class TestSnapshotNodeNameRowsNonDict:
    """Test _snapshot_node_name_rows handling non-dict raw_node and raw_user."""

    def test_snapshot_node_name_rows_non_dict_raw_node(self):
        """Test when raw_node is not a dict, nodes_snapshot gets {"user": None}."""
        mock_client = Mock()
        mock_client.nodes = {
            "12345": "not_a_dict",
            "67890": None,
        }

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            result, client_missing = mu._snapshot_node_name_rows()

            assert client_missing is False
            assert result is not None
            assert result["12345"] == {"user": None}
            assert result["67890"] == {"user": None}

    def test_snapshot_node_name_rows_non_dict_raw_user(self):
        """Test when raw_user is not a dict, nodes_snapshot gets {"user": {"id": None}}."""
        mock_client = Mock()
        mock_client.nodes = {
            "12345": {"user": "user_string_not_dict"},
            "67890": {"user": None},
        }

        with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
            result, client_missing = mu._snapshot_node_name_rows()

            assert client_missing is False
            assert result is not None
            assert result["12345"] == {"user": {"id": None}}
            assert result["67890"] == {"user": {"id": None}}


class TestRefreshNodeNameTablesInvalidInterval:
    """Test refresh_node_name_tables invalid interval handling."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_interval,expected_fallback",
        [(True, 60.0), (float("nan"), 120.0), (float("inf"), 90.0)],
        ids=["boolean", "nan", "inf"],
    )
    async def test_refresh_node_name_tables_invalid_interval(
        self, invalid_interval, expected_fallback, monkeypatch
    ):
        """Test with invalid interval defaults to configured interval."""
        monkeypatch.setattr(mu, "config", {"meshtastic": {}})

        with patch(
            "mmrelay.meshtastic.node_refresh.get_nodedb_refresh_interval_seconds",
            return_value=expected_fallback,
        ):
            with patch(
                "mmrelay.meshtastic.node_refresh.asyncio.to_thread",
                new_callable=AsyncMock,
                return_value=(None, True),
            ):
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    shutdown_event = asyncio.Event()

                    async def run_with_shutdown():
                        await asyncio.sleep(0.05)
                        shutdown_event.set()

                    shutdown_task = asyncio.create_task(run_with_shutdown())
                    try:
                        await mu.refresh_node_name_tables(
                            shutdown_event,
                            refresh_interval_seconds=invalid_interval,
                        )
                    finally:
                        shutdown_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await shutdown_task

                    warning_calls = [
                        str(call) for call in mock_logger.warning.call_args_list
                    ]
                    assert any(
                        "Invalid NodeDB name-cache refresh interval override" in call
                        for call in warning_calls
                    )
                    assert any(str(expected_fallback) in call for call in warning_calls)


# ---------------------------------------------------------------------------
# Tests absorbed from test_meshtastic_utils_edge_cases.py (service domain)
# ---------------------------------------------------------------------------


class TestIsRunningAsServiceEdgeCases(unittest.TestCase):
    """Edge case tests for is_running_as_service."""

    def test_is_running_as_service_detection_failure(self):
        """is_running_as_service returns bool when process detection fails."""
        with patch("os.getppid", side_effect=OSError("Cannot get parent PID")):
            with patch(
                "psutil.Process", side_effect=Exception("Process info unavailable")
            ):
                result = is_running_as_service()
                self.assertIsInstance(result, bool)
