#!/usr/bin/env python3
"""
Core test suite for main application flow in MMRelay.

Tests:
- Basic application initialization and startup wiring
- Startup drain and ready publication
- Database wipe configuration (new and legacy formats)
- Config bool coercion
- Asyncio exception filtering

Other main-related tests are split into focused domain files:
- test_main_run_main.py: run_main() wrapper, argument handling, banner printing
- test_main_shutdown.py: shutdown, connection failures, cleanup paths
- test_main_async.py: async initialization, signal handlers, event-loop
- test_main_startup_rollback.py: startup rollback tests
- test_main_check_connection.py: node refresh supervisor, check_connection
- test_main_sync_error_handling.py: Matrix sync retry/error handling
"""

import asyncio
import contextlib
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.config import DEFAULT_NODEDB_REFRESH_INTERVAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _AutoSetAfterWaitEvent,
    _OnePassEvent,
    _reset_all_mmrelay_globals,
)
from tests.helpers import (
    inline_to_thread,
    make_patched_get_running_loop,
)


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
@patch("mmrelay.main.meshtastic_utils.refresh_node_name_tables", new_callable=AsyncMock)
@patch("mmrelay.main.stop_message_queue")
def test_main_basic_flow(
    mock_stop_queue,
    mock_refresh_node_names,
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
    mock_config,
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
            side_effect=make_patched_get_running_loop(),
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

        asyncio.run(main(mock_config))

    mock_init_db.assert_called_once()
    mock_load_plugins.assert_called_once()
    mock_start_queue.assert_called_once_with(message_delay=2.0)
    mock_connect_meshtastic.assert_called_once_with(passed_config=mock_config)
    mock_connect_matrix.assert_awaited_once_with(passed_config=mock_config)
    assert mock_join_room.await_count == 2
    mock_get_interval.assert_called_once_with(mock_config)
    assert "_node_name_refresh_supervisor" in created_task_coro_names
    assert mock_create_task.call_count >= 1
    mock_refresh_node_names.assert_awaited_once_with(
        shutdown_event,
        refresh_interval_seconds=expected_interval,
    )
    mock_stop_queue.assert_called_once()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
@patch("mmrelay.main.meshtastic_utils.refresh_node_name_tables", new_callable=AsyncMock)
@patch("mmrelay.main.stop_message_queue")
def test_main_publishes_ready_after_sync_start_and_drain_completion(
    mock_stop_queue,
    _mock_refresh_node_names,
    _mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
):
    """
    Ready publication should wait for sync startup and startup-drain completion.
    """
    shutdown_event = _OnePassEvent()
    startup_drain_complete_event = threading.Event()
    readiness_calls: list[str] = []
    log_sequence: list[tuple[str, str]] = []

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    async def _sync_forever_wait(*_args: Any, **_kwargs: Any) -> None:
        await asyncio.sleep(0)
        assert not readiness_calls
        await shutdown_event.wait()

    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_wait)
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_meshtastic.return_value = MagicMock()

    async def _release_startup_drain() -> None:
        await asyncio.sleep(0.01)
        startup_drain_complete_event.set()

    def _write_ready_side_effect() -> None:
        assert startup_drain_complete_event.is_set()
        readiness_calls.append("ready")

    def _capture_main_info(message: str, *args: Any, **_kwargs: Any) -> None:
        rendered = message % args if args else message
        log_sequence.append(("main", rendered))
        if rendered == "Relay startup complete":
            shutdown_event.set()

    def _capture_matrix_info(message: str, *args: Any, **_kwargs: Any) -> None:
        rendered = message % args if args else message
        log_sequence.append(("matrix", rendered))

    async def _run_main() -> None:
        release_startup_drain_task = asyncio.create_task(_release_startup_drain())
        try:
            await main(mock_config)
        finally:
            if release_startup_drain_task.done():
                await release_startup_drain_task
            else:
                release_startup_drain_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await release_startup_drain_task

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch(
            "mmrelay.main.meshtastic_utils.check_connection",
            new_callable=AsyncMock,
        ) as mock_check_conn,
        patch(
            "mmrelay.main.meshtastic_utils.get_startup_drain_complete_event",
            return_value=startup_drain_complete_event,
        ),
        patch(
            "mmrelay.main._write_ready_file",
            side_effect=_write_ready_side_effect,
        ) as mock_write_ready,
        patch("mmrelay.main._ready_heartbeat_seconds", 0),
        patch("mmrelay.main.logger") as mock_main_logger,
        patch("mmrelay.main.matrix_logger") as mock_matrix_logger,
        patch("mmrelay.main.shutdown_plugins"),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        mock_check_conn.return_value = True
        mock_main_logger.info.side_effect = _capture_main_info
        mock_matrix_logger.info.side_effect = _capture_matrix_info

        asyncio.run(_run_main())

    mock_write_ready.assert_called_once()
    assert readiness_calls == ["ready"]
    assert ("matrix", "Starting Matrix sync loop") in log_sequence
    assert ("main", "Relay startup complete") in log_sequence
    sync_start_index = log_sequence.index(("matrix", "Starting Matrix sync loop"))
    ready_index = log_sequence.index(("main", "Relay startup complete"))
    assert sync_start_index < ready_index
    mock_stop_queue.assert_called_once()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
