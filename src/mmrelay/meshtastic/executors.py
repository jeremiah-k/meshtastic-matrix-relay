import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable

import mmrelay.meshtastic_utils as facade

__all__ = [
    "_clear_ble_future",
    "_clear_metadata_future_if_current",
    "_ensure_ble_worker_available",
    "_get_ble_executor",
    "_get_metadata_executor",
    "_maybe_reset_ble_executor",
    "_record_ble_timeout",
    "_reset_metadata_executor_for_stale_probe",
    "_schedule_ble_future_cleanup",
    "_schedule_metadata_future_cleanup",
    "_shutdown_shared_executors",
    "_submit_metadata_probe",
    "reset_executor_degraded_state",
    "shutdown_shared_executors",
]


def _shutdown_shared_executors() -> None:
    """
    Shutdown shared executors on interpreter exit to avoid blocking.

    Attempts to cancel any pending futures and shutdown without waiting
    to prevent interpreter hangs when tasks are stuck.

    Note: This is called via atexit during interpreter shutdown. It performs
    cleanup without waiting to avoid blocking the interpreter exit sequence.
    """
    # Cancel any pending BLE operation
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    ble_future_to_cancel = None
    with facade._ble_executor_lock:
        if facade._ble_future and not facade._ble_future.done():
            facade.logger.debug(
                "Cancelling pending BLE future during executor shutdown"
            )
            ble_future_to_cancel = facade._ble_future
        facade._ble_future = None
        facade._ble_future_address = None
        facade._ble_future_started_at = None
        facade._ble_future_timeout_secs = None
        with facade._ble_timeout_lock:
            facade._ble_timeout_counts.clear()
            facade._ble_executor_orphaned_workers_by_address.clear()
        facade._ble_executor_degraded_addresses.clear()

        executor = facade._ble_executor
        facade._ble_executor = None
    if ble_future_to_cancel is not None:
        ble_future_to_cancel.cancel()
    if executor is not None and not getattr(executor, "_shutdown", False):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)

    # Cancel any pending metadata operation
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    metadata_future_to_cancel = None
    with facade._metadata_future_lock:
        if facade._metadata_future and not facade._metadata_future.done():
            facade.logger.debug(
                "Cancelling pending metadata future during executor shutdown"
            )
            metadata_future_to_cancel = facade._metadata_future
        facade._metadata_future = None
        facade._metadata_future_started_at = None
        facade._metadata_executor_orphaned_workers = 0
        facade._metadata_executor_degraded = False

        executor = facade._metadata_executor
        facade._metadata_executor = None
    with facade._health_probe_request_lock:
        facade._health_probe_request_deadlines.clear()
    if metadata_future_to_cancel is not None:
        metadata_future_to_cancel.cancel()
    if executor is not None and not getattr(executor, "_shutdown", False):
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            executor.shutdown(wait=False)


def shutdown_shared_executors() -> None:
    """
    Public wrapper for shutting down shared Meshtastic executors.

    This is primarily intended for test teardown and explicit cleanup paths.
    """
    facade._shutdown_shared_executors()


