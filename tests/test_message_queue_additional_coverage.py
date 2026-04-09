import asyncio
import time
import typing
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import mmrelay.meshtastic_utils as meshtastic_utils
from mmrelay.constants.queue import MAX_QUEUE_SIZE
from mmrelay.message_queue import MessageQueue, QueuedMessage


def _queued_message(description: str = "msg") -> QueuedMessage:
    return QueuedMessage(
        timestamp=time.time(),
        send_function=lambda: {"id": 1},
        args=(),
        kwargs={},
        description=description,
    )


def test_enqueue_wait_timeout_drops_message_when_queue_remains_full() -> None:
    queue = MessageQueue()
    queue._running = True
    for idx in range(MAX_QUEUE_SIZE):
        queue._queue.append(_queued_message(f"seed-{idx}"))

    accepted = queue.enqueue(
        lambda: None,
        description="overflow-timeout",
        wait=True,
        timeout=0.0,
    )

    assert accepted is False
    assert queue._dropped_messages == 1


def test_enqueue_wait_returns_false_if_queue_stops_while_waiting() -> None:
    queue = MessageQueue()
    queue._running = True
    for idx in range(MAX_QUEUE_SIZE):
        queue._queue.append(_queued_message(f"seed-{idx}"))

    def _sleep_then_stop(_delay: float) -> None:
        queue._running = False

    with patch("mmrelay.message_queue.time.sleep", side_effect=_sleep_then_stop):
        accepted = queue.enqueue(
            lambda: None,
            description="overflow-stop",
            wait=True,
            timeout=None,
        )

    assert accepted is False


@pytest.mark.asyncio
async def test_drain_returns_false_when_stopped_with_pending_work() -> None:
    queue = MessageQueue()
    queue._running = False
    queue._queue.append(_queued_message("pending"))

    drained = await queue.drain(timeout=0.1)
    assert drained is False


@pytest.mark.asyncio
async def test_drain_returns_false_on_timeout() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._queue.append(_queued_message("pending"))

    with patch("mmrelay.message_queue.time.monotonic", side_effect=[0.0, 1.0]):
        drained = await queue.drain(timeout=0.1)

    assert drained is False


def test_ensure_processor_started_handles_missing_running_loop() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._processor_task = None

    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", side_effect=RuntimeError
    ):
        queue.ensure_processor_started()

    assert queue._processor_task is None


def test_ensure_processor_started_restarts_when_existing_task_is_done() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._stop_failed = False
    queue._processor_task = MagicMock(done=MagicMock(return_value=True))

    created_task = MagicMock()
    loop = MagicMock()
    loop.is_running.return_value = True
    loop.create_task.return_value = created_task

    with (
        patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=loop),
        patch.object(queue, "_process_queue", new=MagicMock(return_value=MagicMock())),
    ):
        queue.ensure_processor_started()

    assert queue._processor_task is created_task
    loop.create_task.assert_called_once()


def test_stop_raises_unexpected_task_cleanup_exception() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = False
    task_loop.is_running.return_value = False
    task_loop.run_until_complete.side_effect = ValueError("cleanup failure")

    task = MagicMock()
    task.get_loop.return_value = task_loop
    queue._processor_task = task

    with pytest.raises(ValueError, match="cleanup failure"):
        queue.stop()


@pytest.mark.asyncio
async def test_stop_handles_call_soon_runtime_error_without_stuck_state() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = False
    task_loop.call_soon.side_effect = RuntimeError("loop closed during scheduling")

    task = MagicMock()
    task.get_loop.return_value = task_loop
    queue._processor_task = task

    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", return_value=task_loop
    ):
        queue.stop()

    assert queue._running is False
    assert queue._stopping is False
    assert queue._processor_task is None
    assert queue._stop_failed is False


def test_stop_handles_call_soon_threadsafe_runtime_error_without_stuck_state() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = False
    task_loop.is_running.return_value = True
    task_loop.call_soon_threadsafe.side_effect = RuntimeError(
        "loop closed during scheduling"
    )

    task = MagicMock()
    task.get_loop.return_value = task_loop
    queue._processor_task = task

    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", side_effect=RuntimeError
    ):
        queue.stop()

    assert queue._running is False
    assert queue._stopping is False
    assert queue._processor_task is None
    assert queue._stop_failed is False


