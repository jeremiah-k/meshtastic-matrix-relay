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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Optional

from mmrelay.constants.database import DEFAULT_MSGS_TO_KEEP
from mmrelay.constants.network import MINIMUM_MESSAGE_DELAY, RECOMMENDED_MINIMUM_DELAY
from mmrelay.constants.queue import (
    CONNECTION_ERROR_KEYWORDS,
    CONNECTION_RETRY_SLEEP_SEC,
    DEFAULT_MESSAGE_DELAY,
    MAX_QUEUE_SIZE,
    QUEUE_EXECUTOR_MAX_WORKERS,
    QUEUE_FULL_LOG_INTERVAL_SEC,
    QUEUE_HIGH_WATER_MARK,
    QUEUE_LOG_THRESHOLD,
    QUEUE_MEDIUM_WATER_MARK,
    QUEUE_POLL_INTERVAL_SEC,
    QUEUE_WAIT_RETRY_SLEEP_SEC,
    TASK_SHUTDOWN_TIMEOUT_SEC,
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

        Sets up the unbounded FIFO queue with explicit size checks, timing/state variables for rate limiting and delivery tracking, a thread lock for state transitions, and counters/placeholders for the processor task and executor.

        Note: The queue is intentionally unbounded (no maxlen) to ensure all message drops are
        explicitly logged. Size enforcement is handled in enqueue() with proper logging when
        messages are dropped, rather than silent eviction by deque's maxlen.
        """
        self._queue: deque[QueuedMessage] = deque()  # Explicit size checks in enqueue()
        self._processor_task: Optional[asyncio.Task[None]] = None
        # Lifecycle invariants:
        # - _running=True means enqueue is allowed and the processor can run.
        # - _stopping=True blocks enqueue/start while stop cleanup is in progress.
        # - _stop_failed=True latches only after stop timeout; start remains blocked
        #   until cleanup fully completes and state is cleared.
        self._running = False
        self._stopping = False
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
        self._last_queue_full_log_time: float | None = None
        self._stop_failed = False

    def _clear_failed_stop_state_if_recovered_locked(self) -> bool:
        """
        Clear failed-stop state once task/executor resources are confirmed inactive.

        Returns:
            bool: True if failed-stop state was cleared, False otherwise.
        """
        if not self._stop_failed:
            return False
        task_active = (
            self._processor_task is not None and not self._processor_task.done()
        )
        executor_active = self._executor is not None
        if self._running or task_active or executor_active:
            return False
        if self._processor_task is not None and self._processor_task.done():
            self._processor_task = None
        self._stopping = False
        self._stop_failed = False
        logger.warning(
            "Message queue failed-stop state cleared automatically after cleanup completed."
        )
        return True

    def start(self, message_delay: float = DEFAULT_MESSAGE_DELAY) -> bool:
        """
        Activate the message queue and configure the inter-message send delay.

        When started, the queue accepts enqueued messages for processing and will attempt to schedule its background processor on the current asyncio event loop if available. If `message_delay` is less than or equal to the firmware minimum, a warning is logged.

        Parameters:
            message_delay (float): Desired delay between consecutive sends in seconds; may trigger a warning if less than or equal to the firmware minimum.

        Returns:
            bool: True when the queue is running or successfully started, False when startup is blocked (for example, while failed-stop cleanup is still in progress).
        """
        with self._lock:
            self._clear_failed_stop_state_if_recovered_locked()
            if self._stop_failed:
                logger.error(
                    "Message queue cannot start: previous stop timed out and cleanup has not completed yet."
                )
                return False
            if self._running:
                return True
            if self._stopping:
                return False

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
                    max_workers=QUEUE_EXECUTOR_MAX_WORKERS,
                    thread_name_prefix=f"MessageQueue-{id(self)}",
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
            return True

    def stop(self) -> None:
        """
        Stop the message queue processor and release its resources.

        Cancels the background processor task and, when possible, waits briefly for it to finish on its owning event loop; shuts down the dedicated ThreadPoolExecutor (using a background thread if called from an asyncio event loop) and clears internal state so the queue can be restarted. Thread-safe; this call may wait briefly for shutdown to complete but avoids blocking the current asyncio event loop.
        """
        task = None
        exec_ref = None
        task_cleanup_error: Exception | None = None
        with self._lock:
            if not self._running:
                return

            self._running = False
            self._stopping = True
            task = self._processor_task
            exec_ref = self._executor

        task_cleanup_complete = threading.Event()
        executor_cleanup_complete = threading.Event()
        if task is None:
            task_cleanup_complete.set()
        if exec_ref is None:
            executor_cleanup_complete.set()

        def _mark_stop_failed(reason: str) -> None:
            with self._lock:
                self._stop_failed = True
            logger.error(
                "Message queue stop timed out (%s). Queue remains in failed-stop state until cleanup completes.",
                reason,
            )

        def _finalize_stop_state() -> None:
            # Safe to call multiple times from different code paths; early return below ensures idempotency.
            if not (
                task_cleanup_complete.is_set() and executor_cleanup_complete.is_set()
            ):
                return
            with self._lock:
                if task is not None and self._processor_task is task:
                    self._processor_task = None
                    self._in_flight = False
                    self._has_current = False
                if exec_ref is not None and self._executor is exec_ref:
                    self._executor = None
                if self._stop_failed:
                    if not self._clear_failed_stop_state_if_recovered_locked():
                        logger.warning(
                            "Message queue resources finished cleaning up, but failed-stop state remains set."
                        )
                        return
                else:
                    self._stopping = False
                logger.info("Message queue stopped")

        if task is not None:
            task_loop = task.get_loop()
            current_loop = None
            with contextlib.suppress(RuntimeError):
                current_loop = asyncio.get_running_loop()

            def _make_cancel_handler(
                done_event: threading.Event,
                orig_task: asyncio.Task[None],
            ) -> Callable[[], None]:
                def _cancel_and_cleanup() -> None:
                    orig_task.cancel()
                    if orig_task.done():
                        done_event.set()
                        task_cleanup_complete.set()
                        _finalize_stop_state()
                        return

                    def _on_task_done(_finished_task: asyncio.Task[Any]) -> None:
                        done_event.set()
                        task_cleanup_complete.set()
                        _finalize_stop_state()

                    orig_task.add_done_callback(_on_task_done)

                return _cancel_and_cleanup

            if task_loop.is_closed():
                task_cleanup_complete.set()
                _finalize_stop_state()
            elif current_loop is task_loop:
                # Avoid blocking the owning event loop thread; schedule cancellation
                # inline and let the task done-callback perform cleanup.
                task_done = threading.Event()
                cancel_handler = _make_cancel_handler(task_done, task)
                cancel_scheduled = True
                try:
                    task_loop.call_soon(cancel_handler)
                except RuntimeError:
                    # Loop closed between the state check and scheduling.
                    cancel_scheduled = False
                    task_done.set()
                    task_cleanup_complete.set()
                    _finalize_stop_state()

                if cancel_scheduled:

                    def _watchdog_cleanup() -> None:
                        if not task_done.wait(timeout=TASK_SHUTDOWN_TIMEOUT_SEC):
                            _mark_stop_failed("task cleanup")

                    threading.Thread(
                        target=_watchdog_cleanup,
                        name="MessageQueueWatchdog",
                        daemon=True,
                    ).start()
            elif task_loop.is_running():
                task_done = threading.Event()
                cancel_handler = _make_cancel_handler(task_done, task)
                cancel_scheduled = True
                try:
                    task_loop.call_soon_threadsafe(cancel_handler)
                except RuntimeError:
                    # Loop closed between the state check and scheduling.
                    cancel_scheduled = False
                    task_done.set()
                    task_cleanup_complete.set()
                    _finalize_stop_state()
                if cancel_scheduled:
                    if current_loop is None:
                        task_done.wait(timeout=TASK_SHUTDOWN_TIMEOUT_SEC)
                        if not task_done.is_set():
                            _mark_stop_failed("task cleanup")
                    else:

                        def _watchdog_cleanup() -> None:
                            if not task_done.wait(timeout=TASK_SHUTDOWN_TIMEOUT_SEC):
                                _mark_stop_failed("task cleanup")

                        threading.Thread(
                            target=_watchdog_cleanup,
                            name="MessageQueueWatchdog",
                            daemon=True,
                        ).start()
            else:
                task.cancel()
                try:
                    task_loop.run_until_complete(task)
                except (asyncio.CancelledError, RuntimeError):
                    pass
                except Exception as exc:
                    logger.exception("Unexpected exception during task cleanup")
                    task_cleanup_error = exc
                task_cleanup_complete.set()
                _finalize_stop_state()

        if exec_ref is not None:
            on_loop_thread = False
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop()
                on_loop_thread = True

            def _shutdown(exec_ref: ThreadPoolExecutor) -> None:
                """Shut down executor, cancelling pending futures."""
                exec_ref.shutdown(wait=True, cancel_futures=True)

            executor_done = threading.Event()

            def _shutdown_and_finalize(exec_obj: ThreadPoolExecutor) -> None:
                try:
                    _shutdown(exec_obj)
                finally:
                    executor_done.set()
                    executor_cleanup_complete.set()
                    _finalize_stop_state()

            threading.Thread(
                target=_shutdown_and_finalize,
                args=(exec_ref,),
                name="MessageQueueExecutorShutdown",
                daemon=True,
            ).start()

            def _watch_executor_shutdown() -> None:
                if not executor_done.wait(timeout=TASK_SHUTDOWN_TIMEOUT_SEC):
                    _mark_stop_failed("executor cleanup")

            if on_loop_thread:
                threading.Thread(
                    target=_watch_executor_shutdown,
                    name="MessageQueueExecutorWatchdog",
                    daemon=True,
                ).start()
            else:
                # Blocking wait is acceptable for synchronous callers
                _watch_executor_shutdown()

        _finalize_stop_state()
        if task_cleanup_error is not None:
            raise task_cleanup_error

    def enqueue(
        self,
        send_function: Callable[..., Any],
        *args: Any,
        description: str = "",
        mapping_info: Optional[dict[str, Any]] = None,
        wait: bool = False,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> bool:
        """
        Enqueues a send operation for ordered, rate-limited delivery.

        Parameters:
            description: Human-readable description used for logging.
            mapping_info: Optional metadata to correlate the sent message with an external event (e.g., Matrix IDs); stored after a successful send.
            wait: If True, wait for queue space to become available instead of immediately dropping. Defaults to False.
            timeout: Maximum time in seconds to wait for queue space when `wait` is True; `None` means wait indefinitely.

        Returns:
            `true` if the message was successfully enqueued, `false` otherwise.
        """
        # Ensure processor is started if event loop is now available.
        # This is called outside the lock to prevent potential deadlocks.
        self.ensure_processor_started()

        with self._lock:
            self._clear_failed_stop_state_if_recovered_locked()
            if self._stop_failed:
                logger.error(
                    "Queue is in failed-stop state; cleanup is still in progress."
                )
                return False
            if not self._running or self._stopping:
                # Refuse to send to prevent blocking the event loop
                logger.error(
                    "Queue not running or is stopping; cannot send message: %s. Start the message queue before sending.",
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

            # Try to enqueue the message
            start_time = time.monotonic()
            while True:
                queue_size = len(self._queue)
                if queue_size >= MAX_QUEUE_SIZE:
                    if wait:
                        # Check if we've exceeded timeout
                        if timeout is not None:
                            elapsed = time.monotonic() - start_time
                            if elapsed >= timeout:
                                logger.error(
                                    f"Message queue full ({queue_size}/{MAX_QUEUE_SIZE}) "
                                    f"and wait timed out after {timeout}s, dropping message: {description}"
                                )
                                self._dropped_messages += 1
                                return False

                        # Log queue full warning periodically (every 5 seconds)
                        current_time = time.monotonic()
                        if (
                            self._last_queue_full_log_time is None
                            or current_time - self._last_queue_full_log_time
                            >= QUEUE_FULL_LOG_INTERVAL_SEC
                        ):
                            logger.warning(
                                f"Message queue full ({queue_size}/{MAX_QUEUE_SIZE}), "
                                f"waiting for space: {description}"
                            )
                            self._last_queue_full_log_time = current_time

                        # Release lock and wait a bit before retrying
                        # Use try/finally to ensure lock is always reacquired
                        self._lock.release()
                        try:
                            time.sleep(QUEUE_WAIT_RETRY_SLEEP_SEC)
                        finally:
                            self._lock.acquire()

                        # Re-check queue is still running
                        if not self._running or self._stopping:
                            logger.error(
                                "Queue stopped while waiting; dropping message: %s",
                                description,
                            )
                            return False
                        continue
                    else:
                        # Not waiting - immediately drop the message
                        logger.error(
                            f"Message queue full ({queue_size}/{MAX_QUEUE_SIZE}), "
                            f"dropping message: {description}. "
                            f"Consider increasing queue size or reducing message rate."
                        )
                        self._dropped_messages += 1
                        return False

                # Queue has space, append the message
                self._queue.append(message)
                # Reset the queue full log time since we successfully enqueued
                self._last_queue_full_log_time = None
                break

            # Only log queue status when there are multiple messages
            queue_size = len(self._queue)
            if queue_size >= QUEUE_LOG_THRESHOLD:
                logger.debug(
                    f"Queued message ({queue_size}/{MAX_QUEUE_SIZE}): {description}"
                )
            return True

    def get_queue_size(self) -> int:
        """
        Get the current number of messages queued.

        Returns:
            int: Number of messages in the queue.
        """
        return len(self._queue)

    def _requeue_message(self, message: QueuedMessage) -> bool:
        """
        Requeue a message at the front of the queue to maintain FIFO order.

        This is used when a message was dequeued but couldn't be sent due to
        connection issues. The message is put back at the front so it will be
        the next one processed when connection is restored.

        Parameters:
            message: The QueuedMessage to requeue.

        Returns:
            bool: True if successfully requeued, False if queue is full.
        """
        with self._lock:
            # Check if queue is full - use O(1) appendleft for prepend
            if len(self._queue) >= MAX_QUEUE_SIZE:
                logger.error(
                    f"Cannot requeue message - queue full: {message.description}"
                )
                self._dropped_messages += 1
                return False
            # O(1) prepend using deque's appendleft
            self._queue.appendleft(message)
            return True

    def is_running(self) -> bool:
        """
        Indicates whether the message queue is active.

        Returns:
            `true` if the queue is running, `false` otherwise.
        """
        return self._running

    def reset_failed_stop_state(self) -> bool:
        """
        Clear failed-stop state once old queue resources have fully exited.

        Returns:
            bool: True if the queue can be safely restarted; False when cleanup is still active.
        """
        with self._lock:
            if self._stopping:
                logger.error(
                    "Cannot reset failed-stop state while queue shutdown is in progress"
                )
                return False
            if not self._stop_failed:
                return True
            if self._clear_failed_stop_state_if_recovered_locked():
                return True
            logger.error(
                "Cannot reset failed-stop state while queue resources are still active"
            )
            return False

    def get_status(self) -> dict[str, Any]:
        """
        Get a snapshot of the message queue's runtime status for monitoring and debugging.

        Returns:
            dict: Mapping with the following keys:
                - running (bool): `True` if the queue processor is active, `False` otherwise.
                - queue_size (int): Number of messages currently queued.
                - message_delay (float): Configured minimum delay in seconds between sends.
                - stop_failed (bool): `True` if a previous stop timed out and cleanup has not yet completed, `False` otherwise.
                - processor_task_active (bool): `True` if the internal processor task exists and is not finished, `False` otherwise.
                - last_send_time (float or None): Wall-clock time (seconds since the epoch) of the last successful send, or `None` if no send has occurred.
                - time_since_last_send (float or None): Seconds elapsed since the last send, or `None` if no send has occurred.
                - in_flight (bool): `True` when a message is currently being sent, `False` otherwise.
                - dropped_messages (int): Number of messages dropped due to the queue being full.
                - default_msgs_to_keep (int): Default retention count for persisted message mappings.
        """
        return {
            "running": self._running,
            "queue_size": len(self._queue),
            "message_delay": self._message_delay,
            "stop_failed": self._stop_failed,
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
        while self._queue or self._in_flight or self._has_current:
            if not self._running:
                return False
            if deadline is not None and time.monotonic() > deadline:
                return False
            await asyncio.sleep(QUEUE_POLL_INTERVAL_SEC)
        return True

    def ensure_processor_started(self) -> None:
        """
        Start the background message processor if the queue is running and no processor is active.

        Has no effect if the processor is already running or the queue is not active.
        """
        with self._lock:
            task_inactive = self._processor_task is None or self._processor_task.done()
            if task_inactive and self._processor_task is not None:
                self._processor_task = None

            if self._running and not self._stop_failed and task_inactive:
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
        Process queued messages and send them while respecting connection state and the configured inter-message delay.

        Runs until the queue is stopped or the task is cancelled. On a successful send, updates the queue's last-send timestamps; if a message includes mapping information and the send result exposes an `id`, persists that mapping. Maintains FIFO ordering, requeues messages on transient connection-related failures for later retry, and preserves rate-limiting checks between sends. Cancellation may drop an in-flight message.
        """
        logger.debug("Message queue processor started")
        current_message = None

        while self._running:
            try:
                # Get next message if we don't have one waiting
                if current_message is None:
                    # Monitor queue depth for operational awareness
                    queue_size = len(self._queue)
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
                        current_message = self._queue.popleft()
                        self._has_current = True
                    except IndexError:
                        # No messages, wait a bit and continue
                        await asyncio.sleep(QUEUE_POLL_INTERVAL_SEC)
                        continue

                # Check if we should send (connection state, etc.)
                if not self._should_send_message():
                    # Keep the message and wait - don't requeue to maintain FIFO order
                    logger.debug(
                        f"Connection not ready, waiting to send: {current_message.description}"
                    )
                    await asyncio.sleep(CONNECTION_RETRY_SLEEP_SEC)
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
                        # CRITICAL: Re-check connection state after rate limiting sleep
                        # to avoid race condition where connection drops during the wait
                        if not self._should_send_message():
                            logger.debug(
                                f"Connection dropped during rate limiting, requeueing: {current_message.description}"
                            )
                            # Requeue at front to maintain FIFO order
                            self._requeue_message(current_message)
                            current_message = None
                            self._has_current = False
                            await asyncio.sleep(CONNECTION_RETRY_SLEEP_SEC)
                            continue
                        # After successful wait, continue to send
                    elif time_since_last < MINIMUM_MESSAGE_DELAY:
                        # Warn when messages are sent less than MINIMUM_MESSAGE_DELAY seconds apart
                        logger.warning(
                            f"Messages sent {time_since_last:.1f}s apart, which is below {MINIMUM_MESSAGE_DELAY}s. "
                            f"Due to rate limiting in the Meshtastic Firmware, messages may be dropped."
                        )
                elif self._message_delay < MINIMUM_MESSAGE_DELAY:
                    # Warn on first send if configured delay is below MINIMUM_MESSAGE_DELAY
                    logger.warning(
                        f"Messages are being sent with {self._message_delay}s delay, "
                        f"which is below {MINIMUM_MESSAGE_DELAY}s. "
                        f"Due to rate limiting in the Meshtastic Firmware, messages may be dropped."
                    )

                # Final connection check right before sending to catch race conditions
                if not self._should_send_message():
                    logger.debug(
                        f"Connection not ready before send, requeueing: {current_message.description}"
                    )
                    self._requeue_message(current_message)
                    current_message = None
                    self._has_current = False
                    await asyncio.sleep(CONNECTION_RETRY_SLEEP_SEC)
                    continue

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
                        if current_message.mapping_info:
                            # Robust ID extraction with detailed logging
                            msg_id = None
                            if hasattr(result, "id"):
                                msg_id = result.id
                            elif isinstance(result, dict) and "id" in result:
                                msg_id = result["id"]

                            if msg_id is not None:
                                # Create normalized result object for mapping handler
                                from types import SimpleNamespace

                                normalized_result = SimpleNamespace(id=msg_id)
                                await self._handle_message_mapping(
                                    normalized_result, current_message.mapping_info
                                )
                            else:
                                # Critical: Log detailed error when mapping cannot be stored
                                logger.error(
                                    f"Cannot store message mapping: send result lacks 'id' attribute. "
                                    f"Result type: {type(result).__name__}. "
                                    f"Replies/reactions will not work for this message. "
                                    f"Message: {current_message.description}"
                                )

                except Exception as e:
                    # Check if this is a connection-related error that should trigger requeue
                    # First check exception types, then fall back to string matching
                    is_connection_error = isinstance(
                        e, (ConnectionError, OSError, TimeoutError)
                    )
                    if not is_connection_error:
                        error_msg = str(e).lower()
                        is_connection_error = any(
                            keyword in error_msg
                            for keyword in CONNECTION_ERROR_KEYWORDS
                        )

                    if is_connection_error:
                        logger.warning(
                            f"Connection error sending message '{current_message.description}': {e}. "
                            f"Requeueing to retry later."
                        )
                        # Requeue the message for retry
                        self._requeue_message(current_message)
                        current_message = None
                        self._has_current = False
                        self._in_flight = False
                        await asyncio.sleep(CONNECTION_RETRY_SLEEP_SEC)
                        continue
                    else:
                        logger.exception(
                            f"Error sending queued message '{current_message.description}'"
                        )

                # Clear current message
                current_message = None
                self._in_flight = False
                self._has_current = False

            except asyncio.CancelledError:
                logger.debug("Message queue processor cancelled")
                if current_message:
                    logger.warning(
                        f"Message in flight was dropped during shutdown: {current_message.description}"
                    )
                self._in_flight = False
                self._has_current = False
                break
            except Exception:
                logger.exception("Error in message queue processor")
                await asyncio.sleep(
                    CONNECTION_RETRY_SLEEP_SEC
                )  # Prevent tight error loop

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

        Stores the Meshtastic message id taken from `result.id` (normalized to string) alongside `matrix_event_id`, `room_id`, `text`, and optional `meshnet` from `mapping_info`. If `mapping_info` contains `msgs_to_keep` greater than zero, prunes older mappings to retain that many entries; otherwise uses DEFAULT_MSGS_TO_KEEP.

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
                # CRITICAL: Normalize result.id to string to match database TEXT column
                meshtastic_id = str(result.id)

                # Store the message mapping
                await async_store_message_map(
                    meshtastic_id,
                    matrix_event_id,
                    room_id,
                    text,
                    meshtastic_meshnet=meshnet,
                )
                logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")

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


def start_message_queue(message_delay: float = DEFAULT_MESSAGE_DELAY) -> bool:
    """
    Start the global message queue processor.

    Parameters:
        message_delay (float): Minimum seconds to wait between consecutive message sends.

    Returns:
        bool: True when the queue is running or successfully started, False when startup is blocked.
    """
    return _message_queue.start(message_delay)


def stop_message_queue() -> None:
    """
    Stops the global message queue processor, preventing further message processing until restarted.
    """
    _message_queue.stop()


def reset_message_queue_failed_state() -> bool:
    """
    Clear failed-stop state on the global queue after cleanup completion.

    Returns:
        bool: True if reset succeeded, False when resources are still active.
    """
    return _message_queue.reset_failed_stop_state()


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
