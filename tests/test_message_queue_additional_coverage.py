import asyncio
import time
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
    queue._should_send_message = lambda: True

    fake_loop = MagicMock()
    fake_loop.run_in_executor.side_effect = OSError("transport down")

    async def _sleep_then_stop(_delay: float) -> None:
        queue._running = False

    with (
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
    queue._should_send_message = lambda: True

    class _LoopStub:
        def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
            self._loop = loop

        def run_in_executor(self, _executor, _func):
            fut = self._loop.create_future()
            fut.set_result(None)
            return fut

    real_loop = asyncio.get_running_loop()
    loop_stub = _LoopStub(real_loop)

    async def _sleep_then_stop(_delay: float) -> None:
        queue._running = False

    with (
        patch("mmrelay.message_queue.asyncio.get_running_loop", return_value=loop_stub),
        patch(
            "mmrelay.message_queue.asyncio.sleep",
            new=AsyncMock(side_effect=_sleep_then_stop),
        ),
        patch("mmrelay.message_queue.logger") as mock_logger,
    ):
        await queue._process_queue()

    assert mock_logger.warning.call_count >= 1
    assert any(
        "Message send returned None" in str(call)
        for call in mock_logger.warning.call_args_list
    )


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