def reset_executor_degraded_state(
    ble_address: str | None = None, *, reset_all: bool = False
) -> bool:
    """
    Reset degraded state for executors, allowing recovery after reconnect.

    When executors reach the orphan threshold, they enter a degraded state
    and refuse new work submissions. This function clears that state so normal
    operation can resume after a successful reconnect or manual intervention.

    Note:
        When ble_address is provided (and reset_all is False), this function
        resets both the BLE executor degraded state AND the metadata executor
        degraded state. This connection-scoped behavior reflects that a successful
        BLE reconnect typically also restores the metadata probe path.

    Parameters:
        ble_address (str | None): Specific BLE address to reset. If None and
            reset_all is False, only metadata executor is reset.
        reset_all (bool): If True, reset all degraded state including all
            BLE addresses and metadata executor.

    Returns:
        bool: True if any degraded state was reset, False otherwise.
    """
    reset_any = False

    if reset_all:
        stale_ble_executor = None
        with facade._ble_executor_lock:
            if facade._ble_executor_degraded_addresses:
                facade.logger.info(
                    "Resetting degraded state for all BLE executors: %s",
                    ", ".join(sorted(facade._ble_executor_degraded_addresses)),
                )
                facade._ble_executor_degraded_addresses.clear()
                with facade._ble_timeout_lock:
                    facade._ble_executor_orphaned_workers_by_address.clear()
                if facade._ble_executor is not None:
                    stale_ble_executor = facade._ble_executor
                    facade._ble_executor = None
                reset_any = True
        if stale_ble_executor is not None:
            try:
                stale_ble_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_ble_executor.shutdown(wait=False)
        stale_metadata_executor = None
        with facade._metadata_future_lock:
            if facade._metadata_executor_degraded:
                facade.logger.info("Resetting degraded state for metadata executor")
                facade._metadata_executor_degraded = False
                facade._metadata_executor_orphaned_workers = 0
                if facade._metadata_executor is not None:
                    stale_metadata_executor = facade._metadata_executor
                    facade._metadata_executor = None
                reset_any = True
        if stale_metadata_executor is not None:
            try:
                stale_metadata_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_metadata_executor.shutdown(wait=False)
        return reset_any

    if ble_address is not None:
        stale_ble_executor = None
        with facade._ble_executor_lock:
            if ble_address in facade._ble_executor_degraded_addresses:
                facade.logger.info(
                    "Resetting degraded state for BLE executor: %s",
                    ble_address,
                )
                facade._ble_executor_degraded_addresses.discard(ble_address)
                with facade._ble_timeout_lock:
                    facade._ble_executor_orphaned_workers_by_address.pop(
                        ble_address, None
                    )
                if facade._ble_executor is not None:
                    stale_ble_executor = facade._ble_executor
                    facade._ble_executor = None
                reset_any = True
        if stale_ble_executor is not None:
            try:
                stale_ble_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_ble_executor.shutdown(wait=False)

    stale_metadata_executor = None
    with facade._metadata_future_lock:
        if facade._metadata_executor_degraded:
            facade.logger.info("Resetting degraded state for metadata executor")
            facade._metadata_executor_degraded = False
            facade._metadata_executor_orphaned_workers = 0
            if facade._metadata_executor is not None:
                stale_metadata_executor = facade._metadata_executor
                facade._metadata_executor = None
            reset_any = True
    if stale_metadata_executor is not None:
        try:
            stale_metadata_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_metadata_executor.shutdown(wait=False)

    return reset_any


def _get_ble_executor() -> ThreadPoolExecutor:
    """
    Get or create a BLE executor thread pool.

    Returns the shared BLE executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _ble_executor_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared BLE executor instance.
    """
    if facade._ble_executor is None or getattr(
        facade._ble_executor, "_shutdown", False
    ):
        facade._ble_executor = facade.ThreadPoolExecutor(max_workers=1)
    return facade._ble_executor


def _get_metadata_executor() -> ThreadPoolExecutor:
    """
    Get or create the metadata executor thread pool.

    Returns the shared metadata executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _metadata_future_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared metadata executor instance.
    """
    if facade._metadata_executor is None or getattr(
        facade._metadata_executor, "_shutdown", False
    ):
        facade._metadata_executor = facade.ThreadPoolExecutor(max_workers=1)
    return facade._metadata_executor


def _clear_metadata_future_if_current(done_future: Future[Any]) -> None:
    """
    Clear the shared metadata future if it still refers to `done_future`.
    """
    with facade._metadata_future_lock:
        if facade._metadata_future is done_future:
            facade._metadata_future = None
            facade._metadata_future_started_at = None


