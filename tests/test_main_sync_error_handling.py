#!/usr/bin/env python3
"""
Test suite for Matrix sync loop retry and error handling in main().

Covers:
- timeout warnings and retries
- client error warnings and retries
- connection error exception logging
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_noop,
    _ImmediateEvent,
    _make_patched_get_running_loop,
    _OnePassEvent,
    _reset_all_mmrelay_globals,
    inline_to_thread,
    mock_config,
)

__all__ = [
    "test_sync_timeout_logs_warning_and_retries",
    "test_sync_client_error_logs_warning_and_retries",
    "test_sync_connection_error_logs_exception",
]
# =============================================================================
# TestMatrixSyncLoopErrorHandling (converted from unittest.TestCase)
# =============================================================================


def test_sync_timeout_logs_warning_and_retries():
    """TimeoutError from sync_task.result() logs warning and retries."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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
            patch("mmrelay.main.asyncio.Event", side_effect=_capture_shutdown_event),
        ):
            mock_queue = MagicMock()
            mock_queue.ensure_processor_started = MagicMock()
            mock_get_queue.return_value = mock_queue

            await main(config)

        return mock_logger

    mock_logger = asyncio.run(run_test())

    assert call_count[0] == 2  # first attempt + one retry

    assert any(
        "Matrix sync timed out" in str(call)
        for call in mock_logger.warning.call_args_list
    )


def test_sync_client_error_logs_warning_and_retries():
    """ClientError from sync_task.result() logs warning and retries."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

    assert call_count[0] == 2  # first attempt + one retry

    assert any(
        "Matrix sync failed, retrying" in str(call)
        for call in mock_logger.warning.call_args_list
    )


def test_sync_connection_error_logs_exception():
    """ConnectionError/OSError/RuntimeError/ValueError from sync logs exception."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
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

    assert any(
        "Matrix sync failed" in str(call)
        for call in mock_logger.exception.call_args_list
    )
