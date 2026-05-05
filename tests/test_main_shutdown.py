#!/usr/bin/env python3
"""
Test suite for shutdown and connection failure paths in main().

Covers:
- Meshtastic/Matrix connection failures at startup
- Shutdown cleanup: client close, BLE disconnect, plugin timeout
- Blocking cleanup off event-loop thread
- Shutdown timeout warnings and unexpected close errors
- Shutdown step exception logging and suppression
"""

import asyncio
import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.network import (
    CONNECTION_TYPE_SERIAL,
    MESHTASTIC_CLOSE_TIMEOUT_SECS,
)
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_noop,
    _ImmediateEvent,
    _make_async_raise,
    _make_async_return,
    _make_patched_get_running_loop,
    _reset_all_mmrelay_globals,
    _thread_backed_to_thread,
    inline_to_thread,
)
from tests.constants import TEST_MATRIX_HOMESERVER, TEST_ROOM_ID_1

__all__ = [
    "test_main_meshtastic_connection_failure",
    "test_main_matrix_connection_failure",
    "test_main_closes_meshtastic_client_on_shutdown",
    "test_main_shutdown_disconnects_ble_interface",
    "test_main_shutdown_cancels_reconnect_before_ble_disconnect_and_unsubscribes",
    "test_main_shutdown_runs_blocking_cleanup_off_event_loop_thread",
    "test_main_shutdown_plugin_timeout_continues_cleanup",
    "test_main_shutdown_timeout_warns_and_continues",
    "test_main_shutdown_logs_unexpected_close_error",
    "test_main_shutdown_uses_blocking_timeout_helper",
    "test_main_shutdown_success_logs_close_complete",
    "test_exception_in_shutdown_step_logs_error",
    "test_exception_in_stop_message_queue_logs_error",
    "test_shutdown_exceptions_are_logged_and_suppressed",
]


