#!/usr/bin/env python3
"""
Test suite for async initialization, signal handlers, event-loop setup,
and ready-file/heartbeat tests in main.py.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_noop,
    _ImmediateEvent,
    _make_async_return,
)
from tests.helpers import (
    InlineExecutorLoop,
    inline_to_thread,
    make_patched_get_running_loop,
)


def test_main_async_initialization_sequence():
    """Verify that the asynchronous main() startup sequence invokes database initialization, plugin loading, message-queue startup, and both Matrix and Meshtastic connection routines.

    Sets up a minimal config with one Matrix room, injects AsyncMock/MagicMock clients for Matrix and Meshtastic, and arranges for the Matrix client's sync loop and asyncio.sleep to raise KeyboardInterrupt so the function exits cleanly. Asserts each initialization/connect function is called exactly once.
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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
            side_effect=make_patched_get_running_loop(),
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
        patch("mmrelay.meshtastic_utils.asyncio.sleep", side_effect=KeyboardInterrupt),
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


def test_main_async_with_multiple_rooms():
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
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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
            side_effect=make_patched_get_running_loop(),
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
        patch("mmrelay.meshtastic_utils.asyncio.sleep", side_effect=KeyboardInterrupt),
        patch("mmrelay.matrix_utils.asyncio.sleep", side_effect=KeyboardInterrupt),
        contextlib.suppress(KeyboardInterrupt),
    ):
        asyncio.run(main(config))

    # Verify join_matrix_room was called for each room
    assert mock_join.call_count == 2


def test_main_signal_handler_sets_shutdown_flag():
    """
    Ensure mmrelay sets the meshtastic shutdown flag and registers a signal handler when the event loop installs signal handlers.
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

        import mmrelay.meshtastic_utils as mu

        original_shutting_down = mu.shutting_down
        try:
            asyncio.run(main(config))
            assert mu.shutting_down
        finally:
            mu.shutting_down = original_shutting_down

    assert captured_handlers


def test_main_shutdown_signal_logging_is_idempotent():
    """Repeated shutdown signals should emit the shutdown notice once."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
    }

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    real_get_running_loop = asyncio.get_running_loop

    def _patched_get_running_loop():
        loop = real_get_running_loop()
        if not isinstance(loop, InlineExecutorLoop):
            loop = InlineExecutorLoop(loop)
        if not hasattr(loop, "_signal_handler_patched"):

            def _fake_add_signal_handler(_sig, handler):
                handler()
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
        patch("mmrelay.main.matrix_logger") as mock_matrix_logger,
        patch("mmrelay.main.sys.platform", "linux"),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        original_shutting_down = mu.shutting_down
        try:
            asyncio.run(main(config))
        finally:
            mu.shutting_down = original_shutting_down

    shutdown_logs = [
        call
        for call in mock_matrix_logger.info.call_args_list
        if call.args and call.args[0] == "Shutdown signal received. Closing down..."
    ]
    assert len(shutdown_logs) == 1


