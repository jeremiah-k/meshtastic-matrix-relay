import atexit
import contextlib
import inspect
import logging
from concurrent.futures import Future
from typing import Any, Awaitable, Callable, Coroutine, cast

import mmrelay.meshtastic_utils as facade

__all__ = [
    "_advance_ble_generation",
    "_attach_late_ble_interface_disposer",
    "_discard_ble_iface_generation",
    "_disconnect_ble_by_address",
    "_disconnect_ble_interface",
    "_extract_ble_address_from_interface",
    "_get_ble_generation",
    "_get_ble_iface_generation",
    "_get_ble_unresolved_teardown_generations",
    "_is_ble_discovery_error",
    "_is_ble_duplicate_connect_suppressed_error",
    "_is_ble_generation_stale",
    "_record_ble_teardown_timeout",
    "_register_ble_iface_generation",
    "_resolve_ble_teardown_timeout",
    "_reset_ble_connection_gate_state",
    "_sanitize_ble_address",
    "_scan_for_ble_address",
    "_validate_ble_connection_address",
]


def _is_ble_duplicate_connect_suppressed_error(exc: BaseException) -> bool:
    """
    Return whether an exception message matches Meshtastic duplicate-connect suppression.

    This targets forked meshtastic BLE gate errors such as:
    "Connection suppressed: recently connected elsewhere".
    """
    message = str(exc).strip().lower()
    if not message:
        return False
    return facade.BLE_DUP_CONNECT_SUPPRESSED_TOKEN in message or (
        facade.BLE_CONN_SUPPRESSED_TOKEN in message
        and facade.BLE_CONNECTED_ELSEWHERE_TOKEN in message
    )


def _reset_ble_connection_gate_state(ble_address: str, *, reason: str) -> bool:
    """
    Best-effort reset of process-local BLE connection gate state.

    This recovery hook is only active when the installed Meshtastic library
    exposes a connection-gate reset API. Otherwise this function is a no-op.
    """
    if facade._ble_gate_reset_callable is None:
        return False

    try:
        facade._ble_gate_reset_callable()
    except Exception:
        facade.logger.debug(
            "BLE connection-state reset failed for %s (%s)",
            ble_address,
            reason,
            exc_info=True,
        )
        return False

    facade.logger.warning(
        "Reset BLE connection state for %s (%s)",
        ble_address,
        reason,
    )
    return True


def _attach_late_ble_interface_disposer(
    future: Future[Any],
    ble_address: str,
    reason: str,
    *,
    fallback_iface: Any | None = None,
) -> None:
    """
    Dispose interfaces created by abandoned BLE futures that complete late.

    Timeout paths can drop `_ble_future` while a worker thread is still running.
    If that worker later succeeds, this callback prevents orphaned interfaces
    from remaining connected outside normal ownership.
    """

    def _dispose(done_future: Future[Any]) -> None:
        if done_future.cancelled():
            return

        late_iface = fallback_iface
        try:
            result = done_future.result()
            if result is not None:
                late_iface = result
        except Exception:  # noqa: BLE001 - futures can raise backend-specific errors
            late_iface = fallback_iface

        if late_iface is None:
            return

        if not (hasattr(late_iface, "disconnect") or hasattr(late_iface, "close")):
            return

        with facade.meshtastic_iface_lock:
            active_iface = facade.meshtastic_iface
        if active_iface is late_iface:
            return

        late_address, late_generation = facade._get_ble_iface_generation(
            late_iface,
            fallback_address=ble_address,
        )
        log_address = late_address or ble_address
        late_client_obj = getattr(late_iface, "client", None)

        facade.logger.warning(
            "Cleaning up late BLE interface completion for %s (%s) "
            "(generation=%s iface_id=%s client_id=%s)",
            log_address,
            reason,
            late_generation,
            id(late_iface),
            (id(late_client_obj) if late_client_obj is not None else None),
        )
        try:
            facade._disconnect_ble_interface(
                late_iface,
                reason=f"late completion after {reason}",
                ble_address=late_address,
                generation=late_generation,
            )
        except Exception:  # noqa: BLE001 - cleanup must not propagate
            facade.logger.debug(
                "Late BLE interface cleanup failed for %s (%s)",
                log_address,
                reason,
                exc_info=True,
            )

    future.add_done_callback(_dispose)


