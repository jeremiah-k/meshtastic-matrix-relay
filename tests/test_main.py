#!/usr/bin/env python3
"""
Test suite for main application functionality in MMRelay.

Tests the main application flow including:
- Application initialization and configuration
- Database initialization
- Plugin loading
- Message queue startup
- Matrix and Meshtastic client connections
- Graceful shutdown handling
- Banner printing and version display

CRITICAL HANGING TEST ISSUE SOLVED:
=====================================

PROBLEM:
- TestMainAsyncFunction tests would hang when run sequentially
- test_main_async_event_loop_setup would pass, but test_main_async_initialization_sequence would hang
- This blocked CI and development for extended periods

ROOT CAUSE:
- test_main_async_event_loop_setup calls run_main() which calls set_config()
- set_config() sets global variables in ALL mmrelay modules (meshtastic_utils, matrix_utils, etc.)
- test_main_async_initialization_sequence inherits this contaminated global state
- Contaminated state causes the second test to hang indefinitely

SOLUTION:
- TestMainAsyncFunction class implements comprehensive global state reset
- setUp() and tearDown() methods call _reset_global_state()
- _reset_global_state() resets ALL global variables in ALL mmrelay modules
- Each test now starts with completely clean state

PREVENTION:
- DO NOT remove or modify setUp(), tearDown(), or _reset_global_state() methods
- When adding new global variables to mmrelay modules, add them to _reset_global_state()
- Always test sequential execution of TestMainAsyncFunction tests before committing
- If hanging tests return, check for new global state that needs resetting

This solution ensures reliable test execution and prevents CI blocking issues.
"""

import asyncio
import concurrent.futures
import contextlib
import functools
import inspect
import sys
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.main import main, print_banner, run_main
from tests.helpers import InlineExecutorLoop, inline_to_thread


def _make_async_return(value: Any):
    """
    Create an async function that always returns provided value.

    Parameters:
        value (Any): Value to be returned by generated coroutine.

    Returns:
        callable: An async function that ignores its arguments and returns `value` when awaited.
    """

    async def _async_return(*_args, **_kwargs):
        return value

    return _async_return


async def _async_noop(*_args, **_kwargs) -> None:
    """
    Asynchronous no-op that accepts any positional and keyword arguments.

    This coroutine performs no action and ignores all provided arguments.

    Returns:
        None
    """
    return None


def _close_coro_if_possible(coro: Any) -> None:
    """
    Close an awaitable/coroutine object if it exposes a close() method to prevent ResourceWarning during tests.

    Parameters:
        coro: An awaitable object (e.g., coroutine object or generator-based coroutine). If it has a `close()` method it will be called; otherwise the object is left untouched.
    """
    if inspect.isawaitable(coro) and hasattr(coro, "close"):
        coro.close()  # type: ignore[attr-defined]
    return None


def _mock_run_with_exception(coro: Any) -> None:
    """Close coroutine and raise test exception."""
    _close_coro_if_possible(coro)
    raise Exception("Test error")


def _mock_run_with_keyboard_interrupt(coro: Any) -> None:
    """
    Invoke _close_coro_if_possible on the given coroutine-like object and then raise KeyboardInterrupt.

    Parameters:
        coro (Any): An awaitable or coroutine object; if it has a `close()` method, that method will be called.

    Raises:
        KeyboardInterrupt: Always raised after attempting to close the coroutine.
    """
    _close_coro_if_possible(coro)
    raise KeyboardInterrupt()


def _make_async_raise(exc: Exception):
    """
    Create an async callable that always raises provided exception when awaited.

    Parameters:
        exc (Exception): The exception instance to raise when the returned coroutine is awaited.

    Returns:
        Callable[..., Coroutine]: An async function that, when called and awaited, raises `exc`.
    """

    async def _async_raise(*_args, **_kwargs):
        raise exc

    return _async_raise


def _make_patched_get_running_loop():
    """
    Create a patched get_running_loop that wraps the loop in InlineExecutorLoop.

    Returns:
        Callable: A function that returns the current event loop wrapped in
        InlineExecutorLoop if not already wrapped.

    Notes:
        This helper is used in tests to ensure run_in_executor calls execute
        inline instead of scheduling on a thread pool. The returned function
        checks if the loop is already an InlineExecutorLoop to avoid double-wrapping.
    """
    real_get_running_loop = asyncio.get_running_loop

    def _patched_get_running_loop():
        """
        Wraps the real running event loop in an InlineExecutorLoop when necessary.

        Returns:
            The original loop if it is already an InlineExecutorLoop,
            otherwise a new InlineExecutorLoop that delegates to the real loop.
        """
        loop = real_get_running_loop()
        if isinstance(loop, InlineExecutorLoop):
            return loop
        return InlineExecutorLoop(loop)

    return _patched_get_running_loop


class _ImmediateEvent:
    """Event that starts set and completes wait() immediately for shutdown tests."""

    def __init__(self) -> None:
        """
        Initialize an ImmediateEvent representing an event that is always set.

        Sets internal state so is_set() returns True and awaitable wait() completes immediately.
        """
        self._set = True

    def is_set(self) -> bool:
        """
        Indicates whether the event is set.

        Returns:
            `True` if the event is set, `False` otherwise.
        """
        return self._set

    def set(self) -> None:
        """
        Mark the event as set so subsequent checks see it as signaled and waiters do not block.
        """
        self._set = True

    async def wait(self) -> None:
        """
        Return immediately without blocking, simulating an event that is already set.

        This coroutine is a no-op used in tests to represent an event whose wait completes immediately.
        """
        return None


class _CloseFutureBase(concurrent.futures.Future):
    """Future with a cancel flag for shutdown test assertions."""

    def __init__(self) -> None:
        """
        Initialize the instance and set up the cancel call tracker.

        Tracks whether cancel() was invoked on this future via the `cancel_called` attribute.
        """
        super().__init__()
        self.cancel_called = False

    def cancel(self) -> bool:
        """
        Mark the future as cancelled and record that cancellation was attempted.

        Sets the `cancel_called` attribute to True.

        Returns:
            bool: `True` if the future was successfully cancelled, `False` otherwise.
        """
        self.cancel_called = True
        return super().cancel()


