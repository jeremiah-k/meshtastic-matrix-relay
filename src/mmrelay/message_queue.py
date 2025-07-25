"""
Message queue system for MMRelay.

Provides transparent message queuing with rate limiting to prevent overwhelming
the Meshtastic network. Messages are queued in memory and sent at the configured
rate, respecting connection state and firmware constraints.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Callable, Optional

from mmrelay.log_utils import get_logger

logger = get_logger(name="MessageQueue")

# Default message delay in seconds (minimum 2.0 due to firmware constraints)
DEFAULT_MESSAGE_DELAY = 2.2


@dataclass
class QueuedMessage:
    """Represents a message in the queue with metadata."""

    timestamp: float
    send_function: Callable
    args: tuple
    kwargs: dict
    description: str
    # Optional message mapping information for replies/reactions
    mapping_info: Optional[dict] = None


class MessageQueue:
    """
    Simple FIFO message queue with rate limiting for Meshtastic messages.

    Queues messages in memory and sends them in order at the configured rate to prevent
    overwhelming the mesh network. Respects connection state and automatically
    pauses during reconnections.
    """

    def __init__(self):
        self._queue = Queue()
        self._processor_task = None
        self._running = False
        self._lock = threading.Lock()
        self._last_send_time = 0.0
        self._message_delay = DEFAULT_MESSAGE_DELAY

    def start(self, message_delay: float = DEFAULT_MESSAGE_DELAY):
        """
        Start the message queue processor.

        Args:
            message_delay: Minimum seconds between messages (default: 2.2)
        """
        with self._lock:
            if self._running:
                return

            # Validate and enforce firmware minimum
            if message_delay < 2.0:
                logger.warning(f"Message delay {message_delay}s below firmware minimum (2.0s), using 2.0s")
                self._message_delay = 2.0
            else:
                self._message_delay = message_delay
            self._running = True

            # Start the processor in the event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._processor_task = loop.create_task(self._process_queue())
                    logger.info(
                        f"Message queue started with {self._message_delay}s message delay"
                    )
                else:
                    # Event loop exists but not running yet, defer startup
                    logger.debug(
                        "Event loop not running yet, will start processor later"
                    )
            except RuntimeError:
                # No event loop running, will start when one is available
                logger.debug(
                    "No event loop available, queue processor will start later"
                )

    def stop(self):
        """Stop the message queue processor."""
        with self._lock:
            if not self._running:
                return

            self._running = False

            if self._processor_task:
                self._processor_task.cancel()
                self._processor_task = None

            logger.info("Message queue stopped")

    def enqueue(
        self, send_function: Callable, *args, description: str = "", mapping_info: dict = None, **kwargs
    ) -> bool:
        """
        Enqueue a message for sending.

        Args:
            send_function: Function to call to send the message
            *args: Arguments to pass to send_function
            description: Human-readable description for logging
            mapping_info: Optional dict with message mapping info for replies/reactions
            **kwargs: Keyword arguments to pass to send_function

        Returns:
            bool: True if message was queued, False if queue is full
        """
        if not self._running:
            # Queue not started, send immediately as fallback
            logger.warning(f"Queue not running, sending message immediately: {description}")
            try:
                result = send_function(*args, **kwargs)
                logger.info(f"Immediate send {'successful' if result else 'failed'}: {description}")
                return result is not None
            except Exception as e:
                logger.error(f"Error sending message immediately: {e}")
                return False

        # Ensure processor is started if event loop is now available
        self.ensure_processor_started()

        # Check queue size to prevent memory issues
        if self._queue.qsize() >= 100:  # Reasonable limit for mesh network usage
            logger.warning(f"Message queue full ({self._queue.qsize()}/100), dropping message: {description}")
            return False

        message = QueuedMessage(
            timestamp=time.time(),
            send_function=send_function,
            args=args,
            kwargs=kwargs,
            description=description,
            mapping_info=mapping_info,
        )

        self._queue.put(message)
        # Only log queue status when there are multiple messages
        queue_size = self._queue.qsize()
        if queue_size >= 2:
            logger.debug(f"Queued message ({queue_size}/100): {description}")
        return True

    def get_queue_size(self) -> int:
        """Get current number of messages in queue."""
        return self._queue.qsize()

    def is_running(self) -> bool:
        """Check if queue processor is running."""
        return self._running

    def get_status(self) -> dict:
        """Get detailed queue status for debugging."""
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "message_delay": self._message_delay,
            "processor_task_active": self._processor_task is not None and not self._processor_task.done() if self._processor_task else False,
            "last_send_time": self._last_send_time,
            "time_since_last_send": time.time() - self._last_send_time if self._last_send_time > 0 else None
        }

    def ensure_processor_started(self):
        """Ensure the processor task is started if the queue is running."""
        if self._running and self._processor_task is None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    self._processor_task = loop.create_task(self._process_queue())
                    logger.info(
                        f"Message queue processor started with {self._message_delay}s message delay"
                    )
            except RuntimeError:
                # Still no event loop available
                pass

    async def _process_queue(self):
        """Process messages from the queue with rate limiting."""
        logger.debug("Message queue processor started")
        current_message = None

        while self._running:
            try:
                # Get next message if we don't have one waiting
                if current_message is None:
                    # Monitor queue depth for operational awareness
                    queue_size = self._queue.qsize()
                    if queue_size > 75:  # High queue depth threshold (75% of 100)
                        logger.warning(f"Queue depth high: {queue_size} messages pending")
                    elif queue_size > 50:  # Medium queue depth threshold (50% of 100)
                        logger.info(f"Queue depth moderate: {queue_size} messages pending")

                    # Get next message (non-blocking)
                    try:
                        current_message = self._queue.get_nowait()
                    except Empty:
                        # No messages, wait a bit and continue
                        await asyncio.sleep(0.1)
                        continue

                # Check if we should send (connection state, etc.)
                if not self._should_send_message():
                    # Keep the message and wait - don't requeue to maintain FIFO order
                    logger.debug(f"Connection not ready, waiting to send: {current_message.description}")
                    await asyncio.sleep(1.0)
                    continue

                # Check if we need to wait for message delay (only if we've sent before)
                if self._last_send_time > 0:
                    time_since_last = time.time() - self._last_send_time
                    if time_since_last < self._message_delay:
                        wait_time = self._message_delay - time_since_last
                        logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before sending")
                        await asyncio.sleep(wait_time)
                        continue

                # Send the message
                try:
                    logger.debug(f"Sending queued message: {current_message.description}")
                    # Run synchronous Meshtastic I/O operations in executor to prevent blocking event loop
                    result = await asyncio.get_running_loop().run_in_executor(
                        None, current_message.send_function, *current_message.args, **current_message.kwargs
                    )

                    # Update last send time
                    self._last_send_time = time.time()

                    if result is None:
                        logger.warning(
                            f"Message send returned None: {current_message.description}"
                        )
                    else:
                        logger.debug(f"Successfully sent queued message: {current_message.description}")

                        # Handle message mapping if provided
                        if current_message.mapping_info and hasattr(result, "id"):
                            self._handle_message_mapping(result, current_message.mapping_info)

                except Exception as e:
                    logger.error(
                        f"Error sending queued message '{current_message.description}': {e}"
                    )

                # Mark task as done and clear current message
                self._queue.task_done()
                current_message = None

            except asyncio.CancelledError:
                logger.debug("Message queue processor cancelled")
                if current_message:
                    logger.warning(
                        f"Message in flight was dropped during shutdown: {current_message.description}"
                    )
                break
            except Exception as e:
                logger.error(f"Error in message queue processor: {e}")
                await asyncio.sleep(1.0)  # Prevent tight error loop

    def _should_send_message(self) -> bool:
        """
        Check if we should send a message now.

        Returns:
            bool: True if it's safe to send, False if we should wait
        """
        # Import here to avoid circular imports
        try:
            from mmrelay.meshtastic_utils import meshtastic_client, reconnecting

            # Don't send during reconnection
            if reconnecting:
                logger.debug("Not sending - reconnecting is True")
                return False

            # Don't send if no client
            if meshtastic_client is None:
                logger.debug("Not sending - meshtastic_client is None")
                return False

            # Check if client is connected
            if (
                hasattr(meshtastic_client, "isConnected")
                and not meshtastic_client.isConnected.is_set()
            ):
                logger.debug("Not sending - client not connected")
                return False

            logger.debug("Connection check passed - ready to send")
            return True

        except ImportError as e:
            # ImportError indicates a serious problem with application structure
            logger.error(f"Cannot import meshtastic_utils - serious application error: {e}")
            return False

    def _handle_message_mapping(self, result, mapping_info):
        """
        Handle message mapping after successful send.

        Args:
            result: The result from the send function (should have .id attribute)
            mapping_info: Dict containing mapping information
        """
        try:
            # Import here to avoid circular imports
            from mmrelay.db_utils import store_message_map, prune_message_map

            # Extract mapping information
            matrix_event_id = mapping_info.get("matrix_event_id")
            room_id = mapping_info.get("room_id")
            text = mapping_info.get("text")
            meshnet = mapping_info.get("meshnet")

            if matrix_event_id and room_id and text:
                # Store the message mapping
                store_message_map(
                    result.id,
                    matrix_event_id,
                    room_id,
                    text,
                    meshtastic_meshnet=meshnet,
                )
                logger.debug(f"Stored message map for meshtastic_id: {result.id}")

                # Handle pruning if configured
                msgs_to_keep = mapping_info.get("msgs_to_keep", 500)
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)

        except Exception as e:
            logger.error(f"Error handling message mapping: {e}")


# Global message queue instance
_message_queue = MessageQueue()


def get_message_queue() -> MessageQueue:
    """Get the global message queue instance."""
    return _message_queue


def start_message_queue(message_delay: float = DEFAULT_MESSAGE_DELAY):
    """Start the global message queue with the specified message delay."""
    _message_queue.start(message_delay)


def stop_message_queue():
    """Stop the global message queue."""
    _message_queue.stop()


def queue_message(
    send_function: Callable, *args, description: str = "", mapping_info: dict = None, **kwargs
) -> bool:
    """
    Queue a message for sending through the global message queue.

    Args:
        send_function: Function to call to send the message
        *args: Arguments to pass to send_function
        description: Human-readable description for logging
        mapping_info: Optional dict with message mapping info for replies/reactions
        **kwargs: Keyword arguments to pass to send_function

    Returns:
        bool: True if message was queued, False if failed
    """
    return _message_queue.enqueue(
        send_function, *args, description=description, mapping_info=mapping_info, **kwargs
    )


def get_queue_status() -> dict:
    """Get detailed status of the global message queue for debugging."""
    return _message_queue.get_status()