@patch("mmrelay.main.meshtastic_utils.refresh_node_name_tables", new_callable=AsyncMock)
@patch("mmrelay.main.stop_message_queue")
def test_main_sync_failure_before_drain_does_not_publish_ready(
    mock_stop_queue,
    _mock_refresh_node_names,
    _mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
):
    """
    Early sync failures should be handled before startup drain completes.
    """
    shutdown_event = _OnePassEvent()
    startup_drain_complete_event = threading.Event()
    readiness_calls: list[str] = []
    shutdown_backstop_fired = False
    sync_failure_logged = False

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    async def _sync_forever_fail_once(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("sync failed before startup drain")

    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_fail_once)
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_meshtastic.return_value = MagicMock()

    async def _request_shutdown() -> None:
        nonlocal shutdown_backstop_fired
        await asyncio.sleep(1.0)
        shutdown_backstop_fired = True
        shutdown_event.set()

    def _capture_sync_failure(*_args: Any, **_kwargs: Any) -> None:
        nonlocal sync_failure_logged
        sync_failure_logged = True
        shutdown_event.set()

    def _write_ready_side_effect() -> None:
        assert startup_drain_complete_event.is_set()
        readiness_calls.append("ready")

    async def _run_main() -> None:
        request_shutdown_task = asyncio.create_task(_request_shutdown())
        try:
            await main(mock_config)
        finally:
            if request_shutdown_task.done():
                await request_shutdown_task
            else:
                request_shutdown_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await request_shutdown_task

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch(
            "mmrelay.main.meshtastic_utils.check_connection",
            new_callable=AsyncMock,
        ) as mock_check_conn,
        patch(
            "mmrelay.main.meshtastic_utils.get_startup_drain_complete_event",
            return_value=startup_drain_complete_event,
        ),
        patch(
            "mmrelay.main._write_ready_file",
            side_effect=_write_ready_side_effect,
        ) as mock_write_ready,
        patch("mmrelay.main._ready_heartbeat_seconds", 0),
        patch("mmrelay.main._STARTUP_DRAIN_WAIT_POLL_SECS", 0.01),
        patch("mmrelay.main.matrix_logger") as mock_matrix_logger,
        patch("mmrelay.main.shutdown_plugins"),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        mock_check_conn.return_value = True
        mock_matrix_logger.exception.side_effect = _capture_sync_failure

        asyncio.run(_run_main())

    mock_write_ready.assert_not_called()
    assert readiness_calls == []
    assert any(
        "Matrix sync failed" in str(call)
        for call in mock_matrix_logger.exception.call_args_list
    )

    assert sync_failure_logged
    assert not shutdown_backstop_fired
    mock_stop_queue.assert_called_once()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix", new_callable=AsyncMock)
