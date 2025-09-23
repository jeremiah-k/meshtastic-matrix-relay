#!/usr/bin/env python3
"""
Test suite for Error Boundaries and Recovery scenarios in MMRelay.

Tests error boundaries and recovery including:
- Graceful degradation under failures
- Error isolation between components
- Recovery from transient failures
- Fallback mechanisms
- Circuit breaker patterns
- Error propagation limits
- System stability under cascading failures
"""

import asyncio
import os
import sys
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.meshtastic_utils import on_meshtastic_message
from mmrelay.message_queue import MessageQueue


class TestErrorBoundaries(unittest.TestCase):
    """Test cases for error boundaries and recovery scenarios."""

    def setUp(self):
        """
        Prepare the test environment by resetting global state before each test method.
        """
        # Reset global state
        self._reset_global_state()

    def tearDown(self):
        """
        Clean up global state after each test to ensure test isolation.
        """
        self._reset_global_state()

    def _reset_global_state(self):
        """
        Reset global variables in key MMRelay modules to ensure test isolation.

        This method clears configuration, client, event loop, and subscription state in the `mmrelay.meshtastic_utils`, `mmrelay.matrix_utils`, and `mmrelay.plugin_loader` modules, restoring them to default values between tests.
        """
        # Reset meshtastic_utils globals
        if "mmrelay.meshtastic_utils" in sys.modules:
            module = sys.modules["mmrelay.meshtastic_utils"]
            module.config = None
            module.matrix_rooms = []
            module.meshtastic_client = None
            module.event_loop = None
            module.reconnecting = False
            module.shutting_down = False
            module.reconnect_task = None
            module.subscribed_to_messages = False
            module.subscribed_to_connection_lost = False

        # Reset matrix_utils globals
        if "mmrelay.matrix_utils" in sys.modules:
            module = sys.modules["mmrelay.matrix_utils"]
            if hasattr(module, "config"):
                module.config = None
            if hasattr(module, "matrix_client"):
                module.matrix_client = None

        # Reset plugin_loader globals
        if "mmrelay.plugin_loader" in sys.modules:
            module = sys.modules["mmrelay.plugin_loader"]
            if hasattr(module, "config"):
                module.config = None

    def test_plugin_failure_isolation(self):
        """
        Verifies that a plugin failure during Meshtastic message handling does not prevent other plugins or the core Matrix relay from executing.

        Simulates one plugin raising an exception and another succeeding, ensuring that errors are isolated, logged, and do not disrupt the main relay or other plugins.
        """
        # Create plugins with different failure modes
        failing_plugin = MagicMock()
        failing_plugin.priority = 1
        failing_plugin.plugin_name = "failing_plugin"
        failing_plugin.handle_meshtastic_message = AsyncMock(
            side_effect=Exception("Plugin crashed")
        )

        working_plugin = MagicMock()
        working_plugin.priority = 5
        working_plugin.plugin_name = "working_plugin"
        working_plugin.handle_meshtastic_message = AsyncMock(return_value=False)

        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        with patch(
            "mmrelay.plugin_loader.load_plugins",
            return_value=[failing_plugin, working_plugin],
        ):
            with patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay:
                with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                    # Set up minimal config
                    import mmrelay.meshtastic_utils

                    mmrelay.meshtastic_utils.config = {
                        "matrix_rooms": [
                            {"id": "!room:matrix.org", "meshtastic_channel": 0}
                        ],
                        "meshtastic": {"meshnet_name": "TestMesh"},
                    }
                    mmrelay.meshtastic_utils.matrix_rooms = [
                        {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    ]
                    mmrelay.meshtastic_utils.event_loop = MagicMock()

                    # Process message
                    on_meshtastic_message(packet, mock_interface)

                    # Failing plugin should have been called and failed
                    failing_plugin.handle_meshtastic_message.assert_called_once()
                    mock_logger.exception.assert_called()

                    # Working plugin should still have been called
                    working_plugin.handle_meshtastic_message.assert_called_once()

                # Core functionality (Matrix relay) should still work
                mock_matrix_relay.assert_called_once()

    def test_database_failure_graceful_degradation(self):
        """
        Test that message relay to Matrix continues and fallback node names are used when database lookups fail during message processing.

        Simulates failures in database retrieval of node long and short names, verifying that the system uses fallback information from the interface and successfully relays the message to Matrix.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()
        mock_interface.nodes = {
            "!12345678": {
                "user": {"id": "!12345678", "longName": "Test Node", "shortName": "TN"}
            }
        }

        # Mock database failures
        with patch(
            "mmrelay.db_utils.get_longname", side_effect=Exception("Database error")
        ):
            with patch(
                "mmrelay.db_utils.get_shortname",
                side_effect=Exception("Database error"),
            ):
                with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
                    with patch(
                        "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
                    ) as mock_matrix_relay:
                        with patch("mmrelay.meshtastic_utils.logger"):
                            # Set up config
                            import mmrelay.meshtastic_utils

                            mmrelay.meshtastic_utils.config = {
                                "matrix_rooms": [
                                    {
                                        "id": "!room:matrix.org",
                                        "meshtastic_channel": 0,
                                    }
                                ],
                                "meshtastic": {"meshnet_name": "TestMesh"},
                            }
                            mmrelay.meshtastic_utils.matrix_rooms = [
                                {"id": "!room:matrix.org", "meshtastic_channel": 0}
                            ]
                            mmrelay.meshtastic_utils.event_loop = MagicMock()

                            # Process message
                            on_meshtastic_message(packet, mock_interface)

                            # Should still relay to Matrix despite database failures
                            mock_matrix_relay.assert_called_once()

                        # Should use fallback names from interface
                        call_args = mock_matrix_relay.call_args[0]
                        self.assertIn(
                            "Test Node", call_args[1]
                        )  # Should use interface longName

    def test_matrix_relay_failure_recovery(self):
        """
        Test recovery from a Matrix relay failure by simulating a failed relay on the first message and a successful relay on the second.

        Verifies that the system continues processing subsequent messages after an initial Matrix relay failure, ensuring both attempts are made.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
            "to": 4294967295,
            "id": 123456789,
        }

        mock_interface = MagicMock()

        # First call fails, second succeeds
        matrix_relay_calls = 0

        def matrix_relay_side_effect(*args, **kwargs):
            """
            Simulates a Matrix relay function that fails on the first call and succeeds on subsequent calls.

            Raises:
                Exception: On the first invocation to simulate a relay failure.
            """
            nonlocal matrix_relay_calls
            matrix_relay_calls += 1
            if matrix_relay_calls == 1:
                raise Exception("Matrix relay failed")
            return None

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
            with patch(
                "mmrelay.matrix_utils.matrix_relay",
                new_callable=AsyncMock,
                side_effect=matrix_relay_side_effect,
            ):
                with patch("mmrelay.meshtastic_utils.logger"):
                    # Set up config
                    import mmrelay.meshtastic_utils

                    mmrelay.meshtastic_utils.config = {
                        "matrix_rooms": [
                            {"id": "!room:matrix.org", "meshtastic_channel": 0}
                        ],
                        "meshtastic": {"meshnet_name": "TestMesh"},
                    }
                    mmrelay.meshtastic_utils.matrix_rooms = [
                        {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    ]
                    mmrelay.meshtastic_utils.event_loop = MagicMock()

                    # Process first message (should fail)
                    on_meshtastic_message(packet, mock_interface)

                    # Process second message (should succeed)
                    packet["id"] = 123456790
                    on_meshtastic_message(packet, mock_interface)

                    # Should have attempted both calls
                    self.assertEqual(matrix_relay_calls, 2)

    def test_message_queue_error_boundary(self):
        """
        Test that the MessageQueue processes both failing and successful tasks without interruption.

        Verifies that exceptions in enqueued functions do not halt the queue, all tasks are attempted, and successful tasks are processed even when others fail.
        """
        import asyncio

        async def run_queue_test():
            # Mock the Meshtastic client to allow message sending
            """
            Asynchronously tests that the message queue processes both failing and successful functions without interruption.

            This function enqueues a mix of functions that raise exceptions and functions that succeed, then verifies that all are processed and that failures do not prevent successful executions. It also ensures the queue is properly started and stopped within the test.
            """
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    queue = MessageQueue()
                    queue.start(
                        message_delay=0.01
                    )  # Very fast processing (will be enforced to 2.0s minimum)
                    # Ensure processor starts now that event loop is running
                    queue.ensure_processor_started()

                    success_count = 0
                    failure_count = 0

                    def failing_function():
                        nonlocal failure_count
                        failure_count += 1
                        raise Exception("Function failed")

                    def success_function():
                        nonlocal success_count
                        success_count += 1
                        return MagicMock(id="success_id")

                    try:
                        # Queue fewer functions to reduce test time
                        message_count = 6  # 3 failing, 3 successful
                        for i in range(message_count):
                            if i % 2 == 0:
                                success = queue.enqueue(
                                    failing_function,
                                    description=f"Failing function {i}",
                                )
                                self.assertTrue(
                                    success, f"Failed to enqueue failing function {i}"
                                )
                            else:
                                success = queue.enqueue(
                                    success_function,
                                    description=f"Success function {i}",
                                )
                                self.assertTrue(
                                    success, f"Failed to enqueue success function {i}"
                                )

                        # Wait for processing to complete (6 messages * 2s = 12s + buffer)
                        import time

                        start_time = time.time()
                        timeout = 20  # 20 second timeout
                        processed_count = 0
                        while (
                            processed_count < message_count
                            and time.time() - start_time < timeout
                        ):
                            processed_count = failure_count + success_count
                            await asyncio.sleep(0.5)  # Check every 0.5 seconds

                        # Force queue processing to complete
                        queue.stop()

                        # Verify all messages were processed
                        self.assertEqual(processed_count, message_count)

                        # Verify that failures didn't stop successful processing
                        self.assertEqual(failure_count, 3)  # 3 failing functions called
                        self.assertEqual(
                            success_count, 3
                        )  # 3 successful functions called

                    except Exception:
                        # Ensure queue is stopped even if test fails
                        queue.stop()
                        raise

        asyncio.run(run_queue_test())

    def test_cascading_failure_prevention(self):
        """
        Verify that multiple simultaneous component failures during message processing are contained and do not propagate.

        Simulates failures in the database lookups, plugin loading, and Matrix relay while invoking on_meshtastic_message. The test asserts that no exception escapes the call and that at least one error/exception was logged.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        # Create a chain of potential failures
        with patch(
            "mmrelay.db_utils.get_longname", side_effect=Exception("DB failure")
        ):
            with patch(
                "mmrelay.db_utils.get_shortname", side_effect=Exception("DB failure")
            ):
                with patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins:
                    # Plugin loading fails
                    mock_load_plugins.side_effect = Exception("Plugin loading failed")

                    with patch(
                        "mmrelay.matrix_utils.matrix_relay",
                        new_callable=AsyncMock,
                        side_effect=Exception("Matrix failure"),
                    ):
                        with patch("mmrelay.meshtastic_utils.logger") as mock_logger:
                            # Set up config
                            import mmrelay.meshtastic_utils

                            mmrelay.meshtastic_utils.config = {
                                "matrix_rooms": [
                                    {"id": "!room:matrix.org", "meshtastic_channel": 0}
                                ]
                            }
                            mmrelay.meshtastic_utils.matrix_rooms = [
                                {"id": "!room:matrix.org", "meshtastic_channel": 0}
                            ]

                            # Process message - should not crash despite multiple failures
                            try:
                                on_meshtastic_message(packet, mock_interface)
                                # Should complete without raising exceptions
                            except Exception as e:
                                self.fail(f"Cascading failure occurred: {e}")

                            # Should have logged errors (error or exception) and continued processing
                            self.assertTrue(
                                (
                                    mock_logger.error.call_count
                                    + mock_logger.exception.call_count
                                )
                                > 0
                            )

    def test_transient_failure_recovery(self):
        """
        Verify that MessageQueue recovers from transient failures and eventually attempts all queued tasks.
        
        Enqueues multiple calls to a function that raises on its first two invocations and succeeds thereafter, starts the queue, and waits until every enqueued call has been attempted. Asserts the total number of attempted calls equals the number of enqueued messages, demonstrating the queue continues processing after transient errors. The test starts and stops a MessageQueue instance as part of its operation.
        """
        import asyncio

        async def async_test():
            # Mock the Meshtastic client to allow message sending
            """
            Asynchronously tests recovery from transient failures in queued message processing.

            This function enqueues multiple calls to a function that fails on its first two invocations and succeeds thereafter, verifying that the message queue retries and eventually processes all messages despite initial transient errors.
            """
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    call_count = 0

                    def transient_failure_function():
                        nonlocal call_count
                        call_count += 1
                        if call_count <= 2:  # Fail first 2 times
                            raise Exception("Transient failure")
                        return MagicMock(id="success_id")

                    queue = MessageQueue()
                    queue.start(message_delay=0.01)  # Will be enforced to 2.0s minimum
                    queue.ensure_processor_started()

                    try:
                        # Queue the same function multiple times
                        message_count = 5
                        for i in range(message_count):
                            queue.enqueue(
                                transient_failure_function,
                                description=f"Transient test {i}",
                            )

                        # Wait for processing to complete
                        import time

                        start_time = time.time()
                        timeout = 20  # 20 second timeout
                        while (
                            call_count < message_count
                            and time.time() - start_time < timeout
                        ):
                            await asyncio.sleep(0.5)  # Check every 0.5 seconds

                        # Should have attempted all calls
                        self.assertEqual(call_count, message_count)

                    finally:
                        queue.stop()

        asyncio.run(async_test())

    def test_error_propagation_limits(self):
        """
        Verify that exceptions raised by plugins during Matrix room message handling are contained within error boundaries and do not propagate, ensuring errors are logged but do not disrupt overall processing.
        """

        async def async_test():
            # Test Matrix message handling error containment
            """
            Asynchronously tests that exceptions in Matrix room message plugins are contained and do not propagate beyond the error boundary.

            Verifies that errors raised by plugin handlers during Matrix room message processing are logged and do not cause unhandled exceptions.
            """
            from mmrelay.matrix_utils import on_room_message

            mock_room = MagicMock()
            mock_room.room_id = "!room:matrix.org"

            # Import the mock class from conftest
            from nio import RoomMessageText

            mock_event = MagicMock(spec=RoomMessageText)
            mock_event.sender = "@user:matrix.org"
            mock_event.body = "test message"
            mock_event.server_timestamp = (
                int(time.time() * 1000) + 1000
            )  # Future timestamp
            mock_event.source = {"content": {}}

            # Mock plugin that raises exception
            mock_plugin = MagicMock()
            mock_plugin.handle_room_message = AsyncMock(
                side_effect=Exception("Plugin error")
            )

            with patch(
                "mmrelay.plugin_loader.load_plugins", return_value=[mock_plugin]
            ):
                with patch("mmrelay.matrix_utils.logger") as mock_logger:
                    # Set up config
                    import mmrelay.matrix_utils

                    mmrelay.matrix_utils.config = {
                        "meshtastic": {
                            "connection_type": "serial",
                            "serial_port": "/dev/ttyUSB0",
                            "meshnet_name": "TestMesh",
                        },
                        "matrix_rooms": [
                            {"id": "!room:matrix.org", "meshtastic_channel": 0}
                        ],
                    }
                    mmrelay.matrix_utils.matrix_rooms = [
                        {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    ]

                    # Should not raise exception despite plugin failure
                    try:
                        await on_room_message(mock_room, mock_event)
                    except Exception as e:
                        self.fail(f"Error propagated beyond boundary: {e}")

                    # Should have logged the error
                    mock_logger.error.assert_called()

        asyncio.run(async_test())

    def test_resource_exhaustion_handling(self):
        """
        Test that the message queue continues operating under resource exhaustion by enqueuing multiple memory-intensive tasks.

        Verifies that some tasks are accepted up to the queue's capacity, and that the queue remains operational despite high memory usage.
        """
        queue = MessageQueue()
        queue.start(message_delay=0.01)

        def memory_intensive_function():
            # Simulate memory-intensive operation
            """
            Simulates a memory-intensive operation by allocating a large list.

            Returns:
                MagicMock: A mock object with an 'id' attribute if allocation succeeds.

            Raises:
                Exception: If a MemoryError occurs during allocation.
            """
            try:
                # Try to allocate large amount of memory
                [0] * (10**6)  # 1 million integers
                return MagicMock(id="memory_id")
            except MemoryError as e:
                raise Exception("Out of memory") from e

        try:
            # Queue many memory-intensive operations
            enqueued_count = 0
            for i in range(100):
                success = queue.enqueue(
                    memory_intensive_function, description=f"Memory test {i}"
                )
                if success:
                    enqueued_count += 1
                else:
                    break  # Queue full

            # Should have enqueued some messages (up to queue limit)
            self.assertGreater(enqueued_count, 0)
            self.assertLessEqual(enqueued_count, 100)  # Queue limit

            # Wait for processing
            import time

            time.sleep(2)

            # Queue should still be running despite resource pressure
            self.assertTrue(queue.is_running())

        finally:
            queue.stop()

    def test_configuration_error_fallbacks(self):
        """
        Verify that the system handles missing or malformed configuration data gracefully without raising exceptions.
        """
        # Test with completely missing config
        with patch("mmrelay.meshtastic_utils.logger"):
            import mmrelay.meshtastic_utils

            mmrelay.meshtastic_utils.config = None
            mmrelay.meshtastic_utils.matrix_rooms = None

            packet = {
                "decoded": {"text": "test message", "portnum": 1},
                "fromId": "!12345678",
                "channel": 0,
            }

            mock_interface = MagicMock()

            # Should handle missing config gracefully
            try:
                on_meshtastic_message(packet, mock_interface)
            except Exception as e:
                self.fail(f"Failed to handle missing config: {e}")

        # Test with malformed config
        with patch("mmrelay.meshtastic_utils.logger"):
            mmrelay.meshtastic_utils.config = {"invalid": "config"}
            mmrelay.meshtastic_utils.matrix_rooms = []

            # Should handle malformed config gracefully
            try:
                on_meshtastic_message(packet, mock_interface)
            except Exception as e:
                self.fail(f"Failed to handle malformed config: {e}")


if __name__ == "__main__":
    unittest.main()
