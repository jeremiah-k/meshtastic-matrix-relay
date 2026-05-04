from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import math
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Awaitable, Callable, Coroutine, cast

import mmrelay.meshtastic_utils as facade

__all__ = [
    "_coerce_bool",
    "_coerce_int_id",
    "_coerce_nonnegative_float",
    "_coerce_positive_float",
    "_coerce_positive_int",
    "_coerce_positive_int_id",
    "_fire_and_forget",
    "_make_awaitable",
    "_run_blocking_with_timeout",
    "_submit_coro",
    "_wait_for_future_result_with_shutdown",
    "_wait_for_result",
]


def _coerce_nonnegative_float(value: Any, default: float) -> float:
    """
    Coerce runtime BLE tuning values to a finite non-negative float.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_positive_int(value: Any, default: int) -> int:
    """
    Coerce runtime BLE tuning values to a positive integer.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = int(value)
        if parsed <= 0:
            raise ValueError
        return parsed
    except (TypeError, ValueError, OverflowError):
        return default


def _coerce_positive_int_id(raw_value: Any) -> int | None:
    """
    Convert a potential packet identifier value to a positive integer.

    Returns `None` when conversion fails or value is not positive.
    """
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_int_id(raw_value: Any) -> int | None:
    """
    Convert a potential identifier value to an integer.

    Returns `None` when conversion fails.
    """
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _coerce_positive_float(value: Any, default: float, setting_name: str) -> float:
    """
    Parse and validate a positive float config value.

    Falls back to `default` and logs a warning if conversion fails or if the
    value is not a finite positive number.
    """
    try:
        if isinstance(value, bool):
            raise TypeError
        parsed = float(value)
        if math.isfinite(parsed) and parsed > 0:
            return parsed
    except (TypeError, ValueError, OverflowError):
        pass

    facade.logger.warning(
        "Invalid %s value %r; using default %.1f",
        setting_name,
        value,
        default,
    )
    return default


def _coerce_bool(value: Any, default: bool, setting_name: str) -> bool:
    """
    Parse and validate a boolean config value.

    Accepts booleans directly, and normalizes string values:
    - "true", "1", "yes", "on" (case-insensitive) -> True
    - "false", "0", "no", "off", "" (case-insensitive) -> False

    Falls back to `default` and logs a warning for unrecognized values.
    """
    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        elif normalized in {"", "0", "false", "no", "off"}:
            return False
    elif isinstance(value, (int, float)):
        # For numeric types, use standard bool conversion
        return bool(value)

    facade.logger.warning(
        "Invalid %s value %r; using default %s",
        setting_name,
        value,
        default,
    )
    return default