@patch("mmrelay.main.join_matrix_room", new_callable=AsyncMock)
@patch("mmrelay.main.meshtastic_utils.refresh_node_name_tables", new_callable=AsyncMock)
@patch("mmrelay.main.stop_message_queue")
def test_main_none_startup_drain_event_is_safe_noop(
    mock_stop_queue,
    _mock_refresh_node_names,
    _mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
):
    """Ready publication should be a safe no-op when startup drain event is None."""
    shutdown_event = _OnePassEvent()
    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    async def _sync_forever_once(*_args, **_kwargs):
        await asyncio.sleep(0)
        shutdown_event.set()

    mock_matrix_client.sync_forever = AsyncMock(side_effect=_sync_forever_once)
    mock_connect_matrix.return_value = mock_matrix_client
    mock_connect_meshtastic.return_value = MagicMock()

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
        ),
        patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
        patch("mmrelay.main.asyncio.Event", return_value=shutdown_event),
        patch("mmrelay.main.get_message_queue") as mock_get_queue,
        patch(
            "mmrelay.main.meshtastic_utils.check_connection",
            new_callable=AsyncMock,
        ) as mock_check_conn,
        patch(
            "mmrelay.main.meshtastic_utils.get_startup_drain_complete_event",
            return_value=None,
        ),
        patch("mmrelay.main._write_ready_file") as mock_write_ready,
        patch("mmrelay.main.shutdown_plugins"),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue
        mock_check_conn.return_value = True

        asyncio.run(main(mock_config))

    mock_write_ready.assert_called_once()
    mock_stop_queue.assert_called_once()


def test_refresh_node_name_tables_skips_db_sync_without_meshtastic_client():
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
    assert result is None


@pytest.mark.parametrize("raw_value", ["inf", "not-a-number", True, False, -1.0])
def test_nodedb_refresh_interval_invalid_defaults(raw_value):
    """Invalid nodedb refresh intervals should fall back to the default value."""
    import mmrelay.meshtastic_utils as meshtastic_module

    interval = meshtastic_module.get_nodedb_refresh_interval_seconds(
        {"meshtastic": {"nodedb_refresh_interval": raw_value}}
    )
    assert interval == DEFAULT_NODEDB_REFRESH_INTERVAL


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
            side_effect=make_patched_get_running_loop(),
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
            side_effect=make_patched_get_running_loop(),
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


# =============================================================================
# TestCoerceConfigBool (converted from unittest.TestCase)
# =============================================================================


@patch("mmrelay.main.logger")
def test_coerce_config_bool_unexpected_type_list(mock_logger):
    """List values should return False and log debug."""
    from mmrelay.main import _coerce_config_bool

    result = _coerce_config_bool([1, 2, 3])
    assert not result
    mock_logger.debug.assert_called_once()
    call_args = mock_logger.debug.call_args
    assert "Unexpected config value type" in call_args[0][0]


@patch("mmrelay.main.logger")
def test_coerce_config_bool_unexpected_type_dict(mock_logger):
    """Dict values should return False and log debug."""
    from mmrelay.main import _coerce_config_bool

    result = _coerce_config_bool({"key": "value"})
    assert not result
    mock_logger.debug.assert_called_once()


@patch("mmrelay.main.logger")
def test_coerce_config_bool_unexpected_type_object(mock_logger):
    """Custom object values should return False and log debug."""
    from mmrelay.main import _coerce_config_bool

    class CustomObject:
        pass

    result = _coerce_config_bool(CustomObject())
    assert not result
    mock_logger.debug.assert_called_once()


def test_coerce_config_bool_none_returns_false():
    """None should return False without logging."""
    from mmrelay.main import _coerce_config_bool

    with patch("mmrelay.main.logger") as mock_logger:
        result = _coerce_config_bool(None)
        assert not result
        mock_logger.debug.assert_not_called()


# =============================================================================
# TestAsyncioExceptionFiltering (converted from unittest.TestCase)
# =============================================================================


def test_suppresses_unretrieved_keys_query_timeout():
    """Known keys_query timeout contexts should be treated as suppressible noise."""
    from mmrelay.main import _should_suppress_unretrieved_matrix_task_timeout

    class _DummyCode:
        co_name = "keys_query"

    class _DummyCoro:
        cr_code = _DummyCode()

    class _DummyTask:
        def get_coro(self):
            return _DummyCoro()

        def __repr__(self) -> str:
            return "<Task coro=keys_query()>"

    context = {
        "message": "Task exception was never retrieved",
        "exception": asyncio.TimeoutError(),
        "future": _DummyTask(),
    }
    assert _should_suppress_unretrieved_matrix_task_timeout(context)


def test_does_not_suppress_non_timeout_background_exceptions():
    """Non-timeout exceptions should not be filtered."""
    from mmrelay.main import _should_suppress_unretrieved_matrix_task_timeout

    context = {
        "message": "Task exception was never retrieved",
        "exception": RuntimeError("boom"),
        "future": object(),
    }
    assert not _should_suppress_unretrieved_matrix_task_timeout(context)
