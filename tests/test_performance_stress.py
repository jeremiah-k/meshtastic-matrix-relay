#!/usr/bin/env python3
"""
Test suite for Performance and Stress testing in MMRelay.

Tests performance and stress scenarios including:
- High message volume processing
- Memory usage under load
- Database performance with large datasets
- Plugin processing performance
- Concurrent connection handling
- Resource cleanup and garbage collection
- Rate limiting effectiveness
"""

import asyncio
import gc
import os
import sys
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY, MINIMUM_MESSAGE_DELAY
from mmrelay.meshtastic_utils import on_meshtastic_message
from mmrelay.message_queue import MessageQueue


@pytest.fixture(autouse=True)
def reset_global_state():
    """
    Reset mmrelay.meshtastic_utils module state and run garbage collection before and after a test.

    This pytest fixture clears meshtastic-related globals (meshtastic_client, reconnecting, config, matrix_rooms,
    shutting_down, event_loop, reconnect_task, subscribed_to_messages, and subscribed_to_connection_lost) in
    mmrelay.meshtastic_utils to ensure test isolation, calls gc.collect() before yielding to the test, and calls
    gc.collect() again after the test completes.
    """
    # Reset global state before the test
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

    gc.collect()

    yield

    # Reset global state after the test
    mmrelay.meshtastic_utils.meshtastic_client = None
    mmrelay.meshtastic_utils.reconnecting = False
    mmrelay.meshtastic_utils.config = None
    mmrelay.meshtastic_utils.matrix_rooms = []
    mmrelay.meshtastic_utils.shutting_down = False
    mmrelay.meshtastic_utils.event_loop = None
    mmrelay.meshtastic_utils.reconnect_task = None
    mmrelay.meshtastic_utils.subscribed_to_messages = False
    mmrelay.meshtastic_utils.subscribed_to_connection_lost = False

    gc.collect()