def _scan_for_ble_address(ble_address: str, timeout: float) -> bool:
    """
    Performs a best-effort BLE scan to check whether a device with the given address is discoverable.

    If the Bleak library is unavailable or an active asyncio event loop is running, the function does not perform a scan and returns `false`.

    Returns:
        `true` if the device address was observed in a scan within the given timeout; `false` if the device was not observed, the scan failed, Bleak is unavailable, or scanning was skipped due to an active event loop.
    """
    if not facade.BLE_AVAILABLE:
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
                    coro: Coroutine[Any, Any, Any] = cast(
                        Coroutine[Any, Any, Any],
                        find_device(ble_address, timeout=timeout),
                    )
                    used_fallback = False
                except TypeError:
                    coro = cast(
                        Coroutine[Any, Any, Any],
                        find_device(ble_address),
                    )
                    used_fallback = True

                if used_fallback:
                    result = await facade.asyncio.wait_for(coro, timeout=timeout)
                else:
                    result = await coro
                return result is not None

            expected_address = _sanitize_ble_address(ble_address)
            devices = await BleakScanner.discover(timeout=timeout)
            return any(
                _sanitize_ble_address(str(getattr(device, "address", "") or ""))
                == expected_address
                for device in devices
            )
        except (
            facade.BleakError,
            facade.BleakDBusError,
            OSError,
            RuntimeError,
            TypeError,
            facade.asyncio.TimeoutError,
        ) as exc:
            facade.logger.debug("BLE scan failed for %s: %s", ble_address, exc)
            return False

    try:
        running_loop = facade.asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop and running_loop.is_running():
        facade.logger.debug(
            "Skipping BLE scan for %s; running event loop is active",
            ble_address,
        )
        return False

    try:
        return facade.asyncio.run(_scan())
    except (
        facade.BleakError,
        facade.BleakDBusError,
        OSError,
        RuntimeError,
        TypeError,
        facade.asyncio.TimeoutError,
    ) as exc:
        facade.logger.debug("BLE scan failed for %s: %s", ble_address, exc)
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
    if isinstance(error, KeyError):
        normalized_keys = {
            str(item).strip().strip("'").strip('"') for item in error.args
        }
        if "path" in normalized_keys:
            return True

    def _is_type_or_tuple(candidate: object) -> bool:
        if isinstance(candidate, type):
            return True
        if isinstance(candidate, tuple):
            return all(isinstance(item, type) for item in candidate)
        return False

    ble_interface = getattr(facade.meshtastic.ble_interface, "BLEInterface", None)
    ble_error_type = getattr(ble_interface, "BLEError", None)
    if (
        ble_error_type
        and _is_type_or_tuple(ble_error_type)
        and isinstance(error, ble_error_type)
    ):
        return True

    mesh_interface_module = getattr(facade.meshtastic, "mesh_interface", None)
    mesh_interface = getattr(mesh_interface_module, "MeshInterface", None)
    mesh_error_type = getattr(mesh_interface, "MeshInterfaceError", None)
    if (
        mesh_error_type
        and _is_type_or_tuple(mesh_error_type)
        and isinstance(error, mesh_error_type)
    ):
        return True

    return False


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


def _extract_ble_address_from_interface(iface: Any) -> str | None:
    """Best-effort extraction of a BLE address from an interface/client shape."""
    if iface is None:
        return None

    candidates: list[str | None] = []
    iface_address = getattr(iface, "address", None)
    if isinstance(iface_address, str):
        candidates.append(iface_address)

    client_obj = getattr(iface, "client", None)
    if client_obj is not None:
        client_address = getattr(client_obj, "address", None)
        if isinstance(client_address, str):
            candidates.append(client_address)
        bleak_client = getattr(client_obj, "bleak_client", None)
        if bleak_client is not None:
            bleak_address = getattr(bleak_client, "address", None)
            if isinstance(bleak_address, str):
                candidates.append(bleak_address)

    for candidate in candidates:
        sanitized = _sanitize_ble_address(candidate or "")
        if sanitized:
            return sanitized
    return None