@pytest.mark.asyncio
async def test_process_queue_connection_error_requeues_message() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._executor = MagicMock()
    queue._queue.append(
        QueuedMessage(
            timestamp=time.time(),
            send_function=lambda: None,
            args=(),
            kwargs={},
            description="retry-me",
        )
    )

    connected_client = MagicMock()
    connected_client.is_connected.return_value = True
    fake_loop = MagicMock()
    fake_loop.run_in_executor.side_effect = OSError("transport down")

    async def _sleep_then_stop(_delay: float) -> None:
        queue._running = False

    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", connected_client),
        patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=fake_loop),
        patch(
            "mmrelay.message_queue.asyncio.sleep",
            new=AsyncMock(side_effect=_sleep_then_stop),
        ),
    ):
        await queue._process_queue()

    assert queue.get_queue_size() == 1
    assert queue._queue[0].description == "retry-me"


@pytest.mark.asyncio
async def test_process_queue_logs_warning_when_send_result_is_none() -> None:
    queue = MessageQueue()
    queue._running = True
    queue._executor = MagicMock()
    queue._queue.append(
        QueuedMessage(
            timestamp=time.time(),
            send_function=lambda: None,
            args=(),
            kwargs={},
            description="none-result",
        )
    )

    connected_client = MagicMock()
    connected_client.is_connected.return_value = True

    class _LoopStub:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def run_in_executor(
            self, _executor: typing.Any, _func: typing.Callable[[], typing.Any]
        ) -> asyncio.Future[typing.Any]:
            fut = self._loop.create_future()
            fut.set_result(None)
            return fut

    real_loop = asyncio.get_running_loop()
    loop_stub = _LoopStub(real_loop)

    async def _sleep_then_stop(_delay: float) -> None:
        queue._running = False

    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", connected_client),
        patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=loop_stub),
        patch(
            "mmrelay.message_queue.asyncio.sleep",
            new=AsyncMock(side_effect=_sleep_then_stop),
        ),
        patch("mmrelay.message_queue.logger") as mock_logger,
    ):
        await queue._process_queue()

    mock_logger.warning.assert_called_once()
    assert "Message send returned None" in str(mock_logger.warning.call_args)


def test_should_send_message_returns_false_when_reconnecting() -> None:
    queue = MessageQueue()
    client = MagicMock()
    with (
        patch.object(meshtastic_utils, "reconnecting", True),
        patch.object(meshtastic_utils, "meshtastic_client", client),
    ):
        assert queue._should_send_message() is False


def test_should_send_message_returns_false_when_client_reports_disconnected() -> None:
    queue = MessageQueue()
    client = MagicMock()
    client.is_connected = MagicMock(return_value=False)
    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", client),
    ):
        assert queue._should_send_message() is False


@pytest.mark.asyncio
async def test_handle_message_mapping_skips_prune_when_msgs_to_keep_non_positive() -> (
    None
):
    queue = MessageQueue()
    result = MagicMock()
    result.id = 42
    mapping_info = {
        "matrix_event_id": "$event",
        "room_id": "!room:example.org",
        "text": "hello",
        "msgs_to_keep": 0,
    }

    with (
        patch(
            "mmrelay.db_utils.async_store_message_map", new_callable=AsyncMock
        ) as mock_store,
        patch(
            "mmrelay.db_utils.async_prune_message_map", new_callable=AsyncMock
        ) as mock_prune,
    ):
        await queue._handle_message_mapping(result, mapping_info)

    mock_store.assert_awaited_once()
    mock_prune.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_message_mapping_logs_exception_if_store_fails() -> None:
    queue = MessageQueue()
    result = MagicMock()
    result.id = 99
    mapping_info = {
        "matrix_event_id": "$event",
        "room_id": "!room:example.org",
        "text": "hello",
    }

    with (
        patch(
            "mmrelay.db_utils.async_store_message_map",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db write failed"),
        ),
        patch("mmrelay.message_queue.logger") as mock_logger,
    ):
        await queue._handle_message_mapping(result, mapping_info)

    mock_logger.exception.assert_called_once_with("Error handling message mapping")


# ---- Additional coverage for remaining uncovered lines ----