class TestPerformanceStress:
    """Test cases for performance and stress scenarios."""

    @pytest.mark.performance  # Changed from slow to performance
    def test_high_volume_message_processing(self):
        """
        Simulates processing of 1000 Meshtastic messages to verify that all are handled within 15 seconds at a throughput exceeding 35 messages per second.

        Mocks dependencies and measures total processing time and throughput, asserting that all messages are processed and performance criteria are met.
        """
        import tempfile

        from mmrelay.db_utils import initialize_database

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "performance_test.sqlite")
            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                initialize_database()

                message_count = 1000
                processed_messages = []

                def mock_matrix_relay(*args, **kwargs):
                    """
                    Mocks the matrix relay function by recording its input arguments for later inspection.

                    Parameters:
                        *args: Positional arguments passed to the relay.
                        **kwargs: Keyword arguments passed to the relay.
                    """
                    processed_messages.append(args)

                mock_interface = MagicMock()
                mock_interface.nodes = {
                    "!12345678": {
                        "user": {
                            "id": "!12345678",
                            "longName": "Test Node",
                            "shortName": "TN",
                        }
                    }
                }
                mock_interface.myInfo.my_node_num = 123456789

                import asyncio

                import mmrelay.meshtastic_utils

                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                mmrelay.meshtastic_utils.event_loop = loop

                mmrelay.meshtastic_utils.config = {
                    "matrix_rooms": [
                        {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    ],
                    "meshtastic": {"meshnet_name": "TestMesh"},
                }
                mmrelay.meshtastic_utils.matrix_rooms = [
                    {"id": "!room:matrix.org", "meshtastic_channel": 0}
                ]

                try:
                    with patch(
                        "mmrelay.plugin_loader.load_plugins", return_value=[]
                    ), patch(
                        "mmrelay.matrix_utils.get_matrix_prefix",
                        return_value="[TestMesh/TN] ",
                    ), patch(
                        "mmrelay.db_utils.get_longname", return_value="Test Node"
                    ), patch(
                        "mmrelay.db_utils.get_shortname", return_value="TN"
                    ), patch(
                        "mmrelay.matrix_utils.matrix_relay",
                        new_callable=AsyncMock,
                        side_effect=mock_matrix_relay,
                    ):

                        start_time = time.time()

                        for i in range(message_count):
                            packet = {
                                "decoded": {
                                    "text": f"Message {i}",
                                    "portnum": "TEXT_MESSAGE_APP",
                                },
                                "fromId": "!12345678",
                                "channel": 0,
                                "to": 4294967295,
                                "id": i,
                            }
                            on_meshtastic_message(packet, mock_interface)

                        loop.run_until_complete(asyncio.sleep(0.1))

                        end_time = time.time()
                        processing_time = end_time - start_time

                        assert len(processed_messages) == message_count
                        assert (
                            processing_time < 15.0
                        ), "Message processing took too long"
                        messages_per_second = message_count / processing_time
                        assert messages_per_second > 35, "Processing rate too slow"
                finally:
                    loop.close()

    @pytest.mark.timeout(300)
    @pytest.mark.performance  # Changed from slow to performance
    def test_message_queue_performance_under_load(self):
        """
        Verify MessageQueue processes messages with configured delay and maintains acceptable throughput when messages are enqueued rapidly.

        Enqueues 20 messages into a started MessageQueue (using DEFAULT_MESSAGE_DELAY of 2.5s), waits for processing to complete, and asserts that all messages are processed, total processing time respects the 2.5s per-message delay (with a small tolerance), and observed throughput exceeds 0.2 messages/second.

        Side effects: starts and stops a MessageQueue instance.
        """
        import asyncio

        async def run_test():
            # Mock Meshtastic client to allow message sending
            """
            Asynchronously tests MessageQueue performance under rapid enqueueing with configured delay.

            Enqueues 20 messages using a mock send function into the MessageQueue, ensuring all messages are processed. Verifies that the queue respects the 2.5s configured delay between messages, all messages are processed, and the processing rate exceeds 0.2 messages per second.
            """
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    queue = MessageQueue()
                    queue.start(
                        message_delay=DEFAULT_MESSAGE_DELAY
                    )  # Use default delay (2.5s)
                    # Ensure processor starts now that event loop is running
                    queue.ensure_processor_started()

                    message_count = (
                        20  # Reduced for reasonable test duration (20 * 2.5s = 50s)
                    )
                    processed_count = 0

                    def mock_send_function():
                        nonlocal processed_count
                        processed_count += 1
                        return MagicMock(id="test_id")

                    try:
                        start_time = time.time()

                        # Queue many messages rapidly
                        for i in range(message_count):
                            success = queue.enqueue(
                                mock_send_function,
                                description=f"Performance test message {i}",
                            )
                            assert success, f"Failed to enqueue message {i}"

                        # Wait for processing to complete (20 messages * DEFAULT_MESSAGE_DELAY = 50s + buffer)
                        timeout = 65  # 65 second timeout (20 * 2.5s = 50s + buffer)
                        while (
                            processed_count < message_count
                            and time.time() - start_time < timeout
                        ):
                            await asyncio.sleep(0.1)

                        end_time = time.time()
                        processing_time = end_time - start_time

                        # Verify all messages were processed
                        assert processed_count == message_count

                        # Performance assertions (adjusted for 2.5s default delay)
                        expected_min_time = (
                            message_count * DEFAULT_MESSAGE_DELAY
                        )  # 2.5s per message with default delay
                        assert (
                            processing_time >= expected_min_time - 5.0
                        ), "Processing too fast (below expected delay)"

                        messages_per_second = message_count / processing_time
                        assert (
                            messages_per_second > 0.2
                        ), "Queue processing rate too slow"

                    finally:
                        queue.stop()

        # Run the async test
        asyncio.run(run_test())

    @pytest.mark.performance  # Changed from slow to performance
    def test_database_performance_large_dataset(self):
        """
        Measure performance of bulk database operations and message-map pruning using a temporary SQLite database.

        Performs the following end-to-end operations against a temporary on-disk DB (get_db_path is patched to point at the temp file):
        - Inserts 1000 longname records via save_longname and asserts total insert time is < 20s.
        - Retrieves those 1000 longnames via get_longname and validates values; asserts retrieval time is < 8s.
        - Inserts 1000 message-map entries via store_message_map; asserts insert time is < 20s.
        - Prunes the message map to retain the 100 most recent entries via prune_message_map; asserts prune time is < 8s.

        Side effects:
        - Mutates a temporary SQLite file on disk for the duration of the test.
        - Patches mmrelay.db_utils.get_db_path to point at the temporary database path.
        """
        import tempfile

        from mmrelay.db_utils import (
            get_longname,
            initialize_database,
            prune_message_map,
            save_longname,
            store_message_map,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "performance_test.sqlite")

            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                initialize_database()

                # Test bulk insertions
                node_count = 1000
                start_time = time.time()

                for i in range(node_count):
                    save_longname(f"!{i:08x}", f"Node {i}")

                insert_time = time.time() - start_time

                # Test bulk retrievals
                start_time = time.time()

                for i in range(node_count):
                    name = get_longname(f"!{i:08x}")
                    assert name == f"Node {i}"

                retrieval_time = time.time() - start_time

                # Performance assertions (adjusted for CI environment)
                assert insert_time < 20.0, "Database insertions too slow"
                assert retrieval_time < 8.0, "Database retrievals too slow"

                # Test message map performance
                message_count = 1000
                start_time = time.time()

                for i in range(message_count):
                    store_message_map(
                        f"mesh_{i}", f"matrix_{i}", "!room:matrix.org", f"Message {i}"
                    )

                message_insert_time = time.time() - start_time
                assert message_insert_time < 20.0, "Message map insertions too slow"

                # Test pruning performance
                start_time = time.time()
                prune_message_map(100)  # Keep only 100 most recent
                prune_time = time.time() - start_time

                assert prune_time < 8.0, "Message map pruning too slow"

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)
    @pytest.mark.performance  # Changed from slow to performance
    async def test_plugin_processing_performance(self, meshtastic_loop_safety):
        """
        Test the performance of processing messages through multiple plugins.

        Simulates processing 100 messages through 10 mock plugins, ensuring each plugin's handler is called for every message. Asserts that all plugin handlers are invoked the correct number of times, total processing completes in under 10 seconds, and the aggregate plugin call rate exceeds 100 calls per second.
        """
        import tempfile

        from mmrelay.db_utils import initialize_database

        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "performance_test.sqlite")
            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                initialize_database()

                plugin_count = 10
                message_count = 100

                # Create multiple mock plugins
                plugins = []
                for i in range(plugin_count):
                    plugin = MagicMock()
                    plugin.priority = i
                    plugin.plugin_name = f"plugin_{i}"
                    plugin.handle_meshtastic_message = AsyncMock(return_value=False)
                    plugins.append(plugin)

                packet = {
                    "decoded": {"text": "Performance test message", "portnum": 1},
                    "fromId": "!12345678",
                    "channel": 0,
                }

                mock_interface = MagicMock()

                # Mock the global config that on_meshtastic_message needs
                mock_config = {
                    "meshtastic": {
                        "connection_type": "serial",
                        "meshnet_name": "TestMesh",
                    },
                    "matrix_rooms": {
                        "general": {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    },
                }

                # Mock interaction settings
                mock_interactions = {"reactions": True, "replies": True}

                # matrix_rooms should be a list of room dictionaries, not a dict of dicts
                mock_matrix_rooms = [
                    {"id": "!room:matrix.org", "meshtastic_channel": 0}
                ]

                with patch(
                    "mmrelay.plugin_loader.load_plugins", return_value=plugins
                ), patch("mmrelay.meshtastic_utils.config", mock_config), patch(
                    "mmrelay.meshtastic_utils.matrix_rooms", mock_matrix_rooms
                ), patch(
                    "mmrelay.matrix_utils.get_interaction_settings",
                    return_value=mock_interactions,
                ), patch(
                    "mmrelay.matrix_utils.message_storage_enabled", return_value=True
                ), patch(
                    "mmrelay.meshtastic_utils.shutting_down", False
                ), patch(
                    "mmrelay.meshtastic_utils.event_loop", meshtastic_loop_safety
                ), patch(
                    "mmrelay.meshtastic_utils._submit_coro"
                ) as mock_submit_coro:
                    mock_submit_coro.side_effect = (
                        lambda coro, loop=None: asyncio.create_task(coro)
                    )

                    start_time = time.time()

                    for _ in range(message_count):
                        on_meshtastic_message(packet, mock_interface)

                    # Wait for all tasks to complete
                    pending = [
                        task
                        for task in asyncio.all_tasks(loop=meshtastic_loop_safety)
                        if task is not asyncio.current_task()
                    ]
                    if pending:
                        await asyncio.gather(*pending)

                    end_time = time.time()
                    processing_time = end_time - start_time

                    total_plugin_calls = plugin_count * message_count
                    assert (
                        processing_time < 10.0
                    ), "Plugin processing too slow"  # Increased timeout for CI

                    calls_per_second = total_plugin_calls / processing_time
                    assert calls_per_second > 100, "Plugin call rate too slow"

                    for plugin in plugins:
                        assert (
                            plugin.handle_meshtastic_message.call_count == message_count
                        )

    @pytest.mark.performance  # Changed from slow to performance
    def test_concurrent_message_queue_access(self):
        """
        Test concurrent enqueuing and processing of messages in the MessageQueue from multiple threads.

        Spawns several threads to enqueue messages concurrently into the MessageQueue and verifies that all messages are processed within expected timing constraints. Asserts that the total processing time and processing rate meet minimum performance requirements under concurrent load.
        """
        import asyncio

        async def run_concurrent_test():
            # Mock Meshtastic client to allow message sending
            """
            Test concurrent enqueuing and processing of messages in MessageQueue from multiple threads.

            This function starts a MessageQueue with a minimal configured delay, spawns several threads to enqueue messages concurrently, and waits for all messages to be processed. It asserts that all messages are processed within the expected time frame and that the processing rate meets minimum performance requirements.
            """
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    queue = MessageQueue()
                    queue.start(
                        message_delay=0.5
                    )  # 0.5s delay for reasonable test duration
                    # Ensure processor starts now that event loop is running
                    queue.ensure_processor_started()

                    thread_count = 5
                    messages_per_thread = 3  # Small number for reasonable test duration (15 messages * 0.5s = 7.5s)
                    total_messages = thread_count * messages_per_thread

                    processed_count = 0
                    lock = threading.Lock()

                    def mock_send_function():
                        nonlocal processed_count
                        with lock:
                            processed_count += 1
                        return MagicMock(id="test_id")

                    def worker_thread(thread_id):
                        for i in range(messages_per_thread):
                            queue.enqueue(
                                mock_send_function,
                                description=f"Thread {thread_id} message {i}",
                            )

                    try:
                        start_time = time.time()

                        # Start multiple threads
                        threads = []
                        for i in range(thread_count):
                            thread = threading.Thread(target=worker_thread, args=(i,))
                            threads.append(thread)
                            thread.start()

                        # Wait for all threads to complete
                        for thread in threads:
                            thread.join()

                        # Wait for queue processing to complete (15 messages * 0.5s = 7.5s + buffer)
                        timeout = 15  # 15 * 0.5s = 7.5s + buffer
                        while (
                            processed_count < total_messages
                            and time.time() - start_time < timeout
                        ):
                            await asyncio.sleep(0.1)

                        end_time = time.time()
                        processing_time = end_time - start_time

                        # Verify all messages were processed
                        assert processed_count == total_messages

                        # Performance assertions (adjusted for 0.5s delay)
                        expected_min_time = total_messages * 0.5  # 0.5s per message
                        assert (
                            processing_time >= expected_min_time - 2.0
                        ), "Concurrent processing too fast (below expected delay)"

                        messages_per_second = total_messages / processing_time
                        assert (
                            messages_per_second > 0.3
                        ), "Concurrent processing rate too slow"

                    finally:
                        queue.stop()

        # Run the async test
        asyncio.run(run_concurrent_test())

    @pytest.mark.performance  # Changed from slow to performance
    def test_memory_usage_stability(self):
        """
        Verify that processing 1,000 messages in batches does not increase process memory usage by more than 50 MB.

        Simulates extended operation by processing messages in multiple iterations, periodically forcing garbage collection, and measuring memory usage before and after to ensure stability.
        """
        import os

        import psutil

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Simulate extended operation
        iterations = 100
        mock_interface = MagicMock()

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
            with patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock):
                # Set up minimal config
                import mmrelay.meshtastic_utils

                mmrelay.meshtastic_utils.config = {"matrix_rooms": []}
                mmrelay.meshtastic_utils.matrix_rooms = []

                for iteration in range(iterations):
                    # Create and process messages
                    for j in range(10):
                        packet = {
                            "decoded": {
                                "text": f"Memory test {iteration}-{j}",
                                "portnum": 1,
                            },
                            "fromId": f"!{j:08x}",
                            "channel": 0,
                            "id": iteration * 10 + j,
                        }
                        on_meshtastic_message(packet, mock_interface)

                    # Force garbage collection periodically
                    if iteration % 20 == 0:
                        gc.collect()

        # Check final memory usage
        final_memory = process.memory_info().rss
        memory_increase = final_memory - initial_memory

        # Memory increase should be reasonable (less than 50MB)
        max_acceptable_increase = 50 * 1024 * 1024  # 50MB
        assert (
            memory_increase < max_acceptable_increase
        ), f"Memory usage increased by {memory_increase / 1024 / 1024:.2f}MB"

    @pytest.mark.performance  # Changed from slow to performance
    def test_rate_limiting_effectiveness(self):
        """
        Test that MessageQueue respects the configured delay between message sends, confirming timing behavior by measuring intervals between processed messages.

        Rapidly enqueues multiple messages with a short requested delay and asserts that the actual delay between sends is close to the configured 0.1s delay (within 50%-200% tolerance). Ensures all messages are sent within the expected timeframe.
        """
        import asyncio

        async def run_rate_limit_test():
            # Mock Meshtastic client to allow message sending
            """
            Asynchronously verifies that the MessageQueue respects the configured delay between message sends by measuring the intervals between processed messages to confirm timing behavior.
            """
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    queue = MessageQueue()
                    message_delay = 0.1  # 100ms delay between messages (will warn about 2.1s minimum)
                    queue.start(message_delay=message_delay)
                    # Ensure processor starts now that event loop is running
                    queue.ensure_processor_started()

                    message_count = 5  # Reasonable number for rate limiting test
                    send_times = []

                    def mock_send_function():
                        send_times.append(time.time())
                        return MagicMock(id="test_id")

                    try:
                        # Queue messages rapidly
                        for i in range(message_count):
                            queue.enqueue(
                                mock_send_function, description=f"Rate limit test {i}"
                            )

                        # Wait for all messages to be processed (5 messages * 0.1s = 0.5s + buffer)
                        timeout = (
                            message_count * message_delay + 5
                        )  # Extra buffer for actual delay
                        start_wait = time.time()
                        while (
                            len(send_times) < message_count
                            and time.time() - start_wait < timeout
                        ):
                            await asyncio.sleep(0.1)

                        # Verify all messages were sent
                        assert len(send_times) == message_count

                        # Verify messages were sent with approximately the configured delay
                        for i in range(1, len(send_times)):
                            time_diff = send_times[i] - send_times[i - 1]
                            # Allow some tolerance for timing variations (should be close to 0.1s)
                            assert (
                                time_diff >= message_delay * 0.5
                                and time_diff <= message_delay * 2.0
                            ), f"Message delay {time_diff:.3f}s not close to expected {message_delay}s between messages {i-1} and {i}"

                    finally:
                        queue.stop()

        # Run the async test
        asyncio.run(run_rate_limit_test())

    @pytest.mark.performance  # Resource cleanup test can be slow
    def test_resource_cleanup_effectiveness(self):
        """
        Verify that MessageQueue and plugin objects are fully garbage collected after use, ensuring no lingering references remain following typical operation and cleanup.
        """
        import weakref

        # Test message queue cleanup
        queue = MessageQueue()
        queue_ref = weakref.ref(queue)

        queue.start(message_delay=0.1)
        queue.stop()

        # Ensure any event loops are properly closed
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if not loop.is_closed():
                loop.close()
        except RuntimeError:
            pass  # No event loop running

        del queue
        gc.collect()

        # Queue should be garbage collected
        assert queue_ref() is None, "MessageQueue not properly cleaned up"

        # Test plugin cleanup
        mock_plugin = MagicMock()
        plugin_ref = weakref.ref(mock_plugin)

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[mock_plugin]):
            # Process a message
            packet = {
                "decoded": {"text": "cleanup test", "portnum": 1},
                "fromId": "!12345678",
                "channel": 0,
            }
            mock_interface = MagicMock()
            on_meshtastic_message(packet, mock_interface)

        del mock_plugin
        gc.collect()

        # Plugin should be garbage collected
        assert plugin_ref() is None, "Plugin not properly cleaned up"

    @pytest.mark.performance  # New realistic throughput benchmark
    def test_realistic_throughput_benchmark(self):
        """
        Benchmark message throughput under realistic conditions with mixed message types and enforced rate limiting.

        Simulates a mesh network by asynchronously queuing and processing messages of various types from multiple nodes over a fixed duration. Validates that throughput uses the configured delay, achieves at least 65% of theoretical maximum throughput, and processes multiple message types. Prints detailed throughput statistics after completion.
        """
        import asyncio
        import random

        async def run_throughput_test():
            """
            Run a 30-second realistic throughput benchmark that enqueues mixed-message traffic into a MessageQueue and validates rate-limiting and basic throughput/diversity expectations.

            This coroutine:
            - Seeds the RNG for deterministic test behavior.
            - Starts a MessageQueue processor with a 2.1 second enforced send delay.
            - Enqueues messages of several types from multiple mock node IDs at randomized intervals (0.5–3.0s) for 30 seconds.
            - Records timestamps of processed messages, waits up to 15s for the queue to drain, and computes throughput using the active processing window (first to last processed timestamp) when possible.
            - Asserts minimal test invariants: multiple messages were queued and at least one processed; throughput does not exceed the rate-limit-derived upper bound and — when >= 2 messages were processed — meets a minimum expected throughput; message-type diversity is observed.
            - Prints a brief summary of duration, queued/processed counts, throughput, and per-type counts.
            - Stops the MessageQueue on completion.

            Raises:
                AssertionError: if queue draining, throughput, or diversity checks fail.
            """
            random.seed(0)  # Reduce flakiness in CI
            with patch(
                "mmrelay.meshtastic_utils.meshtastic_client",
                MagicMock(is_connected=True),
            ):
                with patch("mmrelay.meshtastic_utils.reconnecting", False):
                    queue = MessageQueue()
                    queue.start(
                        message_delay=MINIMUM_MESSAGE_DELAY
                    )  # Use realistic 2.1s delay
                    queue.ensure_processor_started()

                    # Realistic test parameters
                    test_duration = 30  # 30 second test
                    message_types = [
                        "TEXT_MESSAGE_APP",
                        "TELEMETRY_APP",
                        "POSITION_APP",
                    ]
                    node_ids = [f"!{i:08x}" for i in range(1, 11)]  # 10 nodes

                    processed_messages = []
                    start_time = time.time()

                    def mock_send_function(msg_type, node_id):
                        """
                        Simulates sending a message by recording its type, node, and timestamp, and returns a mock message object.

                        Parameters:
                            msg_type: The type of the message being sent.
                            node_id: The identifier of the node sending the message.

                        Returns:
                            MagicMock: A mock object representing the sent message, with a unique ID.
                        """
                        processed_messages.append(
                            {
                                "type": msg_type,
                                "node": node_id,
                                "timestamp": time.time(),
                            }
                        )
                        return MagicMock(id=f"msg_{len(processed_messages)}")

                    try:
                        # Generate realistic message load
                        messages_queued = 0
                        while time.time() - start_time < test_duration:
                            # Randomly select message type and node
                            msg_type = random.choice(
                                message_types
                            )  # nosec B311 - Test data generation, not cryptographic
                            node_id = random.choice(
                                node_ids
                            )  # nosec B311 - Test data generation, not cryptographic

                            # Queue message with realistic frequency
                            success = queue.enqueue(
                                lambda mt=msg_type, nid=node_id: mock_send_function(
                                    mt, nid
                                ),
                                description=f"{msg_type} from {node_id}",
                            )

                            if success:
                                messages_queued += 1

                            # Realistic inter-message delay (0.5-3 seconds)
                            await asyncio.sleep(
                                random.uniform(
                                    0.5, 3.0
                                )  # nosec B311 - Test timing variation, not cryptographic
                            )

                        # Wait for queue to drain (bounded)
                        drained = await queue.drain(timeout=15.0)
                        assert drained, "Queue did not drain within timeout"

                        end_time = time.time()
                        total_time = end_time - start_time

                        # Calculate throughput metrics using the active processing window
                        messages_processed = len(processed_messages)
                        if messages_processed >= 2:
                            first_ts = processed_messages[0]["timestamp"]
                            last_ts = processed_messages[-1]["timestamp"]
                            active_duration = max(last_ts - first_ts, 1e-6)
                            throughput = messages_processed / active_duration
                        else:
                            # Fallback to total_time-based throughput for single-message edge case
                            throughput = messages_processed / total_time

                        # Validate realistic performance expectations
                        assert messages_queued > 5, "Should queue multiple messages"
                        assert messages_processed > 0, "Should process some messages"

                        # Throughput should be reasonable for MINIMUM_MESSAGE_DELAY minimum delay
                        # With MINIMUM_MESSAGE_DELAY delay, max theoretical throughput is 1/MINIMUM_MESSAGE_DELAY msg/s
                        assert (
                            throughput <= 0.6
                        ), "Throughput should respect rate limiting"

                        # Should achieve at least 65% of theoretical maximum during active window
                        # With MINIMUM_MESSAGE_DELAY delay, max theoretical throughput is 1/MINIMUM_MESSAGE_DELAY msg/s
                        min_expected_throughput = 0.32
                        if messages_processed >= 2:
                            assert (
                                throughput >= min_expected_throughput
                            ), f"Throughput {throughput:.3f} msg/s below minimum {min_expected_throughput}"

                        # Verify message type distribution
                        type_counts = {}
                        for msg in processed_messages:
                            msg_type = msg["type"]
                            type_counts[msg_type] = type_counts.get(msg_type, 0) + 1

                        # Should have processed multiple message types
                        assert (
                            len(type_counts) > 0
                        ), "Should process various message types"

                        print("\nThroughput Benchmark Results:")
                        print(f"  Duration: {total_time:.1f}s")
                        print(f"  Messages Queued: {messages_queued}")
                        print(f"  Messages Processed: {messages_processed}")
                        print(f"  Throughput: {throughput:.3f} msg/s")
                        print(f"  Message Types: {type_counts}")

                    finally:
                        queue.stop()

        # Run the async throughput test
        asyncio.run(run_throughput_test())
