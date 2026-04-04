#!/usr/bin/env python3
"""
Test suite for uncovered code paths in main.py shutdown logic.

Covers:
- _await_background_task_shutdown error paths (lines 898-922)
- Shutdown with reconnect_task_future set (lines 1115-1121)
"""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_async_return(value):
    async def _async_return(*_args, **_kwargs):
        return value

    return _async_return


async def _async_noop(*_args, **_kwargs) -> None:
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

            async def _check_connection_that_raises_after_delay(*_args, **_kwargs):
                await asyncio.sleep(0.5)
                raise runtime_error

            with (
                patch("mmrelay.main.initialize_database"),
                patch("mmrelay.main.load_plugins"),
                patch("mmrelay.main.start_message_queue"),
                patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                patch(
                    "mmrelay.main.connect_matrix",
                    side_effect=_make_async_return(MagicMock(close=AsyncMock())),
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

            async def _wait_for_that_times_out_on_gather(coro, timeout):
                nonlocal wait_for_call_count
                wait_for_call_count += 1
                if wait_for_call_count >= 2:
                    raise asyncio.TimeoutError()
                return await original_wait_for(coro, timeout)

            async def _check_connection_that_ignores_cancel(*_args, **_kwargs):
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
                    side_effect=_make_async_return(MagicMock(close=AsyncMock())),
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

            def _capture_reconnect_future(*_args, **_kwargs):
                nonlocal captured_future

                async def _fake_reconnect():
                    try:
                        await asyncio.sleep(300)
                    except asyncio.CancelledError:
                        pass

                loop = asyncio.get_running_loop()
                captured_future = loop.create_task(_fake_reconnect())
                mu.reconnect_task_future = captured_future
                mu.reconnect_task = captured_future
                return None

            with (
                patch("mmrelay.main.initialize_database"),
                patch("mmrelay.main.load_plugins"),
                patch("mmrelay.main.start_message_queue"),
                patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
                patch(
                    "mmrelay.main.connect_matrix",
                    side_effect=_make_async_return(MagicMock(close=AsyncMock())),
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
                    side_effect=_capture_reconnect_future,
                ),
                patch("mmrelay.main.logger"),
                patch("mmrelay.main.meshtastic_logger"),
            ):
                mock_queue = MagicMock()
                mock_queue.ensure_processor_started = MagicMock()
                mock_get_queue.return_value = mock_queue

                asyncio.run(main(self.mock_config))

            assert captured_future is not None
            assert captured_future.cancelled()
            assert mu.reconnect_task_future is None
        finally:
            mu.meshtastic_client = original_client
            mu.meshtastic_iface = original_iface
            mu.shutting_down = original_shutting_down
            mu.reconnecting = original_reconnecting
            mu.reconnect_task = original_reconnect_task
            mu.reconnect_task_future = original_reconnect_task_future
