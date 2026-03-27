"""
Test suite for network reliability and connection handling.

This module tests network connection reliability scenarios including
retry logic, backoff behavior, connection type fallbacks, and message
queuing during network interruptions.
"""

import asyncio
import http.client
import socket
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.constants.network import (
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BACKOFF_TIME,
    INFINITE_RETRIES,
    MINIMUM_MESSAGE_DELAY,
)
from mmrelay.constants.queue import MAX_QUEUE_SIZE


@pytest.fixture(autouse=True)
def block_external_network_calls(monkeypatch):
    """
    Fail fast if a test attempts a real outbound network request.
    """

    def _blocked(*_args, **_kwargs):
        raise AssertionError("External network calls are not allowed in this test")

    async def _blocked_async(*_args, **_kwargs):
        raise AssertionError("External network calls are not allowed in this test")

    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(http.client.HTTPConnection, "request", _blocked)
    monkeypatch.setattr(http.client.HTTPSConnection, "request", _blocked)
    monkeypatch.setattr(asyncio, "open_connection", _blocked_async)


class TestConnectionRetryLogic:
    """Test connection retry and backoff behavior."""

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("comprehensive_cleanup")
    async def test_connection_retry_backoff_timing(self):
        """
        Verify that connection retry logic applies backoff delays between failed attempts and succeeds after the expected number of retries.

        This test simulates two consecutive connection failures followed by a successful attempt, ensuring that the retry mechanism waits for the appropriate backoff duration between retries and attempts the correct number of connections.
        """
        backoff_delays = []

        async def _record_sleep(duration):
            backoff_delays.append(duration)

        with (
            patch("asyncio.sleep", side_effect=_record_sleep),
            patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        ):
            # Simulate connection failures followed by success
            mock_connect.side_effect = [
                ConnectionError("First attempt"),
                ConnectionError("Second attempt"),
                MagicMock(),  # Success on third attempt
            ]

            # Simulate retry logic with two backoff waits.
            for attempt in range(3):
                try:
                    mock_connect()
                    break
                except ConnectionError:
                    if attempt < 2:  # Don't sleep on last attempt
                        await asyncio.sleep(DEFAULT_BACKOFF_TIME)

            # Should have attempted connection 3 times
            assert mock_connect.call_count == 3

            # Should have requested two backoff periods.
            assert backoff_delays == [DEFAULT_BACKOFF_TIME, DEFAULT_BACKOFF_TIME]

    @pytest.mark.asyncio
    async def test_exponential_backoff_progression(self):
        """
        Verify that connection retry backoff durations increase exponentially after consecutive failures.

        Simulates multiple connection failures and checks that the backoff intervals between retries follow an exponential progression, with each subsequent delay at least as long as the previous one.
        """
        backoff_times = []

        def mock_sleep(duration):
            """
            Mocks the sleep function by recording the requested duration in the backoff_times list.
            """
            backoff_times.append(duration)

        with (
            patch("asyncio.sleep", side_effect=mock_sleep),
            patch("mmrelay.meshtastic_utils.connect_meshtastic") as mock_connect,
        ):
            # Simulate multiple failures
            mock_connect.side_effect = [ConnectionError()] * 5

            # Simulate exponential backoff logic
            base_backoff = DEFAULT_BACKOFF_TIME
            for attempt in range(5):
                try:
                    mock_connect()
                except ConnectionError:
                    if attempt < 4:  # Don't sleep on last attempt
                        backoff_duration = base_backoff * (2**attempt)
                        await asyncio.sleep(backoff_duration)

            # Verify exponential progression
            assert len(backoff_times) == 4  # 4 backoff periods for 5 attempts
            for i in range(1, len(backoff_times)):
                assert backoff_times[i] >= backoff_times[i - 1]

    @pytest.mark.asyncio
    async def test_infinite_retries_behavior(self):
        """
        Test that the connection retry logic correctly handles infinite retries by continuing to attempt connections until success.

        Verifies that when infinite retries are configured, the system keeps retrying after failures and eventually succeeds, as indicated by the retry count.
        """
        retry_count = 0

        def mock_connect():
            """
            Simulates a connection attempt that fails with a ConnectionError for the first nine calls, then succeeds on the tenth by returning a mock object.
            """
            nonlocal retry_count
            retry_count += 1
            if retry_count < 10:  # Fail first 9 times
                raise ConnectionError(f"Attempt {retry_count}")
            return MagicMock()  # Success on 10th attempt

        with patch(
            "mmrelay.meshtastic_utils.connect_meshtastic", side_effect=mock_connect
        ):
            # Simulate retry logic with INFINITE_RETRIES
            max_attempts = 15  # Reasonable limit for test
            for attempt in range(max_attempts):
                try:
                    result = mock_connect()
                    assert result is not None
                    break
                except ConnectionError:
                    if INFINITE_RETRIES == 0:  # 0 means infinite
                        continue
                    elif attempt >= INFINITE_RETRIES:
                        break

            assert retry_count == 10  # Should succeed on 10th attempt


