import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import reconnect


@pytest.mark.asyncio
async def test_reconnect_rich_progress_breaks_on_shutdown(reset_meshtastic_globals):
    progress_updates = []

    class FakeProgress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def add_task(self, *_args, **_kwargs):
            return "task"

        def update(self, *_args, **_kwargs):
            progress_updates.append(1)

    fake_rich = types.ModuleType("rich")
    fake_progress = types.ModuleType("rich.progress")

    class DummyColumn:
        def __init__(self, *args, **kwargs):
            pass

    fake_progress.Progress = FakeProgress
    fake_progress.BarColumn = DummyColumn
    fake_progress.TextColumn = DummyColumn
    fake_progress.TimeRemainingColumn = DummyColumn
    fake_rich.progress = fake_progress

    def _sleep(_seconds):
        mu.shutting_down = True
        return None

    with (
        patch.dict(sys.modules, {"rich": fake_rich, "rich.progress": fake_progress}),
        patch("mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME", 1),
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            new_callable=AsyncMock,
            side_effect=_sleep,
        ),
        patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await reconnect()

    mock_connect.assert_not_called()
    assert progress_updates
    assert any(
        "Shutdown in progress" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_reconnect_logs_exception_and_backs_off(reset_meshtastic_globals):
    class DummyLoop:
        async def run_in_executor(self, *_args, **_kwargs):
            raise RuntimeError("boom")

    def _mark_shutdown(*_args, **_kwargs):
        mu.shutting_down = True

    with (
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch(
            "mmrelay.meshtastic_utils.asyncio.get_running_loop",
            return_value=DummyLoop(),
        ),
        patch("mmrelay.meshtastic_utils.asyncio.sleep", new_callable=AsyncMock),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        mock_logger.exception.side_effect = _mark_shutdown
        await reconnect()

    mock_logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_logs_cancelled(reset_meshtastic_globals):
    with (
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch(
            "mmrelay.meshtastic_utils.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ),
        patch("mmrelay.meshtastic_utils.logger") as mock_logger,
    ):
        await reconnect()

    mock_logger.info.assert_any_call("Reconnection task was cancelled.")