def test_start_returns_false_when_stopping() -> None:
    """start() should return False when _stopping is True."""
    queue = MessageQueue()
    queue._stopping = True
    result = queue.start()
    assert result is False


def test_start_handles_runtime_error_from_get_running_loop() -> None:
    """start() should handle RuntimeError when no event loop is available."""
    queue = MessageQueue()
    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", side_effect=RuntimeError
    ):
        result = queue.start()
    assert result is True


def test_start_with_loop_not_running() -> None:
    """start() should defer when loop exists but is not running."""
    queue = MessageQueue()
    loop = MagicMock()
    loop.is_running.return_value = False
    with patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=loop):
        result = queue.start()
    assert result is True
    assert queue._processor_task is None


def test_stop_closed_task_loop() -> None:
    """stop() should handle closed task loop."""
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = True

    task = MagicMock()
    task.get_loop.return_value = task_loop
    queue._processor_task = task

    queue.stop()
    assert queue._running is False


def test_stop_same_loop_cancel_scheduled_runtime_error() -> None:
    """stop() should handle RuntimeError when scheduling cancel on same loop."""
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = False
    task_loop.call_soon.side_effect = RuntimeError("closed")

    task = MagicMock()
    task.get_loop.return_value = task_loop
    task.done.return_value = False
    queue._processor_task = task

    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", return_value=task_loop
    ):
        queue.stop()

    assert queue._running is False


def test_stop_other_loop_running_with_no_current_loop() -> None:
    """stop() from non-loop thread when task is on a different running loop."""
    queue = MessageQueue()
    queue._running = True
    queue._executor = None

    task_loop = MagicMock()
    task_loop.is_closed.return_value = False
    task_loop.is_running.return_value = True

    task = MagicMock()
    task.get_loop.return_value = task_loop
    task.done.return_value = False
    queue._processor_task = task

    with patch(
        "mmrelay.message_queue.asyncio.get_running_loop", side_effect=RuntimeError
    ):
        queue.stop()

    assert queue._running is False


def test_enqueue_rejects_when_stopping() -> None:
    """enqueue() should reject when _stopping is True."""
    queue = MessageQueue()
    queue._running = False
    queue._stopping = True
    result = queue.enqueue(lambda: None, description="test")
    assert result is False


def test_reset_failed_stop_state_while_stopping() -> None:
    """reset_failed_stop_state() should return False while stopping."""
    queue = MessageQueue()
    queue._stopping = True
    result = queue.reset_failed_stop_state()
    assert result is False


def test_reset_failed_stop_state_not_failed() -> None:
    """reset_failed_stop_state() should return True when not in failed state."""
    queue = MessageQueue()
    queue._stop_failed = False
    result = queue.reset_failed_stop_state()
    assert result is True


def test_reset_failed_stop_state_cannot_recover() -> None:
    """reset_failed_stop_state() should return False when resources still active."""
    queue = MessageQueue()
    queue._stop_failed = True
    queue._stopping = False
    queue._processor_task = MagicMock()
    queue._processor_task.done.return_value = False
    result = queue.reset_failed_stop_state()
    assert result is False


def test_enqueue_logs_queue_full_periodically() -> None:
    """enqueue with wait should log queue full warning periodically."""
    queue = MessageQueue()
    queue._running = True
    for i in range(MAX_QUEUE_SIZE):
        queue._queue.append(_queued_message(f"fill-{i}"))

    sleep_count = {"n": 0}

    def sleep_then_stop(_delay: float) -> None:
        sleep_count["n"] += 1
        if sleep_count["n"] >= 2:
            queue._running = False

    with (
        patch.object(queue, "ensure_processor_started"),
        patch("mmrelay.message_queue.time.sleep", side_effect=sleep_then_stop),
        patch("mmrelay.message_queue.logger") as mock_logger,
    ):
        accepted = queue.enqueue(
            lambda: None, description="wait-msg", wait=True, timeout=None
        )

    assert accepted is False
    warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
    assert any("queue full" in w.lower() for w in warning_calls)