@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.join_matrix_room")
@patch("mmrelay.main.stop_message_queue")
def test_main_meshtastic_connection_failure(
    mock_stop_queue,
    mock_join_room,
    mock_connect_matrix,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
    mock_connect_meshtastic,
    mock_config,
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
        with pytest.raises(ConnectionError):
            asyncio.run(main(mock_config))

    mock_connect_matrix.assert_not_called()
    mock_join_room.assert_not_called()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.stop_message_queue")
def test_main_matrix_connection_failure(
    mock_stop_queue,
    mock_connect_matrix,
    mock_connect_meshtastic,
    mock_start_queue,
    mock_load_plugins,
    mock_init_db,
    mock_config,
):
    """
    Test that an exception during Matrix connection is raised and not suppressed during main application startup.

    Mocks the Matrix connection to raise an exception and verifies that the main function propagates the error.
    """
    # Mock Meshtastic client
    mock_meshtastic_client = MagicMock()
    mock_connect_meshtastic.return_value = mock_meshtastic_client

    mock_connect_matrix.side_effect = _make_async_raise(
        RuntimeError("Matrix connection failed")
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
        with pytest.raises(RuntimeError, match="Matrix connection failed"):
            asyncio.run(main(mock_config))


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.join_matrix_room")
@patch("mmrelay.main.shutdown_plugins")
@patch("mmrelay.main.stop_message_queue")
def test_main_closes_meshtastic_client_on_shutdown(
    _mock_stop_queue,
    _mock_shutdown_plugins,
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
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
        asyncio.run(main(mock_config))

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
    mock_disconnect_iface,
    _mock_stop_queue,
    mock_get_queue,
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
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
        asyncio.run(main(mock_config))

    mock_disconnect_iface.assert_called_once_with(mock_iface, reason="shutdown")
    import mmrelay.meshtastic_utils as mu

    assert mu.meshtastic_iface is None


def test_main_shutdown_cancels_reconnect_before_ble_disconnect_and_unsubscribes(
    mock_config,
):
    """Shutdown should cancel reconnect before BLE disconnect and unsubscribe callbacks."""

    import mmrelay.meshtastic_utils as mu

    _reset_all_mmrelay_globals()

    mock_iface = MagicMock()
    reconnect_task = MagicMock()
    mu.reconnect_task = reconnect_task

    def _connect_meshtastic(*_args: Any, **_kwargs: Any) -> Any:
        mu.meshtastic_iface = mock_iface
        return mock_iface

    mock_matrix_client = MagicMock()
    mock_matrix_client.close = AsyncMock()

    def _assert_disconnect_after_cancel(iface: Any, reason: str = "") -> None:
        assert iface is mock_iface
        assert reason == "shutdown"
        assert reconnect_task.cancel.called

    try:
        with (
            patch("mmrelay.main.initialize_database"),
            patch("mmrelay.main.load_plugins"),
            patch("mmrelay.main.start_message_queue"),
            patch(
                "mmrelay.main.connect_meshtastic",
                side_effect=_connect_meshtastic,
            ),
            patch(
                "mmrelay.main.connect_matrix",
                side_effect=_make_async_return(mock_matrix_client),
            ),
            patch("mmrelay.main.join_matrix_room", side_effect=_async_noop),
            patch("mmrelay.main.shutdown_plugins"),
            patch("mmrelay.main.stop_message_queue"),
            patch("mmrelay.main.get_message_queue") as mock_get_queue,
            patch(
                "mmrelay.main.meshtastic_utils._disconnect_ble_interface",
                side_effect=_assert_disconnect_after_cancel,
            ) as mock_disconnect_iface,
            patch(
                "mmrelay.main.meshtastic_utils.unsubscribe_meshtastic_callbacks",
            ) as mock_unsubscribe_callbacks,
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            asyncio.run(main(mock_config))

        reconnect_task.cancel.assert_called_once()
        mock_disconnect_iface.assert_called_once_with(mock_iface, reason="shutdown")
        mock_unsubscribe_callbacks.assert_called_once()
    finally:
        _reset_all_mmrelay_globals()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.join_matrix_room")
def test_main_shutdown_runs_blocking_cleanup_off_event_loop_thread(
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
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
        asyncio.run(main(mock_config))

    assert not cleanup_context["plugins_has_running_loop"]
    assert not cleanup_context["queue_has_running_loop"]
    mock_matrix_client.close.assert_awaited_once()


@patch("mmrelay.main.initialize_database")
@patch("mmrelay.main.load_plugins")
@patch("mmrelay.main.start_message_queue")
@patch("mmrelay.main.connect_meshtastic")
@patch("mmrelay.main.connect_matrix")
@patch("mmrelay.main.join_matrix_room")
def test_main_shutdown_plugin_timeout_continues_cleanup(
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
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
        asyncio.run(main(mock_config))

    mock_shutdown_plugins.assert_called_once()
    mock_stop_queue.assert_called_once()
    mock_matrix_client.close.assert_awaited_once()
    assert any(
        "Timed out stopping" in str(call) and "plugins" in str(call)
        for call in mock_logger.warning.call_args_list
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
    mock_config,
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
    with mu._ble_executor_lock:
        original_ble_future = mu._ble_future
        original_ble_future_address = mu._ble_future_address
        original_ble_future_started_at = mu._ble_future_started_at
        original_ble_future_timeout_secs = mu._ble_future_timeout_secs
    try:
        pending_ble_future = MagicMock()
        pending_ble_future.done.return_value = False
        with mu._ble_executor_lock:
            mu._ble_future = pending_ble_future
            mu._ble_future_address = "AA:BB:CC:DD:EE:FF"
            mu._ble_future_started_at = mu.time.monotonic() - 3.0
            mu._ble_future_timeout_secs = 20.0
        with (
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.meshtastic_utils._run_blocking_with_timeout",
                side_effect=TimeoutError(
                    "meshtastic-client-close-shutdown timed out after 10.0s"
                ),
            ),
        ):
            asyncio.run(main(mock_config))
    finally:
        mu.meshtastic_client = original_client
        mu.meshtastic_iface = original_iface
        mu.shutting_down = original_shutting_down
        mu.reconnecting = original_reconnecting
        with mu._ble_executor_lock:
            mu._ble_future = original_ble_future
            mu._ble_future_address = original_ble_future_address
            mu._ble_future_started_at = original_ble_future_started_at
            mu._ble_future_timeout_secs = original_ble_future_timeout_secs

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
    mock_config,
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
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
            patch(
                "mmrelay.main.meshtastic_utils._run_blocking_with_timeout",
                side_effect=ValueError("boom"),
            ),
        ):
            asyncio.run(main(mock_config))
    finally:
        mu.meshtastic_client = original_client
        mu.meshtastic_iface = original_iface
        mu.shutting_down = original_shutting_down
        mu.reconnecting = original_reconnecting

    assert any(
        "Unexpected error during Meshtastic client close" in str(call)
        for call in mock_meshtastic_logger.exception.call_args_list
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
    mock_config,
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
    timeout_sentinel = MESHTASTIC_CLOSE_TIMEOUT_SECS + 1
    try:
        with (
            patch(
                "mmrelay.main.MESHTASTIC_CLOSE_TIMEOUT_SECS",
                timeout_sentinel,
            ),
            patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
            patch(
                "mmrelay.main.asyncio.get_running_loop",
                side_effect=_make_patched_get_running_loop(),
            ),
            patch("mmrelay.main.asyncio.to_thread", side_effect=inline_to_thread),
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
        ):
            asyncio.run(main(mock_config))

        mock_run_blocking_with_timeout.assert_called_once()
        args, kwargs = mock_run_blocking_with_timeout.call_args
        close_callable = args[0]
        assert callable(close_callable)
        mock_connect_meshtastic.return_value.close.assert_called_once()
        assert kwargs.get("timeout") == timeout_sentinel
        assert kwargs.get("label") == "meshtastic-client-close-shutdown"
        assert kwargs.get("timeout_log_level") is None
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
@patch("mmrelay.main.meshtastic_utils._log_ble_shutdown_state")
@patch("mmrelay.main.meshtastic_logger")
@patch("mmrelay.main.meshtastic_utils._run_blocking_with_timeout")
def test_main_shutdown_success_logs_close_complete(
    mock_run_blocking_with_timeout,
    mock_meshtastic_logger,
    mock_log_ble_shutdown_state,
    _mock_stop_queue,
    mock_get_queue,
    mock_join_room,
    mock_connect_matrix,
    mock_connect_meshtastic,
    _mock_start_queue,
    _mock_load_plugins,
    _mock_init_db,
    mock_config,
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
            patch("mmrelay.main.meshtastic_utils.check_connection", new=_async_noop),
        ):
            asyncio.run(main(mock_config))
    finally:
        mu.meshtastic_client = original_client
        mu.meshtastic_iface = original_iface
        mu.shutting_down = original_shutting_down
        mu.reconnecting = original_reconnecting

    mock_run_blocking_with_timeout.assert_called_once()
    mock_log_ble_shutdown_state.assert_called_once_with(context="shutdown")
    mock_meshtastic_logger.info.assert_any_call("Meshtastic client closed successfully")


# =============================================================================
# TestRunBlockingShutdownStep (converted from unittest.TestCase)
# =============================================================================


def test_exception_in_shutdown_step_logs_error():
    """Exceptions in shutdown step are captured and logged."""
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

        assert any(
            "Error while stopping" in str(call)
            for call in mock_logger.error.call_args_list
        )


def test_exception_in_stop_message_queue_logs_error() -> None:
    """Message queue shutdown failures should be logged and not abort teardown."""
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
            mock_stop_message_queue.side_effect = ValueError("Queue stop error")

            asyncio.run(main(config))

            mock_shutdown.assert_called_once()
            mock_stop_message_queue.assert_called_once()
            mock_matrix_client.close.assert_awaited_once()

        assert any(
            "Error while stopping" in str(call) and "message queue" in str(call)
            for call in mock_logger.error.call_args_list
        )


def test_shutdown_exceptions_are_logged_and_suppressed():
    """KeyboardInterrupt and SystemExit raised by shutdown steps should be logged, not re-raised."""
    for exception_class in (KeyboardInterrupt, SystemExit):
        # subTest removed - converted to inline loop
        try:
            config = {
                "matrix_rooms": [{"id": TEST_ROOM_ID_1}],
                "matrix": {"homeserver": TEST_MATRIX_HOMESERVER},
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
                patch("mmrelay.main.shutdown_plugins") as mock_shutdown,
                patch("mmrelay.main.stop_message_queue") as mock_stop_message_queue,
                patch("mmrelay.main.asyncio.Event", return_value=_ImmediateEvent()),
                patch(
                    "mmrelay.main.asyncio.get_running_loop",
                    side_effect=_make_patched_get_running_loop(),
                ),
                patch(
                    "mmrelay.main.asyncio.to_thread",
                    side_effect=inline_to_thread,
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

            assert any(
                "Error while stopping" in str(call)
                for call in mock_logger.error.call_args_list
            )

        finally:
            _reset_all_mmrelay_globals()
