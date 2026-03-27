#!/usr/bin/env python3
"""
Test suite for the MMRelay message queue system.

Tests the FIFO message queue functionality including:
- Message ordering (first in, first out)
- Rate limiting enforcement
- Connection state awareness
- Queue size limits
- Error handling
"""

import asyncio
import contextlib
import time
import unittest
from concurrent.futures import Executor
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mmrelay.constants.queue import MAX_QUEUE_SIZE
from mmrelay.message_queue import (
    MessageQueue,
    QueuedMessage,
    queue_message,
    start_message_queue,
)
from tests.constants import TEST_SHORT_DELAY


class MockSendFunction:
    """Mock send function that records call details for testing purposes."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, text: str, **kwargs) -> dict:
        """
        Simulates sending a message and records the call details.

        Parameters:
            text (str): The message text to send.

        Returns:
            dict: A dictionary containing a unique 'id' for the sent message.
        """
        self.calls.append({"text": text, "kwargs": kwargs, "timestamp": time.time()})
        return {"id": len(self.calls)}


mock_send_function = MockSendFunction()


class TestMessageQueue(unittest.TestCase):
    """Test cases for the MessageQueue class."""

    def setUp(self):
        """
        Set up test fixtures: initialize a MessageQueue, clear mock send records, force sending allowed, and create a dedicated asyncio event loop whose run_in_executor is patched to execute functions synchronously while returning an awaitable Future.

        Detailed behavior:
        - Creates a new MessageQueue assigned to self.queue.
        - Clears mock_send_function.calls.
        - Replaces self.queue._should_send_message with a lambda that returns True.
        - Creates a dedicated asyncio event loop, sets it as the current loop, and stores it on self.loop.
        - Saves the loop's original run_in_executor on self.original_run_in_executor.
        - Replaces run_in_executor with a synchronous wrapper that executes the supplied callable immediately and returns an asyncio.Future completed with the callable's result or exception, preserving the awaitable contract for tests.
        """
        self.queue = MessageQueue()
        # Clear mock function calls for each test
        mock_send_function.calls.clear()
        # Mock the _should_send_message method to always return True for tests
        self.queue._should_send_message = lambda: True

        # Use a dedicated event loop and patch run_in_executor to return an awaitable Future
        real_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(real_loop)
        self.loop = real_loop

        # Store original run_in_executor for restoration
        self.original_run_in_executor = real_loop.run_in_executor

        def sync_run_in_executor(
            executor: Executor | None, func: Callable[..., Any], *args: Any
        ) -> asyncio.Future[Any]:
            """
            Run a callable synchronously but return an awaitable Future that is already completed with its result or exception.

            This preserves the awaitable contract expected by code that uses run_in_executor while executing the provided function synchronously on the current thread.

            Parameters:
                executor: Executor instance (unused but part of the signature).
                func: Function to execute.
                *args: Positional arguments passed to `func`.

            Returns:
                A Future created on the test event loop and completed with `func`'s return value or raised exception.
            """
            fut = real_loop.create_future()
            try:
                res = func(*args)
                fut.set_result(res)
            except Exception as e:
                fut.set_exception(e)
            return fut

        real_loop.run_in_executor = sync_run_in_executor

    def tearDown(self):
        """
        Tear down test fixtures: stop the MessageQueue if running, restore the event loop's original run_in_executor, close the dedicated per-test loop, and clear the global event loop reference.

        This ensures the per-test asyncio loop created in setUp is properly shut down and any monkey-patched
        run_in_executor is restored. Suppresses errors when clearing the global event loop reference.
        """
        if self.queue.is_running():
            self.queue.stop()

        # Restore original run_in_executor and clean up the dedicated event loop created in setUp
        loop = getattr(self, "loop", None)
        if loop is not None:
            try:
                if hasattr(self, "original_run_in_executor"):
                    loop.run_in_executor = self.original_run_in_executor
                if not loop.is_closed():
                    loop.close()
            finally:
                with contextlib.suppress(Exception):
                    asyncio.set_event_loop(None)

    @property
    def sent_messages(self):
        """
        Return the list of records for calls made to the test mock send function.

        Each list item is a dict-like record appended by the mock: it contains at least the sent text/payload, any kwargs, and a timestamp.
        """
        return mock_send_function.calls

    def test_fifo_ordering(self):
        """
        Verifies that the message queue sends messages in the order they were enqueued (FIFO).

        This test enqueues multiple messages, waits for them to be processed, and asserts that they are sent in the same order as they were added to the queue.
        """

        # Use asyncio to properly test the async queue
        async def async_test():
            # Start queue with fast rate for testing
            """
            Asynchronously tests that messages are processed and sent in FIFO order by the message queue.

            This test enqueues multiple messages, waits for them to be processed, and asserts that they are sent in the order they were enqueued.
            """
            self.queue.start(message_delay=TEST_SHORT_DELAY)

            # Ensure processor starts
            self.queue.ensure_processor_started()

            # Queue multiple messages (reduced for faster testing)
            messages = ["First", "Second", "Third"]
            for msg in messages:
                success = self.queue.enqueue(
                    mock_send_function,
                    text=msg,
                    description=f"Test message: {msg}",
                )
                self.assertTrue(success)

            # Wait for processing to complete with a timeout
            drained = await self.queue.drain(timeout=15.0)
            if not drained:
                self.fail(
                    f"Queue processing timed out. Sent {len(self.sent_messages)}/{len(messages)} messages, queue size: {self.queue.get_queue_size()}"
                )

            # Check that messages were sent in order
            self.assertEqual(len(self.sent_messages), len(messages))
            for i, expected_msg in enumerate(messages):
                self.assertEqual(self.sent_messages[i]["text"], expected_msg)

        # Run the async test on the dedicated loop so the patched executor is used
        self.loop.run_until_complete(async_test())

    def test_rate_limiting(self):
        """
        Verify that the message queue enforces rate limiting by delaying the sending of messages according to the configured interval.
        """

        async def async_test():
            """
            Verify the MessageQueue enforces rate limiting: when two messages are enqueued, the first is sent soon after processing starts and the second is delayed until the configured message_delay has elapsed.
            """
            clock = {"now": 0.0}
            sent_timestamps: list[float] = []
            real_sleep = asyncio.sleep

            def _monotonic() -> float:
                return clock["now"]

            def _time() -> float:
                return clock["now"]

            async def _fake_sleep(delay: float) -> None:
                clock["now"] += delay
                await real_sleep(0)

            def _send(text: str) -> dict[str, int | str]:
                sent_timestamps.append(clock["now"])
                return {"id": len(sent_timestamps), "text": text}

            with (
                patch("mmrelay.message_queue.MINIMUM_MESSAGE_DELAY", 0.0),
                patch("mmrelay.message_queue.time.monotonic", side_effect=_monotonic),
                patch("mmrelay.message_queue.time.time", side_effect=_time),
                patch("mmrelay.message_queue.asyncio.sleep", side_effect=_fake_sleep),
            ):
                message_delay = 0.05
                self.queue.start(message_delay=message_delay)
                self.queue.ensure_processor_started()

                # Queue two messages
                self.queue.enqueue(_send, text="First")
                self.queue.enqueue(_send, text="Second")

                self.assertTrue(await self.queue.drain(timeout=1.0))
                self.assertEqual(len(sent_timestamps), 2)
                # Verify rate limiting: second message delayed by at least message_delay
                self.assertGreaterEqual(
                    sent_timestamps[1] - sent_timestamps[0],
                    message_delay,
                )

        self.loop.run_until_complete(async_test())

    def test_stop_waits_for_task_on_running_loop(self):
        """stop should wait on tasks running in a different event loop."""
        queue = MessageQueue()
        queue._running = True

        fake_loop = MagicMock()
        fake_loop.is_closed.return_value = False
        fake_loop.is_running.return_value = True

        fake_task = MagicMock()
        fake_task.get_loop.return_value = fake_loop
        fake_task.done.return_value = False
        fake_task.add_done_callback.side_effect = lambda callback: callback(fake_task)
        queue._processor_task = fake_task

        fake_loop.call_soon_threadsafe.side_effect = (
            lambda callback, *args, **kwargs: callback(*args, **kwargs)
        )

        queue.stop()

        fake_loop.call_soon_threadsafe.assert_called_once()
        fake_task.cancel.assert_called_once()
        fake_task.add_done_callback.assert_called_once()

    @patch("mmrelay.message_queue.asyncio.get_running_loop", side_effect=RuntimeError())
    def test_start_creates_executor_without_event_loop(self, _mock_get_running_loop):
        """start() should still initialize a dedicated executor without an active loop."""
        self.assertIsNone(self.queue._executor)
        self.queue.start(message_delay=1.0)
        self.assertIsNotNone(self.queue._executor)

    def test_stop_timeout_blocks_restart_until_manual_reset(self):
        """Timed-out stop should keep failed-stop state until reset."""
        self.queue._running = True
        self.queue._executor = None

        fake_loop = MagicMock()
        fake_loop.is_closed.return_value = False
        fake_loop.is_running.return_value = True
        fake_loop.call_soon_threadsafe.side_effect = (
            lambda _callback, *_args, **_kwargs: None
        )

        fake_task = MagicMock()
        fake_task.get_loop.return_value = fake_loop
        fake_task.done.return_value = False
        self.queue._processor_task = fake_task

        with patch("mmrelay.message_queue.TASK_SHUTDOWN_TIMEOUT_SEC", 0.001):
            self.queue.stop()

        self.assertTrue(self.queue._stop_failed)
        self.assertTrue(self.queue._stopping)
        self.assertFalse(self.queue._running)

        self.queue.start(message_delay=1.0)
        self.assertFalse(self.queue._running)

        self.assertFalse(self.queue.reset_failed_stop_state())
        self.queue._processor_task = None
        self.assertTrue(self.queue.reset_failed_stop_state())

        self.queue.start(message_delay=1.0)
        self.assertTrue(self.queue._running)

    @patch("mmrelay.message_queue.asyncio.get_running_loop")
    def test_stop_same_loop_done_task_short_circuits_callback(
        self, mock_get_running_loop
    ):
        """When task is already done, same-loop stop should set event without callback."""
        queue = MessageQueue()
        queue._running = True

        fake_loop = MagicMock()
        fake_loop.is_closed.return_value = False
        mock_get_running_loop.return_value = fake_loop

        fake_task = MagicMock()
        fake_task.get_loop.return_value = fake_loop
        fake_task.done.return_value = True
        queue._processor_task = fake_task

        fake_loop.call_soon.side_effect = lambda callback, *args, **kwargs: callback(
            *args, **kwargs
        )

        queue.stop()

        fake_task.cancel.assert_called_once()
        fake_task.add_done_callback.assert_not_called()

    @patch("mmrelay.message_queue.asyncio.get_running_loop")
    def test_stop_other_loop_done_task_short_circuits_callback(
        self, mock_get_running_loop
    ):
        """When task is already done, cross-loop stop should set event without callback."""
        queue = MessageQueue()
        queue._running = True

        task_loop = MagicMock()
        task_loop.is_closed.return_value = False
        task_loop.is_running.return_value = True
        current_loop = MagicMock()
        mock_get_running_loop.return_value = current_loop

        fake_task = MagicMock()
        fake_task.get_loop.return_value = task_loop
        fake_task.done.return_value = True
        queue._processor_task = fake_task

        task_loop.call_soon_threadsafe.side_effect = (
            lambda callback, *args, **kwargs: callback(*args, **kwargs)
        )

        queue.stop()

        fake_task.cancel.assert_called_once()
        fake_task.add_done_callback.assert_not_called()

    def test_enqueue_wait_returns_false_if_queue_stops_while_waiting(self):
        """wait=True enqueue should abort when queue stops during backoff sleep."""
        self.queue._running = True
        self.queue._stopping = False
        dummy_msg = QueuedMessage(
            timestamp=0.0,
            send_function=mock_send_function,
            args=(),
            kwargs={},
            description="placeholder",
            mapping_info=None,
        )
        self.queue._queue.extend([dummy_msg] * MAX_QUEUE_SIZE)

        with (
            patch.object(self.queue, "ensure_processor_started"),
            patch("mmrelay.message_queue.time.sleep") as mock_sleep,
        ):

            def _stop_queue(_seconds: float) -> None:
                self.queue._running = False

            mock_sleep.side_effect = _stop_queue
            accepted = self.queue.enqueue(
                mock_send_function,
                text="blocked",
                description="blocked",
                wait=True,
                timeout=1.0,
            )

        self.assertFalse(accepted)

    def test_process_queue_requeues_when_connection_drops_during_rate_limit(self):
        """Rate-limit sleep must recheck connection and requeue when link drops."""

        async def run_test():
            queue = MessageQueue()
            queue._running = True
            queue._message_delay = 5.0
            queue._last_send_mono = 100.0
            msg = QueuedMessage(
                timestamp=0.0,
                send_function=mock_send_function,
                args=(),
                kwargs={"text": "rate-limited"},
                description="rate-limited",
                mapping_info=None,
            )
            queue._queue.append(msg)

            first_check = True

            def _should_send_message() -> bool:
                nonlocal first_check
                if first_check:
                    first_check = False
                    return True
                return False

            queue._should_send_message = _should_send_message

            async def _fake_sleep(_seconds: float) -> None:
                queue._running = False

            with (
                patch("mmrelay.message_queue.time.monotonic", return_value=101.0),
                patch("mmrelay.message_queue.asyncio.sleep", side_effect=_fake_sleep),
            ):
                await queue._process_queue()

            assert msg in queue._queue

        self.loop.run_until_complete(run_test())

    def test_process_queue_requeues_when_connection_not_ready_before_send(self):
        """Final pre-send connection check should requeue and continue safely."""

        async def run_test():
            queue = MessageQueue()
            queue._running = True
            queue._message_delay = 1.0
            queue._last_send_mono = 0.0
            msg = QueuedMessage(
                timestamp=0.0,
                send_function=mock_send_function,
                args=(),
                kwargs={"text": "pre-send"},
                description="pre-send",
                mapping_info=None,
            )
            queue._queue.append(msg)

            first_check = True

            def _should_send_message() -> bool:
                nonlocal first_check
                if first_check:
                    first_check = False
                    return True
                return False

            queue._should_send_message = _should_send_message

            async def _fake_sleep(_seconds: float) -> None:
                queue._running = False

            with (
                patch("mmrelay.message_queue.time.monotonic", return_value=10.0),
                patch("mmrelay.message_queue.asyncio.sleep", side_effect=_fake_sleep),
            ):
                await queue._process_queue()

            assert msg in queue._queue

        self.loop.run_until_complete(run_test())

    def test_should_send_message_import_error_stops_queue(self):
        """_should_send_message should stop when meshtastic_utils import fails."""
        queue = MessageQueue()

        original_import = __import__

        def raising_import(name, globals=None, locals=None, fromlist=(), level=0):
            """
            Raise ImportError when attempting to import "mmrelay.meshtastic_utils"; otherwise delegate to the original import function.

            Parameters:
                name (str): The module name to import.
                globals (dict | None): The globals dictionary to pass to the import machinery.
                locals (dict | None): The locals dictionary to pass to the import machinery.
                fromlist (tuple): Names to emulate "from <module> import ..." semantics.
                level (int): The package import level (0 for absolute imports).

            Returns:
                Any: The result of the original import call (a module or an attribute from a module).

            Raises:
                ImportError: If `name` is exactly "mmrelay.meshtastic_utils".
            """
            if name == "mmrelay.meshtastic_utils":
                raise ImportError("missing")
            return original_import(name, globals, locals, fromlist, level)

        with (
            patch("builtins.__import__", side_effect=raising_import),
            patch("mmrelay.message_queue.threading.Thread") as mock_thread,
        ):
            result = queue._should_send_message()

        self.assertFalse(result)
        mock_thread.return_value.start.assert_called_once()

    @pytest.mark.usefixtures("comprehensive_cleanup")
    def test_queue_size_limit(self):
        """
        Verify that the message queue enforces its maximum size limit by accepting messages up to the limit and rejecting additional messages beyond capacity.
        """
        # Start the queue but don't let it process (no event loop)
        self.queue._running = True  # Manually set running to prevent immediate sending

        # Fill queue to limit
        for i in range(MAX_QUEUE_SIZE):
            success = self.queue.enqueue(mock_send_function, text=f"Message {i}")
            self.assertTrue(success)

        # Next message should be rejected
        success = self.queue.enqueue(mock_send_function, text="Overflow message")
        self.assertFalse(success)

    def test_fallback_when_not_running(self):
        """
        Test that enqueuing a message is rejected when the queue is not running.

        Verifies that the queue does not accept messages unless it has been started, ensuring the event loop is not blocked and no messages are sent in this state.
        """
        # Don't start the queue
        success = self.queue.enqueue(mock_send_function, text="Immediate message")

        # Should refuse to send to prevent blocking event loop
        self.assertFalse(success)
        self.assertEqual(len(self.sent_messages), 0)

    def test_connection_state_awareness(self):
        """
        Verifies that the message queue does not send messages when the connection state indicates it should not send.

        Ensures that messages remain unsent if the queue's connection check fails, and restores the original connection check after the test.
        """

        async def async_test():
            # Mock the _should_send_message method to return False
            """
            Asynchronously tests that messages are not sent when the queue's connection state prevents sending.

            This function mocks the queue's connection check to simulate a disconnected state, enqueues a message, and verifies that the message is not sent while disconnected. The original connection check is restored after the test.
            """
            original_should_send = self.queue._should_send_message
            self.queue._should_send_message = lambda: False

            self.queue.start(message_delay=TEST_SHORT_DELAY)
            self.queue.ensure_processor_started()

            # Queue a message
            success = self.queue.enqueue(mock_send_function, text="Test message")
            self.assertTrue(success)

            # Wait - message should not be sent due to connection state
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.sent_messages), 0)

            # Restore original method
            self.queue._should_send_message = original_should_send

        self.loop.run_until_complete(async_test())

    @pytest.mark.usefixtures("comprehensive_cleanup")
    def test_error_handling(self):
        """
        Verify that the MessageQueue survives exceptions raised by send functions and continues processing.

        Starts the queue, enqueues a message whose send function raises an exception, waits for processing, and asserts the queue remains running (i.e., the exception does not stop the processor).
        """

        async def async_test():
            """
            Verify that the message queue remains running when a send function raises an exception.

            This asynchronous test enqueues a send function that raises, starts the queue processor, waits briefly for processing, and asserts that the queue's running state is preserved (i.e., the processor did not crash).
            """

            def failing_send_function(text, **kwargs):
                raise Exception("Send failed")

            self.queue.start(message_delay=TEST_SHORT_DELAY)
            self.queue.ensure_processor_started()

            # Queue a message that will fail
            success = self.queue.enqueue(failing_send_function, text="Failing message")
            self.assertTrue(success)  # Queuing should succeed

            # Wait for processing - should not crash
            await asyncio.sleep(0.3)
            # Queue should continue working after error
            self.assertTrue(self.queue.is_running())

        self.loop.run_until_complete(async_test())


class TestGlobalFunctions(unittest.TestCase):
    """Test cases for global queue functions."""

    def setUp(self):
        """
        Prepares the test environment by clearing the call history of the mock send function before each test.
        """
        # Clear mock function calls for each test
        mock_send_function.calls.clear()

    def test_queue_message_function(self):
        """
        Test that the global queue_message function refuses to enqueue messages when the queue is not running.

        Verifies that queue_message returns False and does not send messages if the message queue is inactive.
        """
        # Test with queue not running (should refuse to send)
        success = queue_message(
            mock_send_function,
            text="Test message",
            description="Global function test",
        )

        # Should refuse to send when queue not running to prevent event loop blocking
        self.assertFalse(success)
        self.assertEqual(len(mock_send_function.calls), 0)

    def test_start_message_queue_calls_start(self):
        """start_message_queue should delegate to the global queue."""
        with patch("mmrelay.message_queue._message_queue.start") as mock_start:
            start_message_queue(1.5)
        mock_start.assert_called_once_with(1.5)


@pytest.mark.asyncio
async def test_handle_message_mapping_stores_and_prunes():
    """_handle_message_mapping should store mappings and prune as configured."""
    queue = MessageQueue()
    result = MagicMock()
    result.id = 123
    mapping_info = {
        "matrix_event_id": "$event",
        "room_id": "!room:example.org",
        "text": "hello",
        "meshnet": "mesh",
        "msgs_to_keep": 1,
    }

    with (
        patch(
            "mmrelay.db_utils.async_store_message_map",
            new_callable=AsyncMock,
        ) as mock_store,
        patch(
            "mmrelay.db_utils.async_prune_message_map",
            new_callable=AsyncMock,
        ) as mock_prune,
    ):
        await queue._handle_message_mapping(result, mapping_info)

    mock_store.assert_awaited_once_with(
        "123",
        "$event",
        "!room:example.org",
        "hello",
        meshtastic_meshnet="mesh",
    )
    mock_prune.assert_awaited_once_with(1)


class TestQueuedMessage(unittest.TestCase):
    """Test cases for the QueuedMessage dataclass."""

    def test_message_creation(self):
        """
        Verify that a QueuedMessage instance is correctly created with the expected attributes.
        """

        def dummy_function():
            """
            A placeholder function that performs no operation.
            """
            pass

        message = QueuedMessage(
            timestamp=123.456,
            send_function=dummy_function,
            args=("arg1", "arg2"),
            kwargs={"key": "value"},
            description="Test message",
        )

        self.assertEqual(message.timestamp, 123.456)
        self.assertEqual(message.send_function, dummy_function)
        self.assertEqual(message.args, ("arg1", "arg2"))
        self.assertEqual(message.kwargs, {"key": "value"})
        self.assertEqual(message.description, "Test message")


class TestMessageQueueMethods(unittest.TestCase):
    """Test cases for additional MessageQueue methods."""

    def test_ensure_processor_started(self):
        """Test ensure_processor_started method when event loop is available."""
        queue = MessageQueue()

        # Start the queue with custom delay
        queue.start(message_delay=TEST_SHORT_DELAY)

        # Test ensure_processor_started method
        queue.ensure_processor_started()

        # Verify processor is running
        self.assertTrue(queue._running)

        # Clean up
        queue.stop()

    def test_stop_method(self):
        """Test stop method functionality."""
        queue = MessageQueue()

        # Start the queue
        queue.start(message_delay=TEST_SHORT_DELAY)
        self.assertTrue(queue._running)

        # Stop the queue
        queue.stop()
        self.assertFalse(queue._running)

        # Verify processor task is cleaned up
        self.assertIsNone(queue._processor_task)


class TestDequeBasedRequeue(unittest.TestCase):
    """Tests for deque-based _requeue_message optimization (O(1) prepend)."""

    def test_internal_queue_is_deque(self):
        """Verify the internal queue uses collections.deque."""
        from collections import deque

        queue = MessageQueue()

        self.assertIsInstance(queue._queue, deque)

    def test_requeue_prepends_to_front(self):
        """Test _requeue_message prepends message to front of queue."""
        queue = MessageQueue()
        queue.start(message_delay=TEST_SHORT_DELAY)

        mock_send = MockSendFunction()

        # Enqueue messages in order
        msg1 = QueuedMessage(
            timestamp=1.0,
            send_function=mock_send,
            args=("msg1",),
            kwargs={},
            description="First message",
        )
        msg2 = QueuedMessage(
            timestamp=2.0,
            send_function=mock_send,
            args=("msg2",),
            kwargs={},
            description="Second message",
        )
        msg3 = QueuedMessage(
            timestamp=3.0,
            send_function=mock_send,
            args=("msg3",),
            kwargs={},
            description="Third message",
        )

        queue._queue.append(msg1)
        queue._queue.append(msg2)
        queue._queue.append(msg3)

        # Create a new message to requeue
        urgent_msg = QueuedMessage(
            timestamp=0.5,
            send_function=mock_send,
            args=("urgent",),
            kwargs={},
            description="Urgent message",
        )

        # Requeue should put it at the front
        result = queue._requeue_message(urgent_msg)

        self.assertTrue(result)
        # First item should now be the urgent message
        self.assertEqual(queue._queue[0].description, "Urgent message")
        self.assertEqual(queue._queue[1].description, "First message")
        self.assertEqual(queue._queue[2].description, "Second message")
        self.assertEqual(queue._queue[3].description, "Third message")

        queue.stop()

    def test_requeue_returns_false_when_queue_full(self):
        """Test _requeue_message returns False when queue is at max capacity."""
        queue = MessageQueue()
        queue.start(message_delay=TEST_SHORT_DELAY)

        mock_send = MockSendFunction()

        # Fill the queue to max capacity
        for i in range(MAX_QUEUE_SIZE):
            msg = QueuedMessage(
                timestamp=float(i),
                send_function=mock_send,
                args=(f"msg{i}",),
                kwargs={},
                description=f"Message {i}",
            )
            queue._queue.append(msg)

        self.assertEqual(len(queue._queue), MAX_QUEUE_SIZE)

        # Try to requeue another message
        urgent_msg = QueuedMessage(
            timestamp=0.0,
            send_function=mock_send,
            args=("urgent",),
            kwargs={},
            description="Urgent message",
        )

        result = queue._requeue_message(urgent_msg)

        # Should fail because queue is full
        self.assertFalse(result)
        self.assertEqual(queue._dropped_messages, 1)

        queue.stop()

    def test_requeue_preserves_fifo_after_prepend(self):
        """Test that after requeue, FIFO order is maintained from the prepended item."""
        queue = MessageQueue()
        queue.start(message_delay=TEST_SHORT_DELAY)

        mock_send = MockSendFunction()

        # Add messages in order
        for i in range(3):
            msg = QueuedMessage(
                timestamp=float(i),
                send_function=mock_send,
                args=(f"msg{i}",),
                kwargs={},
                description=f"Message {i}",
            )
            queue._queue.append(msg)

        # Requeue a message to the front
        priority_msg = QueuedMessage(
            timestamp=0.0,
            send_function=mock_send,
            args=("priority",),
            kwargs={},
            description="Priority message",
        )
        queue._requeue_message(priority_msg)

        # Verify order: priority, msg0, msg1, msg2
        descriptions = [m.description for m in queue._queue]
        self.assertEqual(
            descriptions,
            ["Priority message", "Message 0", "Message 1", "Message 2"],
        )

        queue.stop()

    def test_deque_maxlen_behavior(self):
        """Test that deque with maxlen drops oldest when full via append."""
        from collections import deque

        # Create a small deque to test maxlen behavior
        test_deque = deque(maxlen=3)
        test_deque.append(1)
        test_deque.append(2)
        test_deque.append(3)
        test_deque.append(4)  # Should drop 1

        self.assertEqual(list(test_deque), [2, 3, 4])
        self.assertEqual(len(test_deque), 3)

    def test_appendleft_does_not_drop_when_not_full(self):
        """Test that appendleft succeeds when queue is not full."""

        queue = MessageQueue()
        queue.start(message_delay=TEST_SHORT_DELAY)

        mock_send = MockSendFunction()

        # Add one message
        msg1 = QueuedMessage(
            timestamp=1.0,
            send_function=mock_send,
            args=("msg1",),
            kwargs={},
            description="Message 1",
        )
        queue._queue.append(msg1)

        # Requeue should succeed
        urgent = QueuedMessage(
            timestamp=0.0,
            send_function=mock_send,
            args=("urgent",),
            kwargs={},
            description="Urgent",
        )
        result = queue._requeue_message(urgent)

        self.assertTrue(result)
        self.assertEqual(len(queue._queue), 2)
        self.assertEqual(queue._queue[0].description, "Urgent")

        queue.stop()


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
