"""
Test helper utilities shared across test modules.

This module provides reusable test utilities to avoid code duplication
and improve maintainability of the test suite.
"""

import asyncio
import contextlib
import sys
import threading
from typing import Any, Callable, TypeVar, cast

from pubsub import pub

T = TypeVar("T")


class InlineExecutorLoop:
    """
    Wrap an event loop and execute run_in_executor calls inline for tests.

    This shim simulates a running asyncio event loop for synchronous executor
    execution in tests, making tests deterministic by avoiding actual threading.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Store provided event loop for use by this wrapper.

        Parameters:
            loop (asyncio.AbstractEventLoop): The underlying event loop whose attributes
                and non-overridden behavior are delegated to this shim.
        """
        self._loop = loop

    def is_running(self) -> bool:
        """
        Report whether this executor loop should be treated as running.

        Returns:
            True if loop is considered running, False otherwise.
        """
        return True

    def run_in_executor(
        self, _executor: Any, func: Callable[..., T], *args: Any
    ) -> asyncio.Future[T]:
        """
        Execute a callable synchronously and return a Future resolved with its outcome.

        Parameters:
            _executor: Ignored executor placeholder (kept for compatibility with
                loop.run_in_executor signature).
            func (Callable[..., T]): The function to execute.
            *args: Positional arguments to pass to `func`.

        Returns:
            asyncio.Future[T]: A Future that contains `func`'s return value or
                exception raised by `func`.

        Notes:
            Any exception raised by `func` will be set on the returned Future,
            matching the semantics of loop.run_in_executor.
        """
        fut = self._loop.create_future()
        try:
            result = func(*args)
        except BaseException as exc:
            # Mirror run_in_executor semantics where all failures are captured on
            # the returned Future instead of escaping synchronously.
            fut.set_exception(exc)
        else:
            fut.set_result(result)
        return fut

    def __getattr__(self, name: str) -> Any:
        """
        Delegate attribute access to wrapped event loop.

        Parameters:
            name (str): Attribute name being accessed on this wrapper.

        Returns:
            The attribute value from underlying event loop corresponding to `name`.

        Raises:
            AttributeError: If attribute does not exist on the wrapped loop.
        """
        return getattr(self._loop, name)


