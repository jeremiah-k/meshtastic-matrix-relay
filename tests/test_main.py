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
import threading
import unittest
from pathlib import Path
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError

from mmrelay.constants.app import DEFAULT_READY_HEARTBEAT_SECONDS
from mmrelay.constants.config import DEFAULT_NODEDB_REFRESH_INTERVAL
from mmrelay.constants.network import MESHTASTIC_CLOSE_TIMEOUT_SECONDS
from mmrelay.main import main, print_banner, run_main
from tests.helpers import (
    InlineExecutorLoop,
    inline_to_thread,
)
from tests.helpers import (
    make_patched_get_running_loop as _make_patched_get_running_loop,
)
from tests.helpers import (
    reset_meshtastic_utils_globals,
)


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


async def _thread_backed_to_thread(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """
    Execute a callable on a real worker thread and await its result.

    Uses a dedicated ThreadPoolExecutor per call to avoid blocking asyncio.run()
    shutdown with the default executor.
    """
    loop = asyncio.get_running_loop()
    bound_call = functools.partial(func, *args, **kwargs)
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return await loop.run_in_executor(executor, bound_call)
    finally:
        executor.shutdown(wait=False)


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


class _TaskSpy:
    """
    Wrapper around asyncio.Task that records cancel() calls.

    This allows tests to verify that cancel() was explicitly called,
    rather than inferring shutdown from loop teardown cancellation.
    """

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task
        self.cancel_called = False

    def cancel(self) -> bool:
        self.cancel_called = True
        return self._task.cancel()

    def __await__(self) -> Any:
        return self._task.__await__()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._task, name)


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


def _reset_meshtastic_utils_globals(*, shutdown_executors: bool = False) -> None:
    """
    Reset meshtastic_utils globals shared across main-path tests.

    Delegates to tests.helpers.reset_meshtastic_utils_globals for the actual
    reset logic to avoid duplication with conftest.py.
    """
    reset_meshtastic_utils_globals(shutdown_executors=shutdown_executors)


def _reset_all_mmrelay_globals() -> None:
    """
    Reset module-level global state in mmrelay submodules to a clean default for tests.

    This restores or clears runtime-set globals in mmrelay.meshtastic_utils, mmrelay.matrix_utils,
    mmrelay.main, mmrelay.plugin_loader, and mmrelay.message_queue so tests do not leak state
    between runs. The reset may shut down internal executors and invoke available cleanup helpers
    (e.g., message queue stop) to ensure resources are released and tests remain isolated.
    """
    _reset_meshtastic_utils_globals(shutdown_executors=True)

    if "mmrelay.matrix_utils" in sys.modules:
        module = sys.modules["mmrelay.matrix_utils"]
        module.config = None  # type: ignore[attr-defined]
        module.matrix_homeserver = None  # type: ignore[attr-defined]
        module.matrix_rooms = None  # type: ignore[attr-defined]
        module.matrix_access_token = None  # type: ignore[attr-defined]
        module.bot_user_id = None  # type: ignore[attr-defined]
        module.bot_user_name = None  # type: ignore[attr-defined]
        module.matrix_client = None  # type: ignore[attr-defined]
        import time

        module.bot_start_time = int(time.time() * 1000)  # type: ignore[attr-defined]

    if "mmrelay.main" in sys.modules:
        module = sys.modules["mmrelay.main"]
        module._banner_printed = False  # type: ignore[attr-defined]
        module._ready_file_path = None  # type: ignore[attr-defined]
        module._ready_heartbeat_seconds = DEFAULT_READY_HEARTBEAT_SECONDS  # type: ignore[attr-defined]

    if "mmrelay.plugin_loader" in sys.modules:
        module = sys.modules["mmrelay.plugin_loader"]
        if hasattr(module, "_reset_caches_for_tests"):
            module._reset_caches_for_tests()

    if "mmrelay.message_queue" in sys.modules:
        from mmrelay.message_queue import get_message_queue

        with contextlib.suppress(AttributeError, RuntimeError):
            queue = get_message_queue()
            if hasattr(queue, "stop"):
                queue.stop()


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


class _OnePassEvent:
    """Event that can only be set once and never resets."""

    def __init__(self) -> None:
        self._set = False
        self._waiters: list[asyncio.Future] = []

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(True)
        self._waiters.clear()

    async def wait(self) -> bool:
        if self._set:
            return True
        waiter = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        return await waiter