def _reset_metadata_executor_for_stale_probe() -> None:
    """
    Replace the shared metadata executor after stale probe detection.

    A wedged single-worker executor can block all later probe submissions.
    This function abandons the old executor and usually creates a fresh one so
    probe retries are not queued behind a stuck worker.

    When the number of orphaned workers reaches EXECUTOR_ORPHAN_THRESHOLD,
    the executor enters a degraded state: submission of new probes is refused
    and further automatic recovery is disabled. Recovery requires an explicit
    reconnect or process restart.
    """
    stale_executor: ThreadPoolExecutor | None = None
    orphaned_workers: int | None = None
    degraded_now = False

    with facade._metadata_future_lock:
        if facade._metadata_executor_degraded:
            facade.logger.debug(
                "Metadata executor is in degraded state; refusing to reset. "
                "Reconnect or restart required to recover."
            )
            return

        projected_orphans = facade._metadata_executor_orphaned_workers + 1
        if projected_orphans >= facade.EXECUTOR_ORPHAN_THRESHOLD:
            facade._metadata_executor_degraded = True
            facade._metadata_executor_orphaned_workers = projected_orphans
            facade.logger.error(
                "METADATA EXECUTOR DEGRADED: %s workers have been orphaned due to "
                "repeated hangs. Further automatic recovery is disabled. "
                "Reconnect or restart the relay to restore metadata probing.",
                projected_orphans,
            )
            facade._metadata_future = None
            facade._metadata_future_started_at = None
            stale_executor = facade._metadata_executor
            # Keep degraded mode fail-fast: stop automatic executor recreation.
            facade._metadata_executor = None
            degraded_now = True

        if not degraded_now:
            stale_executor = facade._metadata_executor
            facade._metadata_future = None
            facade._metadata_future_started_at = None
            facade._metadata_executor = facade.ThreadPoolExecutor(max_workers=1)
            if stale_executor is not None and not getattr(
                stale_executor, "_shutdown", False
            ):
                facade._metadata_executor_orphaned_workers = projected_orphans
                orphaned_workers = facade._metadata_executor_orphaned_workers

    if (
        not degraded_now
        and stale_executor is not None
        and not getattr(stale_executor, "_shutdown", False)
    ):
        facade.logger.warning(
            "Replacing stale metadata executor after probe timeout; "
            "orphaned metadata workers=%s (threshold=%s)",
            orphaned_workers,
            facade.EXECUTOR_ORPHAN_THRESHOLD,
        )
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)
    elif (
        degraded_now
        and stale_executor is not None
        and not getattr(stale_executor, "_shutdown", False)
    ):
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)


def _schedule_metadata_future_cleanup(
    future: Future[Any],
    reason: str,
) -> None:
    """
    Schedule delayed cleanup for a metadata probe future that appears stuck.

    If the future is still the active metadata future after
    `METADATA_WATCHDOG_SECS`, clear the shared reference so later health checks
    are not permanently suppressed by a stale in-flight marker.
    """

    def _cleanup() -> None:
        if future.done():
            return

        with facade._metadata_future_lock:
            should_clear = facade._metadata_future is future

        if not should_clear:
            return

        facade.logger.warning(
            "Metadata worker still running after %.0fs; clearing stale future (%s)",
            facade.METADATA_WATCHDOG_SECS,
            reason,
        )
        facade._reset_metadata_executor_for_stale_probe()

    try:
        timer = threading.Timer(facade.METADATA_WATCHDOG_SECS, _cleanup)
        timer.daemon = True
        future.add_done_callback(lambda _f: timer.cancel())
        timer.start()
    except Exception as exc:  # noqa: BLE001 - best-effort watchdog setup
        facade.logger.debug(
            "Failed to schedule metadata future cleanup watchdog",
            exc_info=exc,
        )


