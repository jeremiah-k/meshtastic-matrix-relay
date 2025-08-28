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

from mmrelay.constants.database import DEFAULT_MSGS_TO_KEEP
from mmrelay.constants.network import MINIMUM_MESSAGE_DELAY
from mmrelay.constants.queue import (
    DEFAULT_MESSAGE_DELAY,
    MAX_QUEUE_SIZE,
    QUEUE_HIGH_WATER_MARK,
    QUEUE_MEDIUM_WATER_MARK,
)
from mmrelay.log_utils import get_logger

logger = get_logger(name="MessageQueue")


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
        """
        Initialize the MessageQueue with an empty queue, state variables, and a thread lock for safe operation.
        """
        self._queue = Queue()
        self._processor_task = None
        self._running = False
        self._lock = threading.Lock()
        self._last_send_time = 0.0
        self._message_delay = DEFAULT_MESSAGE_DELAY

    def start(self, message_delay: float = DEFAULT_MESSAGE_DELAY):
        """
        Start the message queue processor with a specified minimum delay between messages.

        If the provided delay is below the firmware-enforced minimum, the minimum is used instead. The processor task is started immediately if the asyncio event loop is running; otherwise, startup is deferred until the event loop becomes available.
        """
        with self._lock:
            if self._running:
                return

            # Validate and enforce firmware minimum
            if message_delay < MINIMUM_MESSAGE_DELAY:
                logger.warning(
                    f"Message delay {message_delay}s below firmware minimum ({MINIMUM_MESSAGE_DELAY}s), using {MINIMUM_MESSAGE_DELAY}s"
                )
                self._message_delay = MINIMUM_MESSAGE_DELAY
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
        """
        Stops the message queue processor and cancels the processing task if active.
        """
        with self._lock:
            if not self._running:
                return

            self._running = False

            if self._processor_task:
                self._processor_task.cancel()
                # Wait for the task to complete to ensure proper cleanup
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # If we're in an async context, we can't wait synchronously
                        # The task will be cleaned up by the event loop
                        pass
                    else:
                        # If the loop is not running, we can wait for the task
                        try:
                            loop.run_until_complete(self._processor_task)
                        except asyncio.CancelledError:
                            pass  # Expected when cancelling
                except RuntimeError:
                    # No event loop available
                    pass
                self._processor_task = None

            logger.info("Message queue stopped")

    def enqueue(
        self,
        send_function: Callable,
        *args,
        description: str = "",
        mapping_info: dict = None,
        **kwargs,
    ) -> bool:
        """
        Adds a message to the queue for rate-limited, ordered sending.

        Parameters:
            send_function (Callable): The function to call to send the message.
            *args: Positional arguments for the send function.
            description (str, optional): Human-readable description for logging purposes.
            mapping_info (dict, optional): Optional metadata for message mapping (e.g., replies or reactions).
            **kwargs: Keyword arguments for the send function.

        Returns:
            bool: True if the message was successfully enqueued; False if the queue is not running or is full.
        """
        # Ensure processor is started if event loop is now available.
        # This is called outside the lock to prevent potential deadlocks.
        self.ensure_processor_started()

        with self._lock:
            if not self._running:
                # Refuse to send to prevent blocking the event loop
                logger.error(f"Queue not running, cannot send message: {description}")
                logger.error(
                    "Application is in invalid state - message queue should be started before sending messages"
                )
                return False

            # Check queue size to prevent memory issues
            if self._queue.qsize() >= MAX_QUEUE_SIZE:
                logger.warning(
                    f"Message queue full ({self._queue.qsize()}/{MAX_QUEUE_SIZE}), dropping message: {description}"
                )
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
                logger.debug(
                    f"Queued message ({queue_size}/{MAX_QUEUE_SIZE}): {description}"
                )
            return True

    def get_queue_size(self) -> int:
        """
        Return the number of messages currently in the queue.

        Returns:
            int: The current queue size.
        """
        return self._queue.qsize()

    def is_running(self) -> bool:
        """
        Return whether the message queue processor is currently active.
        """
        return self._running

    def get_status(self) -> dict:
        """
        Return a dictionary with the current status of the message queue, including running state, queue size, message delay, processor activity, last send time, and time since last send.

        Returns:
            dict: Status information about the message queue for debugging and monitoring.
        """
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "message_delay": self._message_delay,
            "processor_task_active": self._processor_task is not None
            and not self._processor_task.done(),
            "last_send_time": self._last_send_time,
            "time_since_last_send": (
                time.time() - self._last_send_time if self._last_send_time > 0 else None
            ),
        }

    def ensure_processor_started(self):
        """
        Start the queue processor task if the queue is running and no processor task exists.

        This method checks if the queue is active and, if so, attempts to create and start the asynchronous processor task within the current event loop.
        """
        with self._lock:
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
        """
        Asynchronously processes messages from the queue, sending each in order while enforcing rate limiting and connection readiness.

        This method runs as a background task, monitoring the queue, waiting for the connection to be ready, and ensuring a minimum delay between sends. Messages are sent using their provided callable, and optional message mapping is handled after successful sends. The processor logs queue depth warnings, handles errors gracefully, and maintains FIFO order even when waiting for connection or rate limits.
        """
        logger.debug("Message queue processor started")
        current_message = None

        while self._running:
            try:
                # Get next message if we don't have one waiting
                if current_message is None:
                    # Monitor queue depth for operational awareness
                    queue_size = self._queue.qsize()
                    if queue_size > QUEUE_HIGH_WATER_MARK:
                        logger.warning(
                            f"Queue depth high: {queue_size} messages pending"
                        )
                    elif queue_size > QUEUE_MEDIUM_WATER_MARK:
                        logger.info(
                            f"Queue depth moderate: {queue_size} messages pending"
                        )

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
                    logger.debug(
                        f"Connection not ready, waiting to send: {current_message.description}"
                    )
                    await asyncio.sleep(1.0)
                    continue

                # Check if we need to wait for message delay (only if we've sent before)
                if self._last_send_time > 0:
                    time_since_last = time.time() - self._last_send_time
                    if time_since_last < self._message_delay:
                        wait_time = self._message_delay - time_since_last
                        logger.debug(
                            f"Rate limiting: waiting {wait_time:.1f}s before sending"
                        )
                        await asyncio.sleep(wait_time)
                        continue

                # Send the message
                try:
                    logger.debug(
                        f"Sending queued message: {current_message.description}"
                    )
                    # Run synchronous Meshtastic I/O operations in executor to prevent blocking event loop
                    # Use lambda with default arguments to properly capture loop variables
                    result = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda msg=current_message: msg.send_function(
                            *msg.args, **msg.kwargs
                        ),
                    )

                    # Update last send time
                    self._last_send_time = time.time()

                    if result is None:
                        logger.warning(
                            f"Message send returned None: {current_message.description}"
                        )
                    else:
                        logger.debug(
                            f"Successfully sent queued message: {current_message.description}"
                        )

                        # Handle message mapping if provided
                        if current_message.mapping_info and hasattr(result, "id"):
                            self._handle_message_mapping(
                                result, current_message.mapping_info
                            )

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
        Determine whether it is currently safe to send a message based on Meshtastic client connection and reconnection state.

        Returns:
            bool: True if the client is connected and not reconnecting; False otherwise.
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
            if hasattr(meshtastic_client, "is_connected"):
                is_conn = meshtastic_client.is_connected
                if not (is_conn() if callable(is_conn) else is_conn):
                    logger.debug("Not sending - client not connected")
                    return False

            logger.debug("Connection check passed - ready to send")
            return True

        except ImportError as e:
            # ImportError indicates a serious problem with application structure,
            # often during shutdown as modules are unloaded.
            logger.critical(
                f"Cannot import meshtastic_utils - serious application error: {e}. Stopping message queue."
            )
            self.stop()
            return False

    def _handle_message_mapping(self, result, mapping_info):
        """
        Update the message mapping database with information about a sent message and prune old mappings if configured.

        Parameters:
            result: The result object from the send function, expected to have an `id` attribute.
            mapping_info (dict): Contains mapping details such as `matrix_event_id`, `room_id`, `text`, and optionally `meshnet` and `msgs_to_keep`.

        If required mapping fields are present, stores the mapping and prunes old entries based on the specified or default retention count.
        """
        try:
            # Import here to avoid circular imports
            from mmrelay.db_utils import prune_message_map, store_message_map

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
                msgs_to_keep = mapping_info.get("msgs_to_keep", DEFAULT_MSGS_TO_KEEP)
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)

        except Exception as e:
            logger.error(f"Error handling message mapping: {e}")


