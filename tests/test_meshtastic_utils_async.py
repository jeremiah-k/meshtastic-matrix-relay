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


class TestCoroutineSubmission(unittest.TestCase):
    """Test cases for coroutine submission functionality."""

    def test_submit_coro_with_non_coroutine_input(self):
        """Test that _submit_coro returns None when given non-coroutine input."""
        from mmrelay.meshtastic_utils import _submit_coro

        # Test with string input
        result = _submit_coro("not a coroutine")
        self.assertIsNone(result)

        # Test with None input
        result = _submit_coro(None)
        self.assertIsNone(result)

        # Test with integer input
        result = _submit_coro(42)
        self.assertIsNone(result)

    def test_submit_coro_returns_future_for_valid_coroutine(self):
        """Test _submit_coro returns a Future-like object for valid coroutines."""
        from mmrelay.meshtastic_utils import _submit_coro

        async def test_coro():
            return "test_result"

        coro = test_coro()
        result = _submit_coro(coro)

        # Should return a Future-like object (either Future or Task)
        self.assertTrue(hasattr(result, "result") or hasattr(result, "done"))

        # Clean up the coroutine
        coro.close()


class TestAsyncHelperUtilities(unittest.TestCase):
    """Test cases for fire-and-forget and awaitable helper behavior."""

    class _ExceptionTask:
        def __init__(
            self,
            return_exc: BaseException | None = None,
            raise_exc: BaseException | None = None,
        ) -> None:
            self._return_exc = return_exc
            self._raise_exc = raise_exc
            self._callbacks: list[Callable[[Any], Any]] = []

        def add_done_callback(self, callback):
            self._callbacks.append(callback)

        def exception(self):
            if self._raise_exc is not None:
                raise self._raise_exc
            return self._return_exc

        def trigger(self) -> None:
            for callback in self._callbacks:
                callback(self)

    def test_fire_and_forget_ignores_non_coroutine(self):
        """Ensure fire-and-forget returns early for non-coroutines."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        with patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit:
            _fire_and_forget("not-a-coro")  # type: ignore[arg-type]

        mock_submit.assert_not_called()

    def test_fire_and_forget_returns_when_submit_none(self):
        """Ensure fire-and-forget returns when _submit_coro yields no task."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        with patch("mmrelay.meshtastic_utils._submit_coro", return_value=None):
            _fire_and_forget(_noop())

    def test_fire_and_forget_ignores_cancelled_error(self):
        """Ensure fire-and-forget ignores CancelledError in callbacks."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(raise_exc=asyncio.CancelledError())

        def _submit(coro, loop=None) -> Any:
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_fire_and_forget_logs_exception_retrieval_failure(self):
        """Ensure fire-and-forget logs when exception retrieval fails."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(raise_exc=RuntimeError("boom"))

        def _submit(coro, loop=None) -> Any:
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_called_once()
            mock_logger.error.assert_not_called()

    def test_fire_and_forget_logs_returned_exception(self):
        """Ensure fire-and-forget logs exceptions returned by a task."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(return_exc=ValueError("Task failed"))

        def _submit(coro, loop=None) -> Any:
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.error.assert_called_once()
            mock_logger.debug.assert_not_called()
            _call_args, call_kwargs = mock_logger.error.call_args
            self.assertIn("exc_info", call_kwargs)
            self.assertIsInstance(call_kwargs["exc_info"], ValueError)
            self.assertEqual(str(call_kwargs["exc_info"]), "Task failed")

    def test_fire_and_forget_ignores_returned_cancelled_error(self):
        """Ensure fire-and-forget ignores CancelledError instances returned by task.exception()."""
        from mmrelay.meshtastic_utils import _fire_and_forget

        async def _noop():
            return None

        fake_task = self._ExceptionTask(return_exc=asyncio.CancelledError())

        def _submit(coro, loop=None) -> Any:
            coro.close()
            return fake_task

        with (
            patch("mmrelay.meshtastic_utils._submit_coro", side_effect=_submit),
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            _fire_and_forget(_noop())
            fake_task.trigger()

            mock_logger.debug.assert_not_called()
            mock_logger.error.assert_not_called()

    def test_make_awaitable_returns_existing_awaitable(self):
        """Ensure _make_awaitable returns objects that are already awaitable."""
        from mmrelay.meshtastic_utils import _make_awaitable

        class DummyAwaitable:
            def __await__(self):
                if False:
                    yield None
                return "done"

        dummy = DummyAwaitable()
        result = _make_awaitable(dummy)

        self.assertIs(result, dummy)


class TestSubmitCoroActualImplementation(unittest.TestCase):
    """Test the actual _submit_coro implementation without global mocking."""

    def setUp(self):
        """
        Prepare test fixture by disabling the module-level asyncio event loop mock and capturing the real `_submit_coro`.

        This saves the current `mmrelay.meshtastic_utils.event_loop` and `_submit_coro` into instance attributes so they can be restored later, sets `event_loop` to None to ensure tests run against the real asyncio behavior, and reloads the `mmrelay.meshtastic_utils` source to obtain the original (unmocked) `_submit_coro` implementation for direct testing.
        """
        import mmrelay.meshtastic_utils as mu

        # Store original event_loop state
        self.original_event_loop = mu.event_loop

        # Reset module state for clean testing
        mu.event_loop = None

        # Store the mocked function so we can restore it
        self.mocked_submit_coro = mu._submit_coro

        # Get the source module without the mock
        import importlib
        import importlib.util

        # Get the source module without the mock
        spec = importlib.util.find_spec("mmrelay.meshtastic_utils")
        assert spec is not None
        source_module = importlib.util.module_from_spec(spec)

        # Execute the module to get the original function
        assert spec.loader is not None
        spec.loader.exec_module(source_module)

        # Get the original _submit_coro function
        self.original_submit_coro = source_module._submit_coro

    def tearDown(self):
        """
        Restore mmrelay.meshtastic_utils global state saved during setUp.

        Restores the module-level event_loop and _submit_coro attributes to the
        original values captured in setUp (self.original_event_loop and
        self.mocked_submit_coro). This ensures other tests are not affected by the
        test-specific event loop or submit coroutine replacement.
        """
        import mmrelay.meshtastic_utils as mu

        # Restore original event_loop state
        mu.event_loop = self.original_event_loop
        # Restore the mock
        mu._submit_coro = self.mocked_submit_coro

    def test_submit_coro_with_no_event_loop_no_running_loop(self):
        """Test _submit_coro with no event loop and no running loop - uses a temporary loop."""
        from concurrent.futures import Future

        async def test_coro():
            """
            Simple coroutine that returns a fixed test string.

            Returns:
                str: The literal string "test_result".
            """
            return "test_result"

        coro = test_coro()

        # Patch to ensure no running loop
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(coro)

            # Should return a Future with the result
            self.assertIsInstance(result, Future)
            self.assertEqual(result.result(), "test_result")

    def test_submit_coro_with_no_event_loop_no_running_loop_exception(self):
        """Test _submit_coro exception handling when coroutine execution fails."""
        from concurrent.futures import Future

        async def failing_coro():
            """
            Coroutine that always raises ValueError with message "Test exception" when awaited.

            Intended for use in tests to simulate a coroutine that fails.

            Raises:
                ValueError: Always raised when the coroutine is awaited with message "Test exception".
            """
            raise ValueError("Test exception")

        coro = failing_coro()

        # Patch to ensure no running loop
        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(coro)
            self.assertIsInstance(result, Future)
            self.assertIsInstance(result.exception(), ValueError)
            self.assertEqual(str(result.exception()), "Test exception")

    def test_submit_coro_with_running_loop(self):
        """Test _submit_coro with a running loop - should use create_task."""

        async def test_coro():
            return "test_result"

        coro = test_coro()

        try:
            # Mock a running loop
            with patch("asyncio.get_running_loop") as mock_get_loop:
                mock_loop = MagicMock()
                mock_task = MagicMock()

                # Mock create_task to close the coroutine when called
                def mock_create_task(coro_arg):
                    coro_arg.close()  # Close the coroutine to prevent warnings
                    return mock_task

                mock_loop.create_task.side_effect = mock_create_task
                mock_get_loop.return_value = mock_loop

                result = self.original_submit_coro(coro)

                # Should call create_task and return the task
                mock_loop.create_task.assert_called_once_with(coro)
                self.assertEqual(result, mock_task)
        finally:
            # Ensure coroutine is properly closed if not already closed
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                coro.close()

    def test_submit_coro_with_event_loop_parameter(self):
        """Test _submit_coro with event loop parameter - should use run_coroutine_threadsafe."""
        import asyncio

        async def test_coro():
            return "test_result"

        coro = test_coro()

        try:
            # Create mock event loop
            mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
            mock_loop.is_closed.return_value = False

            with patch("asyncio.run_coroutine_threadsafe") as mock_run_threadsafe:
                mock_future = MagicMock()

                # Mock run_coroutine_threadsafe to close the coroutine when called
                def mock_run_coro_threadsafe(coro_arg, loop_arg):
                    coro_arg.close()  # Close the coroutine to prevent warnings
                    return mock_future

                mock_run_threadsafe.side_effect = mock_run_coro_threadsafe

                result = self.original_submit_coro(coro, loop=mock_loop)

                # Should call run_coroutine_threadsafe
                mock_run_threadsafe.assert_called_once_with(coro, mock_loop)
                self.assertEqual(result, mock_future)
        finally:
            # Ensure coroutine is properly closed if not already closed
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                coro.close()

    def test_submit_coro_with_non_coroutine_actual(self):
        """
        Verify that _submit_coro returns None when given non-coroutine inputs such as strings, None, or integers.
        """
        # Test with string input
        result = self.original_submit_coro("not a coroutine")
        self.assertIsNone(result)

        # Test with None input
        result = self.original_submit_coro(None)
        self.assertIsNone(result)

        # Test with integer input
        result = self.original_submit_coro(42)
        self.assertIsNone(result)

    def test_submit_coro_accepts_non_coroutine_awaitable(self):
        """Test _submit_coro handles non-coroutine awaitables by awaiting them."""
        from concurrent.futures import Future

        class DummyAwaitable:
            def __await__(self):
                """
                Allow awaiting this object to receive its awaited result.

                Returns:
                    str: The string produced when awaiting the instance, "awaitable-result".
                """
                if False:
                    yield None
                return "awaitable-result"

        awaitable = DummyAwaitable()

        with patch("asyncio.get_running_loop") as mock_get_loop:
            mock_get_loop.side_effect = RuntimeError("No running loop")

            result = self.original_submit_coro(awaitable)

            self.assertIsInstance(result, Future)
            self.assertEqual(result.result(), "awaitable-result")

    def test_submit_coro_with_loop_not_running_falls_back(self):
        """
        Test _submit_coro with event loop that is not running - should fall through to fallback logic
        instead of calling run_coroutine_threadsafe.
        """
        import asyncio

        async def test_coro():
            return "test_result"

        coro = test_coro()

        try:
            mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
            mock_loop.is_closed.return_value = False
            mock_loop.is_running.return_value = False

            with patch("asyncio.run_coroutine_threadsafe") as mock_run_threadsafe:
                mock_get_loop = MagicMock()
                mock_task = MagicMock()

                def mock_create_task(coro_arg):
                    coro_arg.close()
                    return mock_task

                mock_get_loop.create_task.side_effect = mock_create_task

                with patch("asyncio.get_running_loop", return_value=mock_get_loop):
                    result = self.original_submit_coro(coro, loop=mock_loop)

                    # Should NOT call run_coroutine_threadsafe because loop is not running
                    mock_run_threadsafe.assert_not_called()

                    # Should call get_running_loop's create_task (fallback)
                    mock_get_loop.create_task.assert_called_once()
                    self.assertEqual(result, mock_task)
        finally:
            if hasattr(coro, "cr_frame") and coro.cr_frame is not None:
                coro.close()