def _submit_metadata_probe(probe: Callable[[], Any]) -> Future[Any] | None:
    """
    Submit a metadata-related admin probe unless one is already in flight.

    Returns the shared concurrent future for the submitted probe, or `None`
    when another metadata operation is already running.
    """
    stale_detected = False
    with facade._metadata_future_lock:
        if facade._metadata_future is not None and not facade._metadata_future.done():
            if facade._metadata_future_started_at is None or (
                facade.time.monotonic() - facade._metadata_future_started_at
                < facade.METADATA_WATCHDOG_SECS
            ):
                return None
            stale_detected = True

    if stale_detected:
        facade.logger.warning(
            "Metadata worker still running after %.0fs; clearing stale future (%s)",
            facade.METADATA_WATCHDOG_SECS,
            "submit-retry",
        )
        facade._reset_metadata_executor_for_stale_probe()

    submission_error: RuntimeError | None = None
    with facade._metadata_future_lock:
        if facade._metadata_executor_degraded:
            facade.logger.error(
                "Metadata executor degraded: too many orphaned workers. "
                "Reconnect or restart required to restore metadata probing."
            )
            raise facade.MetadataExecutorDegradedError(
                "Metadata executor is degraded; reconnect or restart required"
            )
        if facade._metadata_future is not None and not facade._metadata_future.done():
            return None
        try:
            future = facade._get_metadata_executor().submit(probe)
        except RuntimeError as exc:
            submission_error = exc
            future = None
        if future is not None:
            facade._metadata_future = future
            facade._metadata_future_started_at = facade.time.monotonic()

    if submission_error is not None:
        facade.logger.debug(
            "Metadata probe submission failed; resetting metadata executor",
            exc_info=submission_error,
        )
        facade._reset_metadata_executor_for_stale_probe()
        raise submission_error

    if future is None:
        return None

    future.add_done_callback(facade._clear_metadata_future_if_current)
    facade._schedule_metadata_future_cleanup(future, reason="metadata-probe")
    return future


def _record_ble_timeout(ble_address: str) -> int:
    """
    Increment the recorded BLE timeout count for the given BLE address.

    This operation is thread-safe.

    Parameters:
        ble_address (str): BLE device address to record the timeout for.

    Returns:
        int: The updated timeout count for the specified BLE address (1 or greater).
    """
    with facade._ble_timeout_lock:
        facade._ble_timeout_counts[ble_address] = (
            facade._ble_timeout_counts.get(ble_address, 0) + 1
        )
        return facade._ble_timeout_counts[ble_address]


def _clear_ble_future(done_future: Future[Any]) -> None:
    """
    Release the module's active BLE future reference if it matches the completed future.

    If `done_future` is the currently tracked BLE executor future, clear the tracked
    future and its associated address; also remove the per-address timeout count.
    Parameters:
        done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and should be cleared if it matches the active BLE task.
    """
    with facade._ble_executor_lock:
        if facade._ble_future is done_future:
            facade._ble_future = None
            if facade._ble_future_address:
                with facade._ble_timeout_lock:
                    facade._ble_timeout_counts.pop(facade._ble_future_address, None)
            facade._ble_future_address = None
            facade._ble_future_started_at = None
            facade._ble_future_timeout_secs = None


def _schedule_ble_future_cleanup(
    future: Future[Any],
    ble_address: str,
    reason: str,
) -> None:
    """
    Schedule a delayed cleanup for a stuck BLE future.

    If a BLE task cannot be cancelled, we avoid blocking all future retries by
    clearing the shared future reference after a grace period.
    """
    watchdog_secs = facade._coerce_positive_float(
        facade._ble_future_watchdog_secs,
        facade.BLE_FUTURE_WATCHDOG_SECS,
        "_ble_future_watchdog_secs",
    )

    def _cleanup() -> None:
        """
        Clear a stale BLE worker future when it exceeds the watchdog timeout.

        If the provided future is still running and remains the active BLE future, logs a warning including the watchdog duration, BLE address, and reason, then attempts to reset the executor.
        """
        if future.done():
            return
        with facade._ble_executor_lock:
            if facade._ble_future is not future:
                return
        facade.logger.warning(
            "BLE worker still running after %.0fs for %s; resetting executor (%s)",
            watchdog_secs,
            ble_address,
            reason,
        )
        reset_threshold = facade._coerce_positive_int(
            facade._ble_timeout_reset_threshold,
            facade.BLE_TIMEOUT_RESET_THRESHOLD,
        )
        facade._maybe_reset_ble_executor(ble_address, reset_threshold)

    timer = threading.Timer(watchdog_secs, _cleanup)
    timer.daemon = True
    future.add_done_callback(lambda _f: timer.cancel())
    timer.start()