def _submit_coro(
    coro: Any,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Future[Any] | None:
    """
    Schedule a coroutine or awaitable on an available asyncio event loop and return a Future for its result.

    Parameters:
        coro: The coroutine or awaitable object to execute. If not awaitable, the function returns None.
        loop: Optional target asyncio event loop to run the coroutine on. If omitted, a suitable loop (module-level or running loop) will be used when available.

    Returns:
        A Future containing the coroutine's result, or `None` if `coro` is not awaitable.
    """
    if not inspect.iscoroutine(coro):
        if not inspect.isawaitable(coro):
            # Guard against test mocks returning non-awaitable values (e.g., return_value vs AsyncMock).
            return None

        # Wrap awaitables that are not coroutine objects (e.g., Futures) for scheduling.
        async def _await_wrapper(awaitable: Any) -> Any:
            """
            Await an awaitable and return its result.

            Parameters:
                awaitable (Any): A coroutine, Future, or other awaitable to be awaited.

            Returns:
                Any: The value produced by awaiting `awaitable`.
            """
            return await awaitable

        coro = _await_wrapper(coro)
    loop = loop or facade.event_loop
    if (
        loop
        and isinstance(loop, facade.asyncio.AbstractEventLoop)
        and not loop.is_closed()
        and loop.is_running()
    ):
        return facade.asyncio.run_coroutine_threadsafe(coro, loop)
    # Fallback: schedule on a real loop if present; tests can override this.
    try:
        running = facade.asyncio.get_running_loop()
        return cast(Future[Any], running.create_task(coro))
    except RuntimeError:
        # No running loop: check if we can safely create a new loop
        try:
            facade.logger.debug(
                "No running event loop detected; creating a temporary loop to execute coroutine"
            )
            runner_cls = getattr(facade.asyncio, "Runner", None)
            if runner_cls is not None:
                with runner_cls() as runner:
                    result = runner.run(coro)
            else:
                new_loop = facade.asyncio.new_event_loop()
                try:
                    facade.asyncio.set_event_loop(new_loop)
                    result = new_loop.run_until_complete(coro)
                finally:
                    with contextlib.suppress(Exception):
                        new_loop.run_until_complete(new_loop.shutdown_asyncgens())
                    with contextlib.suppress(Exception):
                        new_loop.run_until_complete(
                            new_loop.shutdown_default_executor()
                        )
                    new_loop.close()
                    with contextlib.suppress(Exception):
                        facade.asyncio.set_event_loop(None)

            result_future: Future[Any] = Future()
            result_future.set_result(result)
            return result_future
        except Exception as e:
            # Final fallback: always return a Future so _fire_and_forget can log
            # exceptions instead of crashing a background thread when no loop is
            # available. We intentionally catch broad exceptions here because the
            # coroutine itself may raise, and we still need a Future wrapper.
            facade.logger.debug(
                "Ultimate fallback triggered for _submit_coro: %s: %s",
                type(e).__name__,
                e,
            )
            error_future: Future[Any] = Future()
            error_future.set_exception(e)
            return error_future


def _fire_and_forget(
    coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop | None = None
) -> None:
    """
    Schedule a coroutine to run in the background and log any non-cancellation exceptions.

    If `coro` is not a coroutine or scheduling fails, the function returns without side effects. The scheduled task will have a done callback that logs exceptions (except `facade.asyncio.CancelledError`).

    Parameters:
        coro (Coroutine[Any, Any, Any]): The coroutine to execute.
        loop (facade.asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted the module-default loop is used.
    """
    if not inspect.iscoroutine(coro):
        return

    task = facade._submit_coro(coro, loop=loop)
    if task is None:
        return

    def _handle_exception(t: asyncio.Future[Any] | Future[Any]) -> None:
        """
        Log non-cancellation exceptions raised by a fire-and-forget task.

        If the provided task or future has an exception and it is not an
        facade.asyncio.CancelledError, logs the exception at error level including the
        traceback. If retrieving the exception raises facade.asyncio.CancelledError it is
        ignored; other errors encountered while inspecting the future are logged at
        debug level.

        Parameters:
            t (facade.asyncio.Future | concurrent.futures.Future): Task or future to inspect.
        """
        try:
            if (exc := t.exception()) and not isinstance(
                exc, facade.asyncio.CancelledError
            ):
                facade.logger.error("Exception in fire-and-forget task", exc_info=exc)
        except facade.asyncio.CancelledError:
            pass
        except Exception as e:
            facade.logger.debug(
                f"Error retrieving exception from fire-and-forget task: {e}"
            )

    task.add_done_callback(_handle_exception)


def _make_awaitable(
    future: Any, loop: asyncio.AbstractEventLoop | None = None
) -> Awaitable[Any] | Any:
    """
    Convert a future-like object into an awaitable, optionally binding it to a given event loop.

    If `future` already implements the awaitable protocol, it is returned unchanged. Otherwise the function wraps the future so awaiting it yields the future's result; when `loop` is provided the wrapper is bound to that event loop.

    Parameters:
        future: A future-like object or an awaitable.
        loop (facade.asyncio.AbstractEventLoop | None): Event loop to bind non-awaitable futures to; if `None`, no explicit loop binding is applied.

    Returns:
        An awaitable that yields the resolved value of `future`, or `future` itself if it already supports awaiting.
    """
    if hasattr(future, "__await__"):
        return future
    target_loop = loop if isinstance(loop, facade.asyncio.AbstractEventLoop) else None
    return facade.asyncio.wrap_future(future, loop=target_loop)


def _run_blocking_with_timeout(
    action: Callable[[], Any],
    timeout: float,
    label: str,
    timeout_log_level: int | None = logging.WARNING,
    *,
    ble_address: str | None = None,
    ble_generation: int | None = None,
    iface_id: int | None = None,
    client_id: int | None = None,
) -> None:
    """
    Run a blocking callable in a daemon thread with a timeout to avoid hangs.

    This is used for sync BLE operations in the official meshtastic library
    (notably BLEClient.disconnect/close), which can block indefinitely and
    prevent clean shutdown if executed on a non-daemon thread.

    Parameters:
        action (Callable[[], Any]): Callable to run in a daemon thread.
        timeout (float): Maximum seconds to wait for completion.
        label (str): Short label used for logging/exception messages.
        timeout_log_level (int | None): Logging level for timeouts, or None to suppress.

    Raises:
        TimeoutError: If the action does not finish before the timeout expires.
        Exception: Any exception raised by the action is re-raised.
    """
    done_event = threading.Event()
    timed_out_event = threading.Event()
    teardown_timeout_recorded_event = threading.Event()
    teardown_timeout_resolved_event = threading.Event()
    teardown_timeout_resolution_lock = threading.Lock()
    action_error: Exception | None = None
    worker_started_at = facade.time.monotonic()
    resolved_stale_generation: bool | None = None
    resolved_remaining_unresolved: int | None = None

    def _resolve_recorded_teardown_timeout_once() -> None:
        """
        Resolve a previously-recorded teardown timeout at most once.

        Worker and caller timeout paths can both attempt to resolve. Guard with a
        lock+event so unresolved teardown bookkeeping remains consistent.
        """

        nonlocal resolved_stale_generation
        nonlocal resolved_remaining_unresolved

        if not teardown_timeout_recorded_event.is_set():
            return
        if not (ble_address and ble_generation is not None and ble_generation > 0):
            return

        resolve_teardown_timeout = getattr(
            facade,
            "_resolve_ble_teardown_timeout",
            None,
        )
        if not callable(resolve_teardown_timeout):
            return

        resolve_teardown_timeout_fn = cast(
            Callable[[str, int], tuple[int, bool]],
            resolve_teardown_timeout,
        )
        with teardown_timeout_resolution_lock:
            if teardown_timeout_resolved_event.is_set():
                return
            (
                resolved_remaining_unresolved,
                resolved_stale_generation,
            ) = resolve_teardown_timeout_fn(
                ble_address,
                ble_generation,
            )
            teardown_timeout_resolved_event.set()

    def _runner() -> None:
        """
        Execute the enclosing scope's action callable, record any raised Exception into the nonlocal `action_error`, and mark completion by calling `done_event.set()`.

        This function does not return a value; its observable effects are writing to the nonlocal `action_error` (set to the caught Exception on error) and setting the `done_event` to signal completion.
        """
        nonlocal action_error
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup
            action_error = exc
        finally:
            done_event.set()
            if timed_out_event.is_set():
                _resolve_recorded_teardown_timeout_once()
                stale_generation_for_log = resolved_stale_generation
                remaining_unresolved_for_log = resolved_remaining_unresolved
                if (
                    stale_generation_for_log is None
                    and ble_address
                    and ble_generation is not None
                    and ble_generation > 0
                ):
                    is_generation_stale = getattr(
                        facade,
                        "_is_ble_generation_stale",
                        None,
                    )
                    if callable(is_generation_stale):
                        stale_generation_for_log = bool(
                            is_generation_stale(ble_address, ble_generation)
                        )
                worker_elapsed = max(
                    0.0,
                    facade.time.monotonic() - worker_started_at,
                )
                facade.logger.warning(
                    "Blocking worker completed after caller timeout: label=%s "
                    "thread=%s elapsed=%.3fs address=%s generation=%s iface_id=%s "
                    "client_id=%s stale_generation=%s remaining_unresolved=%s",
                    label,
                    threading.current_thread().name,
                    worker_elapsed,
                    ble_address,
                    ble_generation,
                    iface_id,
                    client_id,
                    stale_generation_for_log,
                    remaining_unresolved_for_log,
                )

    thread = threading.Thread(
        target=_runner,
        name=f"mmrelay-blocking-{label}",
        daemon=True,
    )
    facade.logger.debug(
        "Starting blocking worker label=%s thread=%s timeout=%.1fs address=%s "
        "generation=%s iface_id=%s client_id=%s start_monotonic=%.6f",
        label,
        thread.name,
        timeout,
        ble_address,
        ble_generation,
        iface_id,
        client_id,
        worker_started_at,
    )
    thread.start()
    if not done_event.wait(timeout=timeout):
        elapsed = max(0.0, facade.time.monotonic() - worker_started_at)
        unresolved_generation_workers: int | None = None
        if ble_address and ble_generation is not None and ble_generation > 0:
            record_teardown_timeout = getattr(
                facade,
                "_record_ble_teardown_timeout",
                None,
            )
            if callable(record_teardown_timeout):
                record_teardown_timeout_fn = cast(
                    Callable[[str, int], int],
                    record_teardown_timeout,
                )
                recorded_unresolved_count = record_teardown_timeout_fn(
                    ble_address,
                    ble_generation,
                )
                unresolved_generation_workers = recorded_unresolved_count
                if recorded_unresolved_count > 0:
                    teardown_timeout_recorded_event.set()
        timed_out_event.set()
        if done_event.is_set():
            # Worker can finish after timeout expiry but before timeout signaling.
            # Resolve now so we don't leave a stale unresolved teardown entry.
            _resolve_recorded_teardown_timeout_once()
        if timeout_log_level is not None:
            facade.logger.log(
                timeout_log_level,
                "%s timed out after %.1fs (thread=%s elapsed=%.3fs address=%s "
                "generation=%s iface_id=%s client_id=%s "
                "unresolved_generation_workers=%s)",
                label,
                timeout,
                thread.name,
                elapsed,
                ble_address,
                ble_generation,
                iface_id,
                client_id,
                unresolved_generation_workers,
            )
        raise TimeoutError(f"{label} timed out after {timeout:.1f}s")
    if action_error is not None:
        facade.logger.debug("%s failed: %s", label, action_error)
        raise action_error


def _wait_for_result(
    result_future: Any,
    timeout: float,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    """
    Wait for and return the resolved value of a future-like or awaitable object, enforcing a timeout.

    Parameters:
        result_future (Any): A concurrent.futures.Future, asyncio Future/Task, awaitable, or object exposing a callable `result(timeout)` method. If None, the function returns False.
        timeout (float): Maximum seconds to wait for the result.
        loop (facade.asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted, the function will use a running loop or create a temporary loop as needed.

    Returns:
        Any: The value produced by the resolved future or awaitable. Returns `False` when `result_future` is `None` or when the function refuses to block the currently running event loop and instead schedules the awaitable to run in the background. Callers should handle False as a "could not wait" signal rather than a failed result.

    Raises:
        facade.asyncio.TimeoutError: If awaiting an asyncio awaitable times out.
        concurrent.futures.TimeoutError: If a concurrent.futures.Future times out.
        Exception: Any exception raised by the resolved future/awaitable is propagated.
    """
    if result_future is None:
        return False

    target_loop = loop if isinstance(loop, facade.asyncio.AbstractEventLoop) else None

    # Handle concurrent.futures.Future directly
    if isinstance(result_future, Future):
        return result_future.result(timeout=timeout)

    # Handle asyncio Future/Task instances
    if isinstance(result_future, facade.asyncio.Future):
        awaitable: Awaitable[Any] = result_future
    elif hasattr(result_future, "result") and callable(result_future.result):
        # Generic future-like object with .result API (used by some tests)
        try:
            return result_future.result(timeout)
        except TypeError:
            return result_future.result()
    else:
        awaitable = facade._make_awaitable(result_future, loop=target_loop)

    async def _runner() -> Any:
        """
        Await the captured awaitable and enforce the captured timeout.

        Returns:
            The result returned by the awaitable.

        Raises:
            facade.asyncio.TimeoutError: If the awaitable does not complete before the timeout expires.
        """
        return await facade.asyncio.wait_for(awaitable, timeout=timeout)

    try:
        running_loop = facade.asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if target_loop and not target_loop.is_closed():
        if target_loop.is_running():
            if running_loop is target_loop:
                # Avoid deadlocking the loop thread; schedule and return.
                facade.logger.warning(
                    "Refusing to block running event loop while waiting for result"
                )
                facade._fire_and_forget(_runner(), loop=target_loop)
                return False
            return facade.asyncio.run_coroutine_threadsafe(
                _runner(), target_loop
            ).result(timeout=timeout)
        return target_loop.run_until_complete(_runner())

    if running_loop and not running_loop.is_closed():
        if running_loop.is_running():
            facade.logger.warning(
                "Refusing to block running event loop while waiting for result"
            )
            facade._fire_and_forget(_runner(), loop=running_loop)
            return False
        return running_loop.run_until_complete(_runner())

    new_loop = facade.asyncio.new_event_loop()
    try:
        facade.asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(_runner())
    finally:
        new_loop.close()
        facade.asyncio.set_event_loop(None)


def _wait_for_future_result_with_shutdown(
    result_future: Future[Any],
    *,
    timeout_seconds: float,
    poll_seconds: float = 1.0,
) -> Any:
    """Wait for a concurrent future while remaining responsive to shutdown.

    Polls `result_future.result()` in short intervals so long BLE operations can
    abort quickly when `shutting_down` is set, instead of waiting the full
    timeout budget in one blocking call.
    """

    deadline = facade.time.monotonic() + float(timeout_seconds)
    poll_budget = max(0.05, float(poll_seconds))
    immediate_timeout_count = 0

    while True:
        if facade.shutting_down:
            raise TimeoutError("Shutdown in progress")

        remaining = deadline - facade.time.monotonic()
        if remaining <= 0:
            raise FuturesTimeoutError()

        wait_budget = min(remaining, poll_budget)
        call_started = facade.time.monotonic()
        try:
            return result_future.result(timeout=wait_budget)
        except FuturesTimeoutError:
            call_elapsed = facade.time.monotonic() - call_started
            # Some mocked futures raise timeout immediately instead of blocking for
            # the timeout duration. Avoid a busy-loop that can run until the full
            # deadline in that scenario.
            if call_elapsed < min(0.01, wait_budget * 0.1):
                immediate_timeout_count += 1
                if immediate_timeout_count >= 3:
                    raise
            else:
                immediate_timeout_count = 0
            continue