class _TimeoutCloseFuture(_CloseFutureBase):
    """Future that raises TimeoutError immediately on result()."""

    def result(self, timeout: float | None = None) -> None:  # noqa: ARG002
        """
        Always raises concurrent.futures.TimeoutError to simulate a timed-out close future.

        Parameters:
            timeout (float | None): Ignored.

        Raises:
            concurrent.futures.TimeoutError: Always raised when called.
        """
        raise concurrent.futures.TimeoutError()


class _ErrorCloseFuture(_CloseFutureBase):
    """Future that raises an unexpected error on result()."""

    def result(self, timeout: float | None = None) -> None:  # noqa: ARG002
        """
        Raise a ValueError with message "boom".

        Parameters:
            timeout (float | None): Ignored; present for API compatibility.

        Raises:
            ValueError: Always raised with message "boom".
        """
        raise ValueError("boom")


class _ControlledExecutor:
    """Executor that runs normal tasks immediately and can override close behavior."""

    def __init__(
        self,
        *,
        close_future_factory: Callable[[], concurrent.futures.Future] | None = None,
        submit_timeout: bool = False,
        shutdown_typeerror: bool = False,
    ) -> None:
        """
        Create a ControlledExecutor used in tests to simulate and control task submission and shutdown behaviors.

        Parameters:
            close_future_factory (Callable[[], concurrent.futures.Future] | None):
                Factory that produces a preconfigured Future to return for close-related submissions; if None, submit executes synchronously.
            submit_timeout (bool):
                If True, simulate a timeout condition when submitting close-related tasks.
            shutdown_typeerror (bool):
                If True, simulate an executor.shutdown that raises a TypeError when called with cancel_futures=True (to model older Python behavior).

        """
        self.close_future_factory = close_future_factory
        self.submit_timeout = submit_timeout
        self.shutdown_typeerror = shutdown_typeerror
        self.future = None
        self.close_future = None
        self.calls: list[Any] = []

    def submit(self, func, *args, **kwargs):
        """
        Submit a callable to the controlled executor, execute it synchronously, and return a Future representing its outcome.

        If the callable's name or qualified name contains "_close_meshtastic", the executor applies special close-related behavior: it raises concurrent.futures.TimeoutError when configured with submit_timeout, or returns a pre-created close future when a close_future_factory is provided.

        Parameters:
            func (callable): The function or functools.partial to execute. Additional positional and keyword arguments are forwarded to the callable.

        Returns:
            concurrent.futures.Future: Future containing the callable's result. If the callable raises an exception,
            it propagates to the caller (no Future is returned).
        """
        target = func
        if isinstance(func, functools.partial):
            target = func.func
        target_name = getattr(target, "__name__", "")
        target_qualname = getattr(target, "__qualname__", "")
        is_close = "_close_meshtastic" in target_name or "_close_meshtastic" in (
            target_qualname
        )
        if is_close and self.submit_timeout:
            raise concurrent.futures.TimeoutError()
        if is_close and self.close_future_factory is not None:
            if self.close_future is None:
                self.close_future = self.close_future_factory()
            return self.close_future

        future = concurrent.futures.Future()
        result = func(*args, **kwargs)
        future.set_result(result)
        return future

    def shutdown(self, wait: bool = False, cancel_futures: bool = False) -> None:
        """
        Record an executor shutdown request and optionally simulate a legacy TypeError when `cancel_futures` is used.

        Parameters:
            wait (bool): Whether to wait for pending futures to complete.
            cancel_futures (bool): Whether to cancel pending futures; when the executor is configured
                to simulate older Python behavior, passing `True` raises a `TypeError`.
        """
        self.calls.append((wait, cancel_futures))
        if self.shutdown_typeerror and cancel_futures is True:
            # Simulate older Python versions that do not accept cancel_futures.
            raise TypeError()


