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
            """
            Initialize the instance.
            
            Parameters:
                *args: Positional arguments accepted for compatibility and ignored.
                **kwargs: Keyword arguments accepted for compatibility and ignored.
            """
            pass

        def __enter__(self):
            """
            Provide the context manager's entrance value for use with a `with` statement.
            
            Returns:
                self: The context manager instance to be bound to the target of the `with` statement.
            """
            return self

        def __exit__(self, exc_type, exc, tb):
            """
            Ensure exceptions are propagated rather than suppressed when used as a context manager.
            
            Parameters:
                exc_type (type | None): Exception class if an exception was raised inside the context, otherwise None.
                exc (BaseException | None): Exception instance raised inside the context, otherwise None.
                tb (types.TracebackType | None): Traceback object for the exception, otherwise None.
            
            Returns:
                bool: `False` to indicate the context manager does not suppress exceptions (they should be re-raised).
            """
            return False

        def add_task(self, *_args, **_kwargs):
            """
            Add a new background task and return its identifier.
            
            Returns:
                str: Identifier for the created task (currently the literal "task").
            """
            return "task"

        def update(self, *_args, **_kwargs):
            """
            Record a progress update by appending a marker to the shared list.
            
            Appends the integer `1` to the outer-scope list `progress_updates` to indicate that a progress update occurred. Accepts arbitrary positional and keyword arguments for compatibility but ignores them.
            
            Parameters:
                _args: Ignored positional arguments.
                _kwargs: Ignored keyword arguments.
            """
            progress_updates.append(1)

    fake_rich = types.ModuleType("rich")
    fake_progress = types.ModuleType("rich.progress")

    class DummyColumn:
        def __init__(self, *args, **kwargs):
            """
            Initialize the instance.
            
            Parameters:
                *args: Positional arguments accepted for compatibility and ignored.
                **kwargs: Keyword arguments accepted for compatibility and ignored.
            """
            pass

    fake_progress.Progress = FakeProgress
    fake_progress.BarColumn = DummyColumn
    fake_progress.TextColumn = DummyColumn
    fake_progress.TimeRemainingColumn = DummyColumn
    fake_rich.progress = fake_progress

    def _sleep(_seconds):
        """
        Mark the module as shutting down.
        
        Sets mmrelay.meshtastic_utils.shutting_down to True and ignores the provided `_seconds` value.
        
        Parameters:
            _seconds (float): Ignored; present to match the asyncio.sleep signature.
        """
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
    assert len(progress_updates) == 1
    assert any(
        "Shutdown in progress" in call.args[0]
        for call in mock_logger.debug.call_args_list
    )


@pytest.mark.asyncio
async def test_reconnect_logs_exception_and_backs_off(reset_meshtastic_globals):
    def _mark_shutdown(*_args, **_kwargs):
        """
        Mark the mesh relay as shutting down.
        
        Sets mmrelay.meshtastic_utils.shutting_down to True. Accepts arbitrary positional and keyword
        arguments so it can be used as a callback or side-effect function.
        """
        mu.shutting_down = True

    running_loop = asyncio.get_running_loop()
    failed_future = running_loop.create_future()
    failed_future.set_exception(RuntimeError("boom"))

    class DummyLoop:
        def run_in_executor(self, *_args, **_kwargs):
            """
            Provide a Future that is already failed with a RuntimeError.
            
            Returns:
                asyncio.Future: A future already completed with RuntimeError("boom").
            """
            return failed_future

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