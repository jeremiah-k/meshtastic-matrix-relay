import asyncio
import atexit
import contextlib
import importlib.util
import inspect
import io
import logging
import re
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Awaitable, Callable, Coroutine, cast

# meshtastic is not marked py.typed; keep import-untyped for strict mypy.
import meshtastic  # type: ignore[import-untyped]
import meshtastic.ble_interface  # type: ignore[import-untyped]
import meshtastic.serial_interface  # type: ignore[import-untyped]
import meshtastic.tcp_interface  # type: ignore[import-untyped]
import serial  # type: ignore[import-untyped]  # For serial port exceptions
import serial.tools.list_ports  # type: ignore[import-untyped]  # Import serial tools for port listing
from meshtastic.protobuf import mesh_pb2, portnums_pb2  # type: ignore[import-untyped]
from pubsub import pub  # type: ignore[import-untyped]

from mmrelay.config import get_meshtastic_config_value
from mmrelay.constants.config import (
    CONFIG_KEY_MESHNET_NAME,
    CONFIG_SECTION_MESHTASTIC,
    DEFAULT_DETECTION_SENSOR,
)
from mmrelay.constants.formats import (
    DETECTION_SENSOR_APP,
    EMOJI_FLAG_VALUE,
    TEXT_MESSAGE_APP,
)
from mmrelay.constants.messages import (
    DEFAULT_CHANNEL_VALUE,
    PORTNUM_DETECTION_SENSOR_APP,
    PORTNUM_TEXT_MESSAGE_APP,
)
from mmrelay.constants.network import (
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_SERIAL_PORT,
    CONFIG_KEY_TIMEOUT,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
    DEFAULT_BACKOFF_TIME,
    DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
    DEFAULT_MESHTASTIC_TIMEOUT,
    ERRNO_BAD_FILE_DESCRIPTOR,
    INFINITE_RETRIES,
)
from mmrelay.db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from mmrelay.log_utils import get_logger
from mmrelay.runtime_utils import is_running_as_service

try:
    BLE_AVAILABLE = importlib.util.find_spec("bleak") is not None
except ValueError:
    BLE_AVAILABLE = "bleak" in sys.modules

# Maximum number of timeout retries when retries are configured as infinite.
MAX_TIMEOUT_RETRIES_INFINITE = 5

# Operator guidance for BLE troubleshooting (reused across multiple timeout handlers)
BLE_TROUBLESHOOTING_GUIDANCE = (
    "Try: 1) Restarting BlueZ: 'sudo systemctl restart bluetooth', "
    "2) Manually disconnecting device: 'bluetoothctl disconnect {ble_address}', "
    "3) Rebooting your machine"
)


# Import BLE exceptions conditionally
try:
    from bleak.exc import BleakDBusError, BleakError
except ImportError:
    BleakDBusError = Exception  # type: ignore[misc,assignment]
    BleakError = Exception  # type: ignore[misc,assignment]


# Global config variable that will be set from config.py
config = None

# Do not import plugin_loader here to avoid circular imports

# Initialize matrix rooms configuration
matrix_rooms: list[dict[str, Any]] = []

# Initialize logger for Meshtastic
logger = get_logger(name="Meshtastic")


# Global variables for the Meshtastic connection and event loop management
meshtastic_client = None
meshtastic_iface = None  # BLE interface instance for process lifetime
event_loop = None  # Will be set from main.py

meshtastic_lock = (
    threading.Lock()
)  # To prevent race conditions on meshtastic_client access

reconnecting = False
shutting_down = False

reconnect_task = None  # To keep track of the reconnect task
meshtastic_iface_lock = (
    threading.Lock()
)  # To prevent race conditions on BLE interface singleton creation

# Subscription flags to prevent duplicate subscriptions
subscribed_to_messages = False
subscribed_to_connection_lost = False

# Shared executor for getMetadata() to avoid leaking threads when metadata calls hang.
# A single worker is enough because getMetadata() is serialized by design.
_metadata_executor = ThreadPoolExecutor(max_workers=1)
_metadata_future: Future[Any] | None = None
_metadata_future_lock = threading.Lock()

# Shared executor for BLE init/connect to avoid leaking threads across retries.
# BLE setup is inherently sequential, so a single worker keeps things predictable.
_ble_executor = ThreadPoolExecutor(max_workers=1)
_ble_executor_lock = threading.Lock()
_ble_future: Future[Any] | None = None
_ble_future_address: str | None = None
_ble_timeout_counts: dict[str, int] = {}
_ble_timeout_lock = threading.Lock()
_BLE_FUTURE_WATCHDOG_SECS = 120.0
_BLE_TIMEOUT_RESET_THRESHOLD = 3
_BLE_SCAN_TIMEOUT_SECS = 4.0


def _shutdown_shared_executors() -> None:
    """
    Shutdown shared executors on interpreter exit to avoid blocking.

    Attempts to cancel any pending futures and shutdown without waiting
    to prevent interpreter hangs when tasks are stuck.

    Note: This is called via atexit during interpreter shutdown. It performs
    cleanup without waiting to avoid blocking the interpreter exit sequence.
    """
    global _ble_future, _ble_future_address, _metadata_future

    # Cancel any pending BLE operation
    with _ble_executor_lock:
        stale_address = _ble_future_address
        if _ble_future and not _ble_future.done():
            logger.debug("Cancelling pending BLE future during executor shutdown")
            _ble_future.cancel()
        _ble_future = None
        _ble_future_address = None
        if stale_address:
            with _ble_timeout_lock:
                _ble_timeout_counts.pop(stale_address, None)

        executor = _ble_executor
        if executor is not None and not getattr(executor, "_shutdown", False):
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    # Cancel any pending metadata operation
    with _metadata_future_lock:
        if _metadata_future and not _metadata_future.done():
            logger.debug("Cancelling pending metadata future during executor shutdown")
            _metadata_future.cancel()
        _metadata_future = None

        executor = _metadata_executor
        if executor is not None and not getattr(executor, "_shutdown", False):
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)


atexit.register(_shutdown_shared_executors)


def _get_ble_executor() -> ThreadPoolExecutor:
    """
    Get or create a BLE executor thread pool.

    Returns the shared BLE executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _ble_executor_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared BLE executor instance.
    """
    global _ble_executor
    if _ble_executor is None or getattr(_ble_executor, "_shutdown", False):
        _ble_executor = ThreadPoolExecutor(max_workers=1)
    return _ble_executor


def _get_metadata_executor() -> ThreadPoolExecutor:
    """
    Get or create the metadata executor thread pool.

    Returns the shared metadata executor, creating it if it has been shut down or is None.
    This handles cases where executor has been shut down during test cleanup.
    Note: Caller must hold _metadata_future_lock to avoid race conditions.

    Returns:
        ThreadPoolExecutor: The shared metadata executor instance.
    """
    global _metadata_executor
    if _metadata_executor is None or getattr(_metadata_executor, "_shutdown", False):
        _metadata_executor = ThreadPoolExecutor(max_workers=1)
    return _metadata_executor


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
    loop = loop or event_loop
    if (
        loop
        and isinstance(loop, asyncio.AbstractEventLoop)
        and not loop.is_closed()
        and loop.is_running()
    ):
        return asyncio.run_coroutine_threadsafe(coro, loop)
    # Fallback: schedule on a real loop if present; tests can override this.
    try:
        running = asyncio.get_running_loop()
        return cast(Future[Any], running.create_task(coro))
    except RuntimeError:
        # No running loop: check if we can safely create a new loop
        try:
            # Try to get the current event loop policy and create a new loop
            # This is safer than asyncio.run() which can cause deadlocks
            policy = asyncio.get_event_loop_policy()
            logger.debug(
                "No running event loop detected; creating a temporary loop to execute coroutine"
            )
            new_loop = policy.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                result = new_loop.run_until_complete(coro)
                result_future: Future[Any] = Future()
                result_future.set_result(result)
                return result_future
            finally:
                new_loop.close()
                asyncio.set_event_loop(None)
        except Exception as e:
            # Final fallback: always return a Future so _fire_and_forget can log
            # exceptions instead of crashing a background thread when no loop is
            # available. We intentionally catch broad exceptions here because the
            # coroutine itself may raise, and we still need a Future wrapper.
            logger.debug(
                "Ultimate fallback triggered for _submit_coro: %s: %s",
                type(e).__name__,
                e,
            )
            error_future: Future[Any] = Future()
            error_future.set_exception(e)
            return error_future


def _clear_ble_future(done_future: Future[Any]) -> None:
    """
    Release the module's active BLE future reference if it matches the completed future.

    If `done_future` is the currently tracked BLE executor future, clear the tracked
    future and its associated address; also remove the per-address timeout count.
    Parameters:
        done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and should be cleared if it matches the active BLE task.
    """
    global _ble_future, _ble_future_address
    with _ble_executor_lock:
        if _ble_future is done_future:
            _ble_future = None
            if _ble_future_address:
                with _ble_timeout_lock:
                    _ble_timeout_counts.pop(_ble_future_address, None)
            _ble_future_address = None


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

    def _cleanup() -> None:
        """
        Clear a stale BLE worker future when it exceeds the watchdog timeout.

        If the provided future is still running and remains the active BLE future, logs a warning including the watchdog duration, BLE address, and reason, then clears the stale future.
        """
        if future.done():
            return
        with _ble_executor_lock:
            if _ble_future is not future:
                return
        logger.warning(
            "BLE worker still running after %.0fs for %s; clearing stale future (%s)",
            _BLE_FUTURE_WATCHDOG_SECS,
            ble_address,
            reason,
        )
        _clear_ble_future(future)

    timer = threading.Timer(_BLE_FUTURE_WATCHDOG_SECS, _cleanup)
    timer.daemon = True
    future.add_done_callback(lambda _f: timer.cancel())
    timer.start()


def _record_ble_timeout(ble_address: str) -> int:
    """
    Increment the recorded BLE timeout count for the given BLE address.

    This operation is thread-safe.

    Parameters:
        ble_address (str): BLE device address to record the timeout for.

    Returns:
        int: The updated timeout count for the specified BLE address (1 or greater).
    """
    with _ble_timeout_lock:
        _ble_timeout_counts[ble_address] = _ble_timeout_counts.get(ble_address, 0) + 1
        return _ble_timeout_counts[ble_address]


def _maybe_reset_ble_executor(ble_address: str, timeout_count: int) -> None:
    """
    Reset the BLE worker executor when an address has reached the timeout threshold.

    Recreates the module's BLE executor and clears any active BLE future/state for the given
    BLE address when `timeout_count` meets or exceeds the configured reset threshold. Performs a
    best-effort cancellation and cleanup of a possibly stuck BLE task and resets the per-address
    timeout counter to zero.

    Parameters:
        ble_address (str): BLE device address associated with the observed timeouts.
        timeout_count (int): Number of consecutive timeouts recorded for that address.
    """
    global _ble_executor, _ble_future, _ble_future_address
    with _ble_executor_lock:
        if timeout_count < _BLE_TIMEOUT_RESET_THRESHOLD:
            return
        logger.warning(
            "BLE worker timed out %s times for %s; recreating executor",
            timeout_count,
            ble_address,
        )
        if _ble_future and not _ble_future.done():
            _ble_future.cancel()
            try:
                _ble_future.result(timeout=0.2)
            except FuturesTimeoutError:
                pass
            except Exception as exc:  # noqa: BLE001 - best-effort reset cleanup
                logger.debug("BLE worker errored during reset: %s", exc)
        if _ble_executor is not None and not _ble_executor._shutdown:
            try:
                _ble_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                _ble_executor.shutdown(wait=False)
        _ble_executor = ThreadPoolExecutor(max_workers=1)
        _ble_future = None
        _ble_future_address = None
    with _ble_timeout_lock:
        _ble_timeout_counts[ble_address] = 0


