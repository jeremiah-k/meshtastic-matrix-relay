#!/usr/bin/env python3
"""
Shared helpers and fixtures for the test_main_* domain test files.

Extracted from tests/test_main.py to keep test modules under 2000 lines.
"""

import asyncio
import concurrent.futures
import contextlib
import functools
import inspect
import sys
import time
from concurrent.futures import Future
from typing import Any, Callable

import pytest

from mmrelay.constants.app import DEFAULT_READY_HEARTBEAT_SECONDS
from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from tests.constants import (
    TEST_BOT_USER_ID,
    TEST_MATRIX_HOMESERVER,
    TEST_ROOM_ID_1,
    TEST_ROOM_ID_2,
)
from tests.helpers import (
    reset_meshtastic_utils_globals,
)


def _make_async_return(value: Any) -> Callable[..., Any]:
    """
    Create an async function that always returns provided value.

    Parameters:
        value (Any): Value to be returned by generated coroutine.

    Returns:
        callable: An async function that ignores its arguments and returns `value` when awaited.
    """

    async def _async_return(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _async_return


async def _async_noop(*_args: Any, **_kwargs: Any) -> None:
    """
    Asynchronous no-op that accepts any positional and keyword arguments.

    This coroutine performs no action and ignores all provided arguments.

    Returns:
        None
    """
    return None


async def _async_block_forever(*_args: Any, **_kwargs: Any) -> None:
    """Block indefinitely for shutdown tests."""
    await asyncio.Event().wait()


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


class _TestError(Exception):
    """Custom exception for testing error handling paths."""


def _mock_run_with_exception(coro: Any) -> None:
    """Close coroutine and raise test exception."""
    _close_coro_if_possible(coro)
    raise _TestError("Test error")


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

    async def _async_raise(*_args: Any, **_kwargs: Any) -> None:
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

    async def wait(self) -> bool:
        """
        Return immediately without blocking, simulating an event that is already set.

        This coroutine returns ``True`` immediately to indicate the event is set.
        """
        return True


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
        self.close_future = None
        self.calls: list[Any] = []

    def submit(self, func: Any, *args: Any, **kwargs: Any) -> Future:
        """
        Submit a callable to the controlled executor, execute it synchronously, and return a Future representing its outcome.

        If the callable's name or qualified name contains "_close_meshtastic", the executor applies special close-related behavior: it raises concurrent.futures.TimeoutError when configured with submit_timeout, or returns a pre-created close future when a close_future_factory is provided.

        Parameters:
            func (callable): The function or functools.partial to execute. Additional positional and keyword arguments are forwarded to the callable.

        Returns:
            concurrent.futures.Future: Future containing the callable's result or exception.
            If the callable raises, the exception is captured via set_exception() and can be
            retrieved by calling result() on the returned Future.
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
            timeout_future = concurrent.futures.Future()
            timeout_future.set_exception(concurrent.futures.TimeoutError())
            return timeout_future
        if is_close and self.close_future_factory is not None:
            if self.close_future is None:
                self.close_future = self.close_future_factory()
            return self.close_future

        future = concurrent.futures.Future()
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            future.set_exception(exc)
        else:
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


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_config():
    """Default mock configuration used by TestMain tests."""
    return {
        "matrix": {
            "homeserver": TEST_MATRIX_HOMESERVER,
            "access_token": "test_token",
            "bot_user_id": TEST_BOT_USER_ID,
        },
        "matrix_rooms": [
            {"id": TEST_ROOM_ID_1, "meshtastic_channel": 0},
            {"id": TEST_ROOM_ID_2, "meshtastic_channel": 1},
        ],
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "serial_port": "/dev/ttyUSB0",
            "message_delay": 2.0,
        },
        "database": {"msg_map": {"wipe_on_restart": False}},
    }


@pytest.fixture
def mock_config_edge_cases():
    """Mock configuration used by TestMainFunctionEdgeCases tests."""
    return {
        "matrix": {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
        },
        "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "serial_port": "/dev/ttyUSB0",
        },
    }
