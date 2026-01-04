"""
Message queue system for MMRelay.

Provides transparent message queuing with rate limiting to prevent overwhelming
the Meshtastic network. Messages are queued in memory and sent at the configured
rate, respecting connection state and firmware constraints.
"""

import asyncio
import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from queue import Empty, Full, Queue
from typing import Any, Callable, Optional, cast

from mmrelay.constants.database import DEFAULT_MSGS_TO_KEEP
from mmrelay.constants.network import MINIMUM_MESSAGE_DELAY, RECOMMENDED_MINIMUM_DELAY
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
    send_function: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    description: str
    # Optional message mapping information for replies/reactions
    mapping_info: Optional[dict[str, Any]] = None


class MessageQueue:
    """
    Simple FIFO message queue with rate limiting for Meshtastic messages.

    Queues messages in memory and sends them in order at the configured rate to prevent
    overwhelming the mesh network. Respects connection state and automatically
    pauses during reconnections.
    """

    def __init__(self) -> None:
        """
        Initialize the MessageQueue's internal structures and default runtime state.

        Sets up the bounded FIFO queue, timing/state variables for rate limiting and delivery tracking, a thread lock for state transitions, and counters/placeholders for the processor task and executor.
        """
        self._queue: Queue[QueuedMessage] = Queue(maxsize=MAX_QUEUE_SIZE)
        self._processor_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._lock = threading.Lock()
        self._last_send_time = 0.0
        self._last_send_mono = 0.0
        self._message_delay = DEFAULT_MESSAGE_DELAY
        self._executor: Optional[ThreadPoolExecutor] = (
            None  # Dedicated ThreadPoolExecutor for this MessageQueue
        )
        self._in_flight = False
        self._has_current = False
        self._dropped_messages = 0

    def start(self, message_delay: float = DEFAULT_MESSAGE_DELAY) -> None:
        """
        Activate the message queue and configure the inter-message send delay.
        
        When started, the queue accepts enqueued messages for processing and will attempt to schedule its background processor on the current asyncio event loop if available. If `message_delay` is less than or equal to the firmware minimum, a warning is logged.
        
        Parameters:
            message_delay (float): Desired delay between consecutive sends in seconds; may trigger a warning if less than or equal to the firmware minimum.
        """
        with self._lock:
            if self._running:
                return

            # Set the message delay as requested
            self._message_delay = message_delay

            # Log warning if delay is at or below MINIMUM_MESSAGE_DELAY seconds due to firmware rate limiting
            if message_delay <= MINIMUM_MESSAGE_DELAY:
                logger.warning(
                    f"Message delay {message_delay}s is at or below {MINIMUM_MESSAGE_DELAY}s. "
                    f"Due to rate limiting in the Meshtastic Firmware, {RECOMMENDED_MINIMUM_DELAY}s or higher is recommended. "
                    f"Messages may be dropped by the firmware if sent too frequently."
                )

            self._running = True

            # Create dedicated executor for this MessageQueue
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix=f"MessageQueue-{id(self)}"
                )

            # Start the processor in the event loop
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
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

    def stop(self) -> None:
        """
        Stop the message queue processor and release its resources.
        
        Cancels the background processor task and, when possible, waits briefly for it to finish on its owning event loop; shuts down the dedicated ThreadPoolExecutor (using a background thread if called from an asyncio event loop) and clears internal state so the queue can be restarted. Thread-safe; this call may wait briefly for shutdown to complete but avoids blocking the current asyncio event loop.
        """
        with self._lock:
            if not self._running:
                return

            self._running = False

            if self._processor_task:
                self._processor_task.cancel()

                # Wait for the task to complete on its owning loop
                task_loop = self._processor_task.get_loop()
                current_loop = None
                with contextlib.suppress(RuntimeError):
                    current_loop = asyncio.get_running_loop()
                if task_loop.is_closed():
                    # Owning loop is closed; nothing we can do to await it
                    pass
                elif current_loop is task_loop:
                    # Avoid blocking the event loop thread; cancellation will finish naturally
                    pass
                elif task_loop.is_running():
                    from asyncio import run_coroutine_threadsafe, shield

                    with contextlib.suppress(Exception):
                        fut: Any = run_coroutine_threadsafe(
                            cast(Any, shield(self._processor_task)), task_loop
                        )
                        # Wait for completion; ignore exceptions raised due to cancellation
                        fut.result(timeout=1.0)
                else:
                    with contextlib.suppress(
                        asyncio.CancelledError, RuntimeError, Exception
                    ):
                        task_loop.run_until_complete(self._processor_task)

                self._processor_task = None

            # Shut down our dedicated executor without blocking the event loop
            if self._executor:
                on_loop_thread = False
                with contextlib.suppress(RuntimeError):
                    loop_chk = asyncio.get_running_loop()
                    on_loop_thread = loop_chk.is_running()

                def _shutdown(exec_ref: ThreadPoolExecutor) -> None:
                    """
                    Shut down an executor, waiting for running tasks to finish; falls back for executors that don't support `cancel_futures`.

                    Attempts to call executor.shutdown(wait=True, cancel_futures=True) and, if that raises a TypeError (older Python versions or executors without the `cancel_futures` parameter), retries with executor.shutdown(wait=True). This call blocks until shutdown completes.
                    """
                    try:
                        exec_ref.shutdown(wait=True, cancel_futures=True)
                    except TypeError:
                        exec_ref.shutdown(wait=True)

                if on_loop_thread:
                    threading.Thread(
                        target=_shutdown,
                        args=(self._executor,),
                        name="MessageQueueExecutorShutdown",
                        daemon=True,
                    ).start()
                else:
                    _shutdown(self._executor)
                self._executor = None

            logger.info("Message queue stopped")

    def enqueue(
        self,
        send_function: Callable[..., Any],
        *args: Any,
        description: str = "",
        mapping_info: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> bool:
        """
        Add a send operation to the queue for ordered, rate-limited delivery.

        Parameters:
            description (str): Human-readable description used for logging.
            mapping_info (dict[str, Any] | None): Optional metadata to correlate the sent message with an external event (e.g., Matrix IDs); stored after a successful send.

        Returns:
            bool: `true` if the message was successfully enqueued, `false` if the queue is not running or is full.
        """
        # Ensure processor is started if event loop is now available.
        # This is called outside the lock to prevent potential deadlocks.
        self.ensure_processor_started()

        with self._lock:
            if not self._running:
                # Refuse to send to prevent blocking the event loop
                logger.error(
                    "Queue not running; cannot send message: %s. Start the message queue before sending.",
                    description,
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
            # Enforce capacity via bounded queue
            try:
                self._queue.put_nowait(message)
            except Full:
                logger.warning(
                    f"Message queue full ({self._queue.qsize()}/{MAX_QUEUE_SIZE}), dropping message: {description}"
                )
                self._dropped_messages += 1
                return False
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
        Report whether the message queue processor is active.

        Returns:
            True if the processor is running, False otherwise.
        """
        return self._running

    def get_status(self) -> dict[str, Any]:
        """
        Get a snapshot of the message queue's runtime status for monitoring and debugging.

        Returns:
            dict: Mapping with the following keys:
                - running (bool): `True` if the queue processor is active, `False` otherwise.
                - queue_size (int): Number of messages currently queued.
                - message_delay (float): Configured minimum delay in seconds between sends.
                - processor_task_active (bool): `True` if the internal processor task exists and is not finished, `False` otherwise.
                - last_send_time (float or None): Wall-clock time (seconds since the epoch) of the last successful send, or `None` if no send has occurred.
                - time_since_last_send (float or None): Seconds elapsed since the last send, or `None` if no send has occurred.
                - in_flight (bool): `True` when a message is currently being sent, `False` otherwise.
                - dropped_messages (int): Number of messages dropped due to the queue being full.
                - default_msgs_to_keep (int): Default retention count for persisted message mappings.
        """
        return {
            "running": self._running,
            "queue_size": self._queue.qsize(),
            "message_delay": self._message_delay,
            "processor_task_active": self._processor_task is not None
            and not self._processor_task.done(),
            "last_send_time": self._last_send_time,
            "time_since_last_send": (
                time.monotonic() - self._last_send_mono
                if self._last_send_mono > 0
                else None
            ),
            "in_flight": self._in_flight,
            "dropped_messages": getattr(self, "_dropped_messages", 0),
            "default_msgs_to_keep": DEFAULT_MSGS_TO_KEEP,
        }

    async def drain(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until the message queue is empty and no message is in flight, or until an optional timeout elapses.
        
        Parameters:
            timeout (Optional[float]): Maximum time to wait in seconds; if None, wait indefinitely.
        
        Returns:
            `True` if the queue drained before being stopped and before the timeout, `False` if the queue was stopped before draining or the timeout was reached.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        while (not self._queue.empty()) or self._in_flight or self._has_current:
            if not self._running:
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.1)
        return True

    def ensure_processor_started(self) -> None:
        """
        Start the background message processor if the queue is running and no processor is active.
        
        Has no effect if the processor is already running or the queue is not active.
        """
        with self._lock:
            if self._running and self._processor_task is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    self._processor_task = loop.create_task(self._process_queue())
                    logger.info(
                        f"Message queue processor started with {self._message_delay}s message delay"
                    )

    async def _process_queue(self) -> None:
        """
        Process queued messages in FIFO order, sending each when the connection is ready and the configured inter-message delay has elapsed.

        Runs until the queue is stopped or the task is cancelled. After a successful send, updates last-send timestamps and, when provided mapping information is present and the send result exposes an `id`, persists the message mapping. Cancellation may drop an in-flight message.
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
                        self._has_current = True
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
                if self._last_send_mono > 0:
                    time_since_last = time.monotonic() - self._last_send_mono
                    if time_since_last < self._message_delay:
                        wait_time = self._message_delay - time_since_last
                        logger.debug(
                            f"Rate limiting: waiting {wait_time:.1f}s before sending"
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    elif time_since_last < MINIMUM_MESSAGE_DELAY:
                        # Warn when messages are sent less than MINIMUM_MESSAGE_DELAY seconds apart
                        logger.warning(
                            f"[Runtime] Messages sent {time_since_last:.1f}s apart, which is below {MINIMUM_MESSAGE_DELAY}s. "
                            f"Due to rate limiting in the Meshtastic Firmware, messages may be dropped."
                        )

                # Send the message
                try:
                    self._in_flight = True
                    logger.debug(
                        f"Sending queued message: {current_message.description}"
                    )
                    # Run synchronous Meshtastic I/O operations in executor to prevent blocking event loop
                    loop = asyncio.get_running_loop()
                    exec_ref = self._executor
                    if exec_ref is None:
                        raise RuntimeError("MessageQueue executor is not initialized")
                    result = await loop.run_in_executor(
                        exec_ref,
                        partial(
                            current_message.send_function,
                            *current_message.args,
                            **current_message.kwargs,
                        ),
                    )

                    # Update last send time
                    self._last_send_time = time.time()
                    self._last_send_mono = time.monotonic()

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
                            await self._handle_message_mapping(
                                result, current_message.mapping_info
                            )

                except Exception as e:
                    logger.error(
                        f"Error sending queued message '{current_message.description}': {e}"
                    )

                # Mark task as done and clear current message
                self._queue.task_done()
                current_message = None
                self._in_flight = False
                self._has_current = False

            except asyncio.CancelledError:
                logger.debug("Message queue processor cancelled")
                if current_message:
                    logger.warning(
                        f"Message in flight was dropped during shutdown: {current_message.description}"
                    )
                    with contextlib.suppress(Exception):
                        self._queue.task_done()
                self._in_flight = False
                self._has_current = False
                break
            except Exception:
                logger.exception("Error in message queue processor")
                await asyncio.sleep(1.0)  # Prevent tight error loop

    def _should_send_message(self) -> bool:
        """
        Determine whether the queue may send a Meshtastic message.
        
        Performs runtime checks: ensures the global reconnecting flag is false, a Meshtastic client object exists, and—if the client exposes a connectivity indicator—that indicator reports connected. If importing Meshtastic utilities fails, triggers an asynchronous stop of the queue.
        
        Returns:
            `True` if not reconnecting, a Meshtastic client exists, and the client is connected when checkable; `False` otherwise.
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
            # Stop asynchronously to avoid blocking the event loop thread.
            threading.Thread(
                target=self.stop, name="MessageQueueStopper", daemon=True
            ).start()
            return False

    async def _handle_message_mapping(
        self, result: Any, mapping_info: dict[str, Any]
    ) -> None:
        """
        Persist a mapping from a sent Meshtastic message to a Matrix event and optionally prune old mappings.

        Stores the Meshtastic message id taken from `result.id` alongside `matrix_event_id`, `room_id`, `text`, and optional `meshnet` from `mapping_info`. If `mapping_info` contains `msgs_to_keep` greater than zero, prunes older mappings to retain that many entries; otherwise uses DEFAULT_MSGS_TO_KEEP.

        Parameters:
            result: Object returned by the send function; must have an `id` attribute containing the Meshtastic message id.
            mapping_info (dict[str, Any]): Mapping details. Relevant keys:
                - matrix_event_id (str): Matrix event ID to map to.
                - room_id (str): Matrix room ID where the event was sent.
                - text (str): Message text to associate with the mapping.
                - meshnet (optional): Mesh network identifier.
                - msgs_to_keep (optional, int): Number of mappings to retain when pruning; if absent, DEFAULT_MSGS_TO_KEEP is used.
        """
        try:
            # Import here to avoid circular imports
            from mmrelay.db_utils import (
                async_prune_message_map,
                async_store_message_map,
            )

            # Extract mapping information
            matrix_event_id = mapping_info.get("matrix_event_id")
            room_id = mapping_info.get("room_id")
            text = mapping_info.get("text")
            meshnet = mapping_info.get("meshnet")

            if matrix_event_id and room_id and text:
                # Store the message mapping
                await async_store_message_map(
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
                    await async_prune_message_map(msgs_to_keep)

        except Exception:
            logger.exception("Error handling message mapping")


# Global message queue instance
_message_queue = MessageQueue()


def get_message_queue() -> MessageQueue:
    """
    Return the global MessageQueue instance used for rate-limited sending of Meshtastic messages.
    
    Returns:
        message_queue (MessageQueue): The module-level MessageQueue instance.
    """
    return _message_queue


def start_message_queue(message_delay: float = DEFAULT_MESSAGE_DELAY) -> None:
    """
    Start the global message queue processor.

    Parameters:
        message_delay (float): Minimum seconds to wait between consecutive message sends.
    """
    _message_queue.start(message_delay)


def stop_message_queue() -> None:
    """
    Stops the global message queue processor, preventing further message processing until restarted.
    """
    _message_queue.stop()


def queue_message(
    send_function: Callable[..., Any],
    *args: Any,
    description: str = "",
    mapping_info: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> bool:
    """
    Enqueues a message for sending via the global message queue.
    
    Parameters:
        send_function: Callable to execute to perform the send; will be invoked with the provided args and kwargs.
        description: Human-readable description used for logging.
        mapping_info: Optional metadata used to persist or associate the sent message with external identifiers (for example, a Matrix event id and room id).
    
    Returns:
        `True` if the message was successfully enqueued, `False` otherwise.
    """
    return _message_queue.enqueue(
        send_function,
        *args,
        description=description,
        mapping_info=mapping_info,
        **kwargs,
    )


def get_queue_status() -> dict[str, Any]:
    """
    Get a snapshot of the global message queue's current status.

    Returns:
        status (dict): Dictionary containing status fields including:
            - running: whether the processor is active
            - queue_size: current number of queued messages
            - message_delay: configured inter-message delay (seconds)
            - processor_task_active: whether the processor task exists and is not done
            - last_send_time: wall-clock timestamp of the last successful send or None
            - time_since_last_send: seconds since last send (monotonic) or None
            - in_flight: whether a send is currently executing
            - dropped_messages: count of messages dropped due to a full queue
            - default_msgs_to_keep: configured number of message mappings to retain
    """
    return _message_queue.get_status()