def _ensure_ble_worker_available(ble_address: str, *, operation: str) -> None:
    """
    Ensure the shared BLE worker is available for a new operation.

    When a previous BLE future remains in-flight beyond its timeout budget, treat
    it as stale and force-reset the worker so retries can make forward progress.
    """
    stale_elapsed_secs: float | None = None
    stale_timeout_secs: float | None = None
    stale_address: str | None = None
    stale_grace_secs = facade._coerce_nonnegative_float(
        facade._ble_future_stale_grace_secs, facade.BLE_FUTURE_STALE_GRACE_SECS
    )
    reset_threshold = facade._coerce_positive_int(
        facade._ble_timeout_reset_threshold, facade.BLE_TIMEOUT_RESET_THRESHOLD
    )

    with facade._ble_executor_lock:
        active_future = facade._ble_future
        if active_future is None or active_future.done():
            return

        if (
            facade._ble_future_started_at is not None
            and facade._ble_future_timeout_secs is not None
        ):
            elapsed = facade.time.monotonic() - facade._ble_future_started_at
            stale_after = facade._ble_future_timeout_secs + stale_grace_secs
            if elapsed >= stale_after:
                stale_elapsed_secs = elapsed
                stale_timeout_secs = facade._ble_future_timeout_secs
                stale_address = facade._ble_future_address or ble_address

    if (
        stale_elapsed_secs is not None
        and stale_timeout_secs is not None
        and stale_address is not None
    ):
        facade.logger.warning(
            "BLE worker appears stale during %s for %s (elapsed=%.1fs, timeout=%.1fs); forcing worker reset",
            operation,
            stale_address,
            stale_elapsed_secs,
            stale_timeout_secs,
        )
        facade._reset_ble_connection_gate_state(
            stale_address,
            reason=f"stale worker during {operation}",
        )
        timeout_count = facade._record_ble_timeout(stale_address)
        facade._maybe_reset_ble_executor(
            stale_address,
            max(timeout_count, reset_threshold),
        )

    with facade._ble_executor_lock:
        if facade._ble_future and not facade._ble_future.done():
            facade.logger.debug(
                "BLE worker busy; skipping %s for %s", operation, ble_address
            )
            raise TimeoutError(
                f"BLE {operation} already in progress for {ble_address}."
            )


