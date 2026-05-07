#!/usr/bin/env python3
"""
Test suite for Matrix sync loop retry and error handling in main().

Covers:
- timeout warnings and retries
- client error warnings and retries
- connection error exception logging
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError

from mmrelay.constants.network import CONNECTION_TYPE_SERIAL
from mmrelay.main import main
from tests._test_main_helpers import (
    _async_noop,
)
from tests.helpers import (
    inline_to_thread,
    make_patched_get_running_loop,
)

# =============================================================================
# TestMatrixSyncLoopErrorHandling (converted from unittest.TestCase)
# =============================================================================


@pytest.mark.parametrize(
    "raised_exc, logger_method, expected_substr",
    [
        (asyncio.TimeoutError("Sync timeout"), "warning", "Matrix sync timed out"),
        (ClientError("Network error"), "warning", "Matrix sync failed, retrying"),
        (ConnectionError("Connection lost"), "exception", "Matrix sync failed"),
    ],
    ids=["timeout", "client_error", "connection_error"],
)
def test_sync_failure_logs_and_retries(
    raised_exc: Exception, logger_method: str, expected_substr: str
) -> None:
    """Sync failures log warnings/errors and retry."""
    config = {
        "matrix_rooms": [{"id": "!room:matrix.org"}],
        "matrix": {"homeserver": "https://matrix.org"},
        "meshtastic": {"connection_type": CONNECTION_TYPE_SERIAL},
    }

    call_count = 0

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

        def sync_forever_side_effect(*_args, **_kwargs) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise raised_exc
            if not created_events:
                raise RuntimeError(
                    "No asyncio.Event was captured; main() may not create one before sync_forever"
                )
            for event in created_events:
                event.set()

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
                side_effect=make_patched_get_running_loop(),
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

    assert call_count == 2  # first attempt + one retry

    method_calls = getattr(mock_logger, logger_method).call_args_list
    assert any(c.args and expected_substr in c.args[0] for c in method_calls)
