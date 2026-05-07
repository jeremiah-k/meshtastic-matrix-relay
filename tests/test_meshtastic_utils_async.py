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
import threading
import unittest
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

# Capture the real _submit_coro before any test patches are applied.
import mmrelay.meshtastic_utils as _mu_real
import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import (
    _get_name_safely,
    _make_awaitable,
    _wait_for_future_result_with_shutdown,
    _wait_for_result,
)

_REAL_SUBMIT_CORO = _mu_real._submit_coro

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
            _fire_and_forget(cast(Any, "not-a-coro"))

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

        def _submit(coro, **_kwargs: Any) -> Any:
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

        def _submit(coro, **_kwargs: Any) -> Any:
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

        def _submit(coro, **_kwargs: Any) -> Any:
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

        def _submit(coro, **_kwargs: Any) -> Any:
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
        Disable the module-level asyncio event loop mock and capture the real `_submit_coro`.

        Saves the current `mmrelay.meshtastic_utils.event_loop` into an instance
        attribute so it can be restored later, sets `event_loop` to None to ensure
        tests run against the real asyncio behavior, and uses the module-level
        captured `_REAL_SUBMIT_CORO` for direct testing.
        """
        import mmrelay.meshtastic_utils as mu

        # Store original event_loop state
        self.original_event_loop = mu.event_loop

        # Reset module state for clean testing
        mu.event_loop = None

        # Store the saved original _submit_coro (captured at module import time)
        self.saved_submit_coro = mu._submit_coro

        # Use the real implementation captured before any patches
        self.original_submit_coro = _REAL_SUBMIT_CORO

    def tearDown(self):
        """
        Restore mmrelay.meshtastic_utils global state saved during setUp.

        Restores the module-level event_loop and _submit_coro attributes to the
        original values captured in setUp (self.original_event_loop and
        self.saved_submit_coro). This ensures other tests are not affected by the
        test-specific event loop or submit coroutine replacement.
        """
        import mmrelay.meshtastic_utils as mu

        # Restore original event_loop state
        mu.event_loop = self.original_event_loop
        # Restore the saved original
        mu._submit_coro = self.saved_submit_coro

    def test_submit_coro_with_no_event_loop_no_running_loop(self):
        """Test _submit_coro with no event loop and no running loop - uses a temporary loop."""

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


# ---------------------------------------------------------------------------
# Helpers for async utility tests (absorbed from test_meshtastic_utils_async_helpers.py)
# ---------------------------------------------------------------------------


class _DummyLoop:
    def is_closed(self):
        return False

    def is_running(self):
        """
        Indicates that this dummy loop is always considered running.

        Returns:
            True, since the dummy loop is always treated as running.
        """
        return True

    def create_task(self, _coro):
        """
        Simulate scheduling a coroutine by closing it and returning a MagicMock representing the created task.

        Parameters:
            _coro: A coroutine object which will be closed.

        Returns:
            MagicMock: A mock object standing in for the scheduled task.
        """
        _coro.close()
        return MagicMock()


def _make_threadsafe_runner(result_value):
    """
    Create a fake thread-safe runner that closes a coroutine and returns a mock future.

    Parameters:
        result_value: Value that the returned mock future's `result()` method will return.

    Returns:
        A callable with signature `(coro, _loop)` that closes `coro` and returns a MagicMock whose `result()` returns `result_value`.
    """
    result_future = MagicMock()
    result_future.result.return_value = result_value

    def _fake_threadsafe(coro, _loop):
        coro.close()
        return result_future

    return _fake_threadsafe


# ---------------------------------------------------------------------------
# Tests for async utility helpers (absorbed from test_meshtastic_utils_async_helpers.py)
# ---------------------------------------------------------------------------


def test_make_awaitable_wraps_future(meshtastic_loop_safety):
    future = Future()
    wrapped = _make_awaitable(future, loop=meshtastic_loop_safety)

    future.set_result("ok")
    result = meshtastic_loop_safety.run_until_complete(wrapped)

    assert wrapped is not future
    assert result == "ok"


def test_wait_for_result_none_returns_false():
    assert _wait_for_result(None, timeout=0.1) is False


def test_wait_for_result_asyncio_future_uses_loop(meshtastic_loop_safety):
    future = meshtastic_loop_safety.create_future()
    future.set_result("done")

    result = _wait_for_result(future, timeout=0.1, loop=meshtastic_loop_safety)

    assert result == "done"


def test_wait_for_result_result_method_typeerror_fallback():
    class ResultOnly:
        def result(self):
            """
            Retrieve the object's result value.

            Returns:
                str: The result string "value".
            """
            return "value"

    result = _wait_for_result(ResultOnly(), timeout=0.1)

    assert result == "value"


def test_wait_for_result_target_loop_running_uses_threadsafe():
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        future.set_result("done")

        with (
            patch.object(loop, "is_running", return_value=True),
            patch.object(loop, "is_closed", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=_make_threadsafe_runner("threadsafe"),
            ),
        ):
            result = _wait_for_result(future, timeout=0.1, loop=loop)
    finally:
        loop.close()

    assert result == "threadsafe"


def test_wait_for_result_running_loop_skips_threadsafe_without_explicit_loop():
    """Returns False when running loop is present without explicit loop argument."""
    loop = asyncio.new_event_loop()
    try:
        future = loop.create_future()
        future.set_result("done")
        with (
            patch(
                "mmrelay.meshtastic_utils.asyncio.get_running_loop",
                return_value=_DummyLoop(),
            ),
            patch(
                "mmrelay.meshtastic_utils.asyncio.run_coroutine_threadsafe",
                side_effect=_make_threadsafe_runner("running"),
            ) as mock_threadsafe,
        ):
            result = _wait_for_result(future, timeout=0.1)
    finally:
        loop.close()

    assert result is False
    mock_threadsafe.assert_not_called()


def test_wait_for_result_running_loop_not_running():
    """
    Verifies that _wait_for_result executes a coroutine on a loop returned by get_running_loop when that loop is not running and returns the coroutine's result.

    Patches asyncio.get_running_loop to return a newly created (not running) event loop, calls _wait_for_result with a coroutine that returns "sync-loop", and asserts the observed result is "sync-loop".
    """
    loop = asyncio.new_event_loop()
    try:
        with patch(
            "mmrelay.meshtastic_utils.asyncio.get_running_loop", return_value=loop
        ):

            async def _sample():
                """
                Provide the literal string "sync-loop".

                Returns:
                    str: The string "sync-loop".
                """
                return "sync-loop"

            result = _wait_for_result(_sample(), timeout=0.1)
    finally:
        loop.close()

    assert result == "sync-loop"


def test_wait_for_result_new_loop_path():
    async def _sample():
        """
        Return the literal string "new-loop".

        Returns:
            result (str): The string "new-loop".
        """
        return "new-loop"

    result = _wait_for_result(_sample(), timeout=0.1)

    assert result == "new-loop"


def test_get_name_safely_returns_sender_on_exception():
    def _bad_lookup(_sender):
        """
        Raise a TypeError to simulate a failing name lookup.

        Parameters:
            _sender: Ignored; present only to match the expected callable signature.

        Raises:
            TypeError: always raised with message "boom".
        """
        raise TypeError("boom")

    assert _get_name_safely(_bad_lookup, 123) == "123"


def test_wait_for_future_result_with_shutdown_returns_result():
    future = Future()
    future.set_result("ok")

    result = _wait_for_future_result_with_shutdown(
        future,
        timeout_seconds=0.1,
        poll_seconds=0.01,
    )

    assert result == "ok"


def test_wait_for_future_result_with_shutdown_aborts_when_shutting_down():
    future = MagicMock()

    with patch.object(mu, "shutting_down", True):
        with pytest.raises(TimeoutError, match="Shutdown in progress"):
            _wait_for_future_result_with_shutdown(
                future,
                timeout_seconds=0.5,
                poll_seconds=0.01,
            )

    future.result.assert_not_called()


def test_wait_for_future_result_with_shutdown_raises_futures_timeout():
    future = MagicMock()
    future.result.side_effect = FuturesTimeoutError

    with patch.object(mu, "shutting_down", False):
        with pytest.raises(FuturesTimeoutError):
            _wait_for_future_result_with_shutdown(
                future,
                timeout_seconds=0.02,
                poll_seconds=0.005,
            )

    assert future.result.call_count >= 1