@pytest.mark.asyncio
async def test_process_queue_logs_queue_depth_high() -> None:
    """_process_queue should log warning when queue depth exceeds high water mark."""
    import mmrelay.meshtastic_utils as meshtastic_utils

    queue = MessageQueue()
    queue._running = True
    queue._message_delay = 0.01

    from mmrelay.constants.queue import QUEUE_HIGH_WATER_MARK

    connected_client = MagicMock()
    connected_client.is_connected.return_value = True

    sent_count = {"n": 0}

    def _send_fn():
        sent_count["n"] += 1
        if sent_count["n"] >= 2:
            queue._running = False
        return {"id": sent_count["n"]}

    for i in range(QUEUE_HIGH_WATER_MARK + 1):
        queue._queue.append(
            QueuedMessage(
                timestamp=time.time(),
                send_function=_send_fn,
                args=(),
                kwargs={},
                description=f"depth-{i}",
            )
        )

    real_loop = asyncio.get_running_loop()

    class _SyncExecutor:
        def run_in_executor(self, _exc, func):
            fut = real_loop.create_future()
            try:
                result = func()
                fut.set_result(result)
            except Exception as e:
                fut.set_exception(e)
            return fut

    sync_exec = _SyncExecutor()

    async def _sleep_then_stop(delay: float) -> None:
        if sent_count["n"] >= 2:
            queue._running = False

    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", connected_client),
        patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=sync_exec),
        patch(
            "mmrelay.message_queue.asyncio.sleep",
            new=AsyncMock(side_effect=_sleep_then_stop),
        ),
    ):
        queue._executor = MagicMock()
        await queue._process_queue()


def test_should_send_message_returns_true_when_connected() -> None:
    """_should_send_message should return True when client is connected."""
    import mmrelay.meshtastic_utils as meshtastic_utils

    queue = MessageQueue()
    client = MagicMock()
    client.is_connected.return_value = True
    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", client),
    ):
        assert queue._should_send_message() is True


def test_should_send_message_callable_is_connected() -> None:
    """_should_send_message should handle callable is_connected."""
    import mmrelay.meshtastic_utils as meshtastic_utils

    queue = MessageQueue()
    client = MagicMock()
    client.is_connected = lambda: True
    with (
        patch.object(meshtastic_utils, "reconnecting", False),
        patch.object(meshtastic_utils, "meshtastic_client", client),
    ):
        assert queue._should_send_message() is True


def test_stop_message_queue_function() -> None:
    """stop_message_queue should call stop on the global queue."""
    from mmrelay.message_queue import _message_queue, stop_message_queue

    with patch.object(_message_queue, "stop") as mock_stop:
        stop_message_queue()
    mock_stop.assert_called_once()


def test_reset_message_queue_failed_state_function() -> None:
    """reset_message_queue_failed_state should delegate to global queue."""
    from mmrelay.message_queue import _message_queue, reset_message_queue_failed_state

    with patch.object(
        _message_queue, "reset_failed_stop_state", return_value=True
    ) as mock_reset:
        result = reset_message_queue_failed_state()
    assert result is True
    mock_reset.assert_called_once()


def test_get_queue_status_function() -> None:
    """get_queue_status should return status from global queue."""
    from mmrelay.message_queue import get_queue_status

    status = get_queue_status()
    assert "running" in status
    assert "queue_size" in status


@pytest.mark.asyncio
async def test_handle_message_mapping_dict_result_with_id() -> None:
    """_handle_message_mapping should work with SimpleNamespace result from dict extraction."""
    from types import SimpleNamespace

    queue = MessageQueue()
    result = SimpleNamespace(id="42")
    mapping_info = {
        "matrix_event_id": "$evt",
        "room_id": "!room:example.org",
        "text": "hello",
    }

    with (
        patch(
            "mmrelay.db_utils.async_store_message_map", new_callable=AsyncMock
        ) as mock_store,
        patch("mmrelay.db_utils.async_prune_message_map", new_callable=AsyncMock),
    ):
        await queue._handle_message_mapping(result, mapping_info)

    mock_store.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_message_mapping_missing_fields_skips_store() -> None:
    """_handle_message_mapping should skip store when required fields are missing."""
    queue = MessageQueue()
    result = MagicMock()
    result.id = 42
    mapping_info = {"room_id": "!room:example.org"}

    with (
        patch(
            "mmrelay.db_utils.async_store_message_map", new_callable=AsyncMock
        ) as mock_store,
    ):
        await queue._handle_message_mapping(result, mapping_info)

    mock_store.assert_not_awaited()
