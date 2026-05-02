import asyncio
import os
import ssl
import sys
from typing import Any, Optional, cast

import mmrelay.matrix_utils as facade
from mmrelay.matrix.compat import (
    format_e2ee_install_command,
    format_e2ee_unavailable_message,
    get_matrix_capabilities,
)

__all__ = [
    "_configure_e2ee",
    "_initialize_matrix_client",
    "_perform_matrix_login",
    "_maybe_upload_e2ee_keys",
    "_close_matrix_client_after_failure",
]


async def _configure_e2ee(
    config_data: dict[str, Any],
    matrix_section: Any,
    e2ee_device_id: Optional[str],
) -> tuple[bool, str | None]:
    """
    Determine whether end-to-end encryption (E2EE) should be enabled and compute the filesystem path to use for the E2EE store.

    Parameters:
        config_data (dict[str, Any]): Full application configuration used to detect whether E2EE is enabled.
        matrix_section (Any): Matrix-specific configuration subsection; when a dict, its "encryption" or "e2ee" keys may provide a `store_path` override.
        e2ee_device_id (Optional[str]): Device ID restored from credentials, if any; used to decide whether a device id must be retrieved later.

    Returns:
        tuple[bool, str | None]: A pair (enabled, store_path) where `enabled` is `True` if E2EE is enabled and required runtime dependencies are available, `False` otherwise; `store_path` is the resolved E2EE store directory path or `None` if not applicable.
    """
    if not isinstance(config_data, dict):
        facade.logger.error(
            f"E2EE setup failed: expected dict for config_data, got {type(config_data).__name__}"
        )
        return False, None

    e2ee_enabled = False
    e2ee_store_path = None
    try:
        e2ee_enabled = facade.is_e2ee_enabled(config_data)
        facade.logger.debug(
            f"E2EE detection: matrix config section present: {'matrix' in config_data}"
        )
        facade.logger.debug(f"E2EE detection: e2ee enabled = {e2ee_enabled}")

        if e2ee_enabled:
            from mmrelay.constants.app import WINDOWS_PLATFORM

            if sys.platform == WINDOWS_PLATFORM:
                facade.logger.error(
                    "E2EE is not supported on Windows due to library limitations."
                )
                facade.logger.error(
                    "Matrix E2EE crypto backends require native libraries that are difficult to install on Windows."
                )
                facade.logger.error(
                    "Please disable E2EE in your configuration or use a Linux/macOS system for E2EE support."
                )
                e2ee_enabled = False
            else:
                matrix_capabilities = get_matrix_capabilities()
                if not matrix_capabilities.encryption_available:
                    facade.logger.error("Missing E2EE dependency")
                    facade.logger.error(
                        format_e2ee_unavailable_message(matrix_capabilities)
                    )
                    facade.logger.error(
                        format_e2ee_install_command(matrix_capabilities)
                    )
                    facade.logger.warning("E2EE will be disabled for this session.")
                    e2ee_enabled = False
                else:
                    facade.logger.debug(
                        "E2EE dependencies available: provider=%s version=%s backend=%s",
                        matrix_capabilities.provider_name,
                        matrix_capabilities.provider_version,
                        matrix_capabilities.crypto_backend,
                    )
                    facade.logger.info("End-to-End Encryption (E2EE) is enabled")

                if e2ee_enabled:
                    store_override = None
                    if isinstance(matrix_section, dict):
                        encryption_section = matrix_section.get("encryption")
                        e2ee_section = matrix_section.get("e2ee")
                        if isinstance(e2ee_section, dict):
                            store_override = e2ee_section.get("store_path")
                        if not store_override and isinstance(encryption_section, dict):
                            store_override = encryption_section.get("store_path")

                    if isinstance(store_override, str) and store_override:
                        e2ee_store_path = os.path.expanduser(store_override)
                    else:
                        e2ee_store_path = str(
                            await asyncio.to_thread(facade.get_e2ee_store_dir)
                        )

                    try:
                        await asyncio.to_thread(
                            os.makedirs, e2ee_store_path, exist_ok=True
                        )
                    except OSError as e:
                        facade.logger.error(
                            "Could not create E2EE store directory %s: %s; disabling E2EE for this session.",
                            e2ee_store_path,
                            e,
                        )
                        e2ee_enabled = False
                        e2ee_store_path = None

                    if e2ee_enabled and e2ee_store_path:
                        try:
                            store_exists = await asyncio.to_thread(
                                os.path.exists, e2ee_store_path
                            )
                            store_files = (
                                await asyncio.to_thread(os.listdir, e2ee_store_path)
                                if store_exists
                                else []
                            )
                            db_files = [f for f in store_files if f.endswith(".db")]
                            if db_files:
                                facade.logger.debug(
                                    f"Found existing E2EE store files: {', '.join(db_files)}"
                                )
                            else:
                                facade.logger.info(
                                    "No existing E2EE store files found; this is expected for first-time setup and encryption will initialize on first use."
                                )

                            facade.logger.debug(
                                f"Using E2EE store path: {e2ee_store_path}"
                            )
                        except OSError as exc:
                            facade.logger.error(
                                "Could not inspect E2EE store at %s: %s",
                                e2ee_store_path,
                                exc,
                            )
                            e2ee_enabled = False
                            e2ee_store_path = None

                        if not e2ee_device_id:
                            facade.logger.debug(
                                "No device_id in credentials; will retrieve from store/whoami later if available"
                            )
    except (KeyError, TypeError) as exc:
        facade.logger.warning(f"Failed to determine E2EE status from config: {exc}")
        facade.logger.debug("E2EE configuration error details", exc_info=True)

    return e2ee_enabled, e2ee_store_path


