#!/usr/bin/env python3
"""
Test suite for uncovered code paths in main.py shutdown logic.

Covers:
- _await_background_task_shutdown error paths (lines 898-922)
- Shutdown with reconnect_task_future set (lines 1115-1121)
"""

import asyncio
import unittest
from collections.abc import Generator
from unittest.mock import MagicMock, patch

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests.constants import (
    TEST_BOT_USER_ID,
    TEST_MATRIX_HOMESERVER,
    TEST_ROOM_ID_1,
    TEST_ROOM_ID_2,
)
from tests.helpers import (
    inline_to_thread,
    make_patched_get_running_loop,
    reset_meshtastic_utils_globals,
)

_make_patched_get_running_loop = make_patched_get_running_loop


def _make_async_return(value: object) -> MagicMock:
    """
    Create an async function that ignores its arguments and always returns the given value.

    Parameters:
        value: The value the created async function will return when awaited.

    Returns:
        async_callable (callable): An async function that accepts any arguments and returns `value` when awaited.
    """

    async def _async_return(*_args: object, **_kwargs: object) -> object:
        return value

    return _async_return


class _ImmediateAwaitable:
    """Lightweight awaitable that resolves immediately without creating coroutines."""

    def __init__(self, value: object = None) -> None:
        """
        Initialize the awaitable that immediately returns a stored value when awaited.

        Parameters:
            value (Any): The value to be returned by awaiting this object. Defaults to None.
        """
        self._value = value

    def __await__(self) -> Generator[None, None, object]:
        """
        Provide the generator required by the await protocol and immediately yield the wrapped value.

        This implements the generator-based __await__ protocol so that awaiting the object
        returns the stored value without any suspension.

        Returns:
            The wrapped value stored in the instance.
        """
        if False:  # pragma: no cover
            yield
        return self._value


def _make_matrix_client_with_awaitable_close() -> MagicMock:
    """
    Create a MagicMock matrix client whose close() returns an awaitable that completes immediately to avoid coroutine warnings.

    Returns:
        MagicMock: A mocked matrix client with `close()` returning an awaitable that yields `None`.
    """
    client = MagicMock()
    client.close = MagicMock(return_value=_ImmediateAwaitable(None))
    return client


async def _async_noop(*_args: object, **_kwargs: object) -> None:
    """
    Immediately completes without performing any action.

    Returns:
        None: Always returns None.
    """
    return None


class _ImmediateEvent:
    def __init__(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set

    def set(self) -> None:
        self._set = True

    async def wait(self) -> None:
        return None


def _reset_all_mmrelay_globals() -> None:
    import contextlib
    import sys

    reset_meshtastic_utils_globals(shutdown_executors=True)

    if "mmrelay.matrix_utils" in sys.modules:
        module = sys.modules["mmrelay.matrix_utils"]
        module.config = None
        module.matrix_homeserver = None
        module.matrix_rooms = None
        module.matrix_access_token = None
        module.bot_user_id = None
        module.bot_user_name = None
        module.matrix_client = None
        import time

        module.bot_start_time = int(time.time() * 1000)

    if "mmrelay.main" in sys.modules:
        module = sys.modules["mmrelay.main"]
        module._banner_printed = False
        module._ready_file_path = None
        module._ready_heartbeat_seconds = 30

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


class TestAwaitBackgroundTaskShutdownErrorPaths(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
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

    def tearDown(self):
        _reset_all_mmrelay_globals()

    def test_await_background_task_shutdown_logs_error_on_runtime_error(self):
        """
        Verifies that mmrelay.main logs an error when a background connection-health task raises a RuntimeError during shutdown waiting.

        Patches runtime dependencies to run main() with a meshtastic connection-health coroutine that raises a RuntimeError after a short delay, runs the shutdown sequence, and asserts that the main logger received an error containing "Error while waiting for" and "connection health task". Restores modified mmrelay.meshtastic_utils module-level globals after the test.
        """
        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        original_reconnect_task = mu.reconnect_task
        original_reconnect_task_future = mu.reconnect_task_future
        try:
            mu.reconnect_task = None
            mu.reconnect_task_future = None

            runtime_error = RuntimeError("background task exploded")

            async def _check_connection_that_raises_after_delay(
                *_args: object, **_kwargs: object
            ) -> None:
                await asyncio.sleep(0.5)
                raise runtime_error

            with (
                patch("mmrelay.main.initialize_database"),
                patch("mmrelay.main.load_plugins"),
                patch("mmrelay.main.start_message_queue"),
                patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                patch(
                    "mmrelay.main.connect_matrix",
                    side_effect=_make_async_return(
                        _make_matrix_client_with_awaitable_close()
                    ),
                ),
                patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    side_effect=_check_connection_that_raises_after_delay,
                ),
                patch("mmrelay.main.logger") as mock_logger,
                patch("mmrelay.main.meshtastic_logger"),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(self.mock_config))

            error_calls = [str(c) for c in mock_logger.error.call_args_list]
            assert any(
                "Error while waiting for" in e and "connection health task" in e
                for e in error_calls
            ), f"Expected error log for RuntimeError during shutdown wait, got: {error_calls}"
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting
            mu.reconnect_task = original_reconnect_task
            mu.reconnect_task_future = original_reconnect_task_future

    def test_await_background_task_shutdown_timeout_on_cancel_gather(self):
        """
        Verify that main's shutdown logs a timeout warning when cancelling background tasks fails to complete.

        Patches the runtime to:
        - make the meshtastic connection health coroutine ignore cancellation,
        - cause `asyncio.wait_for` to raise `asyncio.TimeoutError` on the second invocation,
        - and provide an awaitable matrix client close.

        Asserts that a warning containing "Timed out cancelling" is emitted during shutdown.
        """
        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        original_reconnect_task = mu.reconnect_task
        original_reconnect_task_future = mu.reconnect_task_future
        try:
            mu.reconnect_task = None
            mu.reconnect_task_future = None

            original_wait_for = asyncio.wait_for
            wait_for_call_count = 0

            async def _wait_for_that_times_out_on_gather(
                coro: object, timeout: object = None
            ) -> None:
                nonlocal wait_for_call_count
                wait_for_call_count += 1
                if wait_for_call_count >= 2:
                    raise asyncio.TimeoutError()
                return await original_wait_for(coro, timeout)

            async def _check_connection_that_ignores_cancel(
                *_args: object, **_kwargs: object
            ) -> None:
                try:
                    while True:
                        await asyncio.sleep(10)
                except asyncio.CancelledError:
                    pass

            with (
                patch("mmrelay.main.initialize_database"),
                patch("mmrelay.main.load_plugins"),
                patch("mmrelay.main.start_message_queue"),
                patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                patch(
                    "mmrelay.main.connect_matrix",
                    side_effect=_make_async_return(
                        _make_matrix_client_with_awaitable_close()
                    ),
                ),
                patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.asyncio.wait_for",
                    side_effect=_wait_for_that_times_out_on_gather,
                ),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    side_effect=_check_connection_that_ignores_cancel,
                ),
                patch("mmrelay.main.logger") as mock_logger,
                patch("mmrelay.main.meshtastic_logger"),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(self.mock_config))

            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any(
                "Timed out cancelling" in w for w in warning_calls
            ), f"Expected warning log for timeout during cancel gather, got: {warning_calls}"
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting
            mu.reconnect_task = original_reconnect_task
            mu.reconnect_task_future = original_reconnect_task_future


