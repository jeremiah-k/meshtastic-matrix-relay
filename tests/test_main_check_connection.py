#!/usr/bin/env python3
"""
Test suite for node refresh supervisor and check_connection logic in main().

Covers:
- Node name refresh supervisor
- check_connection shutdown/timeout/cancel/error paths
- Message queue processor start failure
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_block_forever,
    _async_noop,
    _close_coro_if_possible,
    _ImmediateEvent,
    _OnePassEvent,
    _reset_all_mmrelay_globals,
    _TaskSpy,
)
from tests.constants import TEST_MATRIX_HOMESERVER, TEST_ROOM_ID_1
from tests.helpers import (
    inline_to_thread,
    make_patched_get_running_loop,
)

__all__ = [
    "test_supervisor_runs_refresh_before_shutdown_signal",
    "test_returns_early_when_task_is_none",
    "test_timeout_during_shutdown_cancels_task",
    "test_check_connection_exception_is_raised_after_cleanup",
    "test_check_connection_unexpected_return_is_raised_after_cleanup",
    "test_exception_during_shutdown_wait_logs_error",
    "test_cancelled_error_cancels_task_and_returns",
    "test_task_with_exception_result_logs_error",
    "test_exception_during_ensure_processor_started_raised",
]
# =============================================================================
# TestNodeNameRefreshSupervisor (converted from unittest.TestCase)
# =============================================================================


def test_supervisor_runs_refresh_before_shutdown_signal():
    """Supervisor should run one refresh pass before a runtime shutdown signal."""

    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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
        patch("mmrelay.main.connect_meshtastic", return_value=MagicMock()),
        patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
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

    assert refresh_called, "refresh_node_name_tables should be called once"


# =============================================================================
# TestAwaitBackgroundTaskShutdown (converted from unittest.TestCase)
# =============================================================================


def test_returns_early_when_task_is_none():
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
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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


def test_timeout_during_shutdown_cancels_task():
    """TimeoutError during shutdown task wait should cancel and continue."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

    assert (
        check_conn_tasks
    ), f"No connection health task captured. Observed coroutines: {observed_coro_names}"
    assert any(
        spy.cancel_called for spy in check_conn_tasks
    ), "Expected cancel() to be called on check_connection task during shutdown"


def test_check_connection_exception_is_raised_after_cleanup():
    """Exceptions from the health task should become main() failures."""
    config = {
        "matrix_rooms": [{"id": TEST_ROOM_ID_1}],
        "matrix": {"homeserver": TEST_MATRIX_HOMESERVER},
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
            "health_check": {"enabled": True},
        },
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
        mock_matrix_client.sync_forever = AsyncMock(side_effect=_async_block_forever)
        mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
        with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            _reset_all_mmrelay_globals()
            try:
                with pytest.raises(RuntimeError, match="health monitor failed"):
                    asyncio.run(main(config))
            finally:
                _reset_all_mmrelay_globals()

            mock_queue.ensure_processor_started.assert_not_called()
            mock_shutdown_plugins.assert_called_once()
            mock_stop_message_queue.assert_called_once()
            mock_matrix_client.close.assert_awaited_once()


def test_check_connection_unexpected_return_is_raised_after_cleanup():
    """Unexpected clean health-task exits should raise a fatal RuntimeError."""
    config = {
        "matrix_rooms": [{"id": TEST_ROOM_ID_1}],
        "matrix": {"homeserver": TEST_MATRIX_HOMESERVER},
        "meshtastic": {
            "connection_type": CONNECTION_TYPE_SERIAL,
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
        mock_matrix_client.sync_forever = AsyncMock(side_effect=_async_block_forever)
        mock_connect_matrix = AsyncMock(return_value=mock_matrix_client)
        with patch("mmrelay.main.connect_matrix", mock_connect_matrix):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            _reset_all_mmrelay_globals()
            try:
                with pytest.raises(
                    RuntimeError,
                    match="Connection health task exited unexpectedly without an exception",
                ):
                    asyncio.run(main(config))
            finally:
                _reset_all_mmrelay_globals()

            mock_queue.ensure_processor_started.assert_not_called()
            mock_shutdown_plugins.assert_called_once()
            mock_stop_message_queue.assert_called_once()
            mock_matrix_client.close.assert_awaited_once()


def test_exception_during_shutdown_wait_logs_error():
    """Exception during shutdown wait should log error and continue.

    Note: This test verifies the exception path completes without hanging.
    A more thorough test would inject a mock task to verify cancel() is called.
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

            assert shutdown_wait_for_injected
            assert any(
                "Error while waiting for" in str(call)
                for call in mock_logger.error.call_args_list
            )


def test_cancelled_error_cancels_task_and_returns():
    """CancelledError during shutdown should cancel task and return."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

    async def mock_wait_for(coro, timeout=None):
        if timeout == 5.0:
            # Peek inside asyncio wrappers (gather, etc.) to find the
            # target coroutine name using CPython internals with defensive
            # getattr fallbacks to reduce brittleness.
            candidate = getattr(coro, "_coro", coro)
            gather_args = getattr(candidate, "_args", None) or getattr(
                candidate, "_children", None
            )
            if gather_args:
                for arg in gather_args:
                    inner = getattr(arg, "_coro", arg)
                    if (
                        getattr(getattr(inner, "cr_code", None), "co_name", "")
                        == "_check_connection_wait"
                    ):
                        raise asyncio.CancelledError()
                    co_code = getattr(inner, "__code__", None)
                    if (
                        co_code
                        and getattr(co_code, "co_name", "") == "_check_connection_wait"
                    ):
                        raise asyncio.CancelledError()
            else:
                code = getattr(candidate, "cr_code", None) or getattr(
                    candidate, "__code__", None
                )
                name = getattr(code, "co_name", "")
                if name == "_check_connection_wait":
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

    assert (
        check_conn_tasks
    ), f"No connection health task captured. Observed coroutines: {observed_coro_names}"
    assert any(
        spy.cancel_called for spy in check_conn_tasks
    ), "Expected cancel() to be called on check_connection task during shutdown"


def test_task_with_exception_result_logs_error():
    """Exception in task result should log error during cleanup."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

        assert any(
            "Error during" in str(call) for call in mock_logger.error.call_args_list
        )


# =============================================================================
# TestMessageQueueProcessorStartFailure (converted from unittest.TestCase)
# =============================================================================


def test_exception_during_ensure_processor_started_raised():
    """Exception during ensure_processor_started is caught and raised."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
    }

    with (
        patch("mmrelay.main.initialize_database"),
        patch("mmrelay.main.load_plugins"),
        patch("mmrelay.main.start_message_queue"),
        patch(
            "mmrelay.main.connect_matrix", new_callable=AsyncMock
        ) as mock_connect_matrix,
        patch("mmrelay.main.connect_meshtastic") as mock_connect_meshtastic,
        patch("mmrelay.main.join_matrix_room"),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch("mmrelay.main.shutdown_plugins"),
        patch("mmrelay.main.stop_message_queue"),
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
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

        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(main(config))

        assert "Queue processor failed" in str(exc_info.value)
