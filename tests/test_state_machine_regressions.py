"""Cross-thread and async lifecycle regressions for the relay's runtime owners."""

import asyncio
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from mmrelay.message_queue import MessageQueue, QueuedMessage, QueuePhase


class QueueLifecycleRegressions(unittest.TestCase):
    def test_waiting_producer_must_not_cross_restart_generation(self):
        queue = MessageQueue()
        queue.start()
        waiting = threading.Event()
        proceed = threading.Event()
        result = []
        queue._queue.append(QueuedMessage(0, lambda: None, (), {}, "original"))

        original_sleep = time.sleep
        producer_thread = None

        def sleep_for_space(delay):
            if threading.current_thread() is producer_thread:
                waiting.set()
                self.assertTrue(proceed.wait(2))
            else:
                original_sleep(delay)

        def producer():
            result.append(queue.enqueue(lambda: None, description="late", wait=True))

        try:
            with (
                patch("mmrelay.message_queue.MAX_QUEUE_SIZE", 1),
                patch("mmrelay.message_queue.time.sleep", side_effect=sleep_for_space),
            ):
                producer_thread = threading.Thread(target=producer)
                producer_thread.start()
                self.assertTrue(waiting.wait(2))
                queue.stop()
                self.assertTrue(queue.start())
                queue._queue.clear()
                proceed.set()
                producer_thread.join(2)
                self.assertFalse(producer_thread.is_alive())
                self.assertEqual(result, [False])
        finally:
            proceed.set()
            queue.stop()

    def test_full_queue_must_not_block_event_loop_thread(self):
        async def run():
            queue = MessageQueue()
            queue.start()
            try:
                with patch("mmrelay.message_queue.MAX_QUEUE_SIZE", 1):
                    queue._queue.append(
                        QueuedMessage(0, lambda: None, (), {}, "original")
                    )
                    self.assertFalse(queue.enqueue(lambda: None, wait=True))
                    self.assertEqual(queue.get_status()["dropped_messages"], 1)
            finally:
                queue.stop()

        asyncio.run(run())

    def test_stop_preserves_not_yet_submitted_message(self):
        async def run():
            queue = MessageQueue()
            queue._should_send_message = lambda: False
            queue.start()
            queue.enqueue(lambda: None, description="not sent")
            try:
                async with asyncio.timeout(2):
                    while not queue._has_current:
                        await asyncio.sleep(0.001)
                task = queue._processor_task
                queue.stop()
                self.assertIsNotNone(task)
                await asyncio.wait_for(task, 2)
                self.assertEqual(queue.get_queue_size(), 1)
                self.assertEqual(queue._queue[0].description, "not sent")
            finally:
                queue.stop()

        asyncio.run(run())

    def test_failed_executor_construction_never_publishes_running(self):
        queue = MessageQueue()
        with patch("mmrelay.message_queue.ThreadPoolExecutor", side_effect=OSError):
            self.assertFalse(queue.start())
        self.assertEqual(queue.get_status()["phase"], QueuePhase.STOPPED)
        self.assertFalse(queue.is_running())
        self.assertIsNone(queue._executor)
        self.assertTrue(queue.start())
        queue.stop()

    def test_executor_worker_creation_failure_never_publishes_running(self):
        queue = MessageQueue()
        executor = MagicMock()
        executor.submit.side_effect = RuntimeError("can't start new thread")
        with patch("mmrelay.message_queue.ThreadPoolExecutor", return_value=executor):
            self.assertFalse(queue.start())
        self.assertEqual(queue.get_status()["phase"], QueuePhase.STOPPED)
        self.assertIsNone(queue._executor)
        executor.shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    def test_cancelled_submitted_send_is_never_requeued_if_status_is_reset(self):
        async def run():
            queue = MessageQueue()
            entered = threading.Event()
            release = threading.Event()

            def send():
                entered.set()
                release.wait(3)

            queue._should_send_message = lambda: True
            queue.start()
            queue.enqueue(send, description="already submitted")
            try:
                async with asyncio.timeout(2):
                    while not entered.is_set():
                        await asyncio.sleep(0.001)
                task = queue._processor_task
                self.assertIsNotNone(task)
                queue._in_flight = (
                    False  # Simulate status finalizer racing cancellation.
                )
                task.cancel()
                await asyncio.wait_for(task, 2)
                self.assertEqual(queue.get_queue_size(), 0)
            finally:
                release.set()
                queue.stop()

        asyncio.run(run())

    def test_failed_stop_blocks_restart_until_executor_finishes(self):
        queue = MessageQueue()
        queue.start()
        gate = threading.Event()
        active = threading.Event()

        def block():
            active.set()
            gate.wait(3)

        self.assertIsInstance(queue._executor, ThreadPoolExecutor)
        queue._executor.submit(block)
        self.assertTrue(active.wait(2))
        try:
            with patch("mmrelay.message_queue.TASK_SHUTDOWN_TIMEOUT_SEC", 0.01):
                queue.stop()
            self.assertEqual(queue.get_status()["phase"], QueuePhase.FAILED_STOP)
            self.assertFalse(queue.start())
        finally:
            gate.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if queue.get_status()["phase"] == QueuePhase.STOPPED:
                break
            time.sleep(0.01)
        self.assertEqual(queue.get_status()["phase"], QueuePhase.STOPPED)
        self.assertTrue(queue.start())
        queue.stop()


def test_stop_between_final_connection_check_and_submission_preserves_unsent():
    """A stop that wins admission cannot launch a late executor send."""

    async def run():
        queue = MessageQueue()
        send = MagicMock(return_value={"id": 5})
        checks = 0

        def check_connection():
            nonlocal checks
            checks += 1
            if checks == 2:
                queue.stop()
            return True

        queue._should_send_message = check_connection
        assert queue.start(message_delay=0)
        assert queue.enqueue(send, description="not submitted")
        task = queue._processor_task
        assert task is not None
        await asyncio.wait_for(task, 2)
        assert checks == 2
        send.assert_not_called()
        assert queue.get_queue_size() == 1
        assert queue._queue[0].description == "not submitted"
        queue.stop()

    asyncio.run(run())