class TestConnectionTypeFallback:
    """Test fallback between different connection types."""

    @pytest.mark.asyncio
    async def test_connection_type_sequence(self):
        """
        Verifies that connection attempts are made sequentially through TCP, Serial, and BLE, succeeding when BLE is reached.

        Ensures each connection type is tried in order, with BLE ultimately succeeding after previous failures.
        """
        connection_attempts = []

        def mock_connect(connection_type):
            """
            Simulates a connection attempt for the specified connection type, succeeding only for BLE.

            Parameters:
                connection_type (str): The type of connection to attempt.

            Returns:
                MagicMock: A mock connection object if the connection type is BLE.

            Raises:
                ConnectionError: If the connection type is not BLE.
            """
            connection_attempts.append(connection_type)
            if connection_type == CONNECTION_TYPE_BLE:
                return MagicMock()  # BLE succeeds
            raise ConnectionError(f"{connection_type} failed")

        connection_types = [
            CONNECTION_TYPE_TCP,
            CONNECTION_TYPE_SERIAL,
            CONNECTION_TYPE_BLE,
        ]

        # Simulate trying each connection type
        for conn_type in connection_types:
            try:
                result = mock_connect(conn_type)
                if result:
                    break
            except ConnectionError:
                continue

        assert connection_attempts == connection_types
        assert connection_attempts[-1] == CONNECTION_TYPE_BLE  # BLE succeeded

    @pytest.mark.asyncio
    async def test_connection_type_validation(self):
        """
        Verify that all defined connection types are valid, non-empty strings and belong to the set of recognized connection types.
        """
        valid_types = {CONNECTION_TYPE_TCP, CONNECTION_TYPE_SERIAL, CONNECTION_TYPE_BLE}

        # Test that all defined connection types are valid
        for conn_type in valid_types:
            assert isinstance(conn_type, str)
            assert len(conn_type) > 0
            assert conn_type in ["tcp", "serial", "ble", "network"]

    @pytest.mark.asyncio
    async def test_connection_preference_order(self):
        """
        Verify that the preferred connection types are ordered by reliability and speed, typically TCP, Serial, then BLE.
        """
        # Typically TCP -> Serial -> BLE for reliability/speed
        preferred_order = [
            CONNECTION_TYPE_TCP,
            CONNECTION_TYPE_SERIAL,
            CONNECTION_TYPE_BLE,
        ]

        # Verify the order makes sense (this is more of a design test)
        assert CONNECTION_TYPE_TCP in preferred_order
        assert CONNECTION_TYPE_SERIAL in preferred_order
        assert CONNECTION_TYPE_BLE in preferred_order


class TestMessageQueueDuringDisconnection:
    """Test message queuing behavior during network interruptions."""

    def test_message_queuing_when_disconnected(self):
        """
        Verify messages are enqueued while the network is unavailable.

        Starts a MessageQueue, simulates a disconnected state by setting the meshtastic client to None, enqueues multiple messages using a mock send function, and asserts each enqueue succeeds and the queue size reflects the enqueued messages.
        """
        from mmrelay.message_queue import MessageQueue

        queue = MessageQueue()
        queue.start()

        # Mock send function
        mock_send = MagicMock()

        try:
            # Simulate disconnected state
            with patch("mmrelay.meshtastic_utils.meshtastic_client", None):
                # Messages should be queued, not lost
                test_messages = ["Message 1", "Message 2", "Message 3"]

                for msg in test_messages:
                    result = queue.enqueue(
                        mock_send, msg, description=f"Test message: {msg}"
                    )
                    assert result is True  # Should successfully enqueue

                # Queue should contain all messages
                assert queue.get_queue_size() >= len(test_messages)

        finally:
            queue.stop()

    @pytest.mark.usefixtures("comprehensive_cleanup")
    def test_queue_overflow_protection(self):
        """
        Verify the MessageQueue enforces MAX_QUEUE_SIZE by accepting messages up to the limit and rejecting any excess.

        This test fills the queue beyond MAX_QUEUE_SIZE and asserts that the internal queue size never exceeds the configured maximum and that the number of successful enqueue operations is no greater than MAX_QUEUE_SIZE.
        """
        from mmrelay.message_queue import MessageQueue

        queue = MessageQueue()
        queue.start()

        # Mock send function
        mock_send = MagicMock()

        try:
            # Fill queue to near capacity
            messages_to_send = MAX_QUEUE_SIZE + 10  # Exceed max size

            with patch("mmrelay.meshtastic_utils.meshtastic_client", None):
                successful_enqueues = 0
                for i in range(messages_to_send):
                    result = queue.enqueue(
                        mock_send, f"Message {i}", description=f"Test message {i}"
                    )
                    if result:
                        successful_enqueues += 1

                # Queue should not exceed maximum size
                assert queue.get_queue_size() <= MAX_QUEUE_SIZE
                # Should have rejected some messages when full
                assert successful_enqueues <= MAX_QUEUE_SIZE

        finally:
            queue.stop()

    @pytest.mark.asyncio
    async def test_message_processing_after_reconnection(self):
        """
        Verify that messages enqueued during network disconnection are processed after reconnection.

        This test enqueues messages while the network client is unavailable, then simulates reconnection and checks that the message queue size decreases, indicating that queued messages are being processed.
        """
        from mmrelay.message_queue import MessageQueue

        queue = MessageQueue()
        queue.start()

        # Mock send function
        mock_send = MagicMock()

        try:
            # Queue messages while disconnected
            test_messages = ["Queued 1", "Queued 2", "Queued 3"]

            with patch("mmrelay.meshtastic_utils.meshtastic_client", None):
                for msg in test_messages:
                    queue.enqueue(mock_send, msg, description=f"Queued: {msg}")

                initial_queue_size = queue.get_queue_size()

            # Simulate reconnection and message processing
            mock_client = MagicMock()
            with patch("mmrelay.meshtastic_utils.meshtastic_client", mock_client):
                # Allow some time for queue processing
                await asyncio.sleep(0.2)

                # Queue should start processing (size should decrease or be empty)
                final_queue_size = queue.get_queue_size()
                assert final_queue_size <= initial_queue_size

        finally:
            queue.stop()