def _initialize_matrix_client(
    homeserver: str,
    user_id: str,
    device_id: Optional[str],
    e2ee_enabled: bool,
    e2ee_store_path: str | None,
    ssl_context: ssl.SSLContext | None,
) -> "facade.AsyncClient":
    """
    Create and configure a nio AsyncClient for the given Matrix account.

    Parameters:
        homeserver (str): Matrix homeserver URL or server name.
        user_id (str): Full Matrix user ID (MXID) for the client.
        device_id (Optional[str]): Device identifier to restore an existing session; if None a new device may be created.
        e2ee_enabled (bool): Enable end-to-end encryption for the client.
        e2ee_store_path (str | None): Filesystem path to use as the client's store when E2EE is enabled; ignored when `e2ee_enabled` is False.
        ssl_context (ssl.SSLContext | None): Optional SSL context for HTTPS connections.

    Returns:
        AsyncClient: A configured AsyncClient instance ready for login and synchronization.
    """
    client_config = facade.AsyncClientConfig(
        max_limit_exceeded=0,
        max_timeouts=0,
        store_sync_tokens=True,
        encryption_enabled=e2ee_enabled,
    )

    if device_id:
        facade.logger.debug(f"Device ID from credentials: {device_id}")

    client_kwargs: dict[str, Any] = {
        "homeserver": homeserver,
        "user": user_id,
        "store_path": e2ee_store_path if e2ee_enabled else None,
        "config": client_config,
        "ssl": cast(Any, ssl_context),
    }
    if device_id:
        client_kwargs["device_id"] = device_id

    return facade.AsyncClient(**client_kwargs)