def inline_to_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    Run given callable synchronously in the current thread and return its result.

    This helper is used in tests to replace asyncio.to_thread, avoiding
    actual threading for deterministic test behavior.

    Parameters:
        func (Callable[..., Any]): The function to execute.
        *args: Positional arguments to pass to `func`.
        **kwargs: Keyword arguments to pass to `func`.

    Returns:
        Any: The return value produced by calling `func(*args, **kwargs)`.
    """
    return func(*args, **kwargs)


def make_patched_get_running_loop() -> Callable[[], asyncio.AbstractEventLoop]:
    """
    Return a get_running_loop patch that wraps loops in InlineExecutorLoop.

    This keeps run_in_executor paths deterministic in tests by executing inline.
    """
    real_get_running_loop = asyncio.get_running_loop
    wrapped_loops: dict[asyncio.AbstractEventLoop, InlineExecutorLoop] = {}

    def _patched_get_running_loop() -> asyncio.AbstractEventLoop:
        loop = real_get_running_loop()
        if isinstance(loop, InlineExecutorLoop):
            return loop
        wrapped = wrapped_loops.get(loop)
        if wrapped is None:
            wrapped = InlineExecutorLoop(loop)
            wrapped_loops[loop] = wrapped
        return cast(asyncio.AbstractEventLoop, wrapped)

    return _patched_get_running_loop


def reset_meshtastic_utils_globals(*, shutdown_executors: bool = False) -> None:
    """
    Reset meshtastic_utils globals shared across tests.

    This function resets module-level global state in mmrelay.meshtastic_utils
    to clean defaults, optionally shutting down thread pool executors.

    Parameters:
        shutdown_executors: If True, shut down _metadata_executor and _ble_executor
            before resetting them to None.
    """
    if "mmrelay.meshtastic_utils" not in sys.modules:
        return

    module = sys.modules["mmrelay.meshtastic_utils"]
    if hasattr(module, "config"):
        module.config = None  # type: ignore[attr-defined]
    if hasattr(module, "matrix_rooms"):
        module.matrix_rooms = []  # type: ignore[attr-defined]
    if hasattr(module, "meshtastic_client"):
        module.meshtastic_client = None  # type: ignore[attr-defined]
    if hasattr(module, "meshtastic_iface"):
        module.meshtastic_iface = None  # type: ignore[attr-defined]
    if hasattr(module, "event_loop"):
        module.event_loop = None  # type: ignore[attr-defined]
    if hasattr(module, "reconnecting"):
        module.reconnecting = False  # type: ignore[attr-defined]
    if hasattr(module, "shutting_down"):
        module.shutting_down = False  # type: ignore[attr-defined]
    if hasattr(module, "reconnect_task"):
        task = module.reconnect_task  # type: ignore[attr-defined]
        if task is not None and hasattr(task, "cancel"):
            with contextlib.suppress(Exception):
                task.cancel()
        module.reconnect_task = None  # type: ignore[attr-defined]
    if hasattr(module, "reconnect_task_future"):
        future = module.reconnect_task_future  # type: ignore[attr-defined]
        if future is not None and hasattr(future, "cancel"):
            with contextlib.suppress(Exception):
                future.cancel()
        module.reconnect_task_future = None  # type: ignore[attr-defined]
    if hasattr(module, "_connect_attempt_condition"):
        condition = module._connect_attempt_condition  # type: ignore[attr-defined]
        with condition:
            module._connect_attempt_in_progress = False  # type: ignore[attr-defined]
            condition.notify_all()
    elif hasattr(module, "_connect_attempt_in_progress"):
        module._connect_attempt_in_progress = False  # type: ignore[attr-defined]
    if hasattr(module, "subscribed_to_messages"):
        if module.subscribed_to_messages:  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                pub.unsubscribe(module.on_meshtastic_message, "meshtastic.receive")  # type: ignore[attr-defined]
        module.subscribed_to_messages = False  # type: ignore[attr-defined]
    if hasattr(module, "subscribed_to_connection_lost"):
        if module.subscribed_to_connection_lost:  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                pub.unsubscribe(
                    module.on_lost_meshtastic_connection, "meshtastic.connection.lost"
                )  # type: ignore[attr-defined]
        module.subscribed_to_connection_lost = False  # type: ignore[attr-defined]
    if hasattr(module, "_metadata_future"):
        future = module._metadata_future  # type: ignore[attr-defined]
        if future is not None and hasattr(future, "cancel"):
            with contextlib.suppress(Exception):
                future.cancel()
        module._metadata_future = None  # type: ignore[attr-defined]
    if hasattr(module, "_metadata_future_started_at"):
        module._metadata_future_started_at = None  # type: ignore[attr-defined]
    if hasattr(module, "_ble_future"):
        future = module._ble_future  # type: ignore[attr-defined]
        if future is not None and hasattr(future, "cancel"):
            with contextlib.suppress(Exception):
                future.cancel()
        module._ble_future = None  # type: ignore[attr-defined]
    if hasattr(module, "_ble_future_address"):
        module._ble_future_address = None  # type: ignore[attr-defined]
    if hasattr(module, "_ble_future_started_at"):
        module._ble_future_started_at = None  # type: ignore[attr-defined]
    if hasattr(module, "_ble_future_timeout_secs"):
        module._ble_future_timeout_secs = None  # type: ignore[attr-defined]
    if hasattr(module, "_ble_timeout_counts"):
        module._ble_timeout_counts = {}  # type: ignore[attr-defined]
    if hasattr(module, "_ble_executor_orphaned_workers_by_address"):
        module._ble_executor_orphaned_workers_by_address = {}  # type: ignore[attr-defined]
    if hasattr(module, "_metadata_executor_orphaned_workers"):
        module._metadata_executor_orphaned_workers = 0  # type: ignore[attr-defined]
    if hasattr(module, "_ble_executor_degraded_addresses"):
        module._ble_executor_degraded_addresses = set()  # type: ignore[attr-defined]
    if hasattr(module, "_metadata_executor_degraded"):
        module._metadata_executor_degraded = False  # type: ignore[attr-defined]
    if hasattr(module, "_health_probe_request_deadlines"):
        module._health_probe_request_deadlines = {}  # type: ignore[attr-defined]
    if hasattr(module, "_relay_startup_drain_complete_event"):
        module._relay_startup_drain_complete_event = threading.Event()  # type: ignore[attr-defined]
        module._relay_startup_drain_complete_event.set()  # type: ignore[attr-defined]
    if hasattr(module, "_ble_future_watchdog_secs"):
        module._ble_future_watchdog_secs = getattr(  # type: ignore[attr-defined]
            module, "BLE_FUTURE_WATCHDOG_SECS", module._ble_future_watchdog_secs
        )
    if hasattr(module, "_ble_timeout_reset_threshold"):
        module._ble_timeout_reset_threshold = getattr(  # type: ignore[attr-defined]
            module, "BLE_TIMEOUT_RESET_THRESHOLD", module._ble_timeout_reset_threshold
        )
    if hasattr(module, "_ble_scan_timeout_secs"):
        module._ble_scan_timeout_secs = getattr(  # type: ignore[attr-defined]
            module, "BLE_SCAN_TIMEOUT_SECS", module._ble_scan_timeout_secs
        )
    if hasattr(module, "_ble_future_stale_grace_secs"):
        module._ble_future_stale_grace_secs = getattr(  # type: ignore[attr-defined]
            module, "BLE_FUTURE_STALE_GRACE_SECS", module._ble_future_stale_grace_secs
        )
    if hasattr(module, "_ble_interface_create_timeout_secs"):
        module._ble_interface_create_timeout_secs = getattr(  # type: ignore[attr-defined]
            module,
            "BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS",
            module._ble_interface_create_timeout_secs,
        )

    def _shutdown_executor(executor: Any) -> None:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            with contextlib.suppress(RuntimeError):
                executor.shutdown(wait=False)
        except RuntimeError:
            pass

    if shutdown_executors and hasattr(module, "_metadata_executor"):
        executor = module._metadata_executor  # type: ignore[attr-defined]
        if executor is not None:
            _shutdown_executor(executor)
        module._metadata_executor = None  # type: ignore[attr-defined]
    if shutdown_executors and hasattr(module, "_ble_executor"):
        executor = module._ble_executor  # type: ignore[attr-defined]
        if executor is not None:
            _shutdown_executor(executor)
        module._ble_executor = None  # type: ignore[attr-defined]