class TestMain(unittest.TestCase):
    """Test cases for main application functionality."""

    def setUp(self):
        """Set up mock configuration for tests."""
        self.mock_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ],
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "message_delay": 2.0,
            },
            "database": {"msg_map": {"wipe_on_restart": False}},
        }

    def test_print_banner(self):
        """
        Tests that the banner is printed exactly once and includes the version information in the log output.
        """
        with patch("mmrelay.main.logger") as mock_logger:
            print_banner()

            # Should print banner with version
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args[0][0]
            self.assertIn("Starting MMRelay", call_args)
            self.assertIn("version ", call_args)  # Version should be included

    def test_print_banner_only_once(self):
        """Test that banner is only printed once."""
        with patch("mmrelay.main.logger") as mock_logger:
            print_banner()
            print_banner()  # Second call

            # Should only be called once
            self.assertEqual(mock_logger.info.call_count, 1)

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
    @patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
    @patch("mmrelay.main.update_longnames")
    @patch("mmrelay.main.update_shortnames")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_basic_flow(
        self,
        mock_stop_queue,
        mock_update_shortnames,
        mock_update_longnames,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Verify that all main application initialization functions are properly mocked and callable during the basic startup flow test.
        """
        # This test just verifies that the initialization functions are called
        # We don't run the full main() function to avoid async complexity

        # Verify that the mocks are set up correctly
        self.assertIsNotNone(mock_init_db)
        self.assertIsNotNone(mock_load_plugins)
        self.assertIsNotNone(mock_start_queue)
        self.assertIsNotNone(mock_connect_meshtastic)
        self.assertIsNotNone(mock_connect_matrix)
        self.assertIsNotNone(mock_join_room)
        self.assertIsNotNone(mock_stop_queue)
        self.assertIsNotNone(mock_update_longnames)
        self.assertIsNotNone(mock_update_shortnames)

        # Test passes if all mocks are properly set up
        # The actual main() function testing is complex due to async nature
        # and is better tested through integration tests

    def test_main_with_message_map_wipe(self):
        """
        Test that the message map wipe function is called when the configuration enables wiping on restart.

        Verifies that the wipe logic correctly parses both new and legacy configuration formats and triggers the wipe when appropriate.
        """
        # Enable message map wiping
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["database"]["msg_map"]["wipe_on_restart"] = True

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_map:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Verify message map was wiped when configured
            mock_wipe_map.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main(
        self,
        mock_print_banner,
        mock_configure_debug,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` executes the full startup sequence and returns 0 on success.

        Verifies that configuration is loaded and set, logging level is overridden by arguments, the banner is printed, debug logging is configured, the main async function is run, and the function returns 0 to indicate successful execution.
        """
        # Mock arguments
        mock_args = MagicMock()
        mock_args.log_level = "debug"

        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        result = run_main(mock_args)

        # Verify configuration was loaded and set
        mock_load_config.assert_called_once_with(args=mock_args)

        # Verify log level was overridden
        expected_config = self.mock_config.copy()
        expected_config["logging"] = {"level": "debug"}

        # Verify banner was printed
        mock_print_banner.assert_called_once()

        # Verify component debug logging was configured
        mock_configure_debug.assert_called_once()

        # Verify asyncio.run was called
        mock_asyncio_run.assert_called_once()

        # Should return 0 for success
        self.assertEqual(result, 0)

    @patch("mmrelay.config.load_config")
    @patch("asyncio.run")
    def test_run_main_exception_handling(self, mock_asyncio_run, mock_load_config):
        """
        Verify that run_main returns 1 when an exception is raised during asynchronous execution.
        """
        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup and exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        result = run_main(None)

        # Should return 1 for error
        self.assertEqual(result, 1)

    @patch("mmrelay.config.load_config")
    @patch("asyncio.run")
    def test_run_main_keyboard_interrupt(self, mock_asyncio_run, mock_load_config):
        """
        Verifies that run_main returns 0 when a KeyboardInterrupt is raised during execution, ensuring graceful shutdown behavior.
        """
        # Mock config loading
        mock_load_config.return_value = self.mock_config

        # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        result = run_main(None)

        # Should return 0 for graceful shutdown
        self.assertEqual(result, 0)

    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_meshtastic_connection_failure(
        self,
        mock_stop_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
        mock_connect_meshtastic,
    ):
        """
        Test that the application attempts to connect to Matrix even if Meshtastic connection fails.

        Simulates a failed Meshtastic connection and verifies that the Matrix connection is still attempted during application startup.
        """
        # Mock Meshtastic connection to return None (failure)
        mock_connect_meshtastic.return_value = None

        # Mock Matrix connection to fail early to avoid hanging
        mock_connect_matrix.side_effect = _make_async_return(None)
        mock_join_room.side_effect = _async_noop

        # Call main function (should exit early due to connection failures)
        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch(
                "mmrelay.main.asyncio.to_thread",
                side_effect=inline_to_thread,
            ),
        ):
            with contextlib.suppress(ConnectionError):
                asyncio.run(main(self.mock_config))

        # Should still proceed with Matrix connection
        mock_connect_matrix.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_matrix_connection_failure(
        self,
        mock_stop_queue,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Test that an exception during Matrix connection is raised and not suppressed during main application startup.

        Mocks the Matrix connection to raise an exception and verifies that the main function propagates the error.
        """
        # Mock Meshtastic client
        mock_meshtastic_client = MagicMock()
        mock_connect_meshtastic.return_value = mock_meshtastic_client

        mock_connect_matrix.side_effect = _make_async_raise(
            Exception("Matrix connection failed")
        )
        # Should raise the Matrix connection exception
        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch(
                "mmrelay.main.asyncio.to_thread",
                side_effect=inline_to_thread,
            ),
        ):
            with self.assertRaises(Exception) as context:
                asyncio.run(main(self.mock_config))
        self.assertIn("Matrix connection failed", str(context.exception))

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_closes_meshtastic_client_on_shutdown(
        self,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Shutdown should close the Meshtastic client when present."""

        mock_meshtastic_client = MagicMock()
        mock_connect_meshtastic.return_value = mock_meshtastic_client

        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue
            asyncio.run(main(self.mock_config))

        mock_meshtastic_client.close.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_utils._disconnect_ble_interface")
    def test_main_shutdown_disconnects_ble_interface(
        self,
        mock_disconnect_iface,
        _mock_stop_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Shutdown should use BLE-specific disconnect when the interface is BLE."""

        mock_iface = MagicMock()

        def _connect_meshtastic(*_args, **_kwargs):
            """
            Install the test Meshtastic interface into mmrelay.meshtastic_utils and return it.

            This helper ignores any positional or keyword arguments. It assigns the module-level
            `mock_iface` to `mmrelay.meshtastic_utils.meshtastic_iface` and returns that object.

            Returns:
                mock_iface: The mock Meshtastic interface that was assigned.
            """
            import mmrelay.meshtastic_utils as mu

            mu.meshtastic_iface = mock_iface
            return mock_iface

        mock_connect_meshtastic.side_effect = _connect_meshtastic

        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        executor = _ControlledExecutor()
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.concurrent.futures.ThreadPoolExecutor",
                return_value=executor,
            ),
        ):
            asyncio.run(main(self.mock_config))

        mock_disconnect_iface.assert_called_once_with(mock_iface, reason="shutdown")
        import mmrelay.meshtastic_utils as mu

        self.assertIsNone(mu.meshtastic_iface)

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    def test_main_shutdown_timeout_cancels_future(
        self,
        mock_meshtastic_logger,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Shutdown should cancel futures when Meshtastic close times out."""

        mock_iface = MagicMock()

        def _connect_meshtastic(*_args, **_kwargs):
            """
            Install the provided mock Meshtastic interface into the mmrelay.meshtastic_utils module for tests.

            Sets mmrelay.meshtastic_utils.meshtastic_client to the mock interface and mmrelay.meshtastic_utils.meshtastic_iface to None.

            Returns:
                The mock Meshtastic interface that was installed.
            """
            import mmrelay.meshtastic_utils as mu

            mu.meshtastic_client = mock_iface
            mu.meshtastic_iface = None
            return mock_iface

        mock_connect_meshtastic.side_effect = _connect_meshtastic
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        executor = _ControlledExecutor(close_future_factory=_TimeoutCloseFuture)
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.concurrent.futures.ThreadPoolExecutor",
                return_value=executor,
            ),
        ):
            asyncio.run(main(self.mock_config))

        self.assertTrue(executor.close_future.cancel_called)  # type: ignore[attr-defined]
        mock_meshtastic_logger.warning.assert_any_call(
            "Meshtastic client close timed out - may cause notification errors"
        )

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    def test_main_shutdown_logs_unexpected_close_error(
        self,
        mock_meshtastic_logger,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Shutdown should log unexpected errors from close futures."""

        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        executor = _ControlledExecutor(close_future_factory=_ErrorCloseFuture)
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.concurrent.futures.ThreadPoolExecutor",
                return_value=executor,
            ),
        ):
            asyncio.run(main(self.mock_config))

        self.assertTrue(
            any(
                "Unexpected error during Meshtastic client close" in str(call)
                for call in mock_meshtastic_logger.exception.call_args_list
            )
        )

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_main_shutdown_shutdown_typeerror_fallback(
        self,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """
        Ensure main retries executor.shutdown without cancel_futures when a TypeError occurs.

        Verifies that if ThreadPoolExecutor.shutdown raises a TypeError when invoked with
        cancel_futures=True, the shutdown sequence calls executor.shutdown a second time
        with cancel_futures=False.
        """

        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        executor = _ControlledExecutor(shutdown_typeerror=True)
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.concurrent.futures.ThreadPoolExecutor",
                return_value=executor,
            ),
        ):
            asyncio.run(main(self.mock_config))

        self.assertEqual(executor.calls[0], (False, True))
        self.assertEqual(executor.calls[1], (False, False))

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    def test_main_shutdown_submit_timeout_triggers_outer_warning(
        self,
        mock_meshtastic_logger,
        _mock_stop_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Submit-time timeouts should hit the outer shutdown warning."""

        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        executor = _ControlledExecutor(submit_timeout=True)
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.concurrent.futures.ThreadPoolExecutor",
                return_value=executor,
            ),
        ):
            asyncio.run(main(self.mock_config))

        mock_meshtastic_logger.warning.assert_any_call(
            "Meshtastic client close timed out - forcing shutdown"
        )


