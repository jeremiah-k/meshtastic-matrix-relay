import contextlib
import io
import sys
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any, Callable

from meshtastic.protobuf import admin_pb2

import mmrelay.meshtastic_utils as facade
from mmrelay.constants.domain import METADATA_OUTPUT_MAX_LENGTH
from mmrelay.constants.formats import (
    DEFAULT_TEXT_ENCODING,
    ENCODING_ERROR_IGNORE,
    FIRMWARE_VERSION_REGEX,
)
from mmrelay.constants.network import METADATA_WATCHDOG_SECS

__all__ = [
    "_extract_firmware_version_from_client",
    "_extract_firmware_version_from_metadata",
    "_get_device_metadata",
    "_get_name_or_none",
    "_get_name_safely",
    "_missing_metadata_probe_error",
    "_normalize_firmware_version",
]


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


def _normalize_firmware_version(value: Any) -> str | None:
    """
    Normalize a firmware version candidate into a non-empty string.

    Parameters:
        value (Any): Candidate firmware value from metadata sources.

    Returns:
        str | None: Trimmed firmware version string when valid, otherwise None.
    """
    if isinstance(value, bytes):
        value = value.decode(DEFAULT_TEXT_ENCODING, errors=ENCODING_ERROR_IGNORE)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized and normalized.lower() != "unknown":
            return normalized
    return None


def _extract_firmware_version_from_metadata(metadata_source: Any) -> str | None:
    """
    Extract firmware version from a metadata object or mapping.

    Supports both snake_case and camelCase field names for compatibility with
    different Meshtastic payload shapes.

    Parameters:
        metadata_source (Any): Metadata container (protobuf-like object or dict).

    Returns:
        str | None: Firmware version if available, else None.
    """
    if metadata_source is None:
        return None

    if isinstance(metadata_source, dict):
        return _normalize_firmware_version(
            metadata_source.get("firmware_version")
            or metadata_source.get("firmwareVersion")
        )

    return _normalize_firmware_version(
        getattr(metadata_source, "firmware_version", None)
        or getattr(metadata_source, "firmwareVersion", None)
    )


def _extract_firmware_version_from_client(client: Any) -> str | None:
    """
    Return the first normalized firmware version exposed on common client fields.

    Parameters:
        client (Any): Meshtastic client object to inspect.

    Returns:
        str | None: Firmware version if present on the client, local node, or
            local interface metadata.
    """
    local_node = getattr(client, "localNode", None)
    local_iface = getattr(local_node, "iface", None) if local_node else None

    candidates = (
        getattr(client, "metadata", None),
        local_node and getattr(local_node, "metadata", None),
        local_iface and getattr(local_iface, "metadata", None),
    )
    for candidate in candidates:
        parsed = _extract_firmware_version_from_metadata(candidate)
        if parsed is not None:
            return parsed
    return None


def _missing_metadata_probe_error() -> RuntimeError:
    """
    Build the error raised when metadata probe is unavailable on a client.
    """
    return RuntimeError(
        "Meshtastic client has no localNode.getMetadata() for metadata probe"
    )