def _maybe_reset_ble_executor(ble_address: str, timeout_count: int) -> None:
    """
    Reset the BLE worker executor when an address has reached the timeout threshold.

    Recreates the module's BLE executor and clears any active BLE future/state for the given
    BLE address when `timeout_count` meets or exceeds the configured reset threshold. Performs a
    best-effort cancellation and cleanup of a possibly stuck BLE task and resets the per-address
    timeout counter to zero.

    When the number of orphaned workers for a BLE address reaches EXECUTOR_ORPHAN_THRESHOLD,
    that address enters a degraded state: submission of new BLE work is refused and further
    automatic recovery is disabled. Recovery requires an explicit reconnect or process restart.

    Parameters:
        ble_address (str): BLE device address associated with the observed timeouts.
        timeout_count (int): Number of consecutive timeouts recorded for that address.
    """
    if ble_address in facade._ble_executor_degraded_addresses:
        facade.logger.debug(
            "BLE executor for %s is in degraded state; refusing to reset. "
            "Reconnect or restart required to recover.",
            ble_address,
        )
        return

    reset_threshold = facade._coerce_positive_int(
        facade._ble_timeout_reset_threshold, facade.BLE_TIMEOUT_RESET_THRESHOLD
    )
    # Capture future ref inside lock, cancel outside to avoid deadlock with done callbacks
    ble_future_to_cancel = None
    orphaned_workers = 0
    stale_executor: ThreadPoolExecutor | None = None
    with facade._ble_executor_lock:
        if timeout_count < reset_threshold:
            return

        current_orphans = facade._ble_executor_orphaned_workers_by_address.get(
            ble_address, 0
        )
        if current_orphans + 1 >= facade.EXECUTOR_ORPHAN_THRESHOLD:
            facade._ble_executor_degraded_addresses.add(ble_address)
            facade.logger.error(
                "BLE EXECUTOR DEGRADED for %s: %s workers have been orphaned due to "
                "repeated hangs. Further automatic recovery is disabled for this device. "
                "Reconnect or restart the relay to restore BLE connectivity.",
                ble_address,
                current_orphans + 1,
            )
            ble_future_to_cancel = facade._ble_future
            facade._ble_future = None
            stale_executor = facade._ble_executor
            facade._ble_future_address = None
            facade._ble_future_started_at = None
            facade._ble_future_timeout_secs = None
            with facade._ble_timeout_lock:
                facade._ble_timeout_counts[ble_address] = 0

        if ble_address in facade._ble_executor_degraded_addresses:
            degraded_now = True
        else:
            degraded_now = False
            if facade._ble_future and not facade._ble_future.done():
                ble_future_to_cancel = facade._ble_future
            if facade._ble_executor is not None and not getattr(
                facade._ble_executor, "_shutdown", False
            ):
                with facade._ble_timeout_lock:
                    orphaned_workers = current_orphans + 1
                    facade._ble_executor_orphaned_workers_by_address[ble_address] = (
                        orphaned_workers
                    )
                stale_executor = facade._ble_executor
            facade.logger.warning(
                "BLE worker timed out %s times for %s; recreating executor "
                "(orphaned BLE workers=%s, threshold=%s)",
                timeout_count,
                ble_address,
                orphaned_workers,
                facade.EXECUTOR_ORPHAN_THRESHOLD,
            )
            facade._ble_executor = facade.ThreadPoolExecutor(max_workers=1)
            facade._ble_future = None
            facade._ble_future_address = None
            facade._ble_future_started_at = None
            facade._ble_future_timeout_secs = None

    if degraded_now:
        if ble_future_to_cancel is not None:
            ble_future_to_cancel.cancel()
            try:
                ble_future_to_cancel.result(timeout=facade.FUTURE_CANCEL_TIMEOUT_SECS)
            except Exception as exc:  # noqa: BLE001 - best-effort degraded cleanup
                facade.logger.debug(
                    "BLE future cancellation raised error for %s: %s",
                    ble_address,
                    exc,
                )
        if stale_executor is not None and not getattr(
            stale_executor, "_shutdown", False
        ):
            try:
                stale_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                stale_executor.shutdown(wait=False)
        return

    if ble_future_to_cancel is not None:
        ble_future_to_cancel.cancel()
        try:
            ble_future_to_cancel.result(timeout=facade.FUTURE_CANCEL_TIMEOUT_SECS)
        except FuturesTimeoutError:
            pass
        except Exception as exc:  # noqa: BLE001 - best-effort reset cleanup
            facade.logger.debug("BLE worker errored during reset: %s", exc)
    if stale_executor is not None and not getattr(stale_executor, "_shutdown", False):
        try:
            stale_executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            stale_executor.shutdown(wait=False)
    with facade._ble_timeout_lock:
        facade._ble_timeout_counts[ble_address] = 0