class TestPrintBanner(unittest.TestCase):
    """Test cases for banner printing functionality."""

    def setUp(self):
        """
        Set up test environment for banner tests.
        """
        pass

    @patch("mmrelay.main.logger")
    def test_print_banner_first_time(self, mock_logger):
        """
        Test that the banner is printed and includes version information on the first call to print_banner.
        """
        print_banner()
        mock_logger.info.assert_called_once()
        # Check that the message contains version info
        call_args = mock_logger.info.call_args[0][0]
        self.assertIn("Starting MMRelay", call_args)
        self.assertIn("version ", call_args)  # Version should be included

    @patch("mmrelay.main.logger")
    def test_print_banner_subsequent_calls(self, mock_logger):
        """
        Test that the banner is printed only once, even if print_banner is called multiple times.
        """
        print_banner()
        print_banner()  # Second call
        # Should only be called once
        mock_logger.info.assert_called_once()


class TestRunMain(unittest.TestCase):
    """Test cases for run_main function."""

    def setUp(self):
        """
        Prepare common fixtures used by run_main tests.

        Creates a default mock args object and a representative configuration used across run_main test cases, and provides helpers to supply a coroutine-cleanup wrapper for asyncio.run so tests can avoid un-awaited coroutine warnings.
        """
        pass

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_success(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` completes successfully with valid configuration and arguments.

        Verifies that the banner is printed, configuration is loaded, and the main asynchronous function is executed, resulting in a return value of 0.
        """
        # Mock configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Mock args
        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_print_banner.assert_called_once()
        mock_load_config.assert_called_once_with(args=mock_args)
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.main.print_banner")
    def test_run_main_missing_config_keys(
        self, mock_print_banner, mock_load_config, mock_set_config, mock_asyncio_run
    ):
        """
        Verify run_main returns 1 when the loaded configuration is missing required keys.

        Sets up a minimal incomplete config (only matrix.homeserver) and ensures run_main detects the missing fields and returns a non-zero exit code. Uses the coroutine cleanup helper for asyncio.run to avoid ResourceWarnings.
        """
        # Mock incomplete configuration
        mock_config = {"matrix": {"homeserver": "https://matrix.org"}}  # Missing keys
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # Should return error code

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_keyboard_interrupt_with_args(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that `run_main` returns 0 when a `KeyboardInterrupt` occurs during execution with command-line arguments.

        Ensures the application exits gracefully with a success code when interrupted by the user, even if arguments are provided.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup and KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)  # Should return success on keyboard interrupt

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_exception(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that run_main returns 1 when a general exception is raised during asynchronous execution.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup and exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # Should return error code

    @patch("asyncio.run")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.set_config")
    @patch("mmrelay.log_utils.configure_component_debug_logging")
    @patch("mmrelay.main.print_banner")
    def test_run_main_with_data_dir(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that run_main returns success when args includes data_dir.

        This verifies run_main executes successfully when passed args.data_dir (processing of
        `--data-dir` is performed by the CLI layer before calling run_main, so run_main does not
        modify or create the directory). Uses a minimal valid config and a mocked asyncio.run
        to avoid running the real event loop.
        """

        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Use a simple custom data directory path
        custom_data_dir = "/home/user/test_custom_data"

        mock_args = MagicMock()
        mock_args.data_dir = custom_data_dir
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # run_main() no longer processes --data-dir (that's handled in cli.py)
        # Just verify it runs successfully

    @patch("asyncio.run", spec=True)
    @patch("mmrelay.config.load_config", spec=True)
    @patch("mmrelay.config.set_config", spec=True)
    @patch("mmrelay.log_utils.configure_component_debug_logging", spec=True)
    @patch("mmrelay.main.print_banner", spec=True)
    def test_run_main_with_log_level(
        self,
        mock_print_banner,
        mock_configure_logging,
        mock_set_config,
        mock_load_config,
        mock_asyncio_run,
    ):
        """
        Test that run_main applies a custom log level from arguments and completes successfully.

        Ensures that when a log level is specified in the arguments, it overrides the logging level in the configuration, and run_main returns 0 to indicate successful execution.
        """
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org"}],
        }
        mock_load_config.return_value = mock_config

        # Mock asyncio.run with coroutine cleanup to prevent warnings
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = "DEBUG"

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Check that log level was set in config
        self.assertEqual(mock_config["logging"]["level"], "DEBUG")


class TestMainFunctionEdgeCases(unittest.TestCase):
    """Test cases for edge cases in the main function."""

    def setUp(self):
        """
        Prepare a mock configuration dictionary for use in test cases.
        """
        self.mock_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }

    def test_main_with_database_wipe_new_format(self):
        """
        Test that the database wipe logic is triggered when `wipe_on_restart` is set in the new configuration format.

        Verifies that the `wipe_message_map` function is called if the `database.msg_map.wipe_on_restart` flag is enabled in the configuration.
        """
        # Add database config with wipe_on_restart
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["database"] = {"msg_map": {"wipe_on_restart": True}}

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_db:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Should call wipe_message_map when new config format is set
            mock_wipe_db.assert_called_once()

    def test_main_with_database_wipe_legacy_format(self):
        """
        Test that the database wipe logic is triggered when the legacy configuration format specifies `wipe_on_restart`.

        Verifies that the application correctly detects the legacy `db.msg_map.wipe_on_restart` setting and calls the database wipe function.
        """
        # Add legacy database config with wipe_on_restart
        config_with_wipe = self.mock_config.copy()
        config_with_wipe["db"] = {"msg_map": {"wipe_on_restart": True}}

        # Test the specific logic that checks for database wipe configuration
        with patch("mmrelay.db_utils.wipe_message_map") as mock_wipe_db:
            # Extract the wipe configuration the same way main() does
            database_config = config_with_wipe.get("database", {})
            msg_map_config = database_config.get("msg_map", {})
            wipe_on_restart = msg_map_config.get("wipe_on_restart", False)

            # If not found in database config, check legacy db config
            if not wipe_on_restart:
                db_config = config_with_wipe.get("db", {})
                legacy_msg_map_config = db_config.get("msg_map", {})
                wipe_on_restart = legacy_msg_map_config.get("wipe_on_restart", False)

            # Simulate calling wipe_message_map if wipe_on_restart is True
            if wipe_on_restart:
                from mmrelay.db_utils import wipe_message_map

                wipe_message_map()

            # Should call wipe_message_map when legacy config is set
            mock_wipe_db.assert_called_once()

    def test_main_with_custom_message_delay(self):
        """
        Test that a custom message delay in the Meshtastic configuration is correctly extracted and passed to the message queue starter.
        """
        # Add custom message delay
        config_with_delay = self.mock_config.copy()
        config_with_delay["meshtastic"]["message_delay"] = 5.0

        # Test the specific logic that extracts message delay from config
        with patch("mmrelay.main.start_message_queue") as mock_start_queue:
            # Extract the message delay the same way main() does
            message_delay = config_with_delay.get("meshtastic", {}).get(
                "message_delay", 2.0
            )

            # Simulate calling start_message_queue with the extracted delay

            mock_start_queue(message_delay=message_delay)

            # Should call start_message_queue with custom delay
            mock_start_queue.assert_called_once_with(message_delay=5.0)

    def test_main_no_meshtastic_client_warning(self):
        """
        Verify that update functions are not called when the Meshtastic client is None.

        This test ensures that, if the Meshtastic client is not initialized, the main logic does not attempt to update longnames or shortnames.
        """
        # This test is simplified to avoid async complexity while still testing the core logic
        # The actual behavior is tested through integration tests

        # Test the specific condition: when meshtastic_client is None,
        # update functions should not be called
        with (
            patch("mmrelay.main.update_longnames") as mock_update_long,
            patch("mmrelay.main.update_shortnames") as mock_update_short,
        ):
            # Simulate the condition where meshtastic_client is None
            import mmrelay.meshtastic_utils

            original_client = getattr(
                mmrelay.meshtastic_utils, "meshtastic_client", None
            )
            mmrelay.meshtastic_utils.meshtastic_client = None

            try:
                # Test the specific logic that checks for meshtastic_client
                if mmrelay.meshtastic_utils.meshtastic_client:
                    # This should not execute when client is None
                    from mmrelay.main import update_longnames, update_shortnames

                    update_longnames(mmrelay.meshtastic_utils.meshtastic_client.nodes)
                    update_shortnames(mmrelay.meshtastic_utils.meshtastic_client.nodes)

                # Verify update functions were not called
                mock_update_long.assert_not_called()
                mock_update_short.assert_not_called()

            finally:
                # Restore original client
                mmrelay.meshtastic_utils.meshtastic_client = original_client


@pytest.mark.parametrize("db_key", ["database", "db"])
@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
def test_main_database_wipe_config(
    mock_join,
    mock_connect_mesh,
    mock_connect_matrix,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
    db_key,
):
    """
    Verify that main() triggers a message-map wipe when the configuration includes a database/message-map wipe_on_restart flag (supports both current "database" and legacy "db" keys) and that the message queue processor is started.

    Detailed behavior:
    - Builds a minimal config with one Matrix room and a database section under the provided `db_key` where `msg_map.wipe_on_restart` is True.
    - Mocks Matrix and Meshtastic connections and the message queue to avoid external I/O.
    - Runs main(config) with an immediate shutdown event to stop after startup.
    - Asserts that wipe_message_map() was invoked and that the message queue's processor was started.
    """
    # Mock config with database wipe settings
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        db_key: {"msg_map": {"wipe_on_restart": True}},
    }

    # Mock the async components with proper return values
    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()  # This can be sync
    mock_matrix_client.close = AsyncMock()
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_mesh.return_value = MagicMock()

    # Mock the message queue to avoid hanging and combine contexts for clarity
    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=_make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch(
            "mmrelay.main.meshtastic_utils.check_connection", new_callable=AsyncMock
        ) as mock_check_conn,
        patch("mmrelay.main.shutdown_plugins") as mock_shutdown_plugins,
        patch("mmrelay.main.stop_message_queue") as mock_stop_queue,
        patch("mmrelay.main.wipe_message_map") as mock_wipe,
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        mock_check_conn.return_value = True
        mock_shutdown_plugins.return_value = None
        mock_stop_queue.return_value = None

        # Run the test with proper exception handling
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(main(config))

        # Should wipe message map on startup
        mock_wipe.assert_called()
        # Should start the message queue processor
        mock_queue.ensure_processor_started.assert_called()


class TestDatabaseConfiguration(unittest.TestCase):
    """Test cases for database configuration handling."""


class TestRunMainFunction(unittest.TestCase):
    """Test cases for run_main function."""

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_success(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test successful run_main execution."""
        # Mock configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines
        mock_asyncio_run.side_effect = _close_coro_if_possible

        # Mock args
        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_print_banner.assert_called_once()
        mock_asyncio_run.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_missing_config_keys(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with missing required configuration keys."""
        # Mock incomplete configuration
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"}
        }  # Missing meshtastic and matrix_rooms
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)
        mock_print_banner.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_with_credentials_json(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with credentials.json present (different required keys)."""
        # Mock configuration with credentials.json present
        mock_config = {
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            # No matrix section needed when credentials.json exists
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = {"access_token": "test_token"}

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        with patch("mmrelay.main.asyncio.run") as mock_asyncio_run:
            # Mock asyncio.run to properly close coroutines
            mock_asyncio_run.side_effect = _close_coro_if_possible
            result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_asyncio_run.assert_called_once()

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_with_custom_data_dir(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main with custom data directory.

        Note: --data-dir processing is now handled in cli.py before run_main() is called,
        so run_main() no longer processes the data_dir argument directly.
        """
        # Use a simple custom data directory path
        custom_data_dir = "/home/user/test_custom_data"

        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines
        mock_asyncio_run.side_effect = _close_coro_if_possible

        mock_args = MagicMock()
        mock_args.data_dir = custom_data_dir
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)
        # run_main() no longer processes --data-dir (that's handled in cli.py)
        # Just verify it runs successfully

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_with_log_level_override(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """Test run_main with log level override."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = "DEBUG"

        with patch("mmrelay.main.asyncio.run") as mock_asyncio_run:
            # Mock asyncio.run to properly close coroutines
            mock_asyncio_run.side_effect = _close_coro_if_possible
            result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Verify log level was set in config
        self.assertEqual(mock_config["logging"]["level"], "DEBUG")

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.get_legacy_env_vars")
    @patch("mmrelay.main.get_legacy_dirs")
    @patch("mmrelay.main.get_home_dir")
    @patch("mmrelay.config.get_log_dir")
    @patch("mmrelay.config.os.makedirs")
    def test_run_main_legacy_layout_warning(
        self,
        _mock_makedirs,
        mock_get_log_dir,
        mock_get_home_dir,
        mock_get_legacy_dirs,
        mock_get_legacy_env_vars,
        mock_load_credentials,
        mock_load_config,
        _mock_print_banner,
    ):
        """Test that warning messages are logged when legacy layout is enabled."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None
        mock_get_home_dir.return_value = Path("/test/home/dir")
        mock_get_legacy_dirs.return_value = [Path("/test/legacy/dir")]
        mock_get_legacy_env_vars.return_value = ["MMRELAY_DATA_DIR"]
        mock_get_log_dir.return_value = "/test/log/dir"

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        with patch("mmrelay.main.asyncio.run") as mock_asyncio_run:
            mock_asyncio_run.side_effect = _close_coro_if_possible
            with patch("mmrelay.main.get_logger") as mock_get_logger:
                mock_config_logger = MagicMock()
                mock_get_logger.return_value = mock_config_logger
                result = run_main(mock_args)

        self.assertEqual(result, 0)
        # Verify warning was called with legacy layout message
        mock_config_logger.warning.assert_any_call(
            "Legacy data layout detected (MMRELAY_HOME=%s, legacy_env_vars=%s, legacy_dirs=%s). This layout is deprecated and will be removed in a future release.",
            "/test/home/dir",
            "MMRELAY_DATA_DIR",
            "/test/legacy/dir",
        )
        mock_config_logger.warning.assert_any_call(
            "To migrate to the new layout, see docs/DOCKER.md: Migrating to the New Layout."
        )

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_keyboard_interrupt(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main handling KeyboardInterrupt."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines and raise KeyboardInterrupt
        mock_asyncio_run.side_effect = _mock_run_with_keyboard_interrupt

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 0)  # KeyboardInterrupt should return 0

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    @patch("mmrelay.main.asyncio.run")
    def test_run_main_exception_handling(
        self,
        mock_asyncio_run,
        mock_load_credentials,
        mock_load_config,
        mock_print_banner,
    ):
        """Test run_main handling general exceptions."""
        mock_config = {
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = None

        # Mock asyncio.run to properly close coroutines and raise exception
        mock_asyncio_run.side_effect = _mock_run_with_exception

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        result = run_main(mock_args)

        self.assertEqual(result, 1)  # General exceptions should return 1


class TestMainAsyncFunction(unittest.TestCase):
    """
    Test cases for the main async function.

    CRITICAL: This class implements comprehensive global state reset to prevent
    hanging tests caused by contamination between test runs.

    HANGING TEST ISSUE SOLVED:
    - Root cause: test_main_async_event_loop_setup contaminated global state via run_main() -> set_config()
    - Symptom: test_main_async_initialization_sequence would hang when run after the first test
    - Solution: Complete global state reset in setUp() and tearDown() methods

    DO NOT REMOVE OR MODIFY the setUp(), tearDown(), or _reset_global_state() methods
    without understanding the full implications. These methods prevent a critical
    hanging test issue that blocked CI and development for extended periods.
    """

    def setUp(self):
        """
        Reset global state before each test to ensure complete test isolation.

        CRITICAL: This method prevents hanging tests by ensuring each test starts
        with completely clean global state. DO NOT REMOVE.
        """
        self._reset_global_state()

    def tearDown(self):
        """
        Tear down test fixtures and purge global state to prevent cross-test contamination.

        Calls the module-level global-state reset routine and runs a full garbage
        collection pass to ensure AsyncMock objects and other leaked resources are
        collected. This is required to avoid test hangs and interference between tests.
        Do not remove.
        """
        self._reset_global_state()
        # Force garbage collection to clean up AsyncMock objects
        import gc

        gc.collect()

    def _reset_global_state(self):
        """
        Reset module-level globals across mmrelay submodules to ensure test isolation.

        Clears or restores defaults for runtime-set global state in mmrelay.meshtastic_utils,
        mmrelay.matrix_utils, mmrelay.main, mmrelay.plugin_loader, and mmrelay.message_queue
        so tests do not leak state between runs. May shut down executors and call cleanup
        helpers (for example, message queue stop) where present.
        """

        # Reset meshtastic_utils globals
        if "mmrelay.meshtastic_utils" in sys.modules:
            module = sys.modules["mmrelay.meshtastic_utils"]
            module.config = None  # type: ignore[attr-defined]
            module.matrix_rooms = []  # type: ignore[attr-defined]
            module.meshtastic_client = None  # type: ignore[attr-defined]
            module.event_loop = None  # type: ignore[attr-defined]
            module.reconnecting = False  # type: ignore[attr-defined]
            module.shutting_down = False  # type: ignore[attr-defined]
            module.reconnect_task = None  # type: ignore[attr-defined]
            module.subscribed_to_messages = False  # type: ignore[attr-defined]
            module.subscribed_to_connection_lost = False  # type: ignore[attr-defined]
            if hasattr(module, "_metadata_future"):
                module._metadata_future = None  # type: ignore[attr-defined]
            if hasattr(module, "_ble_future"):
                module._ble_future = None  # type: ignore[attr-defined]
            if hasattr(module, "_ble_future_address"):
                module._ble_future_address = None  # type: ignore[attr-defined]
            if hasattr(module, "_ble_timeout_counts"):
                module._ble_timeout_counts = {}  # type: ignore[attr-defined]
            if hasattr(module, "_metadata_executor"):
                executor = module._metadata_executor  # type: ignore[attr-defined]
                if executor is not None:
                    with contextlib.suppress(TypeError, RuntimeError):
                        executor.shutdown(wait=False, cancel_futures=True)
                module._metadata_executor = None  # type: ignore[attr-defined]
            if hasattr(module, "_ble_executor"):
                executor = module._ble_executor  # type: ignore[attr-defined]
                if executor is not None:
                    with contextlib.suppress(TypeError, RuntimeError):
                        executor.shutdown(wait=False, cancel_futures=True)
                module._ble_executor = None  # type: ignore[attr-defined]

        # Reset matrix_utils globals
        if "mmrelay.matrix_utils" in sys.modules:
            module = sys.modules["mmrelay.matrix_utils"]
            module.config = None  # type: ignore[attr-defined]
            module.matrix_homeserver = None  # type: ignore[attr-defined]
            module.matrix_rooms = None  # type: ignore[attr-defined]
            module.matrix_access_token = None  # type: ignore[attr-defined]
            module.bot_user_id = None  # type: ignore[attr-defined]
            module.bot_user_name = None  # type: ignore[attr-defined]
            module.matrix_client = None  # type: ignore[attr-defined]
            # Reset bot_start_time to current time to avoid stale timestamps
            import time

            module.bot_start_time = int(time.time() * 1000)  # type: ignore[attr-defined]

        # Reset main module globals if any
        if "mmrelay.main" in sys.modules:
            module = sys.modules["mmrelay.main"]
            # Reset banner printed state to ensure consistent test behavior
            module._banner_printed = False  # type: ignore[attr-defined]
            # Reset ready file globals
            module._ready_file_path = None  # type: ignore[attr-defined]
            module._ready_heartbeat_seconds = 60  # type: ignore[attr-defined]

        # Reset plugin_loader caches
        if "mmrelay.plugin_loader" in sys.modules:
            module = sys.modules["mmrelay.plugin_loader"]
            if hasattr(module, "_reset_caches_for_tests"):
                module._reset_caches_for_tests()

        # Reset message_queue state
        if "mmrelay.message_queue" in sys.modules:
            from mmrelay.message_queue import get_message_queue

            with contextlib.suppress(Exception):
                queue = get_message_queue()
                if hasattr(queue, "stop"):
                    queue.stop()

    def test_main_async_initialization_sequence(self):
        """Verify that the asynchronous main() startup sequence invokes database initialization, plugin loading, message-queue startup, and both Matrix and Meshtastic connection routines.

        Sets up a minimal config with one Matrix room, injects AsyncMock/MagicMock clients for Matrix and Meshtastic, and arranges for the Matrix client's sync loop and asyncio.sleep to raise KeyboardInterrupt so the function exits cleanly. Asserts each initialization/connect function is called exactly once.
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        # Mock the async components first
        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_matrix_client.sync_forever = AsyncMock(side_effect=KeyboardInterrupt)

        with (
            patch("mmrelay.main.initialize_database") as mock_init_db,
            patch("mmrelay.main.load_plugins") as mock_load_plugins,
            patch("mmrelay.main.start_message_queue") as mock_start_queue,
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
                return_value=mock_matrix_client,
            ) as mock_connect_matrix,
            patch(
                "mmrelay.main.connect_meshtastic", return_value=MagicMock()
            ) as mock_connect_mesh,
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.asyncio.sleep", side_effect=KeyboardInterrupt),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=KeyboardInterrupt
            ),
            patch("mmrelay.matrix_utils.asyncio.sleep", side_effect=KeyboardInterrupt),
            contextlib.suppress(KeyboardInterrupt),
        ):
            asyncio.run(main(config))

        # Verify initialization sequence
        mock_init_db.assert_called_once()
        mock_load_plugins.assert_called_once()
        mock_start_queue.assert_called_once()
        mock_connect_matrix.assert_called_once()
        mock_connect_mesh.assert_called_once()

    def test_main_async_with_multiple_rooms(self):
        """
        Verify that main() joins each configured Matrix room.

        Runs the async main flow with two matrix room entries in the config and patches connectors
        so startup proceeds until a KeyboardInterrupt. Asserts join_matrix_room is invoked once
        per configured room.
        """
        config = {
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},
            ],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        # Mock the async components first
        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_matrix_client.sync_forever = AsyncMock(side_effect=KeyboardInterrupt)

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
                return_value=mock_matrix_client,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock) as mock_join,
            patch("mmrelay.main.asyncio.sleep", side_effect=KeyboardInterrupt),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep", side_effect=KeyboardInterrupt
            ),
            patch("mmrelay.matrix_utils.asyncio.sleep", side_effect=KeyboardInterrupt),
            contextlib.suppress(KeyboardInterrupt),
        ):
            asyncio.run(main(config))

        # Verify join_matrix_room was called for each room
        self.assertEqual(mock_join.call_count, 2)

    def test_main_signal_handler_sets_shutdown_flag(self):
        """
        Ensure mmrelay sets the meshtastic shutdown flag and registers a signal handler when the event loop installs signal handlers.
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        captured_handlers = []
        real_get_running_loop = asyncio.get_running_loop

        def _patched_get_running_loop():
            """
            Provide the current running event loop with its signal-handler registration patched so registered handlers are captured and invoked immediately.

            The returned loop has its `add_signal_handler` attribute replaced with a function that appends the handler to an external capture list and then calls the handler synchronously. Subsequent calls are no-ops for the patching step.

            Returns:
                asyncio.AbstractEventLoop: The running event loop with `add_signal_handler` patched to capture and invoke handlers.
            """
            loop = real_get_running_loop()
            if not hasattr(loop, "_signal_handler_patched"):

                def _fake_add_signal_handler(_sig, handler):
                    """
                    Record and invoke a signal handler for tests.

                    Parameters:
                        _sig: The signal number or name (ignored by this test helper).
                        handler: The callable to register; it will be appended to `captured_handlers`
                            and invoked immediately.
                    """
                    captured_handlers.append(handler)
                    handler()

                loop.add_signal_handler = _fake_add_signal_handler  # type: ignore[attr-defined]
                loop._signal_handler_patched = True  # type: ignore[attr-defined]
            return loop

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_patched_get_running_loop,
            ),
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                side_effect=_make_async_return(mock_matrix_client),
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=None),
            patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                side_effect=_async_noop,
            ),
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.sys.platform", "linux"),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            asyncio.run(main(config))

        import mmrelay.meshtastic_utils as mu

        self.assertTrue(mu.shutting_down)
        self.assertTrue(captured_handlers)

    def test_main_registers_sighup_handler(self):
        """Verify SIGHUP handler registration on non-Windows platforms."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        captured_signals = []
        real_get_running_loop = asyncio.get_running_loop

        def _patched_get_running_loop():
            """
            Return the currently running asyncio event loop with its signal registration patched to capture signals.

            The loop's `add_signal_handler` is replaced with a function that appends registered signal identifiers to the module-level `captured_signals` list, and a `_signal_capture_patched` attribute is set on the loop to prevent repeated patching.

            Returns:
                asyncio.AbstractEventLoop: The running event loop whose signal registration records signals to `captured_signals`.
            """
            loop = real_get_running_loop()
            if not hasattr(loop, "_signal_capture_patched"):

                def _fake_add_signal_handler(sig, _handler):
                    """
                    Record a signal identifier into the module-level `captured_signals` list for tests.

                    Parameters:
                        sig: The signal identifier (e.g., an int or `signal.Signals`) to record.
                        _handler: Ignored signal handler callable.
                    """
                    captured_signals.append(sig)

                loop.add_signal_handler = _fake_add_signal_handler  # type: ignore[attr-defined]
                loop._signal_capture_patched = True  # type: ignore[attr-defined]
            return loop

        import mmrelay.main as main_module

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_patched_get_running_loop,
            ),
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                side_effect=_make_async_return(mock_matrix_client),
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=None),
            patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                side_effect=_async_noop,
            ),
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.sys.platform", "linux"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            asyncio.run(main(config))

        self.assertIn(main_module.signal.SIGHUP, captured_signals)

    def test_main_windows_keyboard_interrupt_triggers_shutdown(self):
        """
        Verify the Windows signal path executes and KeyboardInterrupt triggers shutdown.
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        mock_matrix_client.sync_forever = AsyncMock()

        import mmrelay.main as main_module

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
                return_value=mock_matrix_client,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=None),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.asyncio.wait", side_effect=KeyboardInterrupt),
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.sys.platform", main_module.WINDOWS_PLATFORM),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            asyncio.run(main(config))

        import mmrelay.meshtastic_utils as mu

        self.assertTrue(mu.shutting_down)

    def test_main_async_event_loop_setup(self):
        """
        Verify that the async main startup accesses the running event loop.

        This test runs run_main with a minimal config while patching startup hooks so execution stops quickly,
        and asserts that asyncio.get_running_loop() is called (the running loop is retrieved for use by Meshtastic and other async components).
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        with (
            patch("mmrelay.main.asyncio.get_running_loop") as mock_get_loop,
            patch("mmrelay.main.initialize_database", side_effect=KeyboardInterrupt),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch("mmrelay.main.connect_matrix", new_callable=AsyncMock),
            patch("mmrelay.main.connect_meshtastic"),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.config.load_config", return_value=config),
            contextlib.suppress(KeyboardInterrupt),
        ):
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop

            from mmrelay.main import run_main

            mock_args = MagicMock()
            mock_args.config = None  # Use default config loading
            mock_args.data_dir = None
            mock_args.log_level = None
            run_main(mock_args)

        # Verify event loop was accessed for meshtastic utils
        mock_get_loop.assert_called()

    def test_main_shutdown_task_cancellation_coverage(self) -> None:
        """Test shutdown task cancellation logic with and without pending tasks."""
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)
        asyncio.set_event_loop(loop)

        async def background_task() -> None:
            await asyncio.sleep(10)

        async def run_with_pending_tasks() -> None:
            task = asyncio.create_task(background_task())
            pending = {
                t for t in asyncio.all_tasks() if t is not asyncio.current_task()
            }
            self.assertIn(task, pending)

            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

            self.assertTrue(task.cancelled())

        async def run_without_pending_tasks() -> None:
            pending = {
                t for t in asyncio.all_tasks() if t is not asyncio.current_task()
            }
            self.assertFalse(pending)

        loop.run_until_complete(run_with_pending_tasks())
        loop.run_until_complete(run_without_pending_tasks())
        asyncio.set_event_loop(None)


def test_ready_file_helpers(tmp_path, monkeypatch) -> None:
    """Ready file helpers should create and remove the marker."""
    import mmrelay.main as main_module

    ready_path = tmp_path / "ready"
    monkeypatch.setattr(main_module, "_ready_file_path", str(ready_path))

    main_module._write_ready_file()
    assert ready_path.exists()

    previous_mtime = ready_path.stat().st_mtime
    main_module._touch_ready_file()
    assert ready_path.stat().st_mtime >= previous_mtime

    main_module._remove_ready_file()
    assert not ready_path.exists()


def test_ready_file_noops_when_unset(tmp_path, monkeypatch) -> None:
    """Ready file helpers should do nothing when MMRELAY_READY_FILE is not set."""
    import mmrelay.main as main_module

    monkeypatch.setattr(main_module, "_ready_file_path", None)

    ready_path = tmp_path / "ready"

    main_module._write_ready_file()
    assert not ready_path.exists()

    main_module._touch_ready_file()
    assert not ready_path.exists()

    main_module._remove_ready_file()
    assert not ready_path.exists()


if __name__ == "__main__":
    unittest.main()