def _scan_for_ble_address(ble_address: str, timeout: float) -> bool:
    """
    Performs a best-effort BLE scan to check whether a device with the given address is discoverable.

    If the Bleak library is unavailable or an active asyncio event loop is running, the function does not perform a scan and returns `false`.

    Returns:
        `true` if the device address was observed in a scan within the given timeout; `false` if the device was not observed, the scan failed, Bleak is unavailable, or scanning was skipped due to an active event loop.
    """
    if not BLE_AVAILABLE:
        return False

    try:
        from bleak import BleakScanner
    except ImportError:
        return False

    async def _scan() -> bool:
        """
        Determine whether the target BLE device is discoverable within the scan timeout.

        Returns:
            bool: `True` if a device with the target BLE address is discovered within the timeout, `False` otherwise (including when BLE discovery errors occur).
        """
        try:
            find_device = getattr(BleakScanner, "find_device_by_address", None)
            if callable(find_device):
                try:
                    return await find_device(ble_address, timeout=timeout) is not None
                except TypeError:
                    return (
                        await asyncio.wait_for(
                            find_device(ble_address), timeout=timeout
                        )
                        is not None
                    )

            devices = await BleakScanner.discover(timeout=timeout)
            return any(
                getattr(device, "address", None) == ble_address for device in devices
            )
        except (
            BleakError,
            BleakDBusError,
            OSError,
            RuntimeError,
            asyncio.TimeoutError,
        ) as exc:
            logger.debug("BLE scan failed for %s: %s", ble_address, exc)
            return False

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        logger.debug(
            "Skipping BLE scan for %s; running event loop is active",
            ble_address,
        )
        return False

    try:
        return asyncio.run(_scan())
    except (
        BleakError,
        BleakDBusError,
        OSError,
        RuntimeError,
        asyncio.TimeoutError,
    ) as exc:
        logger.debug("BLE scan failed for %s: %s", ble_address, exc)
        return False


def _is_ble_discovery_error(error: Exception) -> bool:
    """
    Determine whether an exception represents a BLE discovery or connection completion failure.

    Returns:
        True if the exception indicates a BLE discovery or connection completion failure, False otherwise.
    """
    message = str(error)
    if "No Meshtastic BLE peripheral" in message:
        return True
    if "Timed out waiting for connection completion" in message:
        return True

    ble_interface = getattr(meshtastic.ble_interface, "BLEInterface", None)
    ble_error_type = getattr(ble_interface, "BLEError", None)
    if ble_error_type and isinstance(error, ble_error_type):
        return True

    mesh_interface = getattr(meshtastic, "mesh_interface", None)
    mesh_interface_class = getattr(mesh_interface, "MeshInterface", None)
    mesh_error_type = getattr(mesh_interface_class, "MeshInterfaceError", None)
    if mesh_error_type and isinstance(error, mesh_error_type):
        return True

    return False


def _fire_and_forget(
    coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop | None = None
) -> None:
    """
    Schedule a coroutine to run in the background and log any non-cancellation exceptions.

    If `coro` is not a coroutine or scheduling fails, the function returns without side effects. The scheduled task will have a done callback that logs exceptions (except `asyncio.CancelledError`).

    Parameters:
        coro (Coroutine[Any, Any, Any]): The coroutine to execute.
        loop (asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted the module-default loop is used.
    """
    if not inspect.iscoroutine(coro):
        return

    task = _submit_coro(coro, loop=loop)
    if task is None:
        return

    def _handle_exception(t: asyncio.Future[Any] | Future[Any]) -> None:
        """
        Log non-cancellation exceptions raised by a fire-and-forget task.

        If the provided task or future has an exception and it is not an
        asyncio.CancelledError, logs the exception at error level including the
        traceback. If retrieving the exception raises asyncio.CancelledError it is
        ignored; other errors encountered while inspecting the future are logged at
        debug level.

        Parameters:
            t (asyncio.Future | concurrent.futures.Future): Task or future to inspect.
        """
        try:
            if (exc := t.exception()) and not isinstance(exc, asyncio.CancelledError):
                logger.error("Exception in fire-and-forget task", exc_info=exc)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Error retrieving exception from fire-and-forget task: {e}")

    task.add_done_callback(_handle_exception)


def _make_awaitable(
    future: Any, loop: asyncio.AbstractEventLoop | None = None
) -> Awaitable[Any] | Any:
    """
    Convert a future-like object into an awaitable, optionally binding it to a given event loop.

    If `future` already implements the awaitable protocol, it is returned unchanged. Otherwise the function wraps the future so awaiting it yields the future's result; when `loop` is provided the wrapper is bound to that event loop.

    Parameters:
        future: A future-like object or an awaitable.
        loop (asyncio.AbstractEventLoop | None): Event loop to bind non-awaitable futures to; if `None`, no explicit loop binding is applied.

    Returns:
        An awaitable that yields the resolved value of `future`, or `future` itself if it already supports awaiting.
    """
    if hasattr(future, "__await__"):
        return future
    target_loop = loop if isinstance(loop, asyncio.AbstractEventLoop) else None
    return asyncio.wrap_future(future, loop=target_loop)


