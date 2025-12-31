import asyncio
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

import mmrelay.meshtastic_utils as mu
from mmrelay.meshtastic_utils import reconnect


class _FakeProgress:
    updates = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add_task(self, *_args, **_kwargs):
        return "task"

    def update(self, *_args, **_kwargs):
        if self.updates is not None:
            self.updates.append(1)


class _DummyColumn:
    def __init__(self, *args, **kwargs):
        pass


class _FailedExecutorLoop:
    def __init__(self, future):
        self._future = future

    def run_in_executor(self, *_args, **_kwargs):
        return self._future


def _sleep_and_shutdown(_seconds):
    mu.shutting_down = True
    return None


def _mark_shutdown(*_args, **_kwargs):
    mu.shutting_down = True


@pytest.mark.asyncio
async def test_reconnect_rich_progress_breaks_on_shutdown(reset_meshtastic_globals):
    progress_updates = []

    fake_rich = types.ModuleType("rich")
    fake_progress = types.ModuleType("rich.progress")
    _FakeProgress.updates = progress_updates
    fake_progress.Progress = _FakeProgress
    fake_progress.BarColumn = _DummyColumn
    fake_progress.TextColumn = _DummyColumn
    fake_progress.TimeRemainingColumn = _DummyColumn
    fake_rich.progress = fake_progress

    try:
        with (
            patch.dict(
                sys.modules, {"rich": fake_rich, "rich.progress": fake_progress}
            ),
            patch("mmrelay.meshtastic_utils.DEFAULT_BACKOFF_TIME", 1),
            patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=False),
            patch(
                "mmrelay.meshtastic_utils.asyncio.sleep",
                new_callable=AsyncMock,
                side_effect=_sleep_and_shutdown,
            ),
            patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
            patch("mmrelay.meshtastic_utils.logger") as mock_logger,
        ):
            await reconnect()
    finally:
        _FakeProgress.updates = None

    mock_connect.assert_not_called()
    assert len(progress_updates) == 1
    mock_logger.debug.assert_any_call(
        "Shutdown in progress. Aborting reconnection attempts."
    )


@pytest.mark.asyncio
async def test_reconnect_logs_exception_and_backs_off(reset_meshtastic_globals):
    running_loop = asyncio.get_running_loop()
    failed_future = running_loop.create_future()
    failed_future.set_exception(RuntimeError("boom"))
    loop = _FailedExecutorLoop(failed_future)

    with (
        patch("mmrelay.meshtastic_utils.is_running_as_service", return_value=True),
        patch(
            "mmrelay.meshtastic_utils.asyncio.get_running_loop",
            return_value=loop,
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
