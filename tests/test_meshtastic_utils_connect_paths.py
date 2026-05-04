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
class TestUncoveredMeshtasticUtilsPaths(unittest.TestCase):
    """Test cases for uncovered code paths in meshtastic_utils.py."""

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_timeout(self, mock_logger):
        """Test _get_device_metadata when getMetadata() times out."""
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic_utils import _get_device_metadata

        mock_client = Mock()
        mock_client.localNode.getMetadata = Mock()

        import threading

        import mmrelay.meshtastic_utils as mu

        mu._metadata_future = None
        mock_output = Mock()
        mock_output.getvalue.return_value = "firmware_version: 2.3.15"

        timeout_future = Mock()
        timeout_future.done.return_value = False
        timeout_future.add_done_callback = Mock()

        import sys

        orig_stdout = sys.stdout

        redirect_active = threading.Event()
        redirect_active.set()

        def _raise_timeout(*_args, **_kwargs):
            # Simulate a worker redirecting stdout before the timeout hits.
            """
            Simulate a worker that redirects stdout and then raises a FuturesTimeoutError to simulate a timeout
            condition.

            Raises:
                FuturesTimeoutError: Indicates a simulated timeout.
            """
            mu.sys.stdout = mock_output
            redirect_active.clear()
            raise FuturesTimeoutError()

        timeout_future.result.side_effect = _raise_timeout

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.return_value = timeout_future

        with (
            patch("mmrelay.meshtastic_utils.io.StringIO", return_value=mock_output),
            patch(
                "mmrelay.meshtastic_utils.threading.Event", return_value=_FakeEvent()
            ),
            patch("mmrelay.meshtastic_utils._metadata_executor", mock_executor),
            patch("mmrelay.meshtastic_utils.sys.stdout", orig_stdout),
        ):
            result = _get_device_metadata(mock_client)

            # Should still return result with firmware version parsed
            self.assertTrue(result["success"])
            self.assertEqual(result["firmware_version"], "2.3.15")
            # Verify timeout was logged
            mock_logger.debug.assert_called_with(
                f"getMetadata() timed out after {METADATA_WATCHDOG_SECS} seconds"
            )
            # Ensure we deferred cleanup when worker is still running.
            # Verify callbacks were registered for deferred cleanup (at least one).
            self.assertGreaterEqual(
                timeout_future.add_done_callback.call_count,
                1,
                "Expected at least one cleanup callback to be registered",
            )
            # Verify the observable effect: stdio is restored immediately
            # after timeout, not left pointing at the capture buffer.
            self.assertIs(mu.sys.stdout, orig_stdout)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_get_device_metadata_timeout_restores_stdio(self, _mock_logger):
        """Test _get_device_metadata restores stdio when timeout happens mid-redirect."""
        import threading
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic_utils import _get_device_metadata

        mock_client = Mock()
        mock_client.localNode.getMetadata = Mock()

        import mmrelay.meshtastic_utils as mu

        mu._metadata_future = None
        output_capture = Mock()
        output_capture.getvalue.return_value = "firmware_version: 2.3.15"
        output_capture.closed = False

        timeout_future = Mock()
        timeout_future.result.side_effect = FuturesTimeoutError()
        timeout_future.done.return_value = True

        redirect_active = threading.Event()
        redirect_active.set()

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.return_value = timeout_future

        with (
            patch("mmrelay.meshtastic_utils.io.StringIO", return_value=output_capture),
            patch("threading.Event", return_value=redirect_active),
            patch("mmrelay.meshtastic_utils._metadata_executor", mock_executor),
            patch("mmrelay.meshtastic_utils.sys.stdout", output_capture),
        ):
            result = _get_device_metadata(mock_client)

        # The timeout path should still parse output and close capture safely.
        self.assertTrue(result["success"])
        output_capture.close.assert_called_once()

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_is_connected_bool_true(
        self, mock_bleak, _mock_logger
    ):
        """Test _disconnect_ble_by_address when is_connected is a bool (True)."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        def _noop(*_args: object, **_kwargs: object) -> None:
            """
            Synchronous no-op callable that accepts any positional and keyword arguments and does nothing.

            Used as a replacement/mock for synchronous disconnect functions in tests; ignores all inputs and returns None.
            """

        mock_client = Mock()
        mock_client.is_connected = True  # bool, not callable
        mock_client.disconnect = Mock(side_effect=_noop)
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        # Should attempt to disconnect since is_connected is True
        mock_client.disconnect.assert_called()

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_is_connected_bool_false(
        self, mock_bleak, _mock_logger
    ):
        """
        Verify _disconnect_ble_by_address invokes the client's disconnect as a best-effort cleanup when the client's is_connected attribute indicates the device is not connected.

        Asserts that disconnect is called from the cleanup path even if the client reports it is not connected (and is_connected is not callable).
        """
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        def _noop(*_args: object, **_kwargs: object) -> None:
            """
            Synchronous no-op callable that accepts any positional and keyword arguments and does nothing.

            Used as a replacement/mock for synchronous disconnect functions in tests; ignores all inputs and returns None.
            """

        mock_client = Mock()
        mock_client.is_connected = False  # bool, not callable
        mock_client.disconnect = Mock(side_effect=_noop)
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        # The finally block always does a best-effort cleanup disconnect
        # even when is_connected is False
        mock_client.disconnect.assert_called()

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_unknown_is_connected_defaults_false(
        self, mock_bleak, mock_sleep, mock_get_running_loop
    ):
        """Test _disconnect_ble_by_address treats unknown is_connected as False."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        def _noop(*_args: object, **_kwargs: object) -> None:
            """
            No-operation callable that accepts and ignores all positional and keyword arguments.

            Used as a generic placeholder callback or side-effect (for example, in mocks, timers, or disconnect helpers).
            """

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_sleep.side_effect = _noop

        mock_client = Mock()
        mock_client.is_connected = object()
        mock_client.disconnect = Mock(side_effect=_noop)
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_sleep.assert_any_call(BLE_DISCONNECT_SETTLE_SECS)
        mock_client.disconnect.assert_called()

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_disconnect_success_calls_sleep(
        self, mock_bleak, mock_sleep, mock_get_running_loop
    ):
        """Test _disconnect_ble_by_address sleeps after a successful disconnect."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        def _noop(*_args: object, **_kwargs: object) -> None:
            """
            No-operation callable that accepts and ignores all positional and keyword arguments.

            Used as a generic placeholder callback or side-effect (for example, in mocks, timers, or disconnect helpers).
            """

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_sleep.side_effect = _noop

        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.disconnect = Mock(side_effect=_noop)
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_sleep.assert_any_call(BLE_DISCONNECT_SETTLE_SECS)

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.wait_for")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep")
    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_disconnect_timeout_logs_warning(
        self,
        mock_bleak,
        mock_logger,
        mock_sleep,
        mock_wait_for,
        mock_get_running_loop,
    ):
        """Test _disconnect_ble_by_address warns after repeated disconnect timeouts."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        class _ImmediateAwaitable:
            def __await__(self) -> Generator[Any, None, None]:
                """
                Expose this object as awaitable by providing its awaiting generator.

                Returns:
                    Generator: A generator used by the `await` expression; it yields control to the event loop and produces no value.
                """
                if False:  # pragma: no cover
                    yield

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_sleep.return_value = None

        def _timeout_wait_for(awaitable: object, **_kwargs: object) -> NoReturn:
            """
            Close the given awaitable if it is a coroutine, then raise an asyncio.TimeoutError.

            Parameters:
                awaitable (object): The awaitable or coroutine to be closed; if it is a coroutine, it will be closed to free resources.

            Raises:
                asyncio.TimeoutError: Always raised by this function.
            """
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = _timeout_wait_for

        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.disconnect = Mock(return_value=_ImmediateAwaitable())
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_logger.warning.assert_any_call(
            "Disconnect for AA:BB:CC:DD:EE:FF timed out after 3 attempts"
        )

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.wait_for")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep")
    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_disconnect_exception_logs_debug(
        self,
        mock_bleak,
        mock_logger,
        mock_sleep,
        mock_wait_for,
        mock_get_running_loop,
    ):
        """Test _disconnect_ble_by_address handles unexpected disconnect errors."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        class _ImmediateAwaitable:
            def __await__(self) -> Generator[Any, None, None]:
                """
                Expose this object as awaitable by providing its awaiting generator.

                Returns:
                    Generator: A generator used by the `await` expression; it yields control to the event loop and produces no value.
                """
                if False:  # pragma: no cover
                    yield

        def _timeout_wait_for(awaitable: object, **_kwargs: object) -> NoReturn:
            """
            Close the given awaitable if it is a coroutine, then raise an asyncio.TimeoutError.

            Parameters:
                awaitable (object): The awaitable or coroutine to be closed; if it is a coroutine, it will be closed to free resources.

            Raises:
                asyncio.TimeoutError: Always raised by this function.
            """
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError()

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_sleep.return_value = None
        mock_wait_for.side_effect = _timeout_wait_for
        # Force an exception outside the inner retry loop to cover the
        # best-effort cleanup exception path.
        mock_logger.warning.side_effect = ValueError("forced warning failure")

        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.disconnect = Mock(return_value=_ImmediateAwaitable())
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        found = False
        for call in mock_logger.debug.call_args_list:
            args = call.args
            if not args:
                continue
            if "Error disconnecting stale connection" in str(args[0]) and any(
                arg == "AA:BB:CC:DD:EE:FF" for arg in args
            ):
                found = True
                break
        self.assertTrue(found)

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.wait_for")
    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_cleanup_timeout_logs(
        self, mock_bleak, mock_logger, mock_wait_for, mock_get_running_loop
    ):
        """Test _disconnect_ble_by_address logs when cleanup disconnect times out."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        class _ImmediateAwaitable:
            def __await__(self) -> Generator[Any, None, None]:
                """
                Expose this object as awaitable by providing its awaiting generator.

                Returns:
                    Generator: A generator used by the `await` expression; it yields control to the event loop and produces no value.
                """
                if False:  # pragma: no cover
                    yield

        mock_get_running_loop.side_effect = RuntimeError("no loop")

        def _timeout_wait_for(awaitable: object, **_kwargs: object) -> NoReturn:
            """
            Close the given awaitable if it is a coroutine, then raise an asyncio.TimeoutError.

            Parameters:
                awaitable (object): The awaitable or coroutine to be closed; if it is a coroutine, it will be closed to free resources.

            Raises:
                asyncio.TimeoutError: Always raised by this function.
            """
            if inspect.iscoroutine(awaitable):
                awaitable.close()
            raise asyncio.TimeoutError()

        mock_wait_for.side_effect = _timeout_wait_for

        mock_client = Mock()
        mock_client.is_connected = False
        mock_client.disconnect = Mock(return_value=_ImmediateAwaitable())
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_logger.debug.assert_any_call(
            "Final disconnect for AA:BB:CC:DD:EE:FF timed out (cleanup)"
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_disconnect_ble_by_address_bleak_missing(self, mock_logger):
        """Test _disconnect_ble_by_address handles missing Bleak cleanly."""
        import builtins

        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        real_import = builtins.__import__

        def _import_side_effect(name, *args, **kwargs):
            """
            Simulate an import hook that raises ImportError for the "bleak" module and otherwise performs a normal import.

            Parameters:
                name (str): The module name to import; when equal to "bleak" an ImportError is raised.
                *args: Positional arguments forwarded to the real import function.
                **kwargs: Keyword arguments forwarded to the real import function.

            Returns:
                module: The imported module object returned by the underlying import.

            Raises:
                ImportError: If `name` is exactly "bleak".
            """
            if name == "bleak":
                raise ImportError()
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_import_side_effect):
            _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_logger.debug.assert_called_with(
            "BleakClient not available for stale connection cleanup"
        )

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_timeout_handler(
        self,
        mock_bleak,
        mock_run_coroutine_threadsafe,
        mock_get_running_loop,
        mock_logger,
    ):
        """Test _disconnect_ble_by_address handles FuturesTimeoutError."""
        from concurrent.futures import TimeoutError as FuturesTimeoutError

        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        mock_client = Mock()
        mock_client.disconnect = Mock()
        mock_client.is_connected = False
        mock_bleak.return_value = mock_client

        # Simulate no running loop in this thread; fall back to global loop.
        mock_loop = Mock()
        mock_loop.is_running.return_value = True
        mock_get_running_loop.side_effect = RuntimeError("no loop")

        # Mock run_coroutine_threadsafe to return a mock future that times out
        mock_future = Mock()
        mock_future.cancel.return_value = True
        mock_future.done.return_value = False
        mock_future.result = Mock(side_effect=FuturesTimeoutError())

        def _submit(coro, _loop) -> Any:
            coro.close()
            return mock_future

        mock_run_coroutine_threadsafe.side_effect = _submit

        with patch("mmrelay.meshtastic_utils.event_loop", mock_loop):
            _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        # Verify warning was logged for timeout
        mock_logger.warning.assert_called_with(
            f"Stale connection disconnect timed out after {STALE_DISCONNECT_TIMEOUT_SECS:.0f}s for AA:BB:CC:DD:EE:FF"
        )
        # Verify future.cancel() was called
        mock_future.cancel.assert_called_once()

    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.sleep")
    @patch("mmrelay.meshtastic_utils.logger")
    @patch("bleak.BleakClient")
    def test_disconnect_ble_by_address_disconnect_errors_log_final_retry_warning(
        self, mock_bleak, mock_logger, mock_sleep, mock_get_running_loop
    ):
        """Repeated BLE disconnect errors should log the final failure warning."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        def _noop(*_args: object, **_kwargs: object) -> None:
            """
            No-operation callable that accepts and ignores all positional and keyword arguments.

            Used as a generic placeholder callback or side-effect (for example, in mocks, timers, or disconnect helpers).
            """

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_sleep.side_effect = _noop

        mock_client = Mock()
        mock_client.is_connected = True
        mock_client.disconnect.side_effect = RuntimeError("disconnect failed")
        mock_bleak.return_value = mock_client

        _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_logger.warning.assert_any_call(
            "Disconnect for %s failed after %s attempts: %s",
            "AA:BB:CC:DD:EE:FF",
            3,
            ANY,
            exc_info=True,
        )

    @patch("bleak.BleakClient")
    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.asyncio.get_running_loop")
    @patch("mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe")
    def test_disconnect_ble_by_address_logs_completion_with_global_running_loop(
        self,
        mock_run_coroutine_threadsafe,
        mock_get_running_loop,
        mock_logger,
        mock_bleak_client,
    ):
        """Global event-loop cleanup success should log completion."""
        from mmrelay.meshtastic_utils import _disconnect_ble_by_address

        mock_get_running_loop.side_effect = RuntimeError("no loop")
        mock_bleak_client.return_value = Mock(is_connected=False)
        mock_future = Mock()
        mock_future.result.return_value = None

        def _submit(coro, _loop) -> Any:
            coro.close()
            return mock_future

        mock_run_coroutine_threadsafe.side_effect = _submit

        mock_loop = Mock()
        mock_loop.is_running.return_value = True
        with patch("mmrelay.meshtastic_utils.event_loop", mock_loop):
            _disconnect_ble_by_address("AA:BB:CC:DD:EE:FF")

        mock_logger.debug.assert_any_call(
            "Stale connection disconnect completed for AA:BB:CC:DD:EE:FF"
        )

    def test_disconnect_ble_interface_none_input(self):
        """Test _disconnect_ble_interface returns early when iface is None."""
        from mmrelay.meshtastic_utils import _disconnect_ble_interface

        # Should not raise any errors and return early
        _disconnect_ble_interface(None)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_disconnect_ble_interface_client_none(self, _mock_logger):
        """Test _disconnect_ble_interface handles client=None (forked lib race)."""
        from mmrelay.meshtastic_utils import _disconnect_ble_interface

        # Mock interface with client=None (simulates forked lib close race)
        mock_iface = Mock()
        mock_iface.client = None
        mock_iface.close = Mock()

        # Should not raise any errors when client is None
        _disconnect_ble_interface(mock_iface, reason="test")

        # Verify close was still called on the interface
        mock_iface.close.assert_called_once()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_disconnect_ble_interface_client_becomes_none_during_disconnect(
        self, mock_logger
    ):
        """Test _disconnect_ble_interface handles client becoming None during disconnect."""
        from mmrelay.meshtastic_utils import _disconnect_ble_interface

        class _SimulatedDisconnectError(RuntimeError):
            """Test-specific error for simulated disconnect failures."""

        mock_iface = Mock(spec=["close", "client"])
        mock_client = Mock()
        mock_client._exit_handler = None
        mock_iface.client = mock_client
        mock_iface.close = Mock()

        # Simulate client becoming None after first disconnect attempt
        def side_effect_make_none(*_args, **_kwargs):
            mock_iface.client = None
            raise _SimulatedDisconnectError()

        mock_client.disconnect.side_effect = side_effect_make_none

        # Should handle the None client gracefully without raising
        _disconnect_ble_interface(mock_iface, reason="test")

        # Verify debug log was called about client becoming None
        debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
        found_log = any("became None before attempt" in call for call in debug_calls)
        self.assertTrue(found_log, "Expected log about client becoming None")

        # Verify close was still called
        mock_iface.close.assert_called_once()

    @patch("mmrelay.meshtastic_utils.logger")
    @patch("mmrelay.meshtastic_utils.time.sleep")
    def test_connect_meshtastic_ble_interface_creation_timeout(
        self, _mock_sleep, mock_logger
    ):
        """Test connect_meshtastic handles BLEInterface creation timeout."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
            },
            "matrix_rooms": [],
        }

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = [
            _make_timeout_future() for _ in range(MAX_TIMEOUT_RETRIES_INFINITE + 1)
        ]

        with (
            patch("mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 45.0),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)
            self.assertIsNone(result)
            mock_bleak_client.assert_called_with(TEST_BLE_MAC)

            self.assertIsNone(mu.meshtastic_iface)

            error_calls = [
                call
                for call in mock_logger.error.call_args_list
                if "BLE interface creation timed out after" in str(call)
            ]
            self.assertEqual(len(error_calls), MAX_TIMEOUT_RETRIES_INFINITE + 1)
            try:
                supports_auto_reconnect = (
                    "auto_reconnect"
                    in inspect.signature(
                        mu.meshtastic.ble_interface.BLEInterface.__init__
                    ).parameters
                )
            except (TypeError, ValueError):
                supports_auto_reconnect = False
            expected_watchdog = (
                45.0 + BLE_CONNECT_TIMEOUT_SECS if supports_auto_reconnect else 45.0
            )
            assert all(call.args[1] == expected_watchdog for call in error_calls)

            last_error_call = str(error_calls[-1])
            self.assertIn(TEST_BLE_MAC, last_error_call)

            abort_calls = [
                call
                for call in mock_logger.exception.call_args_list
                if "Connection timed out after" in str(call)
                and "unlimited retries" in str(call)
            ]
            self.assertEqual(len(abort_calls), 1)

            warning_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if "Connection attempt" in str(call) and "timed out" in str(call)
            ]
            self.assertEqual(len(warning_calls), MAX_TIMEOUT_RETRIES_INFINITE)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_ble_creation_timeout_auto_reconnect_uses_connect_budget(
        self, mock_logger
    ):
        """Auto-reconnect constructor path should include BLE connect-timeout slack."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        class _BleInterfaceWithAutoReconnect:
            def __init__(
                self,
                address: str | None = None,
                *,
                noProto: bool = False,
                debugOut: object | None = None,
                noNodes: bool = False,
                timeout: int = 300,
                auto_reconnect: bool = False,
            ) -> None:
                self.address = address
                self._unused_params = (
                    noProto,
                    debugOut,
                    noNodes,
                    timeout,
                    auto_reconnect,
                )

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = [
            _make_timeout_future(),
            _make_timeout_future(),
        ]

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils._ble_interface_create_timeout_secs", 15.0),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                _BleInterfaceWithAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)
            self.assertIsNone(result)

        error_calls = [
            call
            for call in mock_logger.error.call_args_list
            if "BLE interface creation timed out after" in str(call)
        ]
        self.assertEqual(len(error_calls), 2)
        expected_watchdog = 15.0 + BLE_CONNECT_TIMEOUT_SECS
        self.assertTrue(all(call.args[1] == expected_watchdog for call in error_calls))

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_ble_signature_unavailable_uses_compatibility_mode(
        self, mock_logger
    ):
        """BLEInterface signature introspection failures should not abort creation path."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        def _make_keyerror_future() -> Mock:
            future = Mock()
            future.result = Mock(side_effect=KeyError("path"))
            future.cancel = Mock(return_value=False)
            return future

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = [
            _make_keyerror_future(),
            _make_keyerror_future(),
        ]

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch(
                "mmrelay.meshtastic_utils.inspect.signature",
                side_effect=ValueError("no signature metadata"),
            ),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)

        self.assertIsNone(result)
        self.assertEqual(mock_executor.submit.call_count, 2)
        self.assertTrue(
            any(
                "BLEInterface signature unavailable; using compatibility mode"
                in str(call)
                for call in mock_logger.debug.call_args_list
            )
        )

    def test_connect_meshtastic_ble_signature_unavailable_stays_compatibility_mode(
        self,
    ):
        """Signature fallback should not re-enable explicit connect() via hasattr checks."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        mock_iface = Mock()
        mock_iface.address = TEST_BLE_MAC
        mock_iface.auto_reconnect = False
        mock_iface.connect = Mock()
        mock_iface.client = Mock()
        mock_iface.client.bleak_client = Mock()
        mock_iface.client.bleak_client.address = TEST_BLE_MAC
        mock_iface.getMyNodeInfo.return_value = {"num": 123}

        create_future = Mock()
        create_future.result = Mock(return_value=mock_iface)
        create_future.cancel = Mock(return_value=True)

        submit_count = 0

        def submit_side_effect(
            _func: object, *_args: object, **_kwargs: object
        ) -> Mock:
            nonlocal submit_count
            submit_count += 1
            if submit_count == 1:
                return create_future
            msg = "connect() should not be scheduled in compatibility-mode fallback"
            raise AssertionError(msg)

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = submit_side_effect

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch(
                "mmrelay.meshtastic_utils.inspect.signature",
                side_effect=ValueError("no signature metadata"),
            ),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            original_client = mu.meshtastic_client
            original_iface = mu.meshtastic_iface
            mu._metadata_future = None
            try:
                result = connect_meshtastic(passed_config=config)
                self.assertIs(result, mock_iface)
            finally:
                mu.meshtastic_client = original_client
                mu.meshtastic_iface = original_iface
                _reset_ble_inflight_state(mu)

        self.assertEqual(submit_count, 1)
        mock_iface.connect.assert_not_called()

    @patch("mmrelay.meshtastic_utils.logger")
    def test_log_ble_shutdown_state_logs_inflight_worker(self, mock_logger):
        """Shutdown diagnostics should log in-flight BLE worker state when present."""
        import mmrelay.meshtastic_utils as mu

        pending_future = Mock()
        pending_future.done.return_value = False
        with mu._ble_executor_lock:
            original_ble_future = mu._ble_future
            original_ble_future_address = mu._ble_future_address
            original_ble_future_started_at = mu._ble_future_started_at
            original_ble_future_timeout_secs = mu._ble_future_timeout_secs
            mu._ble_future = pending_future
            mu._ble_future_address = TEST_BLE_MAC
            mu._ble_future_started_at = mu.time.monotonic() - 2.5
            mu._ble_future_timeout_secs = 20.0
        try:
            mu._log_ble_shutdown_state(context="shutdown")
        finally:
            with mu._ble_executor_lock:
                mu._ble_future = original_ble_future
                mu._ble_future_address = original_ble_future_address
                mu._ble_future_started_at = original_ble_future_started_at
                mu._ble_future_timeout_secs = original_ble_future_timeout_secs

        self.assertTrue(
            any(
                "in-flight BLE worker" in str(call)
                for call in mock_logger.debug.call_args_list
            )
        )

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_ble_creation_error_during_shutdown_logs_debug(
        self, mock_logger
    ):
        """Late BLE worker errors during shutdown should avoid exception-level logs."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        import mmrelay.meshtastic_utils as mu

        def _future_result(*_args: object, **_kwargs: object) -> object:
            mu.shutting_down = True
            raise KeyError("path")

        mock_future = Mock()
        mock_future.result = Mock(side_effect=_future_result)
        mock_future.cancel = Mock(return_value=False)

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.return_value = mock_future

        original_shutting_down = mu.shutting_down
        try:
            with (
                patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
                patch("bleak.BleakClient") as mock_bleak_client,
            ):
                mock_client_instance = Mock()
                mock_client_instance.is_connected = False
                mock_bleak_client.return_value = mock_client_instance
                _reset_ble_inflight_state(mu)
                mu._metadata_future = None
                result = connect_meshtastic(passed_config=config)
        finally:
            mu.shutting_down = original_shutting_down
            _reset_ble_inflight_state(mu)

        self.assertIsNone(result)
        self.assertTrue(
            any(
                "BLE interface creation ended during shutdown" in str(call)
                for call in mock_logger.debug.call_args_list
            )
        )
        self.assertFalse(
            any(
                "BLE interface creation failed" in str(call)
                for call in mock_logger.exception.call_args_list
            )
        )

    @patch("mmrelay.meshtastic_utils._disconnect_ble_interface")
    def test_connect_meshtastic_closes_existing_ble_interface(
        self, mock_disconnect_iface
    ):
        """Test connect_meshtastic closes existing BLE interfaces explicitly."""
        import mmrelay.meshtastic_utils as mu
        from mmrelay.meshtastic_utils import connect_meshtastic

        mock_iface = Mock()
        mu.meshtastic_client = mock_iface
        mu.meshtastic_iface = mock_iface

        config = {"meshtastic": {}, "matrix_rooms": []}

        result = connect_meshtastic(passed_config=config, force_connect=True)

        self.assertIsNone(result)
        mock_disconnect_iface.assert_called_once_with(mock_iface, reason="reconnect")
        self.assertIsNone(mu.meshtastic_iface)

    @patch("mmrelay.meshtastic_utils.logger")
    def test_connect_meshtastic_ble_connect_timeout(self, mock_logger):
        """Test connect_meshtastic handles BLE connect() timeout."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
            },
            "matrix_rooms": [],
        }

        mock_iface = Mock()
        mock_iface.getMyNodeInfo.return_value = {"num": 123}
        mock_iface.connect = Mock()
        mock_iface.auto_reconnect = False

        def _make_interface_future() -> Mock:
            future = Mock()
            future.result = Mock(return_value=mock_iface)
            future.cancel = Mock(return_value=True)
            return future

        future_sequence = iter(
            future
            for _ in range(MAX_TIMEOUT_RETRIES_INFINITE + 1)
            for future in (_make_interface_future(), _make_timeout_future())
        )

        def submit_side_effect(
            _func: object, *_args: object, **_kwargs: object
        ) -> Mock:
            return next(future_sequence)

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = submit_side_effect

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)
            self.assertIsNone(result)
            mock_bleak_client.assert_called_with(TEST_BLE_MAC)

            self.assertIsNone(mu.meshtastic_iface)

            connect_timeout_calls = [
                call
                for call in mock_logger.exception.call_args_list
                if call.args
                and call.args[0]
                == "BLE connect() call timed out after %s seconds for %s."
                and call.args[1] == BLE_CONNECT_TIMEOUT_SECS
                and call.args[2] == TEST_BLE_MAC
            ]
            self.assertEqual(
                len(connect_timeout_calls), MAX_TIMEOUT_RETRIES_INFINITE + 1
            )

            interface_timeout_calls = [
                call
                for call in mock_logger.error.call_args_list
                if "BLE interface creation timed out after" in str(call)
            ]
            self.assertEqual(len(interface_timeout_calls), 0)

            abort_calls = [
                call
                for call in mock_logger.exception.call_args_list
                if "Connection timed out after" in str(call)
                and "unlimited retries" in str(call)
            ]
            self.assertEqual(len(abort_calls), 1)

            warning_calls = [
                call
                for call in mock_logger.warning.call_args_list
                if "Connection attempt" in str(call) and "timed out" in str(call)
            ]
            self.assertEqual(len(warning_calls), MAX_TIMEOUT_RETRIES_INFINITE)

    def test_connect_meshtastic_does_not_scan_after_ble_errors_auto_reconnect(self):
        """Explicit BLE-address retries should not trigger discovery scans."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        class _BleInterfaceWithAutoReconnect:
            def __init__(
                self,
                address: str | None = None,
                *,
                noProto: bool = False,
                debugOut: object | None = None,
                noNodes: bool = False,
                timeout: int = 300,
                auto_reconnect: bool = False,
            ) -> None:
                self.address = address
                self._unused_params = (
                    noProto,
                    debugOut,
                    noNodes,
                    timeout,
                    auto_reconnect,
                )

        mock_iface = Mock()
        mock_iface.auto_reconnect = False

        def _make_interface_future() -> Mock:
            future = Mock()
            future.result = Mock(return_value=mock_iface)
            future.cancel = Mock(return_value=True)
            return future

        def _make_keyerror_future() -> Mock:
            future = Mock()
            future.result = Mock(side_effect=KeyError("path"))
            future.cancel = Mock(return_value=False)
            return future

        future_sequence = iter(
            future
            for _ in range(2)
            for future in (_make_interface_future(), _make_keyerror_future())
        )

        def submit_side_effect(
            _func: object, *_args: object, **_kwargs: object
        ) -> Mock:
            return next(future_sequence)

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = submit_side_effect

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                _BleInterfaceWithAutoReconnect,
            ),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("mmrelay.meshtastic_utils._scan_for_ble_address") as mock_scan,
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)
            self.assertIsNone(result)

            mock_scan.assert_not_called()

    def test_connect_meshtastic_does_not_scan_after_ble_errors_compatibility_mode(
        self,
    ):
        """Compatibility-mode retries for explicit BLE address should stay direct-only."""
        from mmrelay.meshtastic_utils import connect_meshtastic

        config = {
            "meshtastic": {
                "connection_type": CONNECTION_TYPE_BLE,
                "ble_address": TEST_BLE_MAC,
                "retries": 1,
            },
            "matrix_rooms": [],
        }

        class _BleInterfaceCompatibility:
            def __init__(
                self,
                address: str | None = None,
                *,
                noProto: bool = False,
                debugOut: object | None = None,
                noNodes: bool = False,
                timeout: int = 300,
            ) -> None:
                self.address = address
                self._unused_params = (noProto, debugOut, noNodes, timeout)

        def _make_keyerror_future() -> Mock:
            future = Mock()
            future.result = Mock(side_effect=KeyError("path"))
            future.cancel = Mock(return_value=False)
            return future

        future_sequence = iter(
            (
                _make_keyerror_future(),
                _make_keyerror_future(),
            )
        )

        def submit_side_effect(
            _func: object, *_args: object, **_kwargs: object
        ) -> Mock:
            return next(future_sequence)

        mock_executor = Mock()
        mock_executor._shutdown = False
        mock_executor.submit.side_effect = submit_side_effect

        with (
            patch("mmrelay.meshtastic_utils.time.sleep"),
            patch(
                "mmrelay.meshtastic_utils.meshtastic.ble_interface.BLEInterface",
                _BleInterfaceCompatibility,
            ),
            patch("mmrelay.meshtastic_utils._ble_executor", mock_executor),
            patch("mmrelay.meshtastic_utils._scan_for_ble_address") as mock_scan,
            patch("bleak.BleakClient") as mock_bleak_client,
        ):
            mock_client_instance = Mock()
            mock_client_instance.is_connected = False
            mock_bleak_client.return_value = mock_client_instance

            import mmrelay.meshtastic_utils as mu

            _reset_ble_inflight_state(mu)
            mu._metadata_future = None
            result = connect_meshtastic(passed_config=config)
            self.assertIsNone(result)
            mock_scan.assert_not_called()