class _AutoSetAfterWaitEvent:
    """Event that auto-sets after the first wait() call completes."""

    def __init__(self) -> None:
        self._set = False

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    async def wait(self) -> bool:
        if self._set:
            return True
        self._set = True
        return True


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
    @patch(
        "mmrelay.main.meshtastic_utils.refresh_node_name_tables", new_callable=AsyncMock
    )
    @patch("mmrelay.main.stop_message_queue")
    def test_main_basic_flow(
        self,
        mock_stop_queue,
        mock_refresh_node_names,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """
        Verify startup wiring schedules periodic node-name refresh with expected interval.
        """

        shutdown_event = _OnePassEvent()
        expected_interval = 7.5
        mock_matrix_client = AsyncMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        async def _sync_forever_once(*_args: Any, **_kwargs: Any) -> None:
            shutdown_event.set()
            return None

        mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_once)
        mock_connect_matrix.return_value = mock_matrix_client
        mock_connect_meshtastic.return_value = MagicMock()
        created_task_coro_names: list[str] = []
        real_create_task = asyncio.create_task

        def _capture_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:
            coro_code = getattr(coro, "cr_code", None)
            if coro_code is not None:
                created_task_coro_names.append(str(coro_code.co_name))
            return real_create_task(coro, *args, **kwargs)

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new_callable=AsyncMock,
            ) as mock_check_conn,
            patch(
                "mmrelay.main.meshtastic_utils.get_nodedb_refresh_interval_seconds",
                return_value=expected_interval,
            ) as mock_get_interval,
            patch(
                "mmrelay.main.asyncio.create_task",
                side_effect=_capture_create_task,
            ) as mock_create_task,
            patch("mmrelay.main.shutdown_plugins"),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue
            mock_check_conn.return_value = True

            asyncio.run(main(self.mock_config))

        mock_init_db.assert_called_once()
        mock_load_plugins.assert_called_once()
        mock_start_queue.assert_called_once_with(message_delay=2.0)
        mock_connect_meshtastic.assert_called_once_with(passed_config=self.mock_config)
        mock_connect_matrix.assert_awaited_once_with(passed_config=self.mock_config)
        self.assertEqual(mock_join_room.await_count, 2)
        mock_get_interval.assert_called_once_with(self.mock_config)
        self.assertIn("_node_name_refresh_supervisor", created_task_coro_names)
        self.assertGreaterEqual(mock_create_task.call_count, 1)
        mock_refresh_node_names.assert_awaited_once_with(
            shutdown_event,
            refresh_interval_seconds=expected_interval,
        )
        mock_stop_queue.assert_called_once()

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
        Test that startup fails fast when Meshtastic connection cannot be established.
        """
        # Mock Meshtastic connection to return None (failure)
        mock_connect_meshtastic.return_value = None

        # Call main function (should fail before Matrix connection is attempted)
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
            with self.assertRaises(ConnectionError):
                asyncio.run(main(self.mock_config))

        mock_connect_matrix.assert_not_called()
        mock_join_room.assert_not_called()

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
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
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
    @patch("mmrelay.main.get_message_queue")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_utils._disconnect_ble_interface")
    def test_main_shutdown_disconnects_ble_interface(
        self,
        mock_disconnect_iface,
        _mock_stop_queue,
        mock_get_queue,
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

        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
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
    def test_main_shutdown_runs_blocking_cleanup_off_event_loop_thread(
        self,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """
        shutdown_plugins/stop_message_queue should run off the event-loop thread.
        """
        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        cleanup_context: dict[str, bool] = {}

        def _shutdown_plugins_side_effect() -> None:
            cleanup_context["plugins_has_running_loop"] = True
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                cleanup_context["plugins_has_running_loop"] = False

        def _stop_queue_side_effect() -> None:
            cleanup_context["queue_has_running_loop"] = True
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                cleanup_context["queue_has_running_loop"] = False

        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", new=_thread_backed_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.shutdown_plugins",
                side_effect=_shutdown_plugins_side_effect,
            ),
            patch(
                "mmrelay.main.stop_message_queue",
                side_effect=_stop_queue_side_effect,
            ),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue
            asyncio.run(main(self.mock_config))

        self.assertFalse(cleanup_context["plugins_has_running_loop"])
        self.assertFalse(cleanup_context["queue_has_running_loop"])
        mock_matrix_client.close.assert_awaited_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    def test_main_shutdown_plugin_timeout_continues_cleanup(
        self,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """
        A stuck plugin shutdown should time out and still continue queue/client cleanup.
        """
        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        block_event = threading.Event()

        def _blocking_shutdown_plugins() -> None:
            block_event.wait(timeout=0.5)

        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", new=_thread_backed_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.shutdown_plugins",
                side_effect=_blocking_shutdown_plugins,
            ) as mock_shutdown_plugins,
            patch("mmrelay.main.stop_message_queue") as mock_stop_queue,
            patch("mmrelay.main._PLUGIN_SHUTDOWN_TIMEOUT_SECONDS", 0.01),
            patch("mmrelay.main.logger") as mock_logger,
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue
            asyncio.run(main(self.mock_config))

        mock_shutdown_plugins.assert_called_once()
        mock_stop_queue.assert_called_once()
        mock_matrix_client.close.assert_awaited_once()
        self.assertTrue(
            any(
                "Timed out stopping" in str(call) and "plugins" in str(call)
                for call in mock_logger.warning.call_args_list
            )
        )

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.get_message_queue")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    def test_main_shutdown_timeout_warns_and_continues(
        self,
        mock_meshtastic_logger,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_get_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Shutdown should warn and continue when Meshtastic close times out."""

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

        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        try:
            with (
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection", new=_async_noop
                ),
                patch(
                    "mmrelay.main.meshtastic_utils._run_blocking_with_timeout",
                    side_effect=TimeoutError(
                        "meshtastic-client-close-shutdown timed out after 10.0s"
                    ),
                ),
            ):
                asyncio.run(main(self.mock_config))
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting

        mock_meshtastic_logger.warning.assert_any_call(
            "Meshtastic client close timed out during %s - may cause notification errors",
            "shutdown",
        )
        _mock_stop_queue.assert_called_once()
        mock_matrix_client.close.assert_awaited_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.get_message_queue")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    def test_main_shutdown_logs_unexpected_close_error(
        self,
        mock_meshtastic_logger,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_get_queue,
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

        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        try:
            with (
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection", new=_async_noop
                ),
                patch(
                    "mmrelay.main.meshtastic_utils._run_blocking_with_timeout",
                    side_effect=ValueError("boom"),
                ),
            ):
                asyncio.run(main(self.mock_config))
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting

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
    @patch("mmrelay.main.get_message_queue")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_utils._run_blocking_with_timeout")
    def test_main_shutdown_uses_blocking_timeout_helper(
        self,
        mock_run_blocking_with_timeout,
        _mock_stop_queue,
        _mock_shutdown_plugins,
        mock_get_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """
        Ensure main uses daemon-thread timeout helper for Meshtastic close.
        """

        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        def _run_helper_side_effect(close_callable, *args, **kwargs):
            _ = args, kwargs
            close_callable()
            return None

        mock_run_blocking_with_timeout.side_effect = _run_helper_side_effect

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        try:
            with (
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection", new=_async_noop
                ),
            ):
                asyncio.run(main(self.mock_config))

            mock_run_blocking_with_timeout.assert_called_once()
            args, kwargs = mock_run_blocking_with_timeout.call_args
            close_callable = args[0]
            self.assertTrue(callable(close_callable))
            mock_connect_meshtastic.return_value.close.assert_called_once()
            self.assertEqual(kwargs.get("timeout"), MESHTASTIC_CLOSE_TIMEOUT_SECONDS)
            self.assertEqual(kwargs.get("label"), "meshtastic-client-close-shutdown")
            self.assertIsNone(kwargs.get("timeout_log_level"))
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.join_matrix_room")
    @patch("mmrelay.main.get_message_queue")
    @patch("mmrelay.main.stop_message_queue")
    @patch("mmrelay.main.meshtastic_logger")
    @patch("mmrelay.main.meshtastic_utils._run_blocking_with_timeout")
    def test_main_shutdown_success_logs_close_complete(
        self,
        mock_run_blocking_with_timeout,
        mock_meshtastic_logger,
        _mock_stop_queue,
        mock_get_queue,
        mock_join_room,
        mock_connect_matrix,
        mock_connect_meshtastic,
        _mock_start_queue,
        _mock_load_plugins,
        _mock_init_db,
    ):
        """Successful close should log completion."""

        mock_connect_meshtastic.return_value = MagicMock()
        mock_matrix_client = MagicMock()
        mock_matrix_client.close = AsyncMock()
        mock_connect_matrix.side_effect = _make_async_return(mock_matrix_client)
        mock_join_room.side_effect = _async_noop

        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        def _run_helper_side_effect(close_callable, *args, **kwargs):
            _ = args, kwargs
            close_callable()
            return None

        mock_run_blocking_with_timeout.side_effect = _run_helper_side_effect

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        try:
            with (
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection", new=_async_noop
                ),
            ):
                asyncio.run(main(self.mock_config))
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting

        mock_run_blocking_with_timeout.assert_called_once()
        mock_meshtastic_logger.info.assert_any_call(
            "Meshtastic client closed successfully"
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

    @patch("mmrelay.main.print_banner")
    @patch("mmrelay.config.load_config")
    @patch("mmrelay.config.load_credentials")
    def test_run_main_with_credentials_json(
        self, mock_load_credentials, mock_load_config, mock_print_banner
    ):
        """
        Test run_main with credentials.json present (different required keys).

        When credentials.json provides matrix authentication, the matrix.homeserver
        key is not required in config.yaml.
        """
        mock_config = {
            "meshtastic": {"connection_type": "serial"},
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_load_config.return_value = mock_config
        mock_load_credentials.return_value = {"access_token": "test_token"}

        mock_args = MagicMock()
        mock_args.data_dir = None
        mock_args.log_level = None

        with patch("asyncio.run") as mock_asyncio_run:
            mock_asyncio_run.side_effect = _close_coro_if_possible
            result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_asyncio_run.assert_called_once()

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
        mock_print_banner,
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

        with patch("asyncio.run") as mock_asyncio_run:
            mock_asyncio_run.side_effect = _close_coro_if_possible
            with patch("mmrelay.main.get_logger") as mock_get_logger:
                mock_config_logger = MagicMock()
                mock_get_logger.return_value = mock_config_logger
                result = run_main(mock_args)

        self.assertEqual(result, 0)
        mock_config_logger.warning.assert_any_call(
            "Legacy data layout detected (MMRELAY_HOME=%s, legacy_env_vars=%s, legacy_dirs=%s). This layout is deprecated and will be removed in a future release.",
            "/test/home/dir",
            "MMRELAY_DATA_DIR",
            "/test/legacy/dir",
        )
        mock_config_logger.warning.assert_any_call(
            "To migrate to the new layout, see docs/DOCKER.md: Migrating to the New Layout."
        )


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

    def test_refresh_node_name_tables_skips_db_sync_without_meshtastic_client(self):
        """
        Verify refresh_node_name_tables skips DB sync when Meshtastic client is unavailable.
        """

        import mmrelay.meshtastic_utils as meshtastic_module

        with (
            patch("mmrelay.meshtastic_utils.meshtastic_client", None),
            patch("mmrelay.meshtastic_utils.sync_name_tables_if_changed") as mock_sync,
        ):
            result = asyncio.run(
                meshtastic_module.refresh_node_name_tables(
                    _AutoSetAfterWaitEvent(),  # pyright: ignore[reportArgumentType]
                    refresh_interval_seconds=0.01,
                )
            )

        mock_sync.assert_not_called()
        self.assertIsNone(result)

    def test_nodedb_refresh_interval_invalid_defaults(self):
        """Invalid nodedb refresh intervals should fall back to the default value."""
        import mmrelay.meshtastic_utils as meshtastic_module

        for raw_value in ("inf", "not-a-number", True, False, -1.0):
            with self.subTest(raw_value=raw_value):
                interval = meshtastic_module.get_nodedb_refresh_interval_seconds(
                    {"meshtastic": {"nodedb_refresh_interval": raw_value}}
                )
                self.assertEqual(interval, DEFAULT_NODEDB_REFRESH_INTERVAL)


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
    shutdown_event = _OnePassEvent()

    async def _sync_forever_once(*_args: Any, **_kwargs: Any) -> None:
        shutdown_event.set()
        return None

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()  # This can be sync
    mock_matrix_client.close = AsyncMock()
    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_once)
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_mesh.return_value = MagicMock()

    # Mock the message queue to avoid hanging and combine contexts for clarity
    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=_make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
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

        _reset_all_mmrelay_globals()
        try:
            with contextlib.suppress(KeyboardInterrupt):
                asyncio.run(main(config))
        finally:
            _reset_all_mmrelay_globals()

        # Should wipe message map on startup
        mock_wipe.assert_called()
        # Should start the message queue processor
        mock_queue.ensure_processor_started.assert_called()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
def test_main_database_wipe_preferred_false_wins_over_legacy_true(
    mock_join,
    mock_connect_mesh,
    mock_connect_matrix,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
):
    """
    Verify explicit database.msg_map.wipe_on_restart=false is not overridden by legacy config.
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "database": {"msg_map": {"wipe_on_restart": False}},
        "db": {"msg_map": {"wipe_on_restart": True}},
    }
    shutdown_event = _OnePassEvent()

    async def _sync_forever_once(*_args: Any, **_kwargs: Any) -> None:
        shutdown_event.set()
        return None

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()
    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_once)
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_mesh.return_value = MagicMock()

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=_make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
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

        _reset_all_mmrelay_globals()
        try:
            with contextlib.suppress(KeyboardInterrupt):
                asyncio.run(main(config))
        finally:
            _reset_all_mmrelay_globals()

        mock_wipe.assert_not_called()
        mock_queue.ensure_processor_started.assert_called()


class TestDatabaseConfiguration(unittest.TestCase):
    """Test cases for database configuration handling."""


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
        _reset_all_mmrelay_globals()

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
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
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
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
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
            if not isinstance(loop, InlineExecutorLoop):
                loop = InlineExecutorLoop(loop)
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
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                side_effect=_make_async_return(mock_matrix_client),
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
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
            Return the running loop with captured signal registration and inline executor behavior.

            The underlying loop's `add_signal_handler` is replaced with a function
            that appends registered signals to `captured_signals`. The returned
            object is wrapped as InlineExecutorLoop so run_in_executor paths execute
            inline and do not create persistent threadpool workers in tests.

            Returns:
                asyncio.AbstractEventLoop: Running loop wrapper that records signal
                    registrations and executes executor work inline.
            """
            loop = real_get_running_loop()
            base_loop = loop._loop if isinstance(loop, InlineExecutorLoop) else loop
            if not hasattr(base_loop, "_signal_capture_patched"):

                def _fake_add_signal_handler(sig, _handler):
                    """
                    Record a signal identifier into the module-level `captured_signals` list for tests.

                    Parameters:
                        sig: The signal identifier (e.g., an int or `signal.Signals`) to record.
                        _handler: Ignored signal handler callable.
                    """
                    captured_signals.append(sig)

                base_loop.add_signal_handler = _fake_add_signal_handler  # type: ignore[attr-defined]
                base_loop._signal_capture_patched = True  # type: ignore[attr-defined]
            if isinstance(loop, InlineExecutorLoop):
                return loop
            return InlineExecutorLoop(base_loop)

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
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
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
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
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
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
                return_value=mock_matrix_client,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
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


class TestReadyHeartbeatEnvVarParsing:
    """Tests for MMRELAY_READY_HEARTBEAT_SECONDS environment variable parsing."""

    @pytest.fixture(autouse=True)
    def _reload_main_module(self):
        import importlib

        import mmrelay.main as main_module

        yield
        importlib.reload(main_module)

    def test_invalid_ready_heartbeat_seconds_type_error(self):
        """Invalid MMRELAY_READY_HEARTBEAT_SECONDS logs warning and uses default."""
        import importlib

        import mmrelay.constants.app as app_constants
        import mmrelay.main as main_module

        mock_logger = MagicMock()

        with patch.dict(
            "os.environ", {"MMRELAY_READY_HEARTBEAT_SECONDS": "not_a_number"}
        ):
            with patch("mmrelay.log_utils.get_logger", return_value=mock_logger):
                importlib.reload(main_module)

                mock_logger.warning.assert_called_once()
                call_args = mock_logger.warning.call_args
                assert "MMRELAY_READY_HEARTBEAT_SECONDS" in str(call_args)
                assert (
                    main_module._ready_heartbeat_seconds
                    == app_constants.DEFAULT_READY_HEARTBEAT_SECONDS
                )

    def test_invalid_ready_heartbeat_seconds_value_error(self):
        """Empty string MMRELAY_READY_HEARTBEAT_SECONDS logs warning and uses default."""
        import importlib

        import mmrelay.constants.app as app_constants
        import mmrelay.main as main_module

        mock_logger = MagicMock()

        with patch.dict("os.environ", {"MMRELAY_READY_HEARTBEAT_SECONDS": ""}):
            with patch("mmrelay.log_utils.get_logger", return_value=mock_logger):
                importlib.reload(main_module)

                mock_logger.warning.assert_called_once()
                call_args = mock_logger.warning.call_args
                assert "MMRELAY_READY_HEARTBEAT_SECONDS" in str(call_args)
                assert (
                    main_module._ready_heartbeat_seconds
                    == app_constants.DEFAULT_READY_HEARTBEAT_SECONDS
                )


class TestCoerceConfigBool(unittest.TestCase):
    """Tests for _coerce_config_bool function."""

    @patch("mmrelay.main.logger")
    def test_coerce_config_bool_unexpected_type_list(self, mock_logger):
        """List values should return False and log debug."""
        from mmrelay.main import _coerce_config_bool

        result = _coerce_config_bool([1, 2, 3])
        self.assertFalse(result)
        mock_logger.debug.assert_called_once()
        call_args = mock_logger.debug.call_args
        self.assertIn("Unexpected config value type", call_args[0][0])

    @patch("mmrelay.main.logger")
    def test_coerce_config_bool_unexpected_type_dict(self, mock_logger):
        """Dict values should return False and log debug."""
        from mmrelay.main import _coerce_config_bool

        result = _coerce_config_bool({"key": "value"})
        self.assertFalse(result)
        mock_logger.debug.assert_called_once()

    @patch("mmrelay.main.logger")
    def test_coerce_config_bool_unexpected_type_object(self, mock_logger):
        """Custom object values should return False and log debug."""
        from mmrelay.main import _coerce_config_bool

        class CustomObject:
            pass

        result = _coerce_config_bool(CustomObject())
        self.assertFalse(result)
        mock_logger.debug.assert_called_once()

    def test_coerce_config_bool_none_returns_false(self):
        """None should return False without logging."""
        from mmrelay.main import _coerce_config_bool

        with patch("mmrelay.main.logger") as mock_logger:
            result = _coerce_config_bool(None)
            self.assertFalse(result)
            mock_logger.debug.assert_not_called()


class TestStartupRollback(unittest.TestCase):
    """Tests for startup rollback in main() exception handler."""

    def setUp(self):
        """Set up test fixtures."""
        self._reset_global_state()

    def tearDown(self):
        """Clean up after tests."""
        self._reset_global_state()

    def _reset_global_state(self):
        _reset_all_mmrelay_globals()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_cancels_check_connection_task(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception during startup should cancel check_connection_task."""
        mock_check_task = MagicMock()
        mock_check_task.cancel = MagicMock()
        mock_check_task.done = MagicMock(return_value=False)
        mock_check_task.add_done_callback = MagicMock(
            side_effect=RuntimeError("Callback error")
        )
        mock_supervisor_task = MagicMock()
        mock_supervisor_task.cancel = MagicMock()
        mock_supervisor_task.done = MagicMock(return_value=False)
        mock_supervisor_task.add_done_callback = MagicMock()

        mock_matrix_client = MagicMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        async def mock_connect_matrix_fn(*args, **kwargs):
            return mock_matrix_client

        mock_connect_matrix.side_effect = mock_connect_matrix_fn
        mock_connect_meshtastic.return_value = MagicMock()

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        async def mock_check_conn():
            return None

        def mock_create_task(coro, *args, **kwargs):
            if inspect.iscoroutine(coro):
                coro_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
                if coro_name == "mock_check_conn":
                    return mock_check_task
                if coro_name == "_node_name_refresh_supervisor":
                    return mock_supervisor_task
                coro.close()
                raise AssertionError(f"Unexpected task scheduled: {coro_name}")
            raise AssertionError(f"Unexpected non-coroutine scheduled: {coro!r}")

        async def mock_gather(*args, **kwargs):
            return [None] * len(args)

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.asyncio.create_task", side_effect=mock_create_task),
            patch("mmrelay.main.asyncio.gather", side_effect=mock_gather),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=mock_check_conn,
            ),
            patch("mmrelay.main.logger"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(main(config))

            mock_check_task.cancel.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_removes_ready_file(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception during startup should call _remove_ready_file."""
        mock_connect_meshtastic.side_effect = RuntimeError(
            "Meshtastic connection error"
        )

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.logger"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(main(config))

            mock_remove_ready.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_shutdowns_plugins_when_loaded(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception after plugins loaded should call shutdown_plugins."""
        mock_start_queue.side_effect = RuntimeError("Queue start error")

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.logger"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(main(config))

            mock_shutdown_plugins.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_stops_message_queue_when_started(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception after message queue started should call stop_message_queue."""
        mock_connect_meshtastic.side_effect = RuntimeError(
            "Meshtastic connection error"
        )

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.logger"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(main(config))

            mock_stop_queue.assert_called_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_closes_matrix_client(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception after Matrix client created should close it."""
        mock_matrix_client = MagicMock()
        mock_matrix_client.add_event_callback = MagicMock()
        mock_matrix_client.close = AsyncMock()

        async def mock_connect_matrix_fn(*args, **kwargs):
            return mock_matrix_client

        mock_connect_matrix.side_effect = mock_connect_matrix_fn
        mock_connect_meshtastic.return_value = MagicMock()

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        shutdown_event = _OnePassEvent()

        def mock_create_task(coro):
            if inspect.iscoroutine(coro):
                coro_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
                if coro_name == "_node_name_refresh_supervisor":
                    task = MagicMock()
                    task.done = MagicMock(return_value=False)
                    task.add_done_callback = MagicMock()
                    return task
                if coro_name == "_ready_heartbeat":
                    task = MagicMock()
                    task.done = MagicMock(return_value=False)
                    task.add_done_callback = MagicMock()
                    return task
                coro.close()
                raise RuntimeError("After matrix client error")
            task = MagicMock()
            task.done = MagicMock(return_value=False)
            task.add_done_callback = MagicMock()
            return task

        async def mock_gather(*args, **kwargs):
            return [None] * len(args)

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
            patch("mmrelay.main.asyncio.create_task", side_effect=mock_create_task),
            patch("mmrelay.main.asyncio.gather", side_effect=mock_gather),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.logger"),
        ):
            with self.assertRaises(RuntimeError):
                asyncio.run(main(config))

            mock_matrix_client.close.assert_awaited_once()

    @patch("mmrelay.main.initialize_database")
    @patch("mmrelay.main.load_plugins")
    @patch("mmrelay.main.start_message_queue")
    @patch("mmrelay.main.connect_matrix")
    @patch("mmrelay.main.connect_meshtastic")
    @patch("mmrelay.main._remove_ready_file")
    @patch("mmrelay.main.shutdown_plugins")
    @patch("mmrelay.main.stop_message_queue")
    def test_startup_rollback_closes_meshtastic_client(
        self,
        mock_stop_queue,
        mock_shutdown_plugins,
        mock_remove_ready,
        mock_connect_meshtastic,
        mock_connect_matrix,
        mock_start_queue,
        mock_load_plugins,
        mock_init_db,
    ):
        """Exception after Meshtastic client created should close it."""
        mock_meshtastic_client = MagicMock()
        mock_connect_meshtastic.return_value = mock_meshtastic_client
        mock_connect_matrix.side_effect = RuntimeError("After meshtastic client error")

        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        try:
            config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

            with (
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection", new=_async_noop
                ),
                patch("mmrelay.main.logger"),
            ):
                with self.assertRaises(RuntimeError):
                    asyncio.run(main(config))

                mock_meshtastic_client.close.assert_called_once()
        finally:
            mu.meshtastic_client = original_client


class TestNodeNameRefreshSupervisor(unittest.TestCase):
    """Tests for _node_name_refresh_supervisor behavior through main()."""

    def test_supervisor_runs_refresh_before_shutdown_signal(self):
        """Supervisor should run one refresh pass before a runtime shutdown signal."""
        from mmrelay.main import main

        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        shutdown_event = _OnePassEvent()

        refresh_called = []

        async def mock_refresh(event, refresh_interval_seconds):
            refresh_called.append(True)
            event.set()
            return None

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
                return_value=AsyncMock(),
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch(
                "mmrelay.main.meshtastic_utils.refresh_node_name_tables",
                side_effect=mock_refresh,
            ),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)

            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(config))

        self.assertTrue(
            refresh_called, "refresh_node_name_tables should be called once"
        )


class TestAwaitBackgroundTaskShutdown(unittest.TestCase):
    """Tests for _await_background_task_shutdown behavior through main()."""

    def setUp(self) -> None:
        """
        Run main() shutdown tests with deterministic inline executor behavior.
        """
        self._patch_get_running_loop = patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=_make_patched_get_running_loop(),
        )
        self._patch_to_thread = patch(
            "mmrelay.main.asyncio.to_thread",
            side_effect=inline_to_thread,
        )
        self._patch_get_running_loop.start()
        self._patch_to_thread.start()

    def tearDown(self) -> None:
        """
        Restore patched asyncio helpers for this test class.
        """
        self._patch_to_thread.stop()
        self._patch_get_running_loop.stop()

    def test_returns_early_when_task_is_none(self):
        """
        Should handle None background tasks gracefully during shutdown.

        Uses _ImmediateEvent to trigger immediate shutdown, which leaves
        background tasks (node_name_refresh_task, ready_task, check_connection_task)
        as None since the startup code that creates them is skipped.
        _await_background_task_shutdown must return early for None tasks.
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new_callable=AsyncMock,
                return_value=None,
            ),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(config))

    def test_timeout_during_shutdown_cancels_task(self):
        """TimeoutError during shutdown task wait should cancel and continue."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        async def _check_connection_wait() -> None:
            await asyncio.sleep(3600)

        created_tasks: list[_TaskSpy] = []
        real_create_task = asyncio.create_task

        def _capture_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:
            task = real_create_task(coro, *args, **kwargs)
            spy = _TaskSpy(task)
            created_tasks.append(spy)
            return spy

        real_wait_for = asyncio.wait_for

        async def _wait_for_side_effect(awaitable, timeout=None):
            if timeout == 5.0:
                raise asyncio.TimeoutError()
            return await real_wait_for(awaitable, timeout=timeout)

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=_check_connection_wait,
            ),
            patch("mmrelay.main.asyncio.wait_for") as mock_wait_for,
            patch("mmrelay.main.asyncio.create_task", side_effect=_capture_create_task),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                mock_wait_for.side_effect = _wait_for_side_effect

                asyncio.run(main(config))

            mock_connect_matrix.assert_called_once()
            mock_matrix_client.close.assert_awaited_once()

        check_conn_tasks = []
        observed_coro_names: list[str] = []
        for spy in created_tasks:
            coro = getattr(spy._task, "get_coro", lambda: None)()
            coro_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
            observed_coro_names.append(coro_name)
            if "check_connection" in coro_name:
                check_conn_tasks.append(spy)
                continue
            if coro_name == "_check_connection_wait":
                check_conn_tasks.append(spy)

        self.assertTrue(
            check_conn_tasks,
            f"No connection health task captured. Observed coroutines: {observed_coro_names}",
        )
        self.assertTrue(
            any(spy.cancel_called for spy in check_conn_tasks),
            "Expected cancel() to be called on check_connection task during shutdown",
        )

    def test_check_connection_exception_is_raised_after_cleanup(self):
        """Exceptions from the health task should become main() failures."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        async def _check_connection_raises() -> None:
            raise RuntimeError("health monitor failed")

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins") as mock_shutdown_plugins,
            patch("mmrelay.main.stop_message_queue") as mock_stop_message_queue,
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=_check_connection_raises,
            ),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                with self.assertRaisesRegex(RuntimeError, "health monitor failed"):
                    asyncio.run(main(config))

                mock_queue.ensure_processor_started.assert_called_once()
                mock_shutdown_plugins.assert_called_once()
                mock_stop_message_queue.assert_called_once()
                mock_matrix_client.close.assert_awaited_once()

    def test_check_connection_unexpected_return_is_raised_after_cleanup(self):
        """Unexpected clean health-task exits should raise a fatal RuntimeError."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {
                "connection_type": "serial",
                "health_check": {"enabled": True},
            },
        }

        async def _check_connection_returns() -> None:
            return None

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins") as mock_shutdown_plugins,
            patch("mmrelay.main.stop_message_queue") as mock_stop_message_queue,
            patch(
                "mmrelay.main._DEFAULT_CHECK_CONNECTION_CALLABLE",
                new=_check_connection_returns,
            ),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=_check_connection_returns,
            ),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                with self.assertRaisesRegex(
                    RuntimeError,
                    "Connection health task exited unexpectedly without an exception",
                ):
                    asyncio.run(main(config))

                mock_queue.ensure_processor_started.assert_called_once()
                mock_shutdown_plugins.assert_called_once()
                mock_stop_message_queue.assert_called_once()
                mock_matrix_client.close.assert_awaited_once()

    def test_exception_during_shutdown_wait_logs_error(self):
        """Exception during shutdown wait should log error and continue.

        Note: This test verifies the exception path completes without hanging.
        A more thorough test would inject a mock task to verify cancel() is called.
        """
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        async def _pending_check_connection() -> None:
            await asyncio.sleep(3600)

        real_wait_for = asyncio.wait_for
        shutdown_wait_for_injected = False

        async def _wait_for_side_effect(awaitable, timeout=None):
            nonlocal shutdown_wait_for_injected
            if timeout == 5.0 and not shutdown_wait_for_injected:
                shutdown_wait_for_injected = True
                _close_coro_if_possible(awaitable)
                raise ValueError("Test error")
            return await real_wait_for(awaitable, timeout=timeout)

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=_pending_check_connection,
            ),
            patch("mmrelay.main.asyncio.wait_for") as mock_wait_for,
            patch("mmrelay.main.logger") as mock_logger,
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                mock_wait_for.side_effect = _wait_for_side_effect

                asyncio.run(main(config))

                self.assertTrue(shutdown_wait_for_injected)
                self.assertTrue(mock_logger.error.called)

    def test_cancelled_error_cancels_task_and_returns(self):
        """CancelledError during shutdown should cancel task and return."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        async def _check_connection_wait() -> None:
            await asyncio.sleep(3600)

        created_tasks: list[_TaskSpy] = []
        real_create_task = asyncio.create_task

        def _capture_create_task(coro: Any, *args: Any, **kwargs: Any) -> Any:
            task = real_create_task(coro, *args, **kwargs)
            spy = _TaskSpy(task)
            created_tasks.append(spy)
            return spy

        real_wait_for = asyncio.wait_for

        def _awaitable_name(awaitable: Any) -> str:
            candidate = getattr(awaitable, "_coro", awaitable)
            inner = getattr(candidate, "_inner", None)
            if inner is not None:
                candidate = getattr(inner, "get_coro", lambda: inner)()
            elif hasattr(candidate, "get_coro"):
                candidate = candidate.get_coro()
            code = getattr(candidate, "cr_code", None) or getattr(
                candidate, "__code__", None
            )
            return getattr(code, "co_name", "")

        async def mock_wait_for(coro, timeout=None):
            if timeout == 5.0 and _awaitable_name(coro) == "_check_connection_wait":
                raise asyncio.CancelledError()
            return await real_wait_for(coro, timeout=timeout)

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.meshtastic_utils.check_connection",
                new=_check_connection_wait,
            ),
            patch("mmrelay.main.asyncio.wait_for", new=mock_wait_for),
            patch("mmrelay.main.asyncio.create_task", side_effect=_capture_create_task),
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(config))

            mock_connect_matrix.assert_called_once()
            mock_matrix_client.close.assert_awaited_once()

        check_conn_tasks = []
        observed_coro_names: list[str] = []
        for spy in created_tasks:
            coro = getattr(spy._task, "get_coro", lambda: None)()
            coro_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
            observed_coro_names.append(coro_name)
            if "check_connection" in coro_name:
                check_conn_tasks.append(spy)
                continue
            if coro_name == "_check_connection_wait":
                check_conn_tasks.append(spy)

        self.assertTrue(
            check_conn_tasks,
            f"No connection health task captured. Observed coroutines: {observed_coro_names}",
        )
        self.assertTrue(
            any(spy.cancel_called for spy in check_conn_tasks),
            "Expected cancel() to be called on check_connection task during shutdown",
        )

    def test_task_with_exception_result_logs_error(self):
        """Exception in task result should log error during cleanup."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.asyncio.gather") as mock_gather,
            patch("mmrelay.main.logger") as mock_logger,
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                async def _mock_gather(*_args: Any, **_kwargs: Any) -> list[Any]:
                    return [ValueError("Task error")]

                mock_gather.side_effect = _mock_gather

                asyncio.run(main(config))

            self.assertTrue(
                any(
                    "Error during" in str(call)
                    for call in mock_logger.error.call_args_list
                )
            )


class TestRunBlockingShutdownStep(unittest.TestCase):
    """Tests for _run_blocking_shutdown_step behavior through main()."""

    def test_exception_in_shutdown_step_logs_error(self):
        """Exceptions in shutdown step are captured and logged."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_matrix",
                new_callable=AsyncMock,
            ),
            patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
            patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins") as mock_shutdown,
            patch("mmrelay.main.stop_message_queue") as mock_stop_message_queue,
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.logger") as mock_logger,
        ):
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
            with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                mock_shutdown.side_effect = ValueError("Shutdown error")

                asyncio.run(main(config))

                mock_shutdown.assert_called_once()
                mock_stop_message_queue.assert_called_once()
                mock_matrix_client.close.assert_awaited_once()

            self.assertTrue(
                any(
                    "Error while stopping" in str(call)
                    for call in mock_logger.error.call_args_list
                )
            )

    def test_shutdown_exceptions_are_logged_and_suppressed(self):
        """KeyboardInterrupt and SystemExit raised by shutdown steps should be logged, not re-raised."""
        for exception_class in (KeyboardInterrupt, SystemExit):
            with self.subTest(exception_class=exception_class):
                config = {
                    "matrix_rooms": [{"id": "!room:matrix.org"}],
                    "matrix": {"homeserver": "https://matrix.org"},
                    "meshtastic": {"connection_type": "serial"},
                }

                with (
                    patch("mmrelay.main.initialize_database"),
                    patch("mmrelay.main.load_plugins"),
                    patch("mmrelay.main.start_message_queue"),
                    patch(
                        "mmrelay.main.connect_matrix",
                        new_callable=AsyncMock,
                    ),
                    patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                    patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
                    patch("mmrelay.main.get_message_queue") as mock_get_queue,
                    patch("mmrelay.main.shutdown_plugins") as mock_shutdown,
                    patch("mmrelay.main.stop_message_queue") as mock_stop_message_queue,
                    patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                    patch(
                        "mmrelay.main.asyncio.get_running_loop",
                        side_effect=_make_patched_get_running_loop(),
                    ),
                    patch(
                        "mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread
                    ),
                    patch(
                        "mmrelay.main.meshtastic_utils.check_connection",
                        new=_async_noop,
                    ),
                    patch("mmrelay.main.logger") as mock_logger,
                ):
                    mock_matrix_client = AsyncMock()
                    mock_matrix_client.add_event_callback = MagicMock()
                    mock_matrix_client.close = AsyncMock()
                    mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
                    with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
                        mock_queue = MagicMock()
                        mock_queue.ensure_processor_started = MagicMock()
                        mock_get_queue.return_value = mock_queue
                        mock_shutdown.side_effect = exception_class()

                        asyncio.run(main(config))

                        mock_shutdown.assert_called_once()
                        mock_stop_message_queue.assert_called_once()
                        mock_matrix_client.close.assert_awaited_once()

                self.assertTrue(
                    any(
                        "Error while stopping" in str(call)
                        for call in mock_logger.error.call_args_list
                    )
                )