def _get_device_metadata(
    client: Any,
    *,
    force_refresh: bool = False,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    """
    Retrieve firmware version and raw metadata output from a Meshtastic client.

    Prefers structured metadata already present on the client/interface unless
    `force_refresh=True`. If no
    usable firmware version is cached, attempts to call
    `client.localNode.getMetadata()`, captures console output produced by that
    call, and extracts firmware version information from output and any updated
    structured metadata.

    Parameters:
        client (Any): Meshtastic client object expected to expose localNode.getMetadata(); if absent, metadata retrieval is skipped.
        force_refresh (bool): If `True`, always call `getMetadata()` even when
            structured metadata is already cached. Health checks use this mode
            intentionally because it issues an on-wire admin request.
        raise_on_error (bool): If `True`, re-raise metadata probe failures after
            logging so callers can treat failures as liveness errors.

    Returns:
        dict: {
            "firmware_version" (str): Parsed firmware version or "unknown" when not found,
            "raw_output" (str): Captured output from getMetadata(), truncated to 4096 characters with a trailing ellipsis if longer,
            "success" (bool): `True` when a firmware version was successfully parsed, `False` otherwise
        }
    """
    result = {"firmware_version": "unknown", "raw_output": "", "success": False}

    cached_firmware = _extract_firmware_version_from_client(client)
    if cached_firmware is not None and not force_refresh:
        result["firmware_version"] = cached_firmware
        result["success"] = True
        return result

    # Preflight: client may be a mock without localNode/getMetadata
    if not getattr(client, "localNode", None) or not callable(
        getattr(client.localNode, "getMetadata", None)
    ):
        if raise_on_error:
            raise _missing_metadata_probe_error()
        facade.logger.debug(
            "Meshtastic client has no localNode.getMetadata(); skipping metadata retrieval"
        )
        return result

    try:
        # Capture getMetadata() output to extract firmware version.
        # Use a shared executor to prevent thread leaks if getMetadata() hangs.
        output_capture = facade.io.StringIO()
        # Track redirect state so a timeout cannot leave sys.stdout pointing at
        # a closed StringIO (which can trigger "I/O operation on closed file").
        redirect_active = facade.threading.Event()
        orig_stdout = facade.sys.stdout

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
            except ValueError as exc:
                if output_capture.closed or "I/O operation on closed file" in str(exc):
                    return
                raise

        try:
            future = facade._submit_metadata_probe(call_get_metadata)
        except facade.MetadataExecutorDegradedError:
            facade.logger.error(
                "Metadata executor degraded; skipping metadata retrieval. "
                "Reconnect or restart required to restore metadata probing."
            )
            if raise_on_error:
                raise
            return result
        except RuntimeError as exc:
            facade.logger.debug(
                "getMetadata() submission failed; skipping metadata retrieval",
                exc_info=exc,
            )
            if raise_on_error:
                raise
            return result

        if future is None:
            # A previous metadata request is still running; avoid piling up
            # threads and leave the in-flight call to finish in its own time.
            facade.logger.debug("getMetadata() already running; skipping new request")
            return result

        timed_out = False
        future_error: Exception | None = None
        try:
            future.result(timeout=METADATA_WATCHDOG_SECS)
        except FuturesTimeoutError as e:
            timed_out = True
            if raise_on_error:
                future_error = e
            facade.logger.debug(
                f"getMetadata() timed out after {METADATA_WATCHDOG_SECS} seconds"
            )
            # If the worker is still running, restore stdio immediately so the
            # main process does not keep writing to the captured buffer.
            if redirect_active.is_set():
                if facade.sys.stdout is output_capture:
                    facade.sys.stdout = orig_stdout
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

            Close the shared output capture stream once the worker has fully
            finished with it.

            Parameters:
                done_future (concurrent.futures.Future | asyncio.Future): The future that has completed and triggered finalization.
            """
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

        raw_output = console_output
        if len(raw_output) > METADATA_OUTPUT_MAX_LENGTH:
            raw_output = raw_output[: max(METADATA_OUTPUT_MAX_LENGTH - 1, 0)] + "…"
        result["raw_output"] = raw_output

        match = FIRMWARE_VERSION_REGEX.search(console_output)
        parsed_output_firmware = (
            _normalize_firmware_version(match.group(1)) if match else None
        )
        if parsed_output_firmware is not None:
            result["firmware_version"] = parsed_output_firmware
            result["success"] = True
        else:
            refreshed_firmware = _extract_firmware_version_from_client(client)
            if refreshed_firmware is not None:
                result["firmware_version"] = refreshed_firmware
                result["success"] = True

    except Exception as e:  # noqa: BLE001 - metadata failures must not block startup
        # Metadata is optional; never block the main connection path on failures
        # in the admin request or parsing logic.
        facade.logger.debug(
            "Could not retrieve device metadata via localNode.getMetadata()", exc_info=e
        )
        if raise_on_error:
            raise

    return result
