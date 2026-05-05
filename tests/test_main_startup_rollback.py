#!/usr/bin/env python3
"""
Test suite for startup rollback behavior in main().

Covers cancellation of check_connection tasks, ready file removal,
plugin/message-queue shutdown, reconnect state cleanup, and
Matrix/Meshtastic client close during startup rollback.
"""

import asyncio
import inspect
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_noop,
    _CloseFutureBase,
    _ControlledExecutor,
    _ImmediateEvent,
    _make_async_return,
    _make_patched_get_running_loop,
    _OnePassEvent,
    _reset_all_mmrelay_globals,
    _reset_meshtastic_utils_globals,
    _sync_forever_blocks,
    inline_to_thread,
    mock_config,
)

__all__ = [
    "test_startup_rollback_cancels_check_connection_task",
    "test_startup_rollback_removes_ready_file",
    "test_startup_rollback_shutdowns_plugins_when_loaded",
    "test_startup_rollback_stops_message_queue_when_started",
    "test_startup_rollback_cleans_reconnect_state_and_callbacks",
    "test_startup_rollback_closes_matrix_client",
    "test_startup_rollback_closes_meshtastic_client",
]
# =============================================================================
# TestStartupRollback (converted from unittest.TestCase)
# =============================================================================


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main._remove_ready_file")
@patch("mmrelay.main.shutdown_plugins")
@patch("mmrelay.main.stop_message_queue")
def test_startup_rollback_cancels_check_connection_task(
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

    check_conn_sentinel = object()

    def mock_check_conn() -> object:
        # Return a non-coroutine sentinel; create_task is patched below.
        """
        Provide the sentinel value representing a mocked connection check.

        Returns:
            object: The sentinel `check_conn_sentinel` used by the patched `asyncio.create_task` to identify the mocked check-connection invocation.
        """
        return check_conn_sentinel

    def mock_create_task(coro: object, *_args: object, **_kwargs: object) -> object:
        """
        Provide a test stub for asyncio.create_task that returns prebuilt mock tasks for recognized inputs and fails for anything unexpected.

        Parameters:
            coro: Either the special sentinel `check_conn_sentinel`, a coroutine object whose code name may match `_node_name_refresh_supervisor`, or another value passed where an asyncio task would normally be scheduled. Recognized inputs return corresponding mock task objects; coroutine objects that are not recognized are closed before failing.
            *_args, **_kwargs: Ignored.

        Returns:
            The corresponding mock task object for the recognized sentinel or coroutine.

        Raises:
            AssertionError: If a coroutine is scheduled but its name is not expected, or if a non-coroutine, non-sentinel value is scheduled.
        """
        if coro is check_conn_sentinel:
            return mock_check_task
        if inspect.iscoroutine(coro):
            coro_name = getattr(getattr(coro, "cr_code", None), "co_name", "")
            if coro_name == "_node_name_refresh_supervisor":
                coro.close()
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
        with pytest.raises(RuntimeError):
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
    mock_connect_meshtastic.side_effect = RuntimeError("Meshtastic connection error")

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
        with pytest.raises(RuntimeError):
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
        with pytest.raises(RuntimeError):
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
    mock_connect_meshtastic.side_effect = RuntimeError("Meshtastic connection error")

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
        with pytest.raises(RuntimeError):
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
def test_startup_rollback_cleans_reconnect_state_and_callbacks(
    mock_stop_queue,
    mock_shutdown_plugins,
    mock_remove_ready,
    mock_connect_meshtastic,
    mock_connect_matrix,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
):
    """Startup rollback should cancel reconnect work and unsubscribe callbacks."""

    mock_connect_meshtastic.return_value = MagicMock()
    mock_connect_matrix.side_effect = RuntimeError("After meshtastic client error")

    import mmrelay.meshtastic_utils as mu

    original_reconnect_task = mu.reconnect_task
    original_reconnect_future = mu.reconnect_task_future
    reconnect_task = MagicMock()
    reconnect_future = MagicMock()

    try:
        mu.reconnect_task = reconnect_task
        mu.reconnect_task_future = reconnect_future

        config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}

        with (
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.meshtastic_utils.unsubscribe_meshtastic_callbacks"
            ) as mock_unsubscribe,
            patch("mmrelay.main.logger"),
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(main(config))

        reconnect_task.cancel.assert_called_once()
        reconnect_future.cancel.assert_called_once()
        mock_unsubscribe.assert_called_once()
    finally:
        mu.reconnect_task = original_reconnect_task
        mu.reconnect_task_future = original_reconnect_future


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main._remove_ready_file")
@patch("mmrelay.main.shutdown_plugins")
@patch("mmrelay.main.stop_message_queue")
def test_startup_rollback_closes_matrix_client(
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
        with pytest.raises(RuntimeError):
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
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch("mmrelay.main.logger"),
        ):
            with pytest.raises(RuntimeError):
                asyncio.run(main(config))

            mock_meshtastic_client.close.assert_called_once()
    finally:
        mu.meshtastic_client = original_client