def _advance_ble_generation(ble_address: str, *, transition: str) -> int:
    """Increment and return the lifecycle generation for a BLE address."""
    address_key = _sanitize_ble_address(ble_address)
    if not address_key:
        return 0

    with facade._ble_lifecycle_lock:
        generation = facade._ble_generation_by_address.get(address_key, 0) + 1
        facade._ble_generation_by_address[address_key] = generation

    facade.logger.debug(
        "Advanced BLE lifecycle generation for %s to %s (%s)",
        ble_address,
        generation,
        transition,
    )
    return generation


def _get_ble_generation(ble_address: str) -> int:
    """Return the current lifecycle generation for a BLE address."""
    address_key = _sanitize_ble_address(ble_address)
    if not address_key:
        return 0
    with facade._ble_lifecycle_lock:
        return facade._ble_generation_by_address.get(address_key, 0)


def _is_ble_generation_stale(ble_address: str, generation: int) -> bool:
    """Return whether a generation token is stale for a BLE address."""
    address_key = _sanitize_ble_address(ble_address)
    if not address_key:
        return False
    with facade._ble_lifecycle_lock:
        current_generation = facade._ble_generation_by_address.get(address_key, 0)
    return current_generation != generation


def _register_ble_iface_generation(
    iface: Any,
    ble_address: str,
    generation: int,
) -> None:
    """Associate an interface object identity with BLE address/generation ownership."""
    if iface is None:
        return
    address_key = _sanitize_ble_address(ble_address)
    if not address_key or generation <= 0:
        return
    with facade._ble_lifecycle_lock:
        facade._ble_iface_generation_by_id[id(iface)] = (address_key, generation)


def _get_ble_iface_generation(
    iface: Any,
    *,
    fallback_address: str | None = None,
) -> tuple[str | None, int | None]:
    """
    Return the best-known (address, generation) ownership metadata for an interface.
    """
    iface_id = id(iface) if iface is not None else None
    fallback_key = _sanitize_ble_address(fallback_address or "")

    with facade._ble_lifecycle_lock:
        if iface_id is not None:
            mapped = facade._ble_iface_generation_by_id.get(iface_id)
            if mapped is not None:
                return mapped
        if fallback_key:
            return fallback_key, facade._ble_generation_by_address.get(fallback_key)

    extracted = _extract_ble_address_from_interface(iface)
    if not extracted:
        return (fallback_key or None), None

    with facade._ble_lifecycle_lock:
        return extracted, facade._ble_generation_by_address.get(extracted)


def _discard_ble_iface_generation(iface: Any) -> tuple[str | None, int | None]:
    """Drop interface ownership metadata once that interface is no longer active."""
    if iface is None:
        return None, None
    with facade._ble_lifecycle_lock:
        res = facade._ble_iface_generation_by_id.pop(id(iface), None)
    if res is None:
        return None, None
    return res


def _record_ble_teardown_timeout(ble_address: str, generation: int) -> int:
    """Record a timed-out teardown worker for address/generation ownership."""
    address_key = _sanitize_ble_address(ble_address)
    if not address_key or generation <= 0:
        return 0
    with facade._ble_lifecycle_lock:
        key = (address_key, generation)
        count = facade._ble_teardown_unresolved_by_generation.get(key, 0) + 1
        facade._ble_teardown_unresolved_by_generation[key] = count
    return count