def _run_blocking_with_timeout(
    action: Callable[[], Any],
    timeout: float,
    label: str,
    timeout_log_level: int | None = logging.WARNING,
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
    action_error: Exception | None = None

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

    thread = threading.Thread(
        target=_runner,
        name=f"mmrelay-blocking-{label}",
        daemon=True,
    )
    thread.start()
    if not done_event.wait(timeout=timeout):
        if timeout_log_level is not None:
            logger.log(timeout_log_level, "%s timed out after %.1fs", label, timeout)
        raise TimeoutError(f"{label} timed out after {timeout:.1f}s")
    if action_error is not None:
        logger.debug("%s failed: %s", label, action_error)
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
        loop (asyncio.AbstractEventLoop | None): Optional event loop to use; if omitted, the function will use a running loop or create a temporary loop as needed.

    Returns:
        Any: The value produced by the resolved future or awaitable. Returns `False` when `result_future` is `None` or when the function refuses to block the currently running event loop and instead schedules the awaitable to run in the background. Callers should handle False as a "could not wait" signal rather than a failed result.

    Raises:
        asyncio.TimeoutError: If awaiting an asyncio awaitable times out.
        concurrent.futures.TimeoutError: If a concurrent.futures.Future times out.
        Exception: Any exception raised by the resolved future/awaitable is propagated.
    """
    if result_future is None:
        return False

    target_loop = loop if isinstance(loop, asyncio.AbstractEventLoop) else None

    # Handle concurrent.futures.Future directly
    if isinstance(result_future, Future):
        return result_future.result(timeout=timeout)

    # Handle asyncio Future/Task instances
    if isinstance(result_future, asyncio.Future):
        awaitable: Awaitable[Any] = result_future
    elif hasattr(result_future, "result") and callable(result_future.result):
        # Generic future-like object with .result API (used by some tests)
        try:
            return result_future.result(timeout)
        except TypeError:
            return result_future.result()
    else:
        awaitable = _make_awaitable(result_future, loop=target_loop)

    async def _runner() -> Any:
        """
        Await the captured awaitable and enforce the captured timeout.

        Returns:
            The result returned by the awaitable.

        Raises:
            asyncio.TimeoutError: If the awaitable does not complete before the timeout expires.
        """
        return await asyncio.wait_for(awaitable, timeout=timeout)

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if target_loop and not target_loop.is_closed():
        if target_loop.is_running():
            if running_loop is target_loop:
                # Avoid deadlocking the loop thread; schedule and return.
                logger.warning(
                    "Refusing to block running event loop while waiting for result"
                )
                _fire_and_forget(_runner(), loop=target_loop)
                return False
            return asyncio.run_coroutine_threadsafe(_runner(), target_loop).result(
                timeout=timeout
            )
        return target_loop.run_until_complete(_runner())

    if running_loop and not running_loop.is_closed():
        if running_loop.is_running():
            logger.warning(
                "Refusing to block running event loop while waiting for result"
            )
            _fire_and_forget(_runner(), loop=running_loop)
            return False
        return running_loop.run_until_complete(_runner())

    new_loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(new_loop)
        return new_loop.run_until_complete(_runner())
    finally:
        new_loop.close()
        asyncio.set_event_loop(None)


def _resolve_plugin_timeout(cfg: dict[str, Any] | None, default: float = 5.0) -> float:
    """
    Resolve the plugin timeout value from the configuration.

    Reads `meshtastic.plugin_timeout` from `cfg` and returns it as a positive float. If the value is missing, cannot be converted to a number, or is not greater than 0, the provided `default` is returned and a warning is logged.

    Parameters:
        cfg (dict | None): Configuration mapping that may contain a "meshtastic" section with a "plugin_timeout" value.
        default (float): Fallback timeout in seconds used when `cfg` does not provide a valid value.

    Returns:
        float: A positive timeout in seconds.
    """

    raw_value = default
    if isinstance(cfg, dict):
        try:
            raw_value = cfg.get("meshtastic", {}).get("plugin_timeout", default)
        except AttributeError:
            raw_value = default

    try:
        timeout = float(raw_value)
        if timeout > 0:
            return timeout
        logger.warning(
            "Non-positive meshtastic.plugin_timeout value %r; using %ss fallback.",
            raw_value,
            default,
        )
    except (TypeError, ValueError):
        logger.warning(
            "Invalid meshtastic.plugin_timeout value %r; using %ss fallback.",
            raw_value,
            default,
        )

    return default


def _resolve_plugin_result(
    handler_result: Any,
    plugin: Any,
    plugin_timeout: float,
    loop: asyncio.AbstractEventLoop,
) -> bool:
    """
    Resolve a plugin handler result to a boolean, handling async timeouts and bad awaitables.

    Returns True when the plugin should be treated as handled, False otherwise.
    """
    if not inspect.iscoroutine(handler_result) and not inspect.isawaitable(
        handler_result
    ):
        return bool(handler_result)

    result_future = _submit_coro(handler_result, loop=loop)
    if result_future is None:
        logger.warning("Plugin %s returned no awaitable; skipping.", plugin.plugin_name)
        return False
    try:
        return bool(_wait_for_result(result_future, plugin_timeout, loop=loop))
    except (asyncio.TimeoutError, FuturesTimeoutError) as exc:
        logger.warning(
            "Plugin %s did not respond within %ss: %s",
            plugin.plugin_name,
            plugin_timeout,
            exc,
        )
        return True


def _run_meshtastic_plugins(
    *,
    packet: dict[str, Any],
    formatted_message: str | None,
    longname: str | None,
    meshnet_name: str | None,
    loop: asyncio.AbstractEventLoop,
    cfg: dict[str, Any] | None,
    use_keyword_args: bool = False,
    log_with_portnum: bool = False,
    portnum: Any | None = None,
) -> bool:
    """
    Invoke Meshtastic plugins and return True when a plugin handles the message.
    """
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()
    plugin_timeout = _resolve_plugin_timeout(cfg, default=5.0)

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                if use_keyword_args:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message=formatted_message,
                        longname=longname,
                        meshnet_name=meshnet_name,
                    )
                else:
                    handler_result = plugin.handle_meshtastic_message(
                        packet,
                        formatted_message,
                        longname,
                        meshnet_name,
                    )

                found_matching_plugin = _resolve_plugin_result(
                    handler_result,
                    plugin,
                    plugin_timeout,
                    loop,
                )

                if found_matching_plugin:
                    if log_with_portnum:
                        logger.debug(
                            f"Processed {portnum} with plugin {plugin.plugin_name}"
                        )
                    else:
                        logger.debug(f"Processed by plugin {plugin.plugin_name}")
            except Exception:
                logger.exception(f"Plugin {plugin.plugin_name} failed")
                # Continue processing other plugins

    return found_matching_plugin


def _get_name_safely(name_func: Callable[[Any], str | None], sender: Any) -> str:
    """
    Return a display name for a sender, falling back to the sender's string form.

    Parameters:
        name_func (Callable[[Any], str | None]): Function to obtain a name for the sender (e.g., get_longname or get_shortname).
        sender (Any): Sender identifier passed to `name_func`.

    Returns:
        str: The name returned by `name_func`, or `str(sender)` if no name is available or an error occurs.
    """
    try:
        return name_func(sender) or str(sender)
    except (TypeError, AttributeError):
        return str(sender)


def _get_name_or_none(
    name_func: Callable[[Any], str | None], sender: Any
) -> str | None:
    """
    Retrieve a name for a sender using the provided lookup function, or return None if the lookup fails.

    Parameters:
        name_func (Callable[[Any], str | None]): Function that returns a name given the sender (e.g., longname or shortname).
        sender (Any): Sender identifier passed to `name_func`.

    Returns:
        str | None: The name returned by `name_func`, or `None` if the function raises TypeError or AttributeError.
    """
    try:
        return name_func(sender)
    except (TypeError, AttributeError):
        return None


def _get_device_metadata(client: Any) -> dict[str, Any]:
    """
    Retrieve firmware version and raw metadata output from a Meshtastic client.

    Attempts to call client.localNode.getMetadata() (when present), captures any console output produced, and extracts a firmware version string if available.

    Parameters:
        client (Any): Meshtastic client object expected to expose localNode.getMetadata(); if absent, metadata retrieval is skipped.

    Returns:
        dict: {
            "firmware_version" (str): Parsed firmware version or "unknown" when not found,
            "raw_output" (str): Captured output from getMetadata(), truncated to 4096 characters with a trailing ellipsis if longer,
            "success" (bool): `True` when a firmware version was successfully parsed, `False` otherwise
        }
    """
    global _metadata_future
    result = {"firmware_version": "unknown", "raw_output": "", "success": False}

    try:
        # Preflight: client may be a mock without localNode/getMetadata
        if not getattr(client, "localNode", None) or not hasattr(
            client.localNode, "getMetadata"
        ):
            logger.debug(
                "Meshtastic client has no localNode.getMetadata(); skipping metadata retrieval"
            )
            return result

        # Capture getMetadata() output to extract firmware version.
        # Use a shared executor to prevent thread leaks if getMetadata() hangs.
        output_capture = io.StringIO()
        # Track redirect state so a timeout cannot leave sys.stdout pointing at
        # a closed StringIO (which can trigger "I/O operation on closed file").
        redirect_active = threading.Event()
        orig_stdout = sys.stdout

        def call_get_metadata() -> None:
            # Capture stdout only; stderr is left intact to avoid losing
            # critical error output if the worker outlives the timeout.
            """
            Invoke the client's getMetadata() while capturing its standard output.

            Calls client.localNode.getMetadata() with stdout redirected into the module's
            output_capture to prevent metadata noise from polluting process stdout; stderr
            is left unchanged. While the call runs, the module-level redirect_active flag
            is set and is cleared on completion to signal the redirect state.
            """
            try:
                with contextlib.redirect_stdout(output_capture):
                    redirect_active.set()
                    try:
                        client.localNode.getMetadata()
                    finally:
                        redirect_active.clear()
            except ValueError:
                pass

        with _metadata_future_lock:
            if _metadata_future and not _metadata_future.done():
                # A previous metadata request is still running; avoid piling up
                # threads and leave the in-flight call to finish in its own time.
                logger.debug("getMetadata() already running; skipping new request")
                return result

            try:
                future = _get_metadata_executor().submit(call_get_metadata)
            except RuntimeError as exc:
                # The shared executor may already be shutting down; treat this as
                # a non-fatal metadata miss so we don't block connections.
                logger.debug(
                    "getMetadata() submission failed; skipping metadata retrieval",
                    exc_info=exc,
                )
                return result
            _metadata_future = future
        timed_out = False
        future_error: Exception | None = None
        try:
            future.result(timeout=30.0)
        except FuturesTimeoutError:
            timed_out = True
            logger.debug("getMetadata() timed out after 30 seconds")
            # If the worker is still running, restore stdio immediately so the
            # main process does not keep writing to the captured buffer.
            if redirect_active.is_set():
                if sys.stdout is output_capture:
                    sys.stdout = orig_stdout
        except Exception as e:  # noqa: BLE001 - getMetadata errors vary by backend
            future_error = e

        try:
            console_output = output_capture.getvalue()
        except ValueError:
            # If the buffer was closed unexpectedly, treat as empty output.
            console_output = ""

        def _finalize_metadata_capture(done_future: Future[Any]) -> None:
            """
            Finalize capture state for a completed metadata retrieval future.

            If the provided future matches the module-level metadata future, clear that reference.
            Also close the shared output capture stream if it is still open.

            Parameters:
                done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and triggered finalization.
            """
            global _metadata_future
            with _metadata_future_lock:
                if _metadata_future is done_future:
                    _metadata_future = None
            if not output_capture.closed:
                output_capture.close()

        # Only close the buffer when the redirect is no longer active; otherwise
        # writes from the worker will raise ValueError("I/O operation on closed file").
        if timed_out and not future.done():
            future.add_done_callback(_finalize_metadata_capture)
        else:
            _finalize_metadata_capture(future)

        # Re-raise any worker exception so the outer handler can log and
        # return default metadata without hiding failures.
        if future_error is not None:
            raise future_error

        # Cap raw_output length to avoid memory bloat
        if len(console_output) > 4096:
            console_output = console_output[:4096] + "…"
        result["raw_output"] = console_output

        # Parse firmware version from the output using robust regex
        # Case-insensitive, handles quotes, whitespace, and various formats
        match = re.search(
            r"(?i)\bfirmware[\s_/-]*version\b\s*[:=]\s*['\"]?\s*([^\s\r\n'\"]+)",
            console_output,
        )
        if match:
            parsed = match.group(1).strip()
            if parsed:
                result["firmware_version"] = parsed
                result["success"] = True

    except Exception as e:  # noqa: BLE001 - metadata failures must not block startup
        # Metadata is optional; never block the main connection path on failures
        # in the admin request or parsing logic.
        logger.debug(
            "Could not retrieve device metadata via localNode.getMetadata()", exc_info=e
        )

    return result


def _sanitize_ble_address(address: str) -> str:
    """
    Normalize a BLE address by removing common separators and converting to lowercase.

    This matches the sanitization logic used by both official and forked meshtastic
    libraries, ensuring consistent address comparison.

    Parameters:
        address: The BLE address to sanitize.

    Returns:
        Sanitized address with all "-", "_", ":" removed and lowercased.
    """
    if not address:
        return address
    return address.replace("-", "").replace("_", "").replace(":", "").lower()


def _validate_ble_connection_address(interface: Any, expected_address: str) -> bool:
    """
    Validate that a BLE interface is connected to the configured device address.

    Compares the configured address to the interface's connected address after normalizing
    (both addresses have separators removed and are lowercased). Works with both the
    official and forked Meshtastic interface shapes by attempting common attribute paths.
    This is a best-effort check: if the connected address cannot be determined the
    function returns `True` to avoid false negatives; it returns `False` only when a
    determinate mismatch is detected.

    Parameters:
        interface (Any): BLE interface object whose connected address should be inspected.
        expected_address (str): Configured BLE device address to validate against.

    Returns:
        bool: `True` if the connected device matches `expected_address` or the address
        cannot be determined, `False` if a definitive mismatch is found.
    """
    try:
        expected_sanitized = _sanitize_ble_address(expected_address)

        # Try to get the actual connected address from the interface
        actual_address = None
        actual_sanitized = None

        if hasattr(interface, "client") and interface.client is not None:
            # Official version: client has bleak_client attribute
            bleak_client = getattr(interface.client, "bleak_client", None)
            if bleak_client is not None:
                actual_address = getattr(bleak_client, "address", None)
            # Forked version: client might be wrapped differently
            if actual_address is None:
                actual_address = getattr(interface.client, "address", None)

        if actual_address is None:
            logger.warning(
                "Could not determine connected BLE device address for validation. "
                "Proceeding with caution - verify correct device is connected."
            )
            return True

        actual_sanitized = _sanitize_ble_address(actual_address)

        if actual_sanitized == expected_sanitized:
            logger.debug(
                f"BLE connection validation passed: connected to {actual_address} "
                f"(expected: {expected_address})"
            )
            return True
        else:
            logger.error(
                f"BLE CONNECTION VALIDATION FAILED: Connected to {actual_address} "
                f"but expected {expected_address}. This could be caused by "
                "substring matching in device discovery selecting wrong device. "
                "Disconnecting to prevent misconfiguration."
            )
            return False
    except Exception as e:  # noqa: BLE001 - validation is best-effort
        logger.warning(
            f"Error during BLE connection address validation: {e}. "
            "Proceeding with caution."
        )
        return True


def _disconnect_ble_by_address(address: str) -> None:
    """
    Disconnect a potentially stale BlueZ BLE connection for the given address.

    If a BleakClient is available, attempts a graceful disconnect with retries and timeouts.
    Operates correctly from an existing asyncio event loop or by creating a temporary loop
    when none is running. If Bleak is not installed, the function exits silently.

    Parameters:
        address (str): BLE address of the device to disconnect (any common separator format).
    """
    logger.debug(f"Checking for stale BlueZ connection to {address}")

    try:
        from bleak import BleakClient
        from bleak.exc import BleakDBusError as BleakClientDBusError
        from bleak.exc import BleakError as BleakClientError

        async def disconnect_stale_connection() -> None:
            """
            Perform a best-effort disconnect of a stale BlueZ BLE connection for the target address.

            Attempts to detect whether the Bleak client for the configured address is connected and, if so, issues a bounded series of disconnect attempts with timeouts and short settle delays. All errors and timeouts are suppressed (logged at debug/warning levels) so this function never raises; a final cleanup disconnect is always attempted.
            """
            BLEAK_EXCEPTIONS = (
                BleakClientError,
                BleakClientDBusError,
                OSError,
                RuntimeError,
                # Bleak/DBus can raise these during teardown with malformed payloads
                # or unexpected awaitable shapes; cleanup stays best-effort.
                ValueError,
                TypeError,
            )
            client = None
            try:
                client = BleakClient(address)

                connected_status = None
                is_connected_method = getattr(client, "is_connected", None)

                # Bleak exposes either an is_connected() method or a bool attribute,
                # depending on version/backend; treat unknown shapes as disconnected
                # to keep this cleanup best-effort and non-blocking.
                # Bleak backends differ: is_connected may be sync (bool) or async.
                # Handle both to keep this cleanup path resilient to mocks and
                # backend-specific behavior.
                if is_connected_method and callable(is_connected_method):
                    try:
                        connected_result = is_connected_method()
                    except BLEAK_EXCEPTIONS as e:
                        logger.debug(
                            "Failed to call is_connected for %s: %s", address, e
                        )
                        return
                    if inspect.isawaitable(connected_result):
                        connected_status = await cast(Awaitable[bool], connected_result)
                    elif isinstance(connected_result, bool):
                        connected_status = connected_result
                    else:
                        # Unexpected return type; treat as disconnected so cleanup
                        # remains non-blocking in test/mocked environments.
                        connected_status = False
                elif isinstance(is_connected_method, bool):
                    connected_status = is_connected_method
                else:
                    connected_status = False
            except BLEAK_EXCEPTIONS as e:
                # Bleak backends raise a mix of DBus/IO errors; treat them as
                # non-fatal because stale disconnects are best-effort cleanup.
                logger.debug(
                    "Failed to check connection state for %s: %s",
                    address,
                    e,
                    exc_info=True,
                )
                return

            try:
                if connected_status:
                    logger.warning(
                        f"Device {address} is already connected in BlueZ. Disconnecting..."
                    )
                    # Retry logic for disconnect with timeout
                    max_retries = 3
                    for attempt in range(max_retries):
                        try:
                            # Some backends or test doubles return a sync result
                            # from disconnect(); only await when needed.
                            disconnect_result = client.disconnect()
                            if inspect.isawaitable(disconnect_result):
                                await asyncio.wait_for(disconnect_result, timeout=3.0)
                            await asyncio.sleep(2.0)
                            logger.debug(
                                f"Successfully disconnected stale connection to {address} on attempt {attempt + 1}, "
                                f"waiting 2s for BlueZ to settle"
                            )
                            break
                        except asyncio.TimeoutError:
                            if attempt < max_retries - 1:
                                logger.warning(
                                    f"Disconnect attempt {attempt + 1} for {address} timed out, retrying..."
                                )
                                await asyncio.sleep(0.5)
                            else:
                                logger.warning(
                                    f"Disconnect for {address} timed out after {max_retries} attempts"
                                )
                        except BLEAK_EXCEPTIONS as e:
                            # Bleak disconnects can throw DBus/IO errors depending
                            # on adapter state; retry a few times then give up.
                            if attempt < max_retries - 1:
                                logger.warning(
                                    "Disconnect attempt %s for %s failed: %s, retrying...",
                                    attempt + 1,
                                    address,
                                    e,
                                    exc_info=True,
                                )
                                await asyncio.sleep(0.5)
                            else:
                                logger.warning(
                                    "Disconnect for %s failed after %s attempts: %s",
                                    address,
                                    max_retries,
                                    e,
                                    exc_info=True,
                                )
                else:
                    logger.debug(f"Device {address} not currently connected in BlueZ")
            except BLEAK_EXCEPTIONS as e:
                # Stale disconnects are best-effort; do not fail startup/reconnect
                # on cleanup errors from BlueZ/DBus.
                logger.debug(
                    "Error disconnecting stale connection to %s",
                    address,
                    exc_info=e,
                )
            finally:
                try:
                    if client:
                        # Always attempt a short final disconnect to release the
                        # adapter even when we think it's already disconnected.
                        # Some backends or test doubles return a sync result
                        # from disconnect(); only await when needed.
                        disconnect_result = client.disconnect()
                        if inspect.isawaitable(disconnect_result):
                            await asyncio.wait_for(disconnect_result, timeout=2.0)
                        await asyncio.sleep(0.5)
                except asyncio.TimeoutError:
                    logger.debug(f"Final disconnect for {address} timed out (cleanup)")
                except BLEAK_EXCEPTIONS as e:
                    # Ignore disconnect errors during cleanup - connection may already be closed
                    logger.debug(
                        "Final disconnect for %s failed during cleanup",
                        address,
                        exc_info=e,
                    )

        runtime_error: RuntimeError | None = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as e:
            loop = None
            runtime_error = e

        if loop and loop.is_running():
            logger.debug(
                "Found running event loop; scheduling disconnect task for %s",
                address,
            )
            _fire_and_forget(disconnect_stale_connection(), loop=loop)
            return

        if event_loop and getattr(event_loop, "is_running", lambda: False)():
            logger.debug(
                "Using global event loop, waiting for disconnect task for %s",
                address,
            )
            future = asyncio.run_coroutine_threadsafe(
                disconnect_stale_connection(), event_loop
            )
            try:
                future.result(timeout=10.0)
                logger.debug(f"Stale connection disconnect completed for {address}")
            except FuturesTimeoutError:
                logger.warning(
                    f"Stale connection disconnect timed out after 10s for {address}"
                )
                if not future.done():
                    # Cancel the cleanup task so we do not block a new connect
                    # attempt on a hung DBus/Bleak operation.
                    future.cancel()
            return

        # No running event loop in this thread (and no global loop to target);
        # create a temporary loop to perform a blocking best-effort cleanup.
        logger.debug(
            "No running event loop (RuntimeError: %s), creating temporary loop for %s",
            runtime_error,
            address,
        )
        asyncio.run(disconnect_stale_connection())
        logger.debug(f"Stale connection disconnect completed for {address}")
    except ImportError:
        # Bleak is optional in some deployments; skip stale cleanup rather than
        # breaking startup when BLE support isn't installed.
        logger.debug("BleakClient not available for stale connection cleanup")
    except Exception as e:  # noqa: BLE001 - disconnect cleanup must not block startup
        # Other errors during best-effort disconnect (e.g., from future.result() or asyncio.run())
        # are non-fatal; log and continue.
        logger.debug(
            "Error during BLE disconnect cleanup for %s",
            address,
            exc_info=e,
        )


def _disconnect_ble_interface(iface: Any, reason: str = "disconnect") -> None:
    """
    Tear down a BLE interface and release its underlying Bluetooth resources.

    Safely disconnects and closes the provided BLE interface (no-op if `None`), suppressing non-fatal errors
    and ensuring the Bluetooth adapter has time to release the connection.

    Parameters:
        iface (Any): BLE interface instance to disconnect; may be `None`.
        reason (str): Short human-readable reason included in log messages.
    """
    if iface is None:
        return

    # Pre-disconnect delay to allow pending notifications to complete
    # This helps prevent "Unexpected EOF on notification file handle" errors
    logger.debug(f"Waiting before disconnecting BLE interface ({reason})")
    time.sleep(0.5)
    timeout_log_level = logging.DEBUG if reason == "shutdown" else logging.WARNING
    retry_log = logger.debug if reason == "shutdown" else logger.warning
    final_log = logger.debug if reason == "shutdown" else logger.error

    try:
        if hasattr(iface, "_exit_handler") and iface._exit_handler:
            # Best-effort: avoid atexit callbacks blocking shutdown when the
            # official library registers close handlers we already ran.
            with contextlib.suppress(Exception):
                atexit.unregister(iface._exit_handler)
            iface._exit_handler = None

        # Check if interface has a disconnect method (forked version)
        if hasattr(iface, "disconnect"):
            logger.debug(f"Disconnecting BLE interface ({reason})")

            # Retry logic for disconnect operations
            max_disconnect_retries = 3
            for attempt in range(max_disconnect_retries):
                try:
                    disconnect_method = iface.disconnect
                    if inspect.iscoroutinefunction(disconnect_method):
                        _wait_for_result(disconnect_method(), timeout=3.0)
                    else:
                        # Run sync disconnect in a daemon thread to avoid hangs.
                        def _disconnect_sync(
                            method: Callable[[], Any] = disconnect_method,
                        ) -> None:
                            """
                            Call the provided disconnect callable and wait briefly if it returns an awaitable.

                            Parameters:
                                method (Callable[[], Any]): A zero-argument callable that performs a disconnect. If omitted, a module-level
                                    default `disconnect_method` is used. If the callable returns an awaitable, this function will wait up to
                                    3.0 seconds for completion.
                            """
                            result = method()
                            if inspect.isawaitable(result):
                                _wait_for_result(result, timeout=3.0)

                        _run_blocking_with_timeout(
                            _disconnect_sync,
                            timeout=3.0,
                            label=f"ble-interface-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                        )
                    # Give the adapter time to complete the disconnect
                    time.sleep(1.0)
                    logger.debug(
                        f"BLE interface disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_disconnect_retries - 1:
                        retry_log(
                            f"BLE interface disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        time.sleep(0.5)
                    else:
                        final_log(
                            f"BLE interface disconnect failed after {max_disconnect_retries} attempts ({reason}): {e}"
                        )
        else:
            logger.debug(
                f"BLE interface has no disconnect() method, using close() only ({reason})"
            )

        # Always call close() to release resources
        logger.debug(f"Closing BLE interface ({reason})")

        # For BLE interfaces, explicitly disconnect the underlying BleakClient
        # to prevent stale connections in BlueZ (official library bug)
        if hasattr(iface, "client"):
            logger.debug(f"Explicitly disconnecting BLE client ({reason})")

            # Retry logic for client disconnect
            max_client_retries = 2
            for attempt in range(max_client_retries):
                try:
                    disconnect_method = iface.client.disconnect
                    if (
                        hasattr(iface.client, "_exit_handler")
                        and iface.client._exit_handler
                    ):
                        with contextlib.suppress(Exception):
                            atexit.unregister(iface.client._exit_handler)
                        iface.client._exit_handler = None
                    with contextlib.suppress(Exception):
                        atexit.unregister(disconnect_method)

                    if inspect.iscoroutinefunction(disconnect_method):
                        _wait_for_result(disconnect_method(), timeout=2.0)
                    else:
                        # Run sync disconnect in a daemon thread so it cannot
                        # block shutdown if BlueZ/DBus is hung.
                        def _disconnect_sync(
                            method: Callable[[], Any] = disconnect_method,
                        ) -> None:
                            """
                            Call a disconnection callable and, if it returns an awaitable, wait up to 2 seconds for it to complete.

                            Parameters:
                                method (Callable[[], Any]): A synchronous or asynchronous disconnect callable to invoke. If it returns an awaitable, this function will wait up to 2.0 seconds for completion.
                            """
                            result = method()
                            if inspect.isawaitable(result):
                                _wait_for_result(result, timeout=2.0)

                        _run_blocking_with_timeout(
                            _disconnect_sync,
                            timeout=2.0,
                            label=f"ble-client-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                        )
                    time.sleep(1.0)
                    logger.debug(
                        f"BLE client disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_client_retries - 1:
                        retry_log(
                            f"BLE client disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        time.sleep(0.3)
                    else:
                        # Ignore disconnect errors on final attempt - connection may already be closed
                        logger.debug(
                            f"BLE client disconnect failed after {max_client_retries} attempts ({reason}): {e}"
                        )

        close_method = iface.close
        with contextlib.suppress(Exception):
            atexit.unregister(close_method)
        if inspect.iscoroutinefunction(close_method):
            _wait_for_result(close_method(), timeout=5.0)
        else:
            # Close can block indefinitely in the official library; run it in
            # a daemon thread with a timeout to allow clean shutdown.
            def _close_sync(method: Callable[[], Any] = close_method) -> None:
                """
                Invoke a close-like callable and, if it returns an awaitable, wait up to 5 seconds for it to complete.

                Parameters:
                    method (Callable[[], Any]): A zero-argument function that performs a close/teardown action. If the callable returns an awaitable, this function will wait up to 5.0 seconds for completion.
                """
                result = method()
                if inspect.isawaitable(result):
                    _wait_for_result(result, timeout=5.0)

            _run_blocking_with_timeout(
                _close_sync,
                timeout=5.0,
                label=f"ble-interface-close-{reason}",
                timeout_log_level=timeout_log_level,
            )
    except TimeoutError as exc:
        logger.debug("BLE interface %s timed out: %s", reason, exc)
    except Exception as e:  # noqa: BLE001 - cleanup must not block shutdown
        logger.debug(f"Error during BLE interface {reason}", exc_info=e)
    finally:
        # Small delay to ensure the adapter has fully released the connection
        time.sleep(0.5)


def _get_packet_details(
    decoded: dict[str, Any] | None, packet: dict[str, Any], portnum_name: str
) -> dict[str, Any]:
    """
    Extract telemetry, signal, relay, and priority fields from a Meshtastic packet for logging.

    Parameters:
        decoded: Decoded packet payload (may be None); used to extract telemetry fields when present.
        packet: Full packet dictionary; used to extract signal (RSSI/SNR), relay, and priority information.
        portnum_name: Port identifier name (e.g., "TELEMETRY_APP") that determines telemetry parsing.

    Returns:
        dict: Mapping of short detail keys to formatted string values (e.g., 'batt': '85%', 'signal': 'RSSI:-70 SNR:7.5').
    """
    details = {}

    if decoded and isinstance(decoded, dict) and portnum_name == "TELEMETRY_APP":
        if (telemetry := decoded.get("telemetry")) and isinstance(telemetry, dict):
            if (metrics := telemetry.get("deviceMetrics")) and isinstance(
                metrics, dict
            ):
                if (batt := metrics.get("batteryLevel")) is not None:
                    details["batt"] = f"{batt}%"
                if (voltage := metrics.get("voltage")) is not None:
                    details["voltage"] = f"{voltage:.2f}V"
            elif (metrics := telemetry.get("environmentMetrics")) and isinstance(
                metrics, dict
            ):
                if (temp := metrics.get("temperature")) is not None:
                    details["temp"] = f"{temp:.1f}°C"
                if (humidity := metrics.get("relativeHumidity")) is not None:
                    details["humidity"] = f"{humidity:.0f}%"

    signal_info = []
    rssi = packet.get("rxRssi")
    if rssi is not None:
        signal_info.append(f"RSSI:{rssi}")
    snr = packet.get("rxSnr")
    if snr is not None:
        signal_info.append(f"SNR:{snr:.1f}")
    if signal_info:
        details["signal"] = " ".join(signal_info)

    relay = packet.get("relayNode")
    if relay is not None and relay != 0:
        details["relayed"] = f"via {relay}"

    priority = packet.get("priority")
    if priority and priority != "NORMAL":
        details["priority"] = priority

    return details


def _get_portnum_name(portnum: Any) -> str:
    """
    Get a human-readable name for a Meshtastic port identifier.

    Accepts an integer enum value, a string name, or None. For a valid enum integer returns the enum name; for a non-empty string returns it unchanged; for None, an empty string, an unknown integer, or an unexpected type returns a descriptive "UNKNOWN (...)" string.

    Parameters:
        portnum (Any): The port identifier to convert; may be an int enum value, a string name, or None.

    Returns:
        str: The resolved port name or an `UNKNOWN (...)` description for invalid or missing inputs.
    """
    if portnum is None:
        return "UNKNOWN (None)"

    if isinstance(portnum, str):
        if portnum:
            return portnum
        return "UNKNOWN (empty string)"

    if isinstance(portnum, int):
        try:
            return portnums_pb2.PortNum.Name(portnum)  # type: ignore[no-any-return]
        except ValueError:
            return f"UNKNOWN (portnum={portnum})"

    return f"UNKNOWN (type={type(portnum).__name__})"


def serial_port_exists(port_name: str) -> bool:
    """
    Determine whether a serial port with the given device name exists on the system.

    Parameters:
        port_name (str): Device name to check (e.g., '/dev/ttyUSB0' on Unix or 'COM3' on Windows).

    Returns:
        `True` if a matching port device name is present, `False` otherwise.
    """
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return port_name in ports


def connect_meshtastic(
    passed_config: dict[str, Any] | None = None,
    force_connect: bool = False,
) -> Any:
    """
    Establishes a Meshtastic client connection using the configured connection type (serial, BLE, or TCP).

    On success updates the module-level client state (meshtastic_client), may update matrix_rooms when a config is provided, and subscribes to meshtastic receive and connection-lost events once for the process lifetime. Honors shutdown and reconnect state and will respect `force_connect` to replace an existing connection.

    Parameters:
        passed_config (dict[str, Any] | None): Optional configuration to use in place of the module-level config; if provided and contains "matrix_rooms", that value will be used to update module-level matrix_rooms.
        force_connect (bool): If True, forces creating a new connection even if a client already exists.

    Returns:
        The connected Meshtastic client instance on success, or `None` if a connection could not be established or shutdown is in progress.
    """
    global meshtastic_client, meshtastic_iface, shutting_down, reconnecting, config
    global matrix_rooms, _ble_future, _ble_future_address
    if shutting_down:
        logger.debug("Shutdown in progress. Not attempting to connect.")
        return None

    if reconnecting and not force_connect:
        logger.debug("Reconnection already in progress. Not attempting new connection.")
        return None

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

        # If config is valid, extract matrix_rooms
        if config and "matrix_rooms" in config:
            matrix_rooms = config["matrix_rooms"]

    with meshtastic_lock:
        if meshtastic_client and not force_connect:
            return meshtastic_client

        # Close previous connection if exists
        if meshtastic_client:
            try:
                if meshtastic_client is meshtastic_iface:
                    # BLE needs an explicit disconnect to release BlueZ state; a
                    # plain close() can leave the adapter "busy" for the next
                    # connect attempt.
                    _disconnect_ble_interface(meshtastic_iface, reason="reconnect")
                    meshtastic_iface = None
                else:
                    meshtastic_client.close()
            except Exception as e:
                logger.warning(
                    "Error closing previous connection: %s", e, exc_info=True
                )
            meshtastic_client = None

        # Check if config is available
        if config is None:
            logger.error("No configuration available. Cannot connect to Meshtastic.")
            return None

        # Check if meshtastic config section exists
        if (
            CONFIG_SECTION_MESHTASTIC not in config
            or config[CONFIG_SECTION_MESHTASTIC] is None
        ):
            logger.error(
                "No Meshtastic configuration section found. Cannot connect to Meshtastic."
            )
            return None

        # Check if connection_type is specified
        if (
            CONFIG_KEY_CONNECTION_TYPE not in config[CONFIG_SECTION_MESHTASTIC]
            or config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE] is None
        ):
            logger.error(
                "No connection type specified in Meshtastic configuration. Cannot connect to Meshtastic."
            )
            return None

        # Determine connection type and attempt connection
        connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

        # Support legacy "network" connection type (now "tcp")
        if connection_type == CONNECTION_TYPE_NETWORK:
            connection_type = CONNECTION_TYPE_TCP
            logger.warning(
                "Using 'network' connection type (legacy). 'tcp' is now the preferred name and 'network' will be deprecated in a future version."
            )

    # Move retry loop outside the lock to prevent blocking other threads
    meshtastic_settings = config.get("meshtastic", {}) if config else {}
    retry_limit_raw = meshtastic_settings.get("retries")
    if retry_limit_raw is None:
        retry_limit_raw = meshtastic_settings.get("retry_limit", INFINITE_RETRIES)
        if "retry_limit" in meshtastic_settings:
            logger.warning(
                "'retry_limit' is deprecated in meshtastic config; use 'retries' instead"
            )
    try:
        retry_limit = int(retry_limit_raw)
    except (TypeError, ValueError):
        retry_limit = INFINITE_RETRIES
    attempts = 0
    timeout_attempts = 0
    successful = False
    ble_scan_after_failure = False
    ble_scan_reason: str | None = None

    # Get timeout configuration (default: DEFAULT_MESHTASTIC_TIMEOUT)
    timeout_raw = meshtastic_settings.get(
        CONFIG_KEY_TIMEOUT, DEFAULT_MESHTASTIC_TIMEOUT
    )
    try:
        timeout = int(timeout_raw)
        if timeout <= 0:
            logger.warning(
                "Non-positive meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
            timeout = DEFAULT_MESHTASTIC_TIMEOUT
    except (TypeError, ValueError):
        # None or invalid value - use default silently
        if timeout_raw is not None:
            logger.warning(
                "Invalid meshtastic.timeout value %r; using %ss fallback.",
                timeout_raw,
                DEFAULT_MESHTASTIC_TIMEOUT,
            )
        timeout = DEFAULT_MESHTASTIC_TIMEOUT

    while (
        not successful
        and (retry_limit == 0 or attempts <= retry_limit)
        and not shutting_down
    ):
        try:
            client = None
            ble_address: str | None = None
            supports_auto_reconnect = False
            if connection_type == CONNECTION_TYPE_SERIAL:
                # Serial connection
                serial_port = config["meshtastic"].get(CONFIG_KEY_SERIAL_PORT)
                if not serial_port:
                    logger.error(
                        "No serial port specified in Meshtastic configuration."
                    )
                    return None

                logger.info(f"Connecting to serial port {serial_port}")

                # Check if serial port exists before connecting
                if not serial_port_exists(serial_port):
                    raise serial.SerialException(
                        f"Serial port {serial_port} does not exist."
                    )

                client = meshtastic.serial_interface.SerialInterface(
                    serial_port, timeout=timeout
                )

            elif connection_type == CONNECTION_TYPE_BLE:
                # BLE connection
                ble_address = config["meshtastic"].get(CONFIG_KEY_BLE_ADDRESS)
                if ble_address:
                    logger.info(f"Connecting to BLE address {ble_address}")

                    iface = None
                    supports_auto_reconnect = False
                    with meshtastic_iface_lock:
                        # If BLE address has changed, re-create the interface
                        if (
                            meshtastic_iface
                            and getattr(meshtastic_iface, "address", None)
                            != ble_address
                        ):
                            old_address = getattr(
                                meshtastic_iface, "address", "unknown"
                            )
                            logger.info(
                                f"BLE address has changed from {old_address} to {ble_address}. "
                                "Disconnecting old interface and creating new one."
                            )
                            # Properly disconnect the old interface to ensure sequential connections
                            _disconnect_ble_interface(
                                meshtastic_iface, reason="address change"
                            )
                            meshtastic_iface = None

                        if meshtastic_iface is None:
                            # Disconnect any stale BlueZ connection before creating new interface
                            _disconnect_ble_by_address(ble_address)

                            # Create a single BLEInterface instance for process lifetime
                            sanitized_address = _sanitize_ble_address(ble_address)
                            logger.debug(
                                f"Creating new BLE interface for {ble_address} (sanitized: {sanitized_address})"
                            )
                            # Check if auto_reconnect parameter is supported (forked version)
                            ble_init_sig = inspect.signature(
                                meshtastic.ble_interface.BLEInterface.__init__
                            )
                            ble_kwargs = {
                                "address": ble_address,
                                "noProto": False,
                                "debugOut": None,
                                "noNodes": False,
                                "timeout": timeout,
                            }

                            # Add auto_reconnect only if supported (forked version)
                            supports_auto_reconnect = (
                                "auto_reconnect" in ble_init_sig.parameters
                            )
                            if supports_auto_reconnect:
                                ble_kwargs["auto_reconnect"] = False
                                logger.debug(
                                    "Using forked meshtastic library with auto_reconnect=False "
                                    "to ensure sequential connections"
                                )
                            else:
                                logger.debug(
                                    "Using official meshtastic library (auto_reconnect not available)"
                                )
                            if ble_scan_after_failure and not supports_auto_reconnect:
                                logger.debug(
                                    "Scanning for BLE device before retrying %s (%s)",
                                    ble_address,
                                    ble_scan_reason or "previous failure",
                                )
                                _scan_for_ble_address(
                                    ble_address, _BLE_SCAN_TIMEOUT_SECS
                                )
                                ble_scan_after_failure = False
                                ble_scan_reason = None

                            try:
                                # Create BLE interface with timeout protection to prevent indefinite hangs
                                # Use ThreadPoolExecutor to run with timeout, as BLEInterface.__init__
                                # can potentially block indefinitely if BlueZ is in a bad state
                                def create_ble_interface(
                                    kwargs: dict[str, Any],
                                ) -> Any:
                                    """
                                    Create a BLEInterface configured for Meshtastic BLE connections.

                                    Parameters:
                                        kwargs (dict): Keyword arguments forwarded to the Meshtastic BLEInterface constructor (e.g., `address`, `adapter`, `auto_reconnect`, `timeout`). Valid keys depend on the Meshtastic BLEInterface implementation.

                                    Returns:
                                        BLEInterface: A newly constructed Meshtastic BLEInterface instance.
                                    """
                                    return meshtastic.ble_interface.BLEInterface(
                                        **kwargs
                                    )

                                # Use 90-second timeout (3x 30s connection timeout + overhead)
                                # This provides multiple retry cycles while ensuring eventual failure
                                # if connection truly cannot be established.
                                #
                                # Guard against overlapping BLE tasks: if a previous BLE operation is
                                # still running (often due to a hung BlueZ/DBus call), we skip queuing
                                # a new task. Raising TimeoutError here intentionally reuses the
                                # existing retry/backoff logic rather than silently proceeding.
                                with _ble_executor_lock:
                                    # Check if shutting down before submitting new BLE tasks
                                    if shutting_down:
                                        logger.debug(
                                            "Skipping BLE interface creation for %s (shutting down)",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation cancelled for {ble_address} (shutting down)."
                                        )

                                    if _ble_future and not _ble_future.done():
                                        logger.debug(
                                            "BLE worker busy; skipping interface creation for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation already in progress for {ble_address}."
                                        )
                                    try:
                                        future = _get_ble_executor().submit(
                                            create_ble_interface, ble_kwargs
                                        )
                                    except RuntimeError as exc:
                                        # The shared executor can be shutting down during interpreter
                                        # teardown; treat this as a timeout so retry logic applies.
                                        logger.exception(
                                            "BLE interface creation submission failed for %s",
                                            ble_address,
                                        )
                                        raise TimeoutError(
                                            f"BLE interface creation could not be scheduled for {ble_address}."
                                        ) from exc
                                    _ble_future = future
                                    _ble_future_address = ble_address
                                future.add_done_callback(_clear_ble_future)
                                try:
                                    meshtastic_iface = future.result(timeout=90.0)
                                    logger.debug(
                                        f"BLE interface created successfully for {ble_address}"
                                    )
                                    if hasattr(meshtastic_iface, "auto_reconnect"):
                                        supports_auto_reconnect = True
                                except FuturesTimeoutError as err:
                                    # Use logger.exception so we retain the timeout context (TRY400),
                                    # but keep the raised exception concise (TRY003) and emit guidance
                                    # as separate log lines for operators.
                                    logger.exception(
                                        "BLE interface creation timed out after 90 seconds for %s.",
                                        ble_address,
                                    )
                                    logger.warning(
                                        "This may indicate a stale BlueZ connection or Bluetooth adapter issue."
                                    )
                                    logger.warning(
                                        BLE_TROUBLESHOOTING_GUIDANCE.format(
                                            ble_address=ble_address
                                        )
                                    )
                                    # Best-effort cancellation: if the worker is hung we cannot force
                                    # it to stop, but this signals intent and lets retries proceed
                                    # only if the future transitions to done/cancelled.
                                    if future.cancel():
                                        _clear_ble_future(future)
                                    else:
                                        _schedule_ble_future_cleanup(
                                            future,
                                            ble_address,
                                            reason="interface creation timeout",
                                        )
                                        timeout_count = _record_ble_timeout(ble_address)
                                        _maybe_reset_ble_executor(
                                            ble_address, timeout_count
                                        )
                                    meshtastic_iface = None
                                    raise TimeoutError(
                                        f"BLE connection attempt timed out for {ble_address}."
                                    ) from err
                            except Exception:
                                # BLEInterface constructor failed - this is a critical error
                                logger.exception("BLE interface creation failed")
                                raise
                        else:
                            logger.debug(
                                f"Reusing existing BLE interface for {ble_address}"
                            )
                            if hasattr(meshtastic_iface, "auto_reconnect"):
                                supports_auto_reconnect = True
                            else:
                                try:
                                    existing_sig = inspect.signature(
                                        type(meshtastic_iface).__init__
                                    )
                                    supports_auto_reconnect = (
                                        "auto_reconnect" in existing_sig.parameters
                                    )
                                except (TypeError, ValueError):
                                    supports_auto_reconnect = False

                        iface = meshtastic_iface

                    # Connect outside singleton-creation lock to avoid blocking other threads
                    # Official version connects during init; forked version needs explicit
                    # connect(). Skipping connect for official avoids calling connect() with
                    # an implicit None address, which can fail reconnection.
                    if (
                        iface is not None
                        and supports_auto_reconnect
                        and hasattr(iface, "connect")
                    ):
                        logger.info(
                            f"Initiating BLE connection to {ble_address} (sequential mode)"
                        )

                        # Add timeout protection for connect() call to prevent indefinite hangs
                        # Use ThreadPoolExecutor with 30-second timeout (same as CONNECTION_TIMEOUT)
                        def connect_iface(iface_param: Any) -> None:
                            """
                            Establishes the given interface by invoking its no-argument `connect()` method.

                            Parameters:
                                iface_param (Any): An interface-like object whose `connect()` method will be called to open the underlying connection.
                            """
                            iface_param.connect()

                        with _ble_executor_lock:
                            # Check if shutting down before submitting connect() tasks
                            if shutting_down:
                                logger.debug(
                                    "Skipping BLE connect() for %s (shutting down)",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect cancelled for {ble_address} (shutting down)."
                                )

                            if _ble_future and not _ble_future.done():
                                logger.debug(
                                    "BLE worker busy; skipping connect() for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect already in progress for {ble_address}."
                                )
                            try:
                                connect_future = _get_ble_executor().submit(
                                    connect_iface, iface
                                )
                            except RuntimeError as exc:
                                logger.exception(
                                    "BLE connect() submission failed for %s",
                                    ble_address,
                                )
                                raise TimeoutError(
                                    f"BLE connect could not be scheduled for {ble_address}."
                                ) from exc
                            _ble_future = connect_future
                            _ble_future_address = ble_address
                        connect_future.add_done_callback(_clear_ble_future)
                        try:
                            connect_future.result(timeout=30.0)
                            logger.info(f"BLE connection established to {ble_address}")
                        except FuturesTimeoutError as err:
                            # Use logger.exception so timeouts include stack context (TRY400),
                            # but raise a short error and keep operator guidance in logs (TRY003).
                            logger.exception(
                                "BLE connect() call timed out after 30 seconds for %s.",
                                ble_address,
                            )
                            logger.warning(
                                "This may indicate a BlueZ or adapter issue."
                            )
                            logger.warning(
                                f"BlueZ may be in a bad state. {BLE_TROUBLESHOOTING_GUIDANCE.format(ble_address=ble_address)}"
                            )
                            # Best-effort cancellation: a hung BLE connect blocks the worker
                            # thread, so we cancel to allow retries only if it completes.
                            if connect_future.cancel():
                                _clear_ble_future(connect_future)
                            else:
                                _schedule_ble_future_cleanup(
                                    connect_future,
                                    ble_address,
                                    reason="connect timeout",
                                )
                                timeout_count = _record_ble_timeout(ble_address)
                                _maybe_reset_ble_executor(ble_address, timeout_count)
                            # Don't use iface if connect() timed out - it may be in an inconsistent state
                            iface = None
                            meshtastic_iface = None
                            raise TimeoutError(
                                f"BLE connect() timed out for {ble_address}."
                            ) from err
                    elif iface is not None and hasattr(iface, "connect"):
                        logger.debug(
                            "Skipping explicit BLE connect for official library; "
                            "interface connects during init for %s",
                            ble_address,
                        )

                    client = iface
                else:
                    logger.error("No BLE address provided.")
                    return None

            elif connection_type == CONNECTION_TYPE_TCP:
                # TCP connection
                target_host = config["meshtastic"].get(CONFIG_KEY_HOST)
                if not target_host:
                    logger.error(
                        "No host specified in Meshtastic configuration for TCP connection."
                    )
                    return None

                logger.info(f"Connecting to host {target_host}")

                # Connect without progress indicator
                client = meshtastic.tcp_interface.TCPInterface(
                    hostname=target_host, timeout=timeout
                )
            else:
                logger.error(f"Unknown connection type: {connection_type}")
                return None

            successful = True

            # Acquire lock only for the final setup and subscription
            with meshtastic_lock:
                meshtastic_client = client

                # CRITICAL VALIDATION: Verify we're connected to the correct BLE device.
                # This prevents connection to wrong device due to substring matching
                # bugs in meshtastic library's find_device() function. The official
                # version uses substring matching: `address in (device.name, device.address)`
                # which can match a non-target device if its name contains to
                # configured address as a substring.
                #
                # Example vulnerability scenario:
                # - Configured address: AA:BB:CC:DD:EE:FF (Meshtastic device)
                # - Nearby device name: "AA:BB:CC:DD:EE:FF-Sensor" (car/handset/etc)
                # - Result: Bot incorrectly matches and connects to non-Meshtastic device
                #
                # This validation works with both official and forked meshtastic versions.
                # If validation fails, we disconnect immediately to prevent further issues.
                if connection_type == CONNECTION_TYPE_BLE:
                    expected_ble_address = config["meshtastic"].get(
                        CONFIG_KEY_BLE_ADDRESS
                    )
                    if expected_ble_address and not _validate_ble_connection_address(
                        meshtastic_client, expected_ble_address
                    ):
                        # Validation failed - wrong device connected
                        # Disconnect immediately to prevent communication with wrong device
                        logger.error(
                            "BLE connection validation failed - connected to wrong device. "
                            "Disconnecting and raising error to force retry."
                        )
                        try:
                            if meshtastic_client is meshtastic_iface:
                                # BLE interface - use proper disconnect sequence
                                _disconnect_ble_interface(
                                    meshtastic_iface, reason="address validation failed"
                                )
                            else:
                                meshtastic_client.close()
                        except Exception as e:
                            logger.warning(f"Error closing invalid BLE connection: {e}")
                        raise ConnectionRefusedError(
                            f"Connected to wrong BLE device. Expected: {expected_ble_address}"
                        )

                nodeInfo = meshtastic_client.getMyNodeInfo()

                # Safely access node info fields
                user_info = nodeInfo.get("user", {}) if nodeInfo else {}
                short_name = user_info.get("shortName", "unknown")
                hw_model = user_info.get("hwModel", "unknown")

                # Get firmware version from device metadata
                metadata = _get_device_metadata(meshtastic_client)
                firmware_version = metadata["firmware_version"]

                if metadata.get("success"):
                    logger.info(
                        f"Connected to {short_name} / {hw_model} / Meshtastic Firmware version {firmware_version}"
                    )
                else:
                    logger.info(f"Connected to {short_name} / {hw_model}")
                    logger.debug(
                        "Device firmware version unavailable from getMetadata()"
                    )

                # Subscribe to message and connection lost events (only once per application run)
                global subscribed_to_messages, subscribed_to_connection_lost
                if not subscribed_to_messages:
                    pub.subscribe(on_meshtastic_message, "meshtastic.receive")
                    subscribed_to_messages = True
                    logger.debug("Subscribed to meshtastic.receive")

                if not subscribed_to_connection_lost:
                    pub.subscribe(
                        on_lost_meshtastic_connection, "meshtastic.connection.lost"
                    )
                    subscribed_to_connection_lost = True
                    logger.debug("Subscribed to meshtastic.connection.lost")

        except (ConnectionRefusedError, MemoryError):
            # Handle critical errors that should not be retried
            logger.exception("Critical connection error")
            return None
        except (FuturesTimeoutError, TimeoutError) as e:
            if shutting_down:
                break
            attempts += 1
            if retry_limit == INFINITE_RETRIES:
                timeout_attempts += 1
                if timeout_attempts > MAX_TIMEOUT_RETRIES_INFINITE:
                    logger.exception(
                        "Connection timed out after %s attempts (unlimited retries); aborting",
                        attempts,
                    )
                    return None
            elif attempts > retry_limit:
                logger.exception("Connection failed after %s attempts", attempts)
                return None

            wait_time = min(2**attempts, 60)
            logger.warning(
                "Connection attempt %s timed out (%s). Retrying in %s seconds...",
                attempts,
                e,
                wait_time,
            )
            time.sleep(wait_time)
        except (serial.SerialException, BleakDBusError, BleakError) as e:
            # Handle specific connection errors
            if shutting_down:
                logger.debug("Shutdown in progress. Aborting connection attempts.")
                break
            attempts += 1
            if retry_limit == 0 or attempts <= retry_limit:
                wait_time = min(2**attempts, 60)  # Consistent exponential backoff
                logger.warning(
                    "Connection attempt %s failed: %s. Retrying in %s seconds...",
                    attempts,
                    e,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.exception("Connection failed after %s attempts", attempts)
                return None
        except Exception as e:
            if shutting_down:
                logger.debug("Shutdown in progress. Aborting connection attempts.")
                break
            attempts += 1
            if (
                connection_type == CONNECTION_TYPE_BLE
                and ble_address
                and not supports_auto_reconnect
                and _is_ble_discovery_error(e)
            ):
                ble_scan_after_failure = True
                ble_scan_reason = type(e).__name__
            if retry_limit == 0 or attempts <= retry_limit:
                wait_time = min(2**attempts, 60)
                logger.warning(
                    "An unexpected error occurred on attempt %s: %s. Retrying in %s seconds...",
                    attempts,
                    e,
                    wait_time,
                )
                time.sleep(wait_time)
            else:
                logger.exception("Connection failed after %s attempts", attempts)
                return None

    return meshtastic_client


def on_lost_meshtastic_connection(
    interface: Any = None,
    detection_source: str = "unknown",
) -> None:
    """
    Mark the Meshtastic connection as lost, close the current client, and initiate an asynchronous reconnect.

    If a shutdown is in progress or a reconnect is already underway this function returns immediately. Otherwise it:
    - sets the module-level `reconnecting` flag,
    - attempts to close and clear the module-level `meshtastic_client` (handles already-closed file descriptors),
    - schedules the reconnect() coroutine on the global event loop if that loop exists and is open.

    Parameters:
        detection_source (str): Identifier for where or how the loss was detected; used in log messages.
    """
    global meshtastic_client, meshtastic_iface, reconnecting, shutting_down, event_loop, reconnect_task, _ble_future, _ble_future_address
    with meshtastic_lock:
        if shutting_down:
            logger.debug("Shutdown in progress. Not attempting to reconnect.")
            return
        if reconnecting:
            logger.debug(
                "Reconnection already in progress. Skipping additional reconnection attempt."
            )
            return
        reconnecting = True
        logger.error(f"Lost connection ({detection_source}). Reconnecting...")

        if meshtastic_client:
            if meshtastic_client is meshtastic_iface:
                # This is a BLE interface - use proper disconnect sequence
                logger.debug("Disconnecting BLE interface due to connection loss")
                _disconnect_ble_interface(
                    meshtastic_iface, reason=f"connection loss: {detection_source}"
                )
                meshtastic_iface = None
            else:
                # Serial or TCP interface - use standard close()
                try:
                    meshtastic_client.close()
                except OSError as e:
                    if e.errno == ERRNO_BAD_FILE_DESCRIPTOR:
                        # Bad file descriptor, already closed
                        pass
                    else:
                        logger.warning(f"Error closing Meshtastic client: {e}")
                except Exception as e:
                    logger.warning(f"Error closing Meshtastic client: {e}")
        meshtastic_client = None
        with _ble_executor_lock:
            if _ble_future and not _ble_future.done():
                logger.debug(
                    "Clearing stale BLE future before reconnect (%s)",
                    detection_source,
                )
                _ble_future = None
                if _ble_future_address:
                    with _ble_timeout_lock:
                        _ble_timeout_counts.pop(_ble_future_address, None)
                _ble_future_address = None

        if event_loop and not event_loop.is_closed():
            reconnect_task = event_loop.create_task(reconnect())


async def reconnect() -> None:
    """
    Re-establish the Meshtastic connection using exponential backoff.

    Retries connect_meshtastic(force_connect=True) until a connection is obtained, the application begins shutting down, or the task is cancelled. Starts with DEFAULT_BACKOFF_TIME and doubles the wait after each failed attempt, capped at 300 seconds. Stops promptly on cancellation or when shutting_down is set, and ensures the module-level `reconnecting` flag is cleared before returning.
    """
    global meshtastic_client, reconnecting, shutting_down
    backoff_time = DEFAULT_BACKOFF_TIME
    try:
        while not shutting_down:
            try:
                logger.info(
                    f"Reconnection attempt starting in {backoff_time} seconds..."
                )

                # Show reconnection countdown with Rich (if not in a service)
                if not is_running_as_service():
                    try:
                        from rich.progress import (
                            BarColumn,
                            Progress,
                            TextColumn,
                            TimeRemainingColumn,
                        )
                    except ImportError:
                        logger.debug(
                            "Rich not available; falling back to simple reconnection delay"
                        )
                        await asyncio.sleep(backoff_time)
                    else:
                        with Progress(
                            TextColumn("[cyan]Meshtastic: Reconnecting in"),
                            BarColumn(),
                            TextColumn("[cyan]{task.percentage:.0f}%"),
                            TimeRemainingColumn(),
                            transient=True,
                        ) as progress:
                            task = progress.add_task("Waiting", total=backoff_time)
                            for _ in range(backoff_time):
                                if shutting_down:
                                    break
                                await asyncio.sleep(1)
                                progress.update(task, advance=1)
                else:
                    await asyncio.sleep(backoff_time)
                if shutting_down:
                    logger.debug(
                        "Shutdown in progress. Aborting reconnection attempts."
                    )
                    break
                loop = asyncio.get_running_loop()
                # Pass force_connect=True without overwriting the global config
                meshtastic_client = await loop.run_in_executor(
                    None, connect_meshtastic, None, True
                )
                if meshtastic_client:
                    logger.info("Reconnected successfully.")
                    break
            except Exception:
                if shutting_down:
                    break
                logger.exception("Reconnection attempt failed")
                backoff_time = min(backoff_time * 2, 300)  # Cap backoff at 5 minutes
    except asyncio.CancelledError:
        logger.info("Reconnection task was cancelled.")
    finally:
        reconnecting = False


def on_meshtastic_message(packet: dict[str, Any], interface: Any) -> None:
    """
    Route an incoming Meshtastic packet to configured Matrix rooms or installed plugins based on runtime configuration.

    Processes the decoded packet and, depending on interaction settings and packet contents, will relay emoji reactions and replies to mapped Matrix events, dispatch ordinary text messages to Matrix rooms mapped to the packet's channel (unless the message is a direct message to the relay node or handled by a plugin), and hand non-text or unhandled packets to installed plugins with a per-plugin timeout.

    Parameters:
        packet (dict): Decoded Meshtastic packet. Expected keys include:
            - 'decoded' (dict): may contain 'text', 'replyId', 'portnum', and optional 'emoji'
            - 'fromId' or 'from' (sender id)
            - 'to' (destination id)
            - 'id' (packet id)
            - optional 'channel' (mapped channel value)
        interface: Meshtastic interface used to resolve node information and the relay node id. Must provide .myInfo.my_node_num and a .nodes mapping for sender metadata.
    """
    global config, matrix_rooms

    # Validate packet structure
    if not packet or not isinstance(packet, dict):
        logger.error("Received malformed packet: packet is None or not a dict")
        return

    # Log that we received a message (without the full packet details)
    decoded = packet.get("decoded")
    if decoded and isinstance(decoded, dict) and decoded.get("text"):
        logger.info(f"Received Meshtastic message: {decoded.get('text')}")
    else:
        portnum = (
            decoded.get("portnum") if decoded and isinstance(decoded, dict) else None
        )
        portnum_name = _get_portnum_name(portnum)
        details_map = {
            "from": packet.get("fromId") or packet.get("from"),
            "channel": packet.get("channel"),
            "id": packet.get("id"),
        }
        details_map.update(_get_packet_details(decoded, packet, portnum_name))
        details = [f"type={portnum_name}"]
        details.extend(f"{k}={v}" for k, v in details_map.items() if v is not None)
        logger.debug(f"Received non-text Meshtastic message: {', '.join(details)}")

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Meshtastic message.")
        return

    # Import the configuration helpers
    from mmrelay.matrix_utils import get_interaction_settings

    # Get interaction settings
    interactions = get_interaction_settings(config)

    # Filter packets based on interaction settings
    if packet.get("decoded", {}).get("portnum") == TEXT_MESSAGE_APP:
        decoded = packet.get("decoded", {})
        # Filter out reactions if reactions are disabled
        if (
            not interactions["reactions"]
            and "emoji" in decoded
            and decoded.get("emoji") == EMOJI_FLAG_VALUE
        ):
            logger.debug(
                "Filtered out reaction packet due to reactions being disabled."
            )
            return

    from mmrelay.matrix_utils import matrix_relay

    global event_loop

    if shutting_down:
        logger.debug("Shutdown in progress. Ignoring incoming messages.")
        return

    if event_loop is None:
        logger.error("Event loop is not set. Cannot process message.")
        return

    loop = event_loop

    sender = packet.get("fromId") or packet.get("from")
    toId = packet.get("to")

    decoded = packet.get("decoded", {})
    text = decoded.get("text")
    replyId = decoded.get("replyId")
    emoji_flag = "emoji" in decoded and decoded["emoji"] == EMOJI_FLAG_VALUE

    # Determine if this is a direct message to the relay node
    from meshtastic.mesh_interface import BROADCAST_NUM  # type: ignore[import-untyped]

    if not getattr(interface, "myInfo", None):
        logger.warning("Meshtastic interface missing myInfo; cannot determine node id")
        return
    myId = interface.myInfo.my_node_num

    if toId == myId:
        is_direct_message = True
    elif toId == BROADCAST_NUM or toId is None:
        is_direct_message = False
    else:
        logger.debug(
            "Ignoring message intended for node %s (not broadcast or relay).", toId
        )
        return

    meshnet_name = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_MESHNET_NAME]

    # Reaction handling (Meshtastic -> Matrix)
    # If replyId and emoji_flag are present and reactions are enabled, we relay as text reactions in Matrix
    if replyId and emoji_flag and interactions["reactions"]:
        longname = _get_name_safely(get_longname, sender)
        shortname = _get_name_safely(get_shortname, sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig
            abbreviated_text = (
                meshtastic_text[:40] + "..."
                if len(meshtastic_text) > 40
                else meshtastic_text
            )

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reaction
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)

            reaction_symbol = text.strip() if (text and text.strip()) else "⚠️"
            reaction_message = (
                f'\n {prefix}reacted {reaction_symbol} to "{abbreviated_text}"'
            )

            # Relay the reaction as emote to Matrix, preserving the original meshnet name
            _fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    reaction_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=meshtastic_text,
                    emote=True,
                    emoji=True,
                ),
                loop=loop,
            )
        else:
            logger.debug("Original message for reaction not found in DB.")
        return

    # Reply handling (Meshtastic -> Matrix)
    # If replyId is present but emoji is not (or not 1), this is a reply
    if replyId and not emoji_flag and interactions["replies"]:
        longname = _get_name_safely(get_longname, sender)
        shortname = _get_name_safely(get_shortname, sender)
        orig = get_message_map_by_meshtastic_id(replyId)
        if orig:
            # orig = (matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            matrix_event_id, matrix_room_id, meshtastic_text, meshtastic_meshnet = orig

            # Import the matrix prefix function
            from mmrelay.matrix_utils import get_matrix_prefix

            # Get the formatted prefix for the reply
            prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
            formatted_message = f"{prefix}{text}"

            logger.info(f"Relaying Meshtastic reply from {longname} to Matrix")

            # Relay the reply to Matrix with proper reply formatting
            _fire_and_forget(
                matrix_relay(
                    matrix_room_id,
                    formatted_message,
                    longname,
                    shortname,
                    meshnet_name,
                    decoded.get("portnum"),
                    meshtastic_id=packet.get("id"),
                    meshtastic_replyId=replyId,
                    meshtastic_text=text,
                    reply_to_event_id=matrix_event_id,
                ),
                loop=loop,
            )
        else:
            logger.debug("Original message for reply not found in DB.")
        return

    # Normal text messages or detection sensor messages
    if text:
        # Determine the channel for this message
        channel = packet.get("channel")
        if channel is None:
            # If channel not specified, deduce from portnum
            # Note: meshtastic-python emits enum names (e.g., "TEXT_MESSAGE_APP") in decoded dicts,
            # while other paths (protobuf/raw) surface numeric portnums. Support both to avoid drops.
            if decoded.get("portnum") in (
                PORTNUM_TEXT_MESSAGE_APP,
                PORTNUM_DETECTION_SENSOR_APP,
                TEXT_MESSAGE_APP,
                DETECTION_SENSOR_APP,
            ):
                channel = DEFAULT_CHANNEL_VALUE
            else:
                logger.debug(
                    f"Unknown portnum {decoded.get('portnum')}, cannot determine channel"
                )
                return

        # Check if channel is mapped to a Matrix room
        channel_mapped = False
        iterable_rooms = (
            matrix_rooms.values() if isinstance(matrix_rooms, dict) else matrix_rooms
        )
        for room in iterable_rooms:
            if isinstance(room, dict) and room.get("meshtastic_channel") == channel:
                channel_mapped = True
                break

        if not channel_mapped:
            logger.debug(f"Skipping message from unmapped channel {channel}")
            return

        # If detection_sensor is disabled and this is a detection sensor packet, skip it
        portnum = decoded.get("portnum")
        if (
            portnum == PORTNUM_DETECTION_SENSOR_APP or portnum == DETECTION_SENSOR_APP
        ) and not get_meshtastic_config_value(
            config, "detection_sensor", DEFAULT_DETECTION_SENSOR
        ):
            logger.debug(
                "Detection sensor packet received, but detection sensor processing is disabled."
            )
            return

        # Attempt to get longname/shortname from database or nodes
        longname = _get_name_or_none(get_longname, sender)
        if longname is None:
            logger.debug(
                "Failed to get longname from database for %s, will try interface fallback",
                sender,
            )

        shortname = _get_name_or_none(get_shortname, sender)
        if shortname is None:
            logger.debug(
                "Failed to get shortname from database for %s, will try interface fallback",
                sender,
            )

        if not longname or not shortname:
            node = interface.nodes.get(sender)
            if node:
                user = node.get("user")
                if user:
                    if not longname:
                        longname_val = user.get("longName")
                        if longname_val and sender is not None:
                            save_longname(sender, longname_val)
                            longname = longname_val
                    if not shortname:
                        shortname_val = user.get("shortName")
                        if shortname_val and sender is not None:
                            save_shortname(sender, shortname_val)
                            shortname = shortname_val
            else:
                logger.debug(f"Node info for sender {sender} not available yet.")

        # If still not available, fallback to sender ID
        if not longname:
            longname = str(sender)
        if not shortname:
            shortname = str(sender)

        # Import the matrix prefix function
        from mmrelay.matrix_utils import get_matrix_prefix

        # Get the formatted prefix
        prefix = get_matrix_prefix(config, longname, shortname, meshnet_name)
        formatted_message = f"{prefix}{text}"

        # Plugin functionality - Check if any plugin handles this message before relaying
        found_matching_plugin = _run_meshtastic_plugins(
            packet=packet,
            formatted_message=formatted_message,
            longname=longname,
            meshnet_name=meshnet_name,
            loop=loop,
            cfg=config,
        )

        # If message is a DM or handled by plugin, do not relay further
        if is_direct_message:
            logger.debug(
                f"Received a direct message from {longname}: {text}. Not relaying to Matrix."
            )
            return
        if found_matching_plugin:
            logger.debug("Message was handled by a plugin. Not relaying to Matrix.")
            return

        # Relay the message to all Matrix rooms mapped to this channel
        logger.info(f"Relaying Meshtastic message from {longname} to Matrix")

        # Check if matrix_rooms is empty
        if not matrix_rooms:
            logger.error("matrix_rooms is empty. Cannot relay message to Matrix.")
            return

        iterable_rooms = (
            matrix_rooms.values() if isinstance(matrix_rooms, dict) else matrix_rooms
        )
        for room in iterable_rooms:
            if not isinstance(room, dict):
                continue
            if room.get("meshtastic_channel") == channel:
                # Storing the message_map (if enabled) occurs inside matrix_relay() now,
                # controlled by relay_reactions.
                try:
                    _fire_and_forget(
                        matrix_relay(
                            room["id"],
                            formatted_message,
                            longname,
                            shortname,
                            meshnet_name,
                            decoded.get("portnum"),
                            meshtastic_id=packet.get("id"),
                            meshtastic_text=text,
                        ),
                        loop=loop,
                    )
                except Exception:
                    logger.exception("Error relaying message to Matrix")
    else:
        # Non-text messages via plugins
        portnum = decoded.get("portnum")
        _run_meshtastic_plugins(
            packet=packet,
            formatted_message=None,
            longname=None,
            meshnet_name=None,
            loop=loop,
            cfg=config,
            use_keyword_args=True,
            log_with_portnum=True,
            portnum=portnum,
        )


async def check_connection() -> None:
    """
    Periodically verify the Meshtastic connection and trigger a reconnect when the device appears unresponsive.

    Checks run until the module-level `shutting_down` flag is True. Behavior:
    - Controlled by config["meshtastic"]["health_check"]:
      - `enabled` (bool, default True) — enable or disable checks.
      - `heartbeat_interval` (int, seconds, default 60) — interval between checks. For backward compatibility, a top-level `heartbeat_interval` under `config["meshtastic"]` is supported.
    - BLE connections are excluded from periodic checks because BLE libraries provide real-time disconnect detection.
    - For non-BLE connections, attempts a metadata probe (via _get_device_metadata) and, if parsing fails, a fallback probe using `client.getMyNodeInfo()`. If both probes fail and no reconnection is already in progress, calls on_lost_meshtastic_connection(...) to initiate reconnection.

    No return value; side effects are logging and scheduling/triggering reconnection when the device is unresponsive.
    """
    global meshtastic_client, shutting_down, config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot check connection.")
        return

    connection_type = config[CONFIG_SECTION_MESHTASTIC][CONFIG_KEY_CONNECTION_TYPE]

    # Get health check configuration
    health_config = config["meshtastic"].get("health_check", {})
    health_check_enabled = health_config.get("enabled", True)
    heartbeat_interval = health_config.get("heartbeat_interval", 60)

    # Support legacy heartbeat_interval configuration for backward compatibility
    if "heartbeat_interval" in config["meshtastic"]:
        heartbeat_interval = config["meshtastic"]["heartbeat_interval"]

    # Exit early if health checks are disabled
    if not health_check_enabled:
        logger.info("Connection health checks are disabled in configuration")
        return

    ble_skip_logged = False

    while not shutting_down:
        if meshtastic_client and not reconnecting:
            # BLE has real-time disconnection detection in the library
            # Skip periodic health checks to avoid duplicate reconnection attempts
            if connection_type == CONNECTION_TYPE_BLE:
                if not ble_skip_logged:
                    logger.info(
                        "BLE connection uses real-time disconnection detection - health checks disabled"
                    )
                    ble_skip_logged = True
            else:
                try:
                    loop = asyncio.get_running_loop()
                    # Use helper function to get device metadata, run in executor with timeout
                    metadata = await asyncio.wait_for(
                        loop.run_in_executor(
                            None, _get_device_metadata, meshtastic_client
                        ),
                        timeout=DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
                    )
                    if not metadata["success"]:
                        # Fallback probe: device responding at all?
                        try:
                            _ = await asyncio.wait_for(
                                loop.run_in_executor(
                                    None, meshtastic_client.getMyNodeInfo
                                ),
                                timeout=DEFAULT_MESHTASTIC_OPERATION_TIMEOUT,
                            )
                        except Exception as probe_err:
                            raise Exception(
                                "Metadata and nodeInfo probes failed"
                            ) from probe_err
                        else:
                            logger.debug(
                                "Metadata parse failed but device responded to getMyNodeInfo(); skipping reconnect this cycle"
                            )
                            continue

                except Exception as e:
                    # Only trigger reconnection if we're not already reconnecting
                    if not reconnecting:
                        logger.error(
                            f"{connection_type.capitalize()} connection health check failed: {e}"
                        )
                        on_lost_meshtastic_connection(
                            interface=meshtastic_client,
                            detection_source=f"health check failed: {str(e)}",
                        )
                    else:
                        logger.debug(
                            "Skipping reconnection trigger - already reconnecting"
                        )
        elif reconnecting:
            logger.debug("Skipping connection check - reconnection in progress")
        elif not meshtastic_client:
            logger.debug("Skipping connection check - no client available")

        await asyncio.sleep(heartbeat_interval)


def send_text_reply(
    interface: Any,
    text: str,
    reply_id: int,
    destinationId: Any = meshtastic.BROADCAST_ADDR,
    wantAck: bool = False,
    channelIndex: int = 0,
) -> Any:
    """
    Send a Meshtastic text message that references (replies to) a previous Meshtastic message.

    Parameters:
        interface (Any): Meshtastic interface used to send the packet.
        text (str): UTF-8 text to send.
        reply_id (int): ID of the Meshtastic message being replied to.
        destinationId (Any, optional): Recipient address or node ID; defaults to broadcast.
        wantAck (bool, optional): If True, request an acknowledgement for the packet.
        channelIndex (int, optional): Channel index to send the packet on.

    Returns:
        The result returned by the interface's _sendPacket call (typically the sent MeshPacket), or
        `None` if the interface is unavailable or sending fails.
    """
    logger.debug(f"Sending text reply: '{text}' replying to message ID {reply_id}")

    # Check if interface is available
    if interface is None:
        logger.error("No Meshtastic interface available for sending reply")
        return None

    # Create the Data protobuf message with reply_id set
    data_msg = mesh_pb2.Data()
    data_msg.portnum = portnums_pb2.PortNum.TEXT_MESSAGE_APP
    data_msg.payload = text.encode("utf-8")
    data_msg.reply_id = reply_id

    # Create the MeshPacket
    mesh_packet = mesh_pb2.MeshPacket()
    mesh_packet.channel = channelIndex
    mesh_packet.decoded.CopyFrom(data_msg)
    mesh_packet.id = interface._generatePacketId()

    # Send the packet using the existing infrastructure
    try:
        return interface._sendPacket(
            mesh_packet, destinationId=destinationId, wantAck=wantAck
        )
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
    ):
        logger.exception("Failed to send text reply")
        return None
    except SystemExit:
        logger.debug("SystemExit encountered, preserving for graceful shutdown")
        raise


# Backward-compatible alias for older call sites.
sendTextReply = send_text_reply


if __name__ == "__main__":
    # If running this standalone (normally the main.py does the loop), just try connecting and run forever.
    meshtastic_client = connect_meshtastic()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    event_loop = loop  # Set the event loop for use in callbacks
    _check_connection_task = loop.create_task(check_connection())
    loop.run_forever()
