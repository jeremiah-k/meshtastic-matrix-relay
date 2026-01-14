import asyncio
import contextlib
import inspect
import io
import re
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Awaitable, Callable, Coroutine, cast

# meshtastic is not marked py.typed; keep import-untyped for strict mypy.
import meshtastic  # type: ignore[import-untyped]
import meshtastic.ble_interface  # type: ignore[import-untyped]
import meshtastic.serial_interface  # type: ignore[import-untyped]
import meshtastic.tcp_interface  # type: ignore[import-untyped]
import serial  # For serial port exceptions
import serial.tools.list_ports  # Import serial tools for port listing
from meshtastic.protobuf import mesh_pb2, portnums_pb2  # type: ignore[import-untyped]
from pubsub import pub

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

try:
    from bleak import BleakScanner
    from bleak.backends.bluezdbus.manager import DeviceManager

    BLE_AVAILABLE = True
except ImportError:
    BLE_AVAILABLE = False

from mmrelay.db_utils import (
    get_longname,
    get_message_map_by_meshtastic_id,
    get_shortname,
    save_longname,
    save_shortname,
)
from mmrelay.log_utils import get_logger
from mmrelay.runtime_utils import is_running_as_service

# Maximum number of timeout retries when retries are configured as infinite.
MAX_TIMEOUT_RETRIES_INFINITE = 5


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
    if loop and isinstance(loop, asyncio.AbstractEventLoop) and not loop.is_closed():
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