def _resolve_ble_teardown_timeout(
    ble_address: str,
    generation: int,
) -> tuple[int, bool]:
    """
    Resolve one timed-out teardown worker and report remaining unresolved workers.
    """
    address_key = _sanitize_ble_address(ble_address)
    if not address_key or generation <= 0:
        return 0, False

    remaining_for_address = 0
    with facade._ble_lifecycle_lock:
        key = (address_key, generation)
        previous = facade._ble_teardown_unresolved_by_generation.get(key, 0)
        if previous <= 1:
            facade._ble_teardown_unresolved_by_generation.pop(key, None)
        else:
            facade._ble_teardown_unresolved_by_generation[key] = previous - 1

        current_generation = facade._ble_generation_by_address.get(address_key, 0)
        stale_generation = current_generation != generation

        for (
            entry_address,
            _,
        ), count in facade._ble_teardown_unresolved_by_generation.items():
            if entry_address == address_key:
                remaining_for_address += count

    return remaining_for_address, stale_generation


def _get_ble_unresolved_teardown_generations(
    ble_address: str,
) -> list[tuple[int, int]]:
    """Return unresolved teardown worker counts as (generation, count)."""
    address_key = _sanitize_ble_address(ble_address)
    if not address_key:
        return []
    with facade._ble_lifecycle_lock:
        pending = [
            (generation, count)
            for (
                entry_address,
                generation,
            ), count in facade._ble_teardown_unresolved_by_generation.items()
            if entry_address == address_key and count > 0
        ]
    pending.sort(key=lambda item: item[0])
    return pending


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
        expected_sanitized = facade._sanitize_ble_address(expected_address)

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
            facade.logger.warning(
                "Could not determine connected BLE device address for validation. "
                "Proceeding with caution - verify correct device is connected."
            )
            return True

        actual_sanitized = facade._sanitize_ble_address(actual_address)

        if actual_sanitized == expected_sanitized:
            facade.logger.debug(
                f"BLE connection validation passed: connected to {actual_address} "
                f"(expected: {expected_address})"
            )
            return True
        else:
            facade.logger.error(
                f"BLE CONNECTION VALIDATION FAILED: Connected to {actual_address} "
                f"but expected {expected_address}. This could be caused by "
                "substring matching in device discovery selecting wrong device. "
                "Disconnecting to prevent misconfiguration."
            )
            return False
    except Exception as e:  # noqa: BLE001 - validation is best-effort
        facade.logger.warning(
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
    facade.logger.debug(f"Checking for stale BlueZ connection to {address}")

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
                        facade.logger.debug(
                            "Failed to call is_connected for %s: %s", address, e
                        )
                        return
                    if inspect.isawaitable(connected_result):
                        try:
                            connected_status = await facade.asyncio.wait_for(
                                cast(Awaitable[bool], connected_result),
                                timeout=facade.BLE_DISCONNECT_TIMEOUT_SECS,
                            )
                        except facade.asyncio.TimeoutError:
                            facade.logger.debug(
                                "Timed out checking connection state for %s", address
                            )
                            connected_status = False
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
                facade.logger.debug(
                    "Failed to check connection state for %s: %s",
                    address,
                    e,
                    exc_info=True,
                )
                return

            try:
                if connected_status:
                    facade.logger.warning(
                        f"Device {address} is already connected in BlueZ. Disconnecting..."
                    )
                    # Retry logic for disconnect with timeout
                    max_retries = facade.BLE_DISCONNECT_MAX_RETRIES
                    for attempt in range(max_retries):
                        try:
                            # Some backends or test doubles return a sync result
                            # from disconnect(); only await when needed.
                            disconnect_result = client.disconnect()
                            if inspect.isawaitable(disconnect_result):
                                await facade.asyncio.wait_for(
                                    disconnect_result,
                                    timeout=facade.BLE_DISCONNECT_TIMEOUT_SECS,
                                )
                            await facade.asyncio.sleep(
                                facade.BLE_DISCONNECT_SETTLE_SECS
                            )
                            facade.logger.debug(
                                "Successfully disconnected stale connection to %s on attempt %s, "
                                "waiting %.1fs for BlueZ to settle",
                                address,
                                attempt + 1,
                                facade.BLE_DISCONNECT_SETTLE_SECS,
                            )
                            break
                        except facade.asyncio.TimeoutError:
                            if attempt < max_retries - 1:
                                facade.logger.warning(
                                    f"Disconnect attempt {attempt + 1} for {address} timed out, retrying..."
                                )
                                await facade.asyncio.sleep(facade.BLE_RETRY_DELAY_SECS)
                            else:
                                facade.logger.warning(
                                    f"Disconnect for {address} timed out after {max_retries} attempts"
                                )
                        except BLEAK_EXCEPTIONS as e:
                            # Bleak disconnects can throw DBus/IO errors depending
                            # on adapter state; retry a few times then give up.
                            if attempt < max_retries - 1:
                                facade.logger.warning(
                                    "Disconnect attempt %s for %s failed: %s, retrying...",
                                    attempt + 1,
                                    address,
                                    e,
                                    exc_info=True,
                                )
                                await facade.asyncio.sleep(facade.BLE_RETRY_DELAY_SECS)
                            else:
                                facade.logger.warning(
                                    "Disconnect for %s failed after %s attempts: %s",
                                    address,
                                    max_retries,
                                    e,
                                    exc_info=True,
                                )
                else:
                    facade.logger.debug(
                        f"Device {address} not currently connected in BlueZ"
                    )
            except BLEAK_EXCEPTIONS as e:
                # Stale disconnects are best-effort; do not fail startup/reconnect
                # on cleanup errors from BlueZ/DBus.
                facade.logger.debug(
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
                            await facade.asyncio.wait_for(
                                disconnect_result,
                                timeout=facade.BLE_DISCONNECT_TIMEOUT_SECS,
                            )
                        await facade.asyncio.sleep(facade.BLE_DISCONNECT_SETTLE_SECS)
                except facade.asyncio.TimeoutError:
                    facade.logger.debug(
                        f"Final disconnect for {address} timed out (cleanup)"
                    )
                except BLEAK_EXCEPTIONS as e:
                    # Ignore disconnect errors during cleanup - connection may already be closed
                    facade.logger.debug(
                        "Final disconnect for %s failed during cleanup",
                        address,
                        exc_info=e,
                    )

        runtime_error: RuntimeError | None = None
        try:
            loop = facade.asyncio.get_running_loop()
        except RuntimeError as e:
            loop = None
            runtime_error = e

        if loop and loop.is_running():
            facade.logger.debug(
                "Found running event loop; scheduling disconnect task for %s",
                address,
            )
            facade._fire_and_forget(disconnect_stale_connection(), loop=loop)
            return

        if (
            facade.event_loop
            and getattr(facade.event_loop, "is_running", lambda: False)()
        ):
            facade.logger.debug(
                "Using global event loop, waiting for disconnect task for %s",
                address,
            )
            future = facade.asyncio.run_coroutine_threadsafe(
                disconnect_stale_connection(), facade.event_loop
            )
            try:
                future.result(timeout=facade.STALE_DISCONNECT_TIMEOUT_SECS)
                facade.logger.debug(
                    f"Stale connection disconnect completed for {address}"
                )
            except facade.FuturesTimeoutError:
                facade.logger.warning(
                    f"Stale connection disconnect timed out after {facade.STALE_DISCONNECT_TIMEOUT_SECS:.0f}s for {address}"
                )
                if not future.done():
                    # Cancel the cleanup task so we do not block a new connect
                    # attempt on a hung DBus/Bleak operation.
                    future.cancel()
            return

        # No running event loop in this thread (and no global loop to target);
        # create a temporary loop to perform a blocking best-effort cleanup.
        facade.logger.debug(
            "No running event loop (RuntimeError: %s), creating temporary loop for %s",
            runtime_error,
            address,
        )
        facade.asyncio.run(disconnect_stale_connection())
        facade.logger.debug(f"Stale connection disconnect completed for {address}")
    except ImportError:
        # Bleak is optional in some deployments; skip stale cleanup rather than
        # breaking startup when BLE support isn't installed.
        facade.logger.debug("BleakClient not available for stale connection cleanup")
    except Exception as e:  # noqa: BLE001 - disconnect cleanup must not block startup
        # Other errors during best-effort disconnect (e.g., from future.result() or asyncio.run())
        # are non-fatal; log and continue.
        facade.logger.debug(
            "Error during BLE disconnect cleanup for %s",
            address,
            exc_info=e,
        )


def _disconnect_ble_interface(
    iface: Any,
    reason: str = "disconnect",
    *,
    ble_address: str | None = None,
    generation: int | None = None,
) -> None:
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

    iface_id = id(iface)
    resolved_address, mapped_generation = facade._get_ble_iface_generation(
        iface,
        fallback_address=ble_address,
    )
    if generation is None:
        generation = mapped_generation
    if generation is None and resolved_address is not None:
        generation = facade._get_ble_generation(resolved_address)

    client_obj = getattr(iface, "client", None)
    client_id = id(client_obj) if client_obj is not None else None
    facade.logger.debug(
        "Starting BLE teardown reason=%s address=%s generation=%s iface_id=%s client_id=%s",
        reason,
        resolved_address,
        generation,
        iface_id,
        client_id,
    )

    # Pre-disconnect delay to allow pending notifications to complete
    # This helps prevent "Unexpected EOF on notification file handle" errors
    facade.logger.debug(f"Waiting before disconnecting BLE interface ({reason})")
    facade.time.sleep(0.5)
    timeout_log_level = logging.DEBUG if reason == "shutdown" else logging.WARNING
    retry_log = facade.logger.debug if reason == "shutdown" else facade.logger.warning
    final_log = facade.logger.debug if reason == "shutdown" else facade.logger.error

    try:
        if hasattr(iface, "_exit_handler") and iface._exit_handler:
            # Best-effort: avoid atexit callbacks blocking shutdown when the
            # official library registers close handlers we already ran.
            with contextlib.suppress(Exception):
                atexit.unregister(iface._exit_handler)
            iface._exit_handler = None

        # Check if interface has a disconnect method (forked version)
        if hasattr(iface, "disconnect"):
            facade.logger.debug(f"Disconnecting BLE interface ({reason})")

            # Retry logic for disconnect operations
            max_disconnect_retries = 3
            for attempt in range(max_disconnect_retries):
                try:
                    disconnect_method = iface.disconnect
                    if inspect.iscoroutinefunction(disconnect_method):
                        facade._wait_for_result(disconnect_method(), timeout=3.0)
                    else:
                        # Run sync disconnect in a daemon thread to avoid hangs.
                        def _disconnect_interface_sync(
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
                                facade._wait_for_result(result, timeout=3.0)

                        facade._run_blocking_with_timeout(
                            _disconnect_interface_sync,
                            timeout=3.0,
                            label=f"ble-interface-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                            ble_address=resolved_address,
                            ble_generation=generation,
                            iface_id=iface_id,
                            client_id=client_id,
                        )
                    # Give the adapter time to complete the disconnect
                    facade.time.sleep(1.0)
                    facade.logger.debug(
                        f"BLE interface disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_disconnect_retries - 1:
                        retry_log(
                            f"BLE interface disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        facade.time.sleep(0.5)
                    else:
                        final_log(
                            f"BLE interface disconnect failed after {max_disconnect_retries} attempts ({reason}): {e}"
                        )
        else:
            facade.logger.debug(
                f"BLE interface has no disconnect() method, using close() only ({reason})"
            )

        # Always call close() to release resources
        facade.logger.debug(f"Closing BLE interface ({reason})")

        # For BLE interfaces, explicitly disconnect the underlying BleakClient
        # to prevent stale connections in BlueZ (official library bug)
        # Check that client attribute exists AND is not None (handles forked lib close race)
        if getattr(iface, "client", None) is not None:
            facade.logger.debug(f"Explicitly disconnecting BLE client ({reason})")

            # Retry logic for client disconnect
            max_client_retries = 2
            for attempt in range(max_client_retries):
                # Re-check client before each attempt (may become None during close)
                client_obj = getattr(iface, "client", None)
                if client_obj is None:
                    facade.logger.debug(
                        f"BLE client became None before attempt {attempt + 1} ({reason}), skipping"
                    )
                    break
                try:
                    disconnect_method = client_obj.disconnect
                    # Check _exit_handler on the client object safely
                    client_exit_handler = getattr(client_obj, "_exit_handler", None)
                    if client_exit_handler:
                        with contextlib.suppress(ValueError):
                            atexit.unregister(client_exit_handler)
                        with contextlib.suppress(AttributeError, TypeError):
                            client_obj._exit_handler = None
                    with contextlib.suppress(ValueError):
                        atexit.unregister(disconnect_method)

                    if inspect.iscoroutinefunction(disconnect_method):
                        facade._wait_for_result(disconnect_method(), timeout=2.0)
                    else:
                        # Run sync disconnect in a daemon thread so it cannot
                        # block shutdown if BlueZ/DBus is hung.
                        def _disconnect_client_sync(
                            method: Callable[[], Any] = disconnect_method,
                        ) -> None:
                            """
                            Call a disconnection callable and, if it returns an awaitable, wait up to 2 seconds for it to complete.

                            Parameters:
                                method (Callable[[], Any]): A synchronous or asynchronous disconnect callable to invoke. If it returns an awaitable, this function will wait up to 2.0 seconds for completion.
                            """
                            result = method()
                            if inspect.isawaitable(result):
                                facade._wait_for_result(result, timeout=2.0)

                        facade._run_blocking_with_timeout(
                            _disconnect_client_sync,
                            timeout=2.0,
                            label=f"ble-client-disconnect-{reason}",
                            timeout_log_level=timeout_log_level,
                            ble_address=resolved_address,
                            ble_generation=generation,
                            iface_id=iface_id,
                            client_id=(
                                id(client_obj) if client_obj is not None else client_id
                            ),
                        )
                    facade.time.sleep(1.0)
                    facade.logger.debug(
                        f"BLE client disconnect succeeded on attempt {attempt + 1} ({reason})"
                    )
                    break
                except Exception as e:
                    if attempt < max_client_retries - 1:
                        retry_log(
                            f"BLE client disconnect attempt {attempt + 1} failed ({reason}): {e}, retrying..."
                        )
                        facade.time.sleep(0.3)
                    else:
                        # Ignore disconnect errors on final attempt - connection may already be closed
                        facade.logger.debug(
                            f"BLE client disconnect failed after {max_client_retries} attempts ({reason}): {e}"
                        )

        close_method = iface.close
        with contextlib.suppress(Exception):
            atexit.unregister(close_method)
        if inspect.iscoroutinefunction(close_method):
            facade._wait_for_result(close_method(), timeout=5.0)
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
                    facade._wait_for_result(result, timeout=5.0)

            iface_client_obj = getattr(iface, "client", None)
            facade._run_blocking_with_timeout(
                _close_sync,
                timeout=5.0,
                label=f"ble-interface-close-{reason}",
                timeout_log_level=timeout_log_level,
                ble_address=resolved_address,
                ble_generation=generation,
                iface_id=iface_id,
                client_id=(
                    id(iface_client_obj) if iface_client_obj is not None else client_id
                ),
            )
    except TimeoutError as exc:
        facade.logger.debug("BLE interface %s timed out: %s", reason, exc)
    except Exception as e:  # noqa: BLE001 - cleanup must not block shutdown
        facade.logger.debug(f"Error during BLE interface {reason}", exc_info=e)
    finally:
        facade._discard_ble_iface_generation(iface)
        # Small delay to ensure the adapter has fully released the connection
        facade.time.sleep(0.5)