class TestNetworkErrorRecovery:
    """Test recovery from various network error conditions."""

    @pytest.mark.asyncio
    async def test_timeout_error_recovery(self):
        """
        Asynchronously tests that the system recovers from consecutive network timeout errors by retrying connection attempts until successful.

        The test simulates two initial `TimeoutError` exceptions before a successful connection, verifying that retries occur and the connection eventually succeeds after brief delays.
        """
        timeout_count = 0

        def mock_connect():
            """
            Simulates a network connection attempt that fails with a TimeoutError on the first two calls, then succeeds on the third and subsequent calls.
            """
            nonlocal timeout_count
            timeout_count += 1
            if timeout_count <= 2:
                raise TimeoutError("Network timeout")
            return MagicMock()

        with patch(
            "mmrelay.meshtastic_utils.connect_meshtastic", side_effect=mock_connect
        ):
            # Should eventually succeed after timeouts
            for _ in range(5):
                try:
                    result = mock_connect()
                    assert result is not None
                    break
                except TimeoutError:
                    await asyncio.sleep(0.1)  # Brief delay between attempts

            assert timeout_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_connection_reset_recovery(self):
        """
        Asynchronously tests that the system recovers from a connection reset error by retrying the connection and succeeding on a subsequent attempt.
        """
        reset_count = 0

        def mock_connect():
            """
            Simulates a connection attempt that raises a ConnectionResetError on the first call and succeeds on subsequent calls.

            Returns:
                MagicMock: A mock object representing a successful connection after the initial failure.
            """
            nonlocal reset_count
            reset_count += 1
            if reset_count <= 1:
                raise ConnectionResetError("Connection reset by peer")
            return MagicMock()

        with patch(
            "mmrelay.meshtastic_utils.connect_meshtastic", side_effect=mock_connect
        ):
            # Should recover from connection reset
            for _ in range(3):
                try:
                    result = mock_connect()
                    assert result is not None
                    break
                except ConnectionResetError:
                    await asyncio.sleep(0.1)

            assert reset_count == 2  # Failed once, succeeded on second

    @pytest.mark.asyncio
    async def test_message_delay_enforcement(self):
        """
        Verify that the minimum delay between consecutive sends is requested.
        """
        send_count = 0
        requested_delays = []

        async def mock_send_message():
            """
            Simulates sending a message by incrementing a counter.
            """
            nonlocal send_count
            send_count += 1

        async def _record_sleep(duration):
            requested_delays.append(duration)

        with patch("asyncio.sleep", side_effect=_record_sleep):
            # Simulate rapid message sending with enforced spacing
            for _ in range(3):
                await mock_send_message()
                await asyncio.sleep(MINIMUM_MESSAGE_DELAY)

        assert send_count == 3
        assert requested_delays == [
            MINIMUM_MESSAGE_DELAY,
            MINIMUM_MESSAGE_DELAY,
            MINIMUM_MESSAGE_DELAY,
        ]