async def _perform_matrix_login(
    client: "facade.AsyncClient",
    auth_info: "facade.MatrixAuthInfo",
) -> Optional[str]:
    """
    Restore or establish the client's Matrix session and obtain the device ID used for E2EE.

    Updates the given AsyncClient's authentication state from auth_info (access token, user_id and, when available, device_id). If stored credentials are present but missing a device_id, the function calls whoami to discover and persist the device_id and user_id to auth_info.credentials (saved to auth_info.credentials_path) when possible. Warnings are logged if discovery or persistence fails.

    Parameters:
        auth_info (MatrixAuthInfo): Authentication information; `credentials` and `credentials_path` may be updated with discovered `user_id` and/or `device_id`.

    Returns:
        device_id (str | None): The E2EE device_id that was restored or discovered, or None if no device_id is available.
    """
    from mmrelay.constants.config import CONFIG_KEY_DEVICE_ID, CONFIG_KEY_USER_ID

    e2ee_device_id = auth_info.device_id
    user_id = auth_info.user_id
    access_token = auth_info.access_token

    if auth_info.credentials:
        if e2ee_device_id and user_id:
            client.restore_login(
                user_id=user_id,
                device_id=e2ee_device_id,
                access_token=access_token,
            )
            facade.logger.info(
                f"Restored login session for {user_id} with device {e2ee_device_id}"
            )
        else:
            facade.logger.info("First-run E2EE setup: discovering device_id via whoami")
            client.access_token = access_token
            client.user_id = user_id

            try:
                whoami_response = await client.whoami()
                credentials_updated = False

                discovered_user_id = getattr(whoami_response, "user_id", None)
                if discovered_user_id and discovered_user_id != user_id:
                    if user_id:
                        facade.logger.warning(
                            f"Matrix user_id mismatch: credentials say {user_id} but whoami says {discovered_user_id}. "
                            "Updating credentials to match whoami."
                        )
                    user_id = discovered_user_id
                    client.user_id = user_id
                    auth_info.user_id = user_id
                    if auth_info.credentials is not None:
                        auth_info.credentials[CONFIG_KEY_USER_ID] = user_id
                        credentials_updated = True

                discovered_device_id = getattr(whoami_response, "device_id", None)
                if discovered_device_id and discovered_device_id != e2ee_device_id:
                    e2ee_device_id = discovered_device_id
                    client.device_id = e2ee_device_id
                    facade.logger.info(
                        f"Discovered device_id from whoami: {e2ee_device_id}"
                    )

                    if auth_info.credentials is not None:
                        auth_info.credentials[CONFIG_KEY_DEVICE_ID] = e2ee_device_id
                        credentials_updated = True

                if credentials_updated and auth_info.credentials is not None:
                    try:
                        await asyncio.to_thread(
                            facade.save_credentials,
                            auth_info.credentials,
                            credentials_path=auth_info.credentials_path,
                        )
                        facade.logger.info(
                            "Updated credentials.json with discovered information"
                        )
                    except OSError:
                        facade.logger.exception(
                            "Failed to persist updated session information"
                        )

                    if e2ee_device_id and user_id:
                        client.restore_login(
                            user_id=user_id,
                            device_id=e2ee_device_id,
                            access_token=access_token,
                        )
                        facade.logger.info(
                            "Restored login session for %s with device %s",
                            user_id,
                            e2ee_device_id,
                        )
                    else:
                        facade.logger.warning(
                            "Credentials updated but cannot restore full login session "
                            "(device_id=%s, user_id=%s)",
                            e2ee_device_id,
                            user_id,
                        )
                else:
                    if not getattr(whoami_response, "device_id", None):
                        facade.logger.warning(
                            "whoami response did not contain device_id"
                        )
                    else:
                        facade.logger.debug(
                            "whoami confirmed existing device_id and user_id; no credential updates needed"
                        )
            except facade.WHOAMI_USER_ID_FALLBACK_EXCEPTIONS as e:
                facade.logger.warning(f"Failed to discover device_id via whoami: {e}")
                facade.logger.warning("E2EE may not work properly without a device_id")
    else:
        client.access_token = access_token
        if user_id:
            client.user_id = user_id
        else:
            try:
                whoami_response = await client.whoami()
                discovered_user_id = getattr(whoami_response, "user_id", None)

                if discovered_user_id:
                    client.user_id = discovered_user_id
                    user_id = discovered_user_id
                    auth_info.user_id = user_id
                    facade.logger.debug(
                        "Discovered user_id via whoami: %s", discovered_user_id
                    )
                else:
                    facade.logger.warning("whoami response did not contain user_id")
            except facade.NIO_COMM_EXCEPTIONS as e:
                facade.logger.warning("Failed to discover user_id via whoami: %s", e)

    return e2ee_device_id


async def _maybe_upload_e2ee_keys(client: "facade.AsyncClient") -> None:
    """
    Upload end-to-end encryption (E2EE) keys using the given Matrix client when the client requests it.

    If the client's `should_upload_keys` is true, calls the client's key upload routine. Network/transport errors are caught and logged (with a recommendation to regenerate credentials); no exception is propagated.

    Parameters:
        client (AsyncClient): Matrix AsyncClient whose `keys_upload()` will be invoked when needed.
    """
    try:
        if client.should_upload_keys:
            facade.logger.info("Uploading encryption keys...")
            await client.keys_upload()
            facade.logger.info("Encryption keys uploaded successfully")
        else:
            facade.logger.debug("No key upload needed - keys already present")
    except facade.NIO_COMM_EXCEPTIONS:
        facade.logger.exception(
            "Failed to upload E2EE keys. Consider regenerating credentials with: mmrelay auth login"
        )


async def _close_matrix_client_after_failure(
    client: "facade.AsyncClient | None", context: str
) -> None:
    """
    Close the provided Matrix AsyncClient and clear the module-level client reference when appropriate.

    Closes the given client instance, re-raises asyncio.CancelledError, and suppresses common nio/network exceptions; uses the `context` string in debug log messages when suppressing errors. If the module-global `matrix_client` refers to the same instance, it is set to None.

    Parameters:
        client (AsyncClient | None): The Matrix client to close; no action is taken if None.
        context (str): Short description of why the client is being closed, used in debug logging.
    """
    if not client:
        return
    try:
        await client.close()
    except asyncio.CancelledError:
        raise
    except facade.NIO_COMM_EXCEPTIONS:
        facade.logger.debug(
            "Ignoring error while closing client after %s", context, exc_info=True
        )
    finally:
        if facade.matrix_client is client:
            facade.matrix_client = None
