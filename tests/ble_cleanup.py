"""
BLE cleanup utilities for test teardown.

Provides best-effort helpers to cancel and drain in-flight BLE future/task state
so that test teardown does not fail while clearing bookkeeping.
"""

__all__ = ["cleanup_ble_future_state"]

import asyncio
import concurrent.futures
import contextlib

from mmrelay import log_utils

logger = log_utils.get_logger(__name__)

_DRAIN_EXCEPTIONS = (
    TimeoutError,
    asyncio.CancelledError,
    asyncio.InvalidStateError,
    concurrent.futures.CancelledError,
    concurrent.futures.TimeoutError,
    concurrent.futures.InvalidStateError,
)


def _clear_ble_module_state(module: object) -> None:
    """Clear BLE-related state attributes on a module."""
    for attr in (
        "_ble_future",
        "_ble_future_address",
        "_ble_future_started_at",
        "_ble_future_timeout_secs",
    ):
        if hasattr(module, attr):
            setattr(module, attr, None)


def _safe_is_done(future: object) -> bool:
    """
    Determine whether a future-like object reports it is completed.

    Parameters:
        future (object): Object expected to provide a callable `done()` method.

    Returns:
        bool: `True` if `future.done()` exists and returns a truthy value; `False` otherwise (including when `done()` is absent or raises known invalid-state errors).
    """
    done_fn = getattr(future, "done", None)
    if not callable(done_fn):
        return False
    with contextlib.suppress(
        RuntimeError,
        asyncio.InvalidStateError,
        concurrent.futures.InvalidStateError,
    ):
        return bool(done_fn())
    return False


def _drain_future_result_safely(future: object, timeout: float) -> None:
    """
    Drain a future/task result best-effort so teardown does not leak exceptions.
    """
    exception_fn = getattr(future, "exception", None)
    is_done = _safe_is_done(future)
    if is_done and callable(exception_fn):
        # For completed futures/tasks, consume stored exceptions without re-raising.
        with contextlib.suppress(*_DRAIN_EXCEPTIONS):
            exception_fn()
        return

    result_fn = getattr(future, "result", None)
    if not callable(result_fn):
        return

    try:
        result_fn(timeout=timeout)
    except TypeError:
        try:
            result_fn()
        except _DRAIN_EXCEPTIONS:
            return
        except Exception as exc:
            logger.debug(
                "Suppressing future-drain exception during teardown: %s",
                exc,
            )
            return
    except _DRAIN_EXCEPTIONS:
        return
    except Exception as exc:
        logger.debug(
            "Suppressing future-drain exception during teardown: %s",
            exc,
        )
        return


def cleanup_ble_future_state(module: object) -> None:
    """
    Best-effort cancel and drain BLE in-flight future/task state on a module.

    This helper intentionally swallows expected timeout/cancellation/state errors
    because test teardown should not fail while clearing in-flight bookkeeping.
    """
    ble_future = getattr(module, "_ble_future", None)
    ble_address = getattr(module, "_ble_future_address", None)
    timeout_counts = getattr(module, "_ble_timeout_counts", None)
    if ble_future is None:
        if isinstance(timeout_counts, dict) and ble_address is not None:
            timeout_counts.pop(ble_address, None)
        _clear_ble_module_state(module)
        return

    cancel_fn = getattr(ble_future, "cancel", None)
    is_done = _safe_is_done(ble_future)

    if callable(cancel_fn) and not is_done:
        if isinstance(ble_future, asyncio.Task):

            def _consume_task_result(done_task: asyncio.Task[object]) -> None:
                with contextlib.suppress(
                    asyncio.CancelledError,
                    asyncio.InvalidStateError,
                ):
                    done_task.exception()

            try:
                loop = ble_future.get_loop()
                if not loop.is_closed():
                    if loop.is_running():
                        same_loop = False
                        with contextlib.suppress(RuntimeError):
                            same_loop = asyncio.get_running_loop() is loop
                        if same_loop:
                            ble_future.cancel()
                            ble_future.add_done_callback(_consume_task_result)
                        else:
                            loop.call_soon_threadsafe(ble_future.cancel)
                            cleanup_future = asyncio.run_coroutine_threadsafe(
                                asyncio.wait_for(ble_future, 0.2),
                                loop,
                            )
                            cleanup_future.result(timeout=0.5)
                    else:
                        ble_future.cancel()
                        loop.run_until_complete(asyncio.wait_for(ble_future, 0.2))
            except (
                asyncio.TimeoutError,
                asyncio.CancelledError,
                RuntimeError,
                asyncio.InvalidStateError,
                concurrent.futures.TimeoutError,
                concurrent.futures.CancelledError,
                concurrent.futures.InvalidStateError,
            ) as exc:
                logger.debug(
                    "Expected BLE Task cleanup exception: %s",
                    exc,
                )
            except Exception as exc:
                logger.debug(
                    "Suppressing BLE Task cleanup exception: %s",
                    exc,
                )
        else:
            try:
                cancel_fn()
            except Exception as exc:
                logger.debug(
                    "Suppressing BLE future cleanup exception: %s",
                    exc,
                )
            _drain_future_result_safely(ble_future, timeout=0.2)

    # Drain completed-task exceptions as well (prevents "exception was never retrieved").
    is_done_now = _safe_is_done(ble_future)
    if is_done_now:
        _drain_future_result_safely(ble_future, timeout=0.1)

    if isinstance(timeout_counts, dict) and ble_address is not None:
        timeout_counts.pop(ble_address, None)
    _clear_ble_module_state(module)