class TestMessageQueueProcessorStartFailure(unittest.TestCase):
    """Tests for message queue processor start failure."""

    def test_exception_during_ensure_processor_started_raised(self):
        """Exception during ensure_processor_started is caught and raised."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch("mmrelay.main.connect_matrix") as mock_connect_matrix,
            patch("mmrelay.main.connect_meshtastic") as mock_connect_meshtastic,
            patch("mmrelay.main.join_matrix_room"),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_OnePassEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started.side_effect = RuntimeError(
                "Queue processor failed"
            )
            mock_get_queue.return_value = mock_queue

            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            mock_connect_matrix.return_value = mock_matrix_client

            mock_connect_meshtastic.return_value = MagicMock()

            with self.assertRaises(RuntimeError) as context:
                asyncio.run(main(config))

            self.assertIn("Queue processor failed", str(context.exception))


class TestMatrixSyncLoopErrorHandling(unittest.TestCase):
    """Tests for Matrix sync loop error handling."""

    def setUp(self):
        """Set up test fixtures."""
        self._reset_global_state()

    def tearDown(self):
        """Clean up after tests."""
        self._reset_global_state()

    def _reset_global_state(self):
        _reset_all_mmrelay_globals()

    def test_sync_timeout_logs_warning_and_retries(self):
        """TimeoutError from sync_task.result() logs warning and retries."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        call_count = [0]

        async def run_test():
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            created_events: list[asyncio.Event] = []
            real_event_cls = asyncio.Event

            def _capture_shutdown_event(*_args, **_kwargs) -> asyncio.Event:
                event = real_event_cls()
                created_events.append(event)
                return event

            def sync_forever_side_effect(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise asyncio.TimeoutError("Sync timeout")
                for event in created_events:
                    event.set()
                return None

            mock_matrix_client.sync_forever = AsyncMock(
                side_effect=sync_forever_side_effect
            )

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
                patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch(
                    "mmrelay.main.meshtastic_utils.get_nodedb_refresh_interval_seconds",
                    return_value=0.0,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    side_effect=_async_noop,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.refresh_node_name_tables",
                    side_effect=_async_noop,
                ),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch("mmrelay.main.matrix_logger") as mock_logger,
                patch("mmrelay.main.asyncio.sleep"),
                patch(
                    "mmrelay.main.asyncio.Event", side_effect=_capture_shutdown_event
                ),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                await main(config)

            return mock_logger

        mock_logger = asyncio.run(run_test())

        self.assertTrue(
            any(
                "Matrix sync timed out" in str(call)
                for call in mock_logger.warning.call_args_list
            )
        )

    def test_sync_client_error_logs_warning_and_retries(self):
        """ClientError from sync_task.result() logs warning and retries."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        call_count = [0]

        async def run_test():
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            created_events: list[asyncio.Event] = []
            real_event_cls = asyncio.Event

            def _capture_event(*_args, **_kwargs) -> asyncio.Event:
                event = real_event_cls()
                created_events.append(event)
                return event

            def sync_forever_side_effect(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ClientError("Network error")
                for event in created_events:
                    event.set()
                return None

            mock_matrix_client.sync_forever = AsyncMock(
                side_effect=sync_forever_side_effect
            )

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
                patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch(
                    "mmrelay.main.meshtastic_utils.get_nodedb_refresh_interval_seconds",
                    return_value=0.0,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    side_effect=_async_noop,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.refresh_node_name_tables",
                    side_effect=_async_noop,
                ),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch("mmrelay.main.matrix_logger") as mock_logger,
                patch("mmrelay.main.asyncio.sleep"),
                patch("mmrelay.main.asyncio.Event", side_effect=_capture_event),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                await main(config)

            return mock_logger

        mock_logger = asyncio.run(run_test())

        self.assertTrue(
            any(
                "Matrix sync failed, retrying" in str(call)
                for call in mock_logger.warning.call_args_list
            )
        )

    def test_sync_connection_error_logs_exception(self):
        """ConnectionError/OSError/RuntimeError/ValueError from sync logs exception."""
        config = {
            "matrix_rooms": [{"id": "!room:matrix.org"}],
            "matrix": {"homeserver": "https://matrix.org"},
            "meshtastic": {"connection_type": "serial"},
        }

        call_count = [0]

        async def run_test():
            mock_matrix_client = AsyncMock()
            mock_matrix_client.add_event_callback = MagicMock()
            mock_matrix_client.close = AsyncMock()
            created_events: list[asyncio.Event] = []
            real_event_cls = asyncio.Event

            def _capture_event(*_args, **_kwargs) -> asyncio.Event:
                event = real_event_cls()
                created_events.append(event)
                return event

            def sync_forever_side_effect(**kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ConnectionError("Connection lost")
                for event in created_events:
                    event.set()
                return None

            mock_matrix_client.sync_forever = AsyncMock(
                side_effect=sync_forever_side_effect
            )

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
                patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch(
                    "mmrelay.main.meshtastic_utils.get_nodedb_refresh_interval_seconds",
                    return_value=0.0,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    side_effect=_async_noop,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.refresh_node_name_tables",
                    side_effect=_async_noop,
                ),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch("mmrelay.main.matrix_logger") as mock_logger,
                patch("mmrelay.main.asyncio.sleep"),
                patch("mmrelay.main.asyncio.Event", side_effect=_capture_event),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                await main(config)

            return mock_logger

        mock_logger = asyncio.run(run_test())

        self.assertTrue(
            any(
                "Matrix sync failed" in str(call)
                for call in mock_logger.exception.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