class TestShutdownWithReconnectTaskFuture(unittest.TestCase):
    def setUp(self):
        self.mock_config = {
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

    def tearDown(self):
        _reset_all_mmrelay_globals()

    def test_shutdown_cancels_and_awaits_reconnect_task_future(self):
        """
        Verifies that shutdown cancels and awaits any active meshtastic reconnect task future.

        Sets up a long-running reconnect task future, runs the main shutdown sequence, and asserts that the reconnect future was either cancelled or observed cancellation, is completed, and that the global `reconnect_task_future` has been cleared.
        """
        import mmrelay.meshtastic_utils as mu

        original_client = mu.meshtastic_client
        original_iface = mu.meshtastic_iface
        original_shutting_down = mu.shutting_down
        original_reconnecting = mu.reconnecting
        original_reconnect_task = mu.reconnect_task
        original_reconnect_task_future = mu.reconnect_task_future
        try:
            mu.reconnect_task = None

            captured_future = None
            cancel_observed = False

            def _capture_reconnect_future(
                *_args: object, **_kwargs: object
            ) -> MagicMock:
                """
                Create and store a long-running reconnect task and record it in module state.

                Starts an asyncio Task that sleeps for an extended period; if the task is cancelled,
                the outer-scope flag `cancel_observed` is set to True. The created Task is stored
                in the outer-scope `captured_future` and assigned to `mu.reconnect_task_future` and
                `mu.reconnect_task` for later inspection and shutdown handling.
                """
                nonlocal captured_future
                nonlocal cancel_observed

                async def _fake_reconnect() -> None:
                    nonlocal cancel_observed
                    try:
                        await asyncio.sleep(300)
                    except asyncio.CancelledError:
                        cancel_observed = True

                loop = asyncio.get_running_loop()
                captured_future = loop.create_task(_fake_reconnect())
                mu.reconnect_task_future = captured_future
                mu.reconnect_task = captured_future
                return None

            async def _capture_reconnect_future_async(
                *_args: object, **_kwargs: object
            ) -> None:
                """
                Trigger creation and capture of the reconnect task/future during tests.

                Acting as an async shim, this function invokes the test helper that creates a long-running reconnect task and stores its Task/future for later inspection and shutdown verification. Intended for use as an async replacement for the connection-health callback in tests.
                """
                _capture_reconnect_future(*_args, **_kwargs)

            with (
                patch("mmrelay.main.initialize_database"),
                patch("mmrelay.main.load_plugins"),
                patch("mmrelay.main.start_message_queue"),
                patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                patch(
                    "mmrelay.main.connect_matrix",
                    side_effect=_make_async_return(
                        _make_matrix_client_with_awaitable_close()
                    ),
                ),
                patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
                patch("mmrelay.main.shutdown_plugins"),
                patch("mmrelay.main.stop_message_queue"),
                patch("mmrelay.main.get_message_queue") as mock_get_queue,
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
                patch(
                    "mmrelay.main.meshtastic_utils.check_connection",
                    new=_capture_reconnect_future_async,
                ),
                patch("mmrelay.main.logger"),
                patch("mmrelay.main.meshtastic_logger"),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(self.mock_config))

            assert captured_future is not None
            assert captured_future.cancelled() or cancel_observed
            assert captured_future.done()
            assert mu.reconnect_task_future is None
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting
            mu.reconnect_task = original_reconnect_task
            mu.reconnect_task_future = original_reconnect_task_future
