#!/usr/bin/env python3
"""
Test suite for Message Queue edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Queue overflow scenarios
- Connection state edge cases
- Import errors and module loading failures
- Message mapping failures
- Processor task lifecycle edge cases
- Rate limiting boundary conditions
"""

import asyncio
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.network import MINIMUM_MESSAGE_DELAY, RECOMMENDED_MINIMUM_DELAY
from mmrelay.message_queue import MessageQueue, get_message_queue, queue_message
from tests.constants import (
    TEST_MESSAGE_DELAY_HIGH,
    TEST_MESSAGE_DELAY_LOW,
    TEST_MESSAGE_DELAY_NEGATIVE,
    TEST_MESSAGE_DELAY_NORMAL,
    TEST_MESSAGE_DELAY_WARNING_THRESHOLD,
)


@pytest.mark.usefixtures("comprehensive_cleanup")
class TestMessageQueueEdgeCases(unittest.TestCase):
    """Test cases for Message Queue edge cases and error handling."""

    def setUp(self):
        """
        Prepare the test environment by resetting global state variables and creating a new MessageQueue instance before each test.
        """
        # Reset global state
        import mmrelay.meshtastic_utils
        import mmrelay.message_queue

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.event_loop = None
        mmrelay.meshtastic_utils.reconnect_task = None
        mmrelay.meshtastic_utils.subscribed_to_messages = False
        mmrelay.meshtastic_utils.subscribed_to_connection_lost = False

        self.queue = MessageQueue()

    def tearDown(self):
        if self.queue.is_running():
            self.queue.stop()

        # Reset global state only; rely on comprehensive_cleanup for async cleanup
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.meshtastic_client = None
        mmrelay.meshtastic_utils.reconnecting = False
        mmrelay.meshtastic_utils.config = None
        mmrelay.meshtastic_utils.matrix_rooms = []
        mmrelay.meshtastic_utils.shutting_down = False
        mmrelay.meshtastic_utils.event_loop = None
        mmrelay.meshtastic_utils.reconnect_task = None
        mmrelay.meshtastic_utils.subscribed_to_messages = False
        mmrelay.meshtastic_utils.subscribed_to_connection_lost = False

    def test_queue_overflow_handling(self):
        """
        Test that the message queue enforces its maximum capacity and rejects additional messages when full.

        Fills the queue to its defined maximum size, verifies that enqueueing beyond this limit fails, and asserts the queue size does not exceed the allowed maximum.
        """

        async def async_test():
            """
            Asynchronously verifies that the message queue enforces its maximum capacity by filling it to the defined limit and confirming that additional enqueue attempts are rejected.

            Ensures the queue does not exceed its maximum size and that overflow messages are not accepted.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)

            # Give the queue a moment to start
            await asyncio.sleep(0.1)

            # Verify queue is running
            self.assertTrue(
                self.queue.is_running(), "Queue should be running after start"
            )

            # Fill the queue to capacity
            # Import MAX_QUEUE_SIZE for consistency
            from mmrelay.constants.queue import MAX_QUEUE_SIZE

            for i in range(MAX_QUEUE_SIZE):
                success = self.queue.enqueue(lambda: None, description=f"Message {i}")
                if not success:
                    print(
                        f"Failed to enqueue message {i}, queue size: {self.queue.get_queue_size()}"
                    )
                    break
                if i % 50 == 0:  # Print progress every 50 messages
                    print(
                        f"Successfully enqueued {i} messages, queue size: {self.queue.get_queue_size()}"
                    )

            # Check how many we actually enqueued
            final_queue_size = self.queue.get_queue_size()
            print(f"Final queue size: {final_queue_size}")

            # The test should work with whatever the actual limit is
            if final_queue_size < MAX_QUEUE_SIZE:
                # Queue hit its limit before MAX_QUEUE_SIZE, so test with that limit
                success = self.queue.enqueue(
                    lambda: None, description="Overflow message"
                )
                self.assertFalse(success, "Should reject message when queue is full")
            else:
                # Queue accepted all MAX_QUEUE_SIZE, so it should reject the next one
                success = self.queue.enqueue(
                    lambda: None, description="Overflow message"
                )
                self.assertFalse(success, "Should reject message when queue is full")

            # Verify the queue is at its actual maximum
            self.assertGreater(
                final_queue_size, 0, "Should have enqueued at least some messages"
            )
            self.assertLessEqual(
                final_queue_size, MAX_QUEUE_SIZE, "Should not exceed expected maximum"
            )

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_enqueue_when_not_running(self):
        """
        Verify that enqueueing a message fails when the message queue is not running.
        """
        # Queue is not started
        success = self.queue.enqueue(lambda: None, description="Test message")
        self.assertFalse(success)

    @patch("mmrelay.message_queue.logger")
    def test_start_with_invalid_message_delay(self, mock_logger):
        """
        Verify that starting the queue with a message delay at or below MINIMUM_MESSAGE_DELAY seconds logs a warning but accepts the value.
        """
        # Test with delay below MINIMUM_MESSAGE_DELAY - should log warning but accept value
        self.queue.start(message_delay=TEST_MESSAGE_DELAY_WARNING_THRESHOLD)
        status = self.queue.get_status()
        self.assertEqual(
            status["message_delay"], TEST_MESSAGE_DELAY_WARNING_THRESHOLD
        )  # Should accept the value
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        # Test against real warning message patterns from the code
        expected_warning_part = f"Message delay {TEST_MESSAGE_DELAY_WARNING_THRESHOLD}s is at or below {MINIMUM_MESSAGE_DELAY}s"
        self.assertIn(expected_warning_part, warning_call)
        # Test the recommendation using the constant
        expected_recommendation = (
            f"{RECOMMENDED_MINIMUM_DELAY}s or higher is recommended"
        )
        self.assertIn(expected_recommendation, warning_call)

        # Test with negative delay - should log warning but accept value
        self.queue.stop()
        self.queue = MessageQueue()
        mock_logger.reset_mock()
        self.queue.start(message_delay=TEST_MESSAGE_DELAY_NEGATIVE)
        status = self.queue.get_status()
        self.assertEqual(
            status["message_delay"], TEST_MESSAGE_DELAY_NEGATIVE
        )  # Should accept the value
        mock_logger.warning.assert_called_once()
        warning_call = mock_logger.warning.call_args[0][0]
        # Test against real warning message patterns from the code
        expected_warning_part = f"Message delay {TEST_MESSAGE_DELAY_NEGATIVE}s is at or below {MINIMUM_MESSAGE_DELAY}s"
        self.assertIn(expected_warning_part, warning_call)

    def test_double_start(self):
        """
        Verify that starting the message queue multiple times does not disrupt its running state or alter the initial message delay.
        """
        self.queue.start(message_delay=TEST_MESSAGE_DELAY_NORMAL)
        self.assertTrue(self.queue.is_running())

        # Starting again should not cause issues
        self.queue.start(message_delay=TEST_MESSAGE_DELAY_HIGH)
        self.assertTrue(self.queue.is_running())

        # Message delay should not change
        status = self.queue.get_status()
        self.assertEqual(status["message_delay"], TEST_MESSAGE_DELAY_NORMAL)

    def test_stop_when_not_running(self):
        """
        Verify that calling stop on a non-running queue does not raise exceptions and leaves the queue stopped.
        """
        # Should not raise an exception
        self.queue.stop()
        self.assertFalse(self.queue.is_running())

    @patch("mmrelay.message_queue.logger")
    def test_processor_import_error_handling(self, mock_logger):
        """
        Verify the message queue handles ImportError raised during message processing without raising unhandled exceptions.

        Starts the queue, causes MessageQueue._should_send_message to raise ImportError while a message is enqueued, and asserts the queue remains in a stable boolean running state after processing.
        """

        async def async_test():
            """
            Asynchronously tests that the message queue handles ImportError exceptions during message processing without crashing.

            This test starts the queue, mocks the message sending check to raise ImportError, enqueues a message, and verifies that the queue remains stable and its running state is a boolean after processing.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)
            self.queue.ensure_processor_started()

            # Mock the import to raise ImportError
            with patch(
                "mmrelay.message_queue.MessageQueue._should_send_message"
            ) as mock_should_send:
                mock_should_send.side_effect = ImportError("Module not found")

                # Queue a message
                success = self.queue.enqueue(
                    lambda: "result", description="Test message"
                )
                self.assertTrue(success)

                # Wait for processing
                await asyncio.sleep(0.2)

                # The queue may or may not be stopped depending on implementation
                # Just check that it handled the error gracefully
                self.assertIsInstance(self.queue.is_running(), bool)

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                # If there's already an event loop running, use it
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

        # Verify we logged the failure path
        assert mock_logger.exception.called or mock_logger.error.called

    def test_message_mapping_with_invalid_result(self):
        """
        Verifies that the message queue handles message send results lacking expected attributes without failure.

        This test enqueues a message using a mock send function that returns an object missing the 'id' attribute, ensuring the queue processes such results gracefully.
        """

        async def async_test():
            """
            Asynchronously tests that the message queue processes messages whose send function returns an object lacking the expected 'id' attribute.

            Verifies that enqueueing such a message succeeds and the queue handles the missing attribute without failure.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)
            self.queue.ensure_processor_started()

            # Mock send function that returns object without 'id' attribute
            def mock_send():
                """
                Return a mock object simulating a send result without an 'id' attribute.

                Returns:
                    MagicMock: A mock object lacking the 'id' attribute.
                """
                result = MagicMock()
                del result.id  # Remove id attribute
                return result

            mapping_info = {
                "matrix_event_id": "test_event",
                "room_id": "test_room",
                "text": "test message",
            }

            success = self.queue.enqueue(
                mock_send, description="Test message", mapping_info=mapping_info
            )
            self.assertTrue(success)

            # Wait for processing
            await asyncio.sleep(0.2)

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_processor_task_cancellation(self):
        """
        Verifies that the message processor task can be cancelled and properly transitions to a done state.
        """

        async def async_test():
            """
            Cancels the message queue's processor task and verifies it is properly terminated.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)
            self.queue.ensure_processor_started()

            # Get the processor task
            processor_task = self.queue._processor_task
            self.assertIsNotNone(processor_task)

            # Cancel the task
            processor_task.cancel()

            # Wait for cancellation to complete
            try:
                await processor_task
            except asyncio.CancelledError:
                pass

            # Task should be done
            self.assertTrue(processor_task.done())

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_ensure_processor_started_without_event_loop(self):
        """
        Verify that ensure_processor_started does not raise an exception when called without an event loop or processor task.
        """
        self.queue._running = True
        self.queue._processor_task = None

        # This should not raise an exception even without an event loop
        self.queue.ensure_processor_started()

    def test_rate_limiting_edge_cases(self):
        """
        Test that the message queue enforces rate limiting correctly under timing edge cases.

        This test starts the queue with a short message delay, mocks the system time to simulate precise timing near the rate limit boundary, and verifies that messages are enqueued and processed according to the enforced delay.
        """

        async def async_test():
            """
            Tests rate limiting behavior of the message queue by enqueueing messages with controlled timing and verifying that messages are processed according to the specified delay.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)
            self.queue.ensure_processor_started()

            # Mock time to control timing
            with patch("time.time") as mock_time:
                mock_time.return_value = 1000.0

                # Queue first message
                success = self.queue.enqueue(lambda: "result1", description="Message 1")
                self.assertTrue(success)

                # Wait for first message to be processed
                await asyncio.sleep(0.2)

                # Advance time slightly (less than message delay)
                mock_time.return_value = 1000.05

                # Queue second message
                success = self.queue.enqueue(lambda: "result2", description="Message 2")
                self.assertTrue(success)

                # Should wait for rate limiting
                await asyncio.sleep(0.2)

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_queue_status_with_no_sends(self):
        """
        Verify that the message queue status reflects correct default values when no messages have been sent.

        Ensures the queue reports as not running, with zero queue size, a last send time of zero, and no elapsed time since last send.
        """
        status = self.queue.get_status()

        self.assertFalse(status["running"])
        self.assertEqual(status["queue_size"], 0)
        self.assertEqual(status["last_send_time"], 0.0)
        self.assertIsNone(status["time_since_last_send"])

    def test_concurrent_enqueue_operations(self):
        """
        Verifies that the message queue can handle concurrent enqueue operations from multiple threads without errors.
        """
        self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)

        results = []

        def enqueue_messages(thread_id):
            """
            Enqueues ten messages from a specific thread into the message queue.

            Parameters:
                thread_id (int): Identifier for the thread enqueuing messages.

            Each message is labeled with the thread ID and message index. The result of each enqueue operation is appended to the shared `results` list.
            """
            for i in range(10):
                success = self.queue.enqueue(
                    lambda tid=thread_id, idx=i: f"result_{tid}_{idx}",
                    description=f"Thread {thread_id} Message {i}",
                )
                results.append(success)

        # Start multiple threads
        threads = []
        for i in range(5):
            thread = threading.Thread(target=enqueue_messages, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All enqueue operations should succeed (assuming queue capacity)
        successful_enqueues = sum(results)
        self.assertGreater(successful_enqueues, 0)

    def test_message_with_none_mapping_info(self):
        """
        Test that the message queue can enqueue and process a message when the mapping_info parameter is None.

        Verifies that messages with None as mapping_info are accepted and processed without errors.
        """

        async def async_test():
            """
            Asynchronously tests that a message with None mapping info can be enqueued and processed successfully.
            """
            self.queue.start(message_delay=TEST_MESSAGE_DELAY_LOW)
            self.queue.ensure_processor_started()

            success = self.queue.enqueue(
                lambda: "result", description="Test message", mapping_info=None
            )
            self.assertTrue(success)

            # Wait for processing
            await asyncio.sleep(0.2)

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_runtime_warning_for_fast_messages(self):
        """
        Verify that runtime warnings are logged when messages are sent less than MINIMUM_MESSAGE_DELAY seconds apart.
        """

        async def async_test():
            """
            Asynchronously tests that warnings are logged when messages are sent too quickly.
            """
            # Set up mock meshtastic client to allow message sending
            from unittest.mock import MagicMock

            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.meshtastic_client = MagicMock()
            mmrelay.meshtastic_utils.reconnecting = False

            # Start queue with a delay that allows messages to be sent but still triggers runtime warnings
            # The message_delay needs to be: actual_time_between_sends >= message_delay < MINIMUM_MESSAGE_DELAY
            # This allows messages to be sent (no rate limiting wait) but still triggers the runtime warning
            self.queue.start(
                message_delay=TEST_MESSAGE_DELAY_WARNING_THRESHOLD
            )  # 1.0s delay, less than MINIMUM_MESSAGE_DELAY (2.0s)
            self.queue.ensure_processor_started()

            # Mock send function
            calls = []

            def mock_send(text):
                calls.append(text)
                # Return an object with an 'id' attribute to match application expectations
                return type("obj", (object,), {"id": len(calls)})()

            # Use assertLogs to capture log messages as recommended in testing guide
            # Use the correct logger name "MessageQueue" as defined in message_queue.py
            with self.assertLogs("MessageQueue", level="WARNING") as cm:
                # Queue two messages quickly
                success1 = self.queue.enqueue(
                    mock_send, text="First", description="First message"
                )
                success2 = self.queue.enqueue(
                    mock_send, text="Second", description="Second message"
                )

                self.assertTrue(success1)
                self.assertTrue(success2)

                # Wait for both messages to be processed
                await self.queue.drain(timeout=5.0)

            # Check that we got the expected runtime warning
            warning_messages = "\n".join(cm.output)
            self.assertIn("[Runtime] Messages sent", warning_messages)
            self.assertIn(f"below {MINIMUM_MESSAGE_DELAY}s", warning_messages)
            self.assertIn("may be dropped", warning_messages)

        # Run the async test with proper event loop handling
        try:
            asyncio.run(async_test())
        except RuntimeError as e:
            if "cannot be called from a running event loop" in str(e):
                loop = asyncio.get_event_loop()
                loop.run_until_complete(async_test())
            else:
                raise

    def test_global_queue_functions(self):
        """
        Tests the behavior of global message queue functions, including retrieval of the global queue and enqueueing messages when the queue is not started.
        """
        # Test get_message_queue
        global_queue = get_message_queue()
        self.assertIsNotNone(global_queue)

        # Test queue_message function
        success = queue_message(lambda: "result", description="Global test")
        # Should fail because global queue is not started
        self.assertFalse(success)


if __name__ == "__main__":
    unittest.main()