def test_main_registers_sighup_handler():
    """Verify SIGHUP handler registration on non-Windows platforms."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

    assert main_module.signal.SIGHUP in captured_signals


def test_main_windows_keyboard_interrupt_triggers_shutdown():
    """
    Verify the Windows signal path executes and KeyboardInterrupt triggers shutdown.
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
    }

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    mock_matrix_client.sync_forever = AsyncMock()

    import mmrelay.main as main_module

    with (
        patch(
            "mmrelay.main.asyncio.get_running_loop",
            side_effect=make_patched_get_running_loop(),
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

        import mmrelay.meshtastic_utils as mu

        original_shutting_down = mu.shutting_down
        try:
            asyncio.run(main(config))
            assert mu.shutting_down
        finally:
            mu.shutting_down = original_shutting_down


def test_main_async_event_loop_setup():
    """
    Verify that the async main startup accesses the running event loop.

    This test runs run_main with a minimal config while patching startup hooks so execution stops quickly,
    and asserts that asyncio.get_running_loop() is called (the running loop is retrieved for use by Meshtastic and other async components).
    """
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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


def test_main_restores_loop_exception_handler_on_early_init_failure() -> None:
    """Loop exception handler should be restored when startup fails before main try."""
    config = {"matrix_rooms": [{"id": "!room:matrix.org"}]}
    mock_loop = MagicMock()
    previous_handler = object()

    with (
        patch("mmrelay.main.asyncio.get_running_loop", return_value=mock_loop),
        patch(
            "mmrelay.main._install_loop_exception_handler",
            return_value=previous_handler,
        ),
        patch(
            "mmrelay.main.initialize_database",
            side_effect=RuntimeError("init failed"),
        ),
        pytest.raises(RuntimeError, match="init failed"),
    ):
        asyncio.run(main(config))

    mock_loop.set_exception_handler.assert_called_with(previous_handler)


def test_main_shutdown_task_cancellation_coverage() -> None:
    """Exercise the production shutdown path's task-cancellation logic by running main() with a blocking background task that forces _await_background_task_shutdown to time out, cancel, and drain the pending task."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
    }

    mock_matrix_client = AsyncMock()
    mock_matrix_client.add_event_callback = MagicMock()
    mock_matrix_client.close = AsyncMock()

    async def blocking_check_connection() -> None:
        """Block forever so check_connection_task stays pending at shutdown, forcing _await_background_task_shutdown to exercise its timeout→cancel path."""
        await asyncio.Event().wait()

    real_get_running_loop = asyncio.get_running_loop

    def _patched_get_running_loop() -> asyncio.AbstractEventLoop:
        loop = real_get_running_loop()
        if not isinstance(loop, InlineExecutorLoop):
            loop = InlineExecutorLoop(loop)
        if not hasattr(loop, "_signal_handler_patched"):

            def _fake_add_signal_handler(_sig, handler):
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
            side_effect=blocking_check_connection,
        ),
        patch("mmrelay.main.shutdown_plugins"),
        patch("mmrelay.main.stop_message_queue"),
        patch("mmrelay.main.sys.platform", "linux"),
    ):
        mock_queue = MagicMock()
        mock_queue.ensure_processor_started = MagicMock()
        mock_get_queue.return_value = mock_queue

        import mmrelay.meshtastic_utils as mu

        original_shutting_down = mu.shutting_down
        try:
            asyncio.run(main(config))
            assert mu.shutting_down
        finally:
            mu.shutting_down = original_shutting_down


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
    def _reload_main_module(self) -> Iterator[None]:
        import importlib

        import mmrelay.main as main_module

        yield
        importlib.reload(main_module)

    def test_invalid_ready_heartbeat_seconds_type_error(self) -> None:
        """Invalid MMRELAY_READY_HEARTBEAT_SECONDS logs warning and uses default."""
        import importlib

        import mmrelay.constants.app as app_constants
        import mmrelay.main as main_module

        mock_logger = MagicMock()

        with (
            patch.dict(
                "os.environ", {"MMRELAY_READY_HEARTBEAT_SECONDS": "not_a_number"}
            ),
            patch("mmrelay.log_utils.get_logger", return_value=mock_logger),
        ):
            importlib.reload(main_module)

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "MMRELAY_READY_HEARTBEAT_SECONDS" in str(call_args)
            assert (
                main_module._ready_heartbeat_seconds
                == app_constants.DEFAULT_READY_HEARTBEAT_SECONDS
            )

    def test_invalid_ready_heartbeat_seconds_value_error(self) -> None:
        """Empty string MMRELAY_READY_HEARTBEAT_SECONDS logs warning and uses default."""
        import importlib

        import mmrelay.constants.app as app_constants
        import mmrelay.main as main_module

        mock_logger = MagicMock()

        with (
            patch.dict("os.environ", {"MMRELAY_READY_HEARTBEAT_SECONDS": ""}),
            patch("mmrelay.log_utils.get_logger", return_value=mock_logger),
        ):
            importlib.reload(main_module)

            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args
            assert "MMRELAY_READY_HEARTBEAT_SECONDS" in str(call_args)
            assert (
                main_module._ready_heartbeat_seconds
                == app_constants.DEFAULT_READY_HEARTBEAT_SECONDS
            )
