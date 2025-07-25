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
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.message_queue import (
    MAX_QUEUE_SIZE,
    MessageQueue,
    QueuedMessage,
    queue_message,
)


def mock_send_function(text, **kwargs):
    """Mock function to simulate sending a message."""
    # This will be called synchronously due to executor mocking
    mock_send_function.calls.append(
        {"text": text, "kwargs": kwargs, "timestamp": time.time()}
    )
    return {"id": len(mock_send_function.calls)}


# Initialize calls list
mock_send_function.calls = []


class TestMessageQueue(unittest.TestCase):
    """Test cases for the MessageQueue class."""

    def setUp(self):
        """Set up test fixtures."""
        self.queue = MessageQueue()
        # Clear mock function calls for each test
        mock_send_function.calls.clear()
        # Mock the _should_send_message method to always return True for tests
        self.queue._should_send_message = lambda: True

        # Mock asyncio.get_running_loop to make executor run synchronously
        self.loop_patcher = patch("asyncio.get_running_loop")
        mock_get_loop = self.loop_patcher.start()

        # Create a mock loop that runs executor functions synchronously
        mock_loop = MagicMock()

        async def sync_executor(executor, func, *args, **kwargs):
            """Run function synchronously instead of in executor."""
            return func(*args, **kwargs)

        mock_loop.run_in_executor = sync_executor
        mock_get_loop.return_value = mock_loop

    def tearDown(self):
        """Clean up after tests."""
        if self.queue.is_running():
            self.queue.stop()
        self.loop_patcher.stop()

    @property
    def sent_messages(self):
        """Get sent messages for testing."""
        return mock_send_function.calls

    def test_fifo_ordering(self):
        """Test that messages are sent in FIFO order."""

        # Use asyncio to properly test the async queue
        async def async_test():
            # Start queue with fast rate for testing
            self.queue.start(message_delay=0.1)

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

            # Wait for processing (need enough time for all 3 messages with 2.0s rate limiting)
            await asyncio.sleep(6.5)  # 3 messages * 2.0s + buffer

            # Check that messages were sent in order
            self.assertEqual(len(self.sent_messages), len(messages))
            for i, expected_msg in enumerate(messages):
                self.assertEqual(self.sent_messages[i]["text"], expected_msg)

        # Run the async test
        asyncio.run(async_test())

    def test_rate_limiting(self):
        """Test that rate limiting is enforced."""

        async def async_test():
            message_delay = 2.1  # Use minimum message delay for testing
            self.queue.start(message_delay=message_delay)
            self.queue.ensure_processor_started()

            # Queue two messages
            self.queue.enqueue(mock_send_function, text="First")
            self.queue.enqueue(mock_send_function, text="Second")

            # Wait for first message
            await asyncio.sleep(1.0)
            self.assertEqual(len(self.sent_messages), 1)

            # Second message should not be sent yet (rate limit not passed)
            await asyncio.sleep(1.0)
            self.assertEqual(len(self.sent_messages), 1)

            # Wait for rate limit to pass
            await asyncio.sleep(1.5)
            self.assertEqual(len(self.sent_messages), 2)

        asyncio.run(async_test())

    def test_queue_size_limit(self):
        """Test that queue respects size limits."""
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
        """Test that queue refuses to send when not running to prevent event loop blocking."""
        # Don't start the queue
        success = self.queue.enqueue(mock_send_function, text="Immediate message")

        # Should refuse to send to prevent blocking event loop
        self.assertFalse(success)
        self.assertEqual(len(self.sent_messages), 0)

    def test_connection_state_awareness(self):
        """Test that queue respects connection state."""

        async def async_test():
            # Mock the _should_send_message method to return False
            original_should_send = self.queue._should_send_message
            self.queue._should_send_message = lambda: False

            self.queue.start(message_delay=0.1)
            self.queue.ensure_processor_started()

            # Queue a message
            success = self.queue.enqueue(mock_send_function, text="Test message")
            self.assertTrue(success)

            # Wait - message should not be sent due to connection state
            await asyncio.sleep(0.3)
            self.assertEqual(len(self.sent_messages), 0)

            # Restore original method
            self.queue._should_send_message = original_should_send

        asyncio.run(async_test())

    def test_error_handling(self):
        """Test error handling in message sending."""

        async def async_test():
            def failing_send_function(text, **kwargs):
                raise Exception("Send failed")

            self.queue.start(message_delay=0.1)
            self.queue.ensure_processor_started()

            # Queue a message that will fail
            success = self.queue.enqueue(failing_send_function, text="Failing message")
            self.assertTrue(success)  # Queuing should succeed

            # Wait for processing - should not crash
            await asyncio.sleep(0.3)
            # Queue should continue working after error
            self.assertTrue(self.queue.is_running())

        asyncio.run(async_test())


class TestGlobalFunctions(unittest.TestCase):
    """Test cases for global queue functions."""

    def setUp(self):
        """Set up test fixtures."""
        # Clear mock function calls for each test
        mock_send_function.calls.clear()

    def test_queue_message_function(self):
        """Test the global queue_message function."""
        # Test with queue not running (should refuse to send)
        success = queue_message(
            mock_send_function,
            text="Test message",
            description="Global function test",
        )

        # Should refuse to send when queue not running to prevent event loop blocking
        self.assertFalse(success)
        self.assertEqual(len(mock_send_function.calls), 0)


class TestQueuedMessage(unittest.TestCase):
    """Test cases for the QueuedMessage dataclass."""

    def test_message_creation(self):
        """Test QueuedMessage creation and attributes."""

        def dummy_function():
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


if __name__ == "__main__":
    # Run tests
    unittest.main(verbosity=2)