def _wait_for_result(
    result_future: Any,
    timeout: float,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Any:
    """
    Await and return the value of a future-like object from synchronous code, enforcing a timeout.

    Supports concurrent.futures.Future, asyncio.Future/Task, awaitables, and objects exposing a callable `result(timeout)` method. If called when an event loop is running, schedules the awaitable on that loop; otherwise it will run the awaitable on the provided loop or create a temporary loop.

    Parameters:
        result_future (Any): The future/awaitable or future-like object to resolve.
        timeout (float): Maximum seconds to wait for the result.
        loop (asyncio.AbstractEventLoop | None): Optional event loop to use for awaiting; if omitted, a running loop will be used or a temporary loop will be created.

    Returns:
        Any: The value produced by the resolved future/awaitable.

    Raises:
        asyncio.TimeoutError: If awaiting the awaitable times out.
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

    if target_loop and not target_loop.is_closed():
        if target_loop.is_running():
            return asyncio.run_coroutine_threadsafe(_runner(), target_loop).result(
                timeout=timeout
            )
        return target_loop.run_until_complete(_runner())

    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and not running_loop.is_closed():
        if running_loop.is_running():
            return asyncio.run_coroutine_threadsafe(_runner(), running_loop).result(
                timeout=timeout
            )
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
    Extract firmware version and raw metadata output from a Meshtastic client.

    Attempts to invoke client.localNode.getMetadata() (if present), captures its console output, and parses a firmware version string. Returns a dict containing the parsed firmware version, the captured raw output (possibly truncated), and a success flag indicating whether a firmware version was found.

    Parameters:
        client: An object implementing a Meshtastic client interface; expected to provide a localNode with a getMetadata() method. If the method is absent or parsing fails, defaults are returned.

    Returns:
        dict: {
            "firmware_version": str — parsed firmware version or "unknown" when not found,
            "raw_output": str — captured output from getMetadata() (truncated to 4096 characters with a trailing ellipsis if longer),
            "success": bool — `true` when a firmware_version was successfully parsed, `false` otherwise
        }
    """
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

        # Capture getMetadata() output to extract firmware version
        output_capture = io.StringIO()
        with (
            contextlib.redirect_stdout(output_capture),
            contextlib.redirect_stderr(output_capture),
        ):
            client.localNode.getMetadata()

        console_output = output_capture.getvalue()
        output_capture.close()

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

    except Exception as e:
        logger.debug(
            "Could not retrieve device metadata via localNode.getMetadata()", exc_info=e
        )

    return result


def _disconnect_ble_by_address(address: str) -> None:
    """
    Disconnect a BLE device by its address if it's already connected in BlueZ.

    This function handles the case where a device is still connected in BlueZ
    but there's no active meshtastic interface (state inconsistency).

    Parameters:
        address: The BLE address of the device to disconnect.

    Returns:
        None
    """
    if not BLE_AVAILABLE:
        return

    try:
        import asyncio

        from bleak import BleakClient

        logger.debug(f"Checking if device {address} is already connected in BlueZ")

        try:
            client = BleakClient(address)
            is_connected = asyncio.run(client.is_connected())
            if is_connected:
                logger.warning(
                    f"Device {address} is already connected in BlueZ. "
                    f"Disconnecting before creating new interface."
                )
                asyncio.run(client.disconnect())
                time.sleep(0.5)
        except Exception:
            pass

    except ImportError:
        pass


def _disconnect_ble_interface(iface: Any, reason: str = "disconnect") -> None:
    """
    Properly disconnect a BLE interface with appropriate delays to allow the adapter to release resources.

    This function ensures sequential BLE connections by:
    1. Calling disconnect() if available (forked meshtastic version)
    2. Calling close() to release all resources
    3. Adding a delay to allow the Bluetooth adapter to fully release the connection

    Parameters:
        iface: The BLE interface instance to disconnect. Can be None.
        reason: Reason for disconnection, used in log messages.

    Returns:
        None
    """
    if iface is None:
        return

    try:
        # Check if interface has a disconnect method (forked version)
        if hasattr(iface, "disconnect"):
            logger.debug(f"Disconnecting BLE interface ({reason})")
            iface.disconnect()
            # Give the adapter time to complete the disconnect
            time.sleep(1.0)
        else:
            logger.debug(
                f"BLE interface has no disconnect() method, using close() only ({reason})"
            )

        # Always call close() to release resources
        logger.debug(f"Closing BLE interface ({reason})")
        iface.close()
    except Exception as e:
        logger.debug(f"Error during BLE interface {reason}: {e}")
    finally:
        # Small delay to ensure the adapter has fully released the connection
        time.sleep(0.5)


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
    global meshtastic_client, meshtastic_iface, shutting_down, reconnecting, config, matrix_rooms
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
                meshtastic_client.close()
            except Exception as e:
                logger.warning(f"Error closing previous connection: {e}")
            if meshtastic_client is meshtastic_iface:
                meshtastic_iface = None
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
                            logger.debug(
                                f"Creating new BLE interface for {ble_address}"
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
                            if "auto_reconnect" in ble_init_sig.parameters:
                                ble_kwargs["auto_reconnect"] = False
                                logger.debug(
                                    "Using forked meshtastic library with auto_reconnect=False "
                                    "to ensure sequential connections"
                                )
                            else:
                                logger.debug(
                                    "Using official meshtastic library (auto_reconnect not available)"
                                )

                            try:
                                meshtastic_iface = (
                                    meshtastic.ble_interface.BLEInterface(**ble_kwargs)
                                )
                                logger.debug(
                                    f"BLE interface created successfully for {ble_address}"
                                )
                            except Exception:
                                # BLEInterface constructor failed - this is a critical error
                                logger.exception("BLE interface creation failed")
                                raise
                        else:
                            logger.debug(
                                f"Reusing existing BLE interface for {ble_address}"
                            )

                        iface = meshtastic_iface

                    # Connect outside singleton-creation lock to avoid blocking other threads
                    # Official version connects automatically during init (no connect() method)
                    # Forked version has separate connect() method that we need to call
                    if iface is not None and hasattr(iface, "connect"):
                        logger.info(
                            f"Initiating BLE connection to {ble_address} (sequential mode)"
                        )
                        iface.connect()
                        logger.info(f"BLE connection established to {ble_address}")

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
    _interface: Any = None,
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
    global meshtastic_client, meshtastic_iface, reconnecting, shutting_down, event_loop, reconnect_task
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
        logger.debug("Received non-text Meshtastic message")

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
                            _interface=meshtastic_client,
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