# Global message queue instance
_message_queue = MessageQueue()


def get_message_queue() -> MessageQueue:
    """
    Return the global instance of the message queue used for managing and rate-limiting message sending.
    """
    return _message_queue


def start_message_queue(message_delay: float = DEFAULT_MESSAGE_DELAY):
    """
    Start the global message queue processor with the given minimum delay between messages.

    Parameters:
        message_delay (float): Minimum number of seconds to wait between sending messages.
    """
    _message_queue.start(message_delay)


def stop_message_queue():
    """
    Stops the global message queue processor, preventing further message processing until restarted.
    """
    _message_queue.stop()


def queue_message(
    send_function: Callable,
    *args,
    description: str = "",
    mapping_info: dict = None,
    **kwargs,
) -> bool:
    """
    Enqueues a message for sending via the global message queue.

    Parameters:
        send_function (Callable): The function to execute for sending the message.
        description (str, optional): Human-readable description of the message for logging purposes.
        mapping_info (dict, optional): Additional metadata for message mapping, such as reply or reaction information.

    Returns:
        bool: True if the message was successfully enqueued; False if the queue is not running or full.
    """
    return _message_queue.enqueue(
        send_function,
        *args,
        description=description,
        mapping_info=mapping_info,
        **kwargs,
    )


def get_queue_status() -> dict:
    """
    Return detailed status information about the global message queue.

    Returns:
        dict: A dictionary containing the running state, queue size, message delay, processor task activity, last send time, and time since last send.
    """
    return _message_queue.get_status()
