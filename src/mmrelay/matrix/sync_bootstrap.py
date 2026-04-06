from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import logging
import os
import re
import ssl
from typing import Any, Optional, cast
from urllib.parse import urlparse

from nio import (
    AsyncClientConfig,
    SyncError,
)

import mmrelay.config as config_module
import mmrelay.matrix_utils as facade
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_DEVICE_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_KEY_USER_ID,
    E2EE_KEY_SHARING_DELAY_SECONDS,
)
from mmrelay.constants.network import (
    MATRIX_LOGIN_TIMEOUT,
)
from mmrelay.paths import E2EENotSupportedError

NIO_COMM_EXCEPTIONS = facade.NIO_COMM_EXCEPTIONS
JSONSCHEMA_VALIDATION_ERROR = facade.JSONSCHEMA_VALIDATION_ERROR
SYNC_RETRY_EXCEPTIONS = facade.SYNC_RETRY_EXCEPTIONS
WHOAMI_USER_ID_FALLBACK_EXCEPTIONS = facade.WHOAMI_USER_ID_FALLBACK_EXCEPTIONS

__all__ = [
    "_perform_initial_sync",
    "_post_sync_setup",
    "connect_matrix",
    "login_matrix_bot",
    "join_matrix_room",
]


async def _perform_initial_sync(
    client: facade.AsyncClient, matrix_homeserver: str
) -> Any | None:
    """
    Perform the initial Matrix sync and tolerate common homeserver quirks.

    Performs a full-state initial sync and, on schema validation or transport issues, retries using an invite-safe filter. If invites contain malformed invite_state payloads, a retry may ignore those invite_state events to allow the sync to succeed. When an invite-safe filter is applied, the filter is recorded on the client (mmrelay_sync_filter and mmrelay_first_sync_filter) to disable invite handling for subsequent syncs.

    Returns:
        The sync response object when successful, or `None` if no response was obtained.

    Raises:
        MatrixSyncTimeoutError: if the initial sync operation times out.
        MatrixSyncFailedError: if the sync ultimately fails due to communication or validation errors.
    """
    invite_safe_filter: dict[str, Any] = {"room": {"invite": {"limit": 0}}}
    sync_response: Any | None = None

    max_sync_attempts = facade.MATRIX_INITIAL_SYNC_MAX_ATTEMPTS
    sync_attempt = 1
    retry_delay = facade.MATRIX_SYNC_RETRY_DELAY_SECS

    while True:
        try:
            sync_response = await asyncio.wait_for(
                client.sync(timeout=facade.MATRIX_EARLY_SYNC_TIMEOUT, full_state=True),
                timeout=facade.MATRIX_SYNC_OPERATION_TIMEOUT,
            )
            break
        except asyncio.TimeoutError:
            reached_attempt_limit = (
                max_sync_attempts > 0 and sync_attempt >= max_sync_attempts
            )
            if not reached_attempt_limit:
                attempt_display = (
                    f"{sync_attempt}/{max_sync_attempts}"
                    if max_sync_attempts > 0
                    else f"{sync_attempt}/∞"
                )
                facade.logger.warning(
                    "Initial sync timed out after %.1f seconds (attempt %s); retrying in %.1fs",
                    facade.MATRIX_SYNC_OPERATION_TIMEOUT,
                    attempt_display,
                    retry_delay,
                )
                await asyncio.sleep(retry_delay)
                sync_attempt += 1
                retry_delay = min(
                    retry_delay * 2.0, facade.MATRIX_INITIAL_SYNC_RETRY_MAX_DELAY_SECS
                )
                continue

            facade.logger.exception(
                "Initial sync timed out after %.1f seconds (final attempt %d/%d)",
                facade.MATRIX_SYNC_OPERATION_TIMEOUT,
                sync_attempt,
                max_sync_attempts,
            )
            facade.logger.error(
                "This indicates a network connectivity issue or slow Matrix server."
            )
            facade.logger.error("Troubleshooting steps:")
            facade.logger.error("1. Check your internet connection")
            facade.logger.error(
                f"2. Verify the homeserver is accessible: {matrix_homeserver}"
            )
            facade.logger.error(
                "3. Try again in a few minutes - the server may be temporarily overloaded"
            )
            facade.logger.error(
                "4. Consider using a different Matrix homeserver if the problem persists"
            )
            raise facade.MatrixSyncTimeoutError() from None
        except asyncio.CancelledError:
            facade.logger.exception("Initial sync cancelled")
            raise
        except JSONSCHEMA_VALIDATION_ERROR as exc:
            facade.logger.exception("Initial sync response failed schema validation.")
            facade.logger.warning(
                "This usually indicates a non-compliant homeserver or proxy response."
            )
            facade.logger.warning(
                "Retrying initial sync without invites to tolerate invalid invite_state payloads."
            )
            try:
                sync_response = await asyncio.wait_for(
                    client.sync(
                        timeout=facade.MATRIX_EARLY_SYNC_TIMEOUT,
                        full_state=False,
                        sync_filter=invite_safe_filter,
                    ),
                    timeout=facade.MATRIX_SYNC_OPERATION_TIMEOUT,
                )
                cast(Any, client).mmrelay_sync_filter = invite_safe_filter
                cast(Any, client).mmrelay_first_sync_filter = invite_safe_filter
                facade.logger.info(
                    "Initial sync completed after invite-safe retry. "
                    "Invite handling is disabled for subsequent syncs."
                )
            except JSONSCHEMA_VALIDATION_ERROR:
                facade.logger.exception("Invite-safe sync retry failed")
                facade.logger.warning(
                    "Invite-safe sync retry failed schema validation; "
                    "attempting to ignore invalid invite_state payloads."
                )

                async def _sync_ignore_invalid_invites() -> Any:
                    """
                    Perform a Matrix sync using an invite-safe filter while ignoring malformed or schema-invalid invite_state payloads.

                    Temporarily treats invalid invite_state values as empty for the duration of the sync to avoid schema validation failures, then restores the original nio SyncResponse behavior.

                    Returns:
                        The sync response object (typically a `nio.responses.SyncResponse`).
                    """
                    import nio.responses as nio_responses

                    original_descriptor = vars(nio_responses.SyncResponse).get(
                        "_get_invite_state"
                    )
                    original_callable = getattr(
                        nio_responses.SyncResponse, "_get_invite_state", None
                    )

                    def _safe_get_invite_state(parsed_dict: Any) -> list[Any]:
                        """
                        Extract invite-state events from a parsed invite payload.

                        Parameters:
                            parsed_dict (Any): Parsed invite payload; expected to be a dict containing an "events" key.

                        Returns:
                            list[Any]: The list of invite-state events if present and successfully processed, otherwise an empty list.
                        """
                        if (
                            not isinstance(parsed_dict, dict)
                            or "events" not in parsed_dict
                        ):
                            return []
                        try:
                            if callable(original_callable):
                                return cast(
                                    list[Any],
                                    original_callable(parsed_dict),
                                )
                        except JSONSCHEMA_VALIDATION_ERROR:
                            facade.logger.warning(
                                "Invalid invite_state payload; ignoring invite_state events."
                            )
                            return []
                        return []

                    async with facade._MATRIX_STARTUP_SYNC_LOCK:
                        try:
                            # Class-level monkey-patch protected by the startup sync lock.
                            # A concurrent sync_forever() could theoretically observe the
                            # patched method, but this is acceptable because the safe
                            # wrapper is strictly more robust (it only adds error handling).
                            # The patch is always restored in the finally block below.
                            nio_responses.SyncResponse._get_invite_state = staticmethod(  # pyright: ignore[reportAttributeAccessIssue]  # type: ignore[misc]
                                _safe_get_invite_state
                            )
                            return await asyncio.wait_for(
                                client.sync(
                                    timeout=facade.MATRIX_EARLY_SYNC_TIMEOUT,
                                    full_state=False,
                                    sync_filter=invite_safe_filter,
                                ),
                                timeout=facade.MATRIX_SYNC_OPERATION_TIMEOUT,
                            )
                        finally:
                            if original_descriptor is not None:
                                nio_responses.SyncResponse._get_invite_state = (
                                    original_descriptor
                                )
                            else:
                                if (
                                    "_get_invite_state"
                                    in nio_responses.SyncResponse.__dict__
                                ):
                                    with contextlib.suppress(AttributeError, TypeError):
                                        del nio_responses.SyncResponse._get_invite_state

                try:
                    sync_response = await _sync_ignore_invalid_invites()
                    cast(Any, client).mmrelay_sync_filter = invite_safe_filter
                    cast(Any, client).mmrelay_first_sync_filter = invite_safe_filter
                    facade.logger.info(
                        "Initial sync completed after invite-safe retry "
                        "with invalid invite_state payloads ignored."
                    )
                except (ImportError, AttributeError):
                    facade.logger.debug(
                        "Invite-safe sync retry handler failed", exc_info=True
                    )
                except asyncio.CancelledError:
                    facade.logger.exception("Invite-ignoring sync retry cancelled")
                    raise
                except SYNC_RETRY_EXCEPTIONS:
                    facade.logger.exception("Invite-ignoring sync retry failed")
            except asyncio.TimeoutError:
                facade.logger.exception(
                    "Invite-safe sync retry timed out after %s seconds",
                    facade.MATRIX_SYNC_OPERATION_TIMEOUT,
                )
            except asyncio.CancelledError:
                facade.logger.exception("Invite-safe sync retry cancelled")
                raise
            except NIO_COMM_EXCEPTIONS:
                facade.logger.exception("Invite-safe sync retry failed")

            if sync_response is None:
                facade.logger.exception("Matrix sync failed")
                raise facade.MatrixSyncFailedError() from exc
            break
        except NIO_COMM_EXCEPTIONS as exc:
            facade.logger.exception("Matrix sync failed")
            raise facade.MatrixSyncFailedError() from exc

    return sync_response


async def _post_sync_setup(
    client: facade.AsyncClient,
    sync_response: Any | None,
    config_data: dict[str, Any],
    matrix_rooms: Any,
    matrix_homeserver: str,
    bot_user_id: str,
    e2ee_enabled: bool,
) -> None:
    """
    Perform post-initial-sync setup: validate the sync result, resolve any configured room aliases in-place, evaluate and log per-room and overall E2EE/encryption status, and determine the bot's display name.

    Parameters:
        client (AsyncClient): Connected Matrix client used for alias resolution and state inspection.
        sync_response (Any | None): Result of the initial sync; if it represents a sync error the function aborts.
        config_data (dict[str, Any]): Configuration data used to evaluate E2EE settings and for display output.
        matrix_rooms (Any): In-memory matrix_rooms mapping (list or dict); room aliases found here will be resolved in-place to canonical room IDs.
        matrix_homeserver (str): Homeserver URL used for diagnostic logging when sync errors occur.
        bot_user_id (str): The bot's Matrix user ID used to fetch and store the bot display name.
        e2ee_enabled (bool): True when end-to-end encryption is intended for this client; client.e2ee_enabled is set to this value.

    Raises:
        MatrixSyncFailedDetailsError: If the provided sync_response indicates a SyncError; error type and a short, user-facing error message are included.
    """
    if isinstance(sync_response, SyncError):
        error_type = sync_response.__class__.__name__
        error_details = facade._get_detailed_matrix_error_message(sync_response)
        facade.logger.error(f"Initial sync failed: {error_type}")
        facade.logger.error(f"Error details: {error_details}")

        facade.logger.error(
            "This usually indicates a network connectivity issue or server problem."
        )
        facade.logger.error("Troubleshooting steps:")
        facade.logger.error("1. Check your internet connection")
        facade.logger.error(
            f"2. Verify the homeserver URL is correct: {matrix_homeserver}"
        )
        facade.logger.error("3. Ensure the Matrix server is online and accessible")
        facade.logger.error("4. Check if your credentials are still valid")

        raise facade.MatrixSyncFailedDetailsError(error_type, error_details)

    facade.logger.info(f"Initial sync completed. Found {len(client.rooms)} rooms.")

    e2ee_status = await asyncio.to_thread(
        facade.get_e2ee_status, config_data or {}, config_module.config_path
    )

    async def _resolve_alias(alias: str) -> str | None:
        """
        Resolve a Matrix room alias to its canonical room ID.

        Parameters:
            alias (str): A Matrix room alias (for example, "#room:server") to resolve.

        Returns:
            str | None: The canonical Matrix room ID (for example, "!roomid:server") if resolution succeeds, `None` if the client is unavailable or the alias could not be resolved.
        """
        if not client:
            facade.logger.warning(
                f"Cannot resolve alias {alias}: Matrix client is not available"
            )
            return None

        facade.logger.debug(f"Resolving alias from config: {alias}")
        try:
            response = await client.room_resolve_alias(alias)
            room_id = getattr(response, "room_id", None)
            if room_id:
                facade.logger.debug(f"Resolved alias {alias} to {room_id}")
                return cast(str, room_id)
            error_details = (
                getattr(response, "message", response)
                if response
                else "No response from server"
            )
            facade.logger.warning(f"Could not resolve alias {alias}: {error_details}")
        except NIO_COMM_EXCEPTIONS:
            facade.logger.exception(f"Error resolving alias {alias}")
        except (TypeError, ValueError, AttributeError):
            facade.logger.exception(f"Error resolving alias {alias}")
        except OSError:
            facade.logger.exception(f"Error resolving alias {alias}")
        return None

    await facade._resolve_aliases_in_mapping(matrix_rooms, _resolve_alias)

    facade._display_room_channel_mappings(client.rooms, config_data, dict(e2ee_status))

    warnings = facade.get_room_encryption_warnings(client.rooms, dict(e2ee_status))
    for warning in warnings:
        facade.logger.warning(warning)

    encrypted_count = sum(
        1 for room in client.rooms.values() if getattr(room, "encrypted", False)
    )
    facade.logger.debug(
        f"Found {encrypted_count} encrypted rooms out of {len(client.rooms)} total rooms"
    )
    facade.logger.debug(f"E2EE status: {e2ee_status['overall_status']}")

    if e2ee_enabled and encrypted_count == 0 and len(client.rooms) > 0:
        facade.logger.debug("No encrypted rooms detected - all rooms are plaintext")

    if e2ee_enabled:
        facade.logger.debug(
            f"Waiting for {E2EE_KEY_SHARING_DELAY_SECONDS} seconds to allow for key sharing..."
        )
        await asyncio.sleep(E2EE_KEY_SHARING_DELAY_SECONDS)

    try:
        response = await client.get_displayname(bot_user_id)
        displayname = getattr(response, "displayname", None)
        if displayname:
            facade.bot_user_name = displayname
        else:
            facade.bot_user_name = bot_user_id
    except NIO_COMM_EXCEPTIONS as e:
        facade.logger.debug(f"Failed to get bot display name for {bot_user_id}: {e}")
        facade.bot_user_name = bot_user_id

    cast(Any, client).e2ee_enabled = e2ee_enabled


async def connect_matrix(
    passed_config: dict[str, Any] | None = None,
) -> "facade.AsyncClient | None":
    """
    Create and initialize a Matrix AsyncClient using available credentials, optional end-to-end encryption, and an initial sync so the client has populated room state.

    Parameters:
        passed_config (dict[str, Any] | None): Optional configuration override for this connection attempt; when provided it is used instead of the module-level configuration.

    Returns:
        AsyncClient | None: The configured and initialized AsyncClient on success; `None` if credentials, configuration, or connection setup failed and no client could be created.

    Raises:
        MissingMatrixRoomsError: If the required top-level "matrix_rooms" configuration is missing or invalid.
        MatrixSyncTimeoutError: If the initial Matrix sync times out.
        MatrixSyncFailedError: If the initial Matrix sync fails.
        MatrixSyncFailedDetailsError: If the initial Matrix sync fails with detailed error information.
    """

    if passed_config is not None:
        facade.config = passed_config
        config_module.relay_config = passed_config

    if facade.config is None:
        facade.logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    if facade.matrix_client:
        return facade.matrix_client

    matrix_section = (
        facade.config.get("matrix") if isinstance(facade.config, dict) else None
    )

    auth_info = await facade._resolve_and_load_credentials(
        facade.config if isinstance(facade.config, dict) else None,
        matrix_section,
    )
    if auth_info is None:
        return None

    facade.matrix_homeserver = auth_info.homeserver
    facade.matrix_access_token = auth_info.access_token
    facade.bot_user_id = auth_info.user_id
    e2ee_device_id = auth_info.device_id

    if not isinstance(facade.config, dict):
        facade.logger.error(
            "Configuration is not a valid mapping. Cannot connect to Matrix."
        )
        return None

    if "matrix_rooms" not in facade.config or not isinstance(
        facade.config["matrix_rooms"], (dict, list)
    ):
        facade.logger.error(
            "Configuration is missing 'matrix_rooms' section or it is not a valid format (list or dict)"
        )
        facade.logger.error(
            "Please ensure your config.yaml includes a valid matrix_rooms configuration"
        )
        raise facade.MissingMatrixRoomsError()
    facade.matrix_rooms = facade.config["matrix_rooms"]

    ssl_context = facade._create_ssl_context()
    if ssl_context is None:
        facade.logger.warning(
            "Failed to create certifi/system SSL context; proceeding with AsyncClient defaults"
        )

    e2ee_enabled, e2ee_store_path = await facade._configure_e2ee(
        facade.config, matrix_section, e2ee_device_id
    )

    if not isinstance(facade.matrix_homeserver, str) or not facade.matrix_homeserver:
        facade.logger.error("Matrix homeserver is missing or invalid.")
        return None
    if (
        not isinstance(facade.matrix_access_token, str)
        or not facade.matrix_access_token
    ):
        facade.logger.error("Matrix access token is missing or invalid.")
        return None
    if not isinstance(facade.bot_user_id, str) or not facade.bot_user_id:
        facade.logger.warning(
            "Matrix user ID is missing or invalid; will attempt whoami after login."
        )
        facade.bot_user_id = ""

    async with facade._MATRIX_STARTUP_SYNC_LOCK:
        if facade.matrix_client:
            return facade.matrix_client

        facade._refresh_bot_start_timestamps()

        client = facade._initialize_matrix_client(
            homeserver=facade.matrix_homeserver,
            user_id=facade.bot_user_id,
            device_id=e2ee_device_id,
            e2ee_enabled=e2ee_enabled,
            e2ee_store_path=e2ee_store_path,
            ssl_context=ssl_context,
        )

    try:
        await facade._perform_matrix_login(client, auth_info)

        if auth_info.user_id and auth_info.user_id != facade.bot_user_id:
            facade.bot_user_id = auth_info.user_id

        if not facade.bot_user_id:
            resolved_user_id = client.user_id
            if isinstance(resolved_user_id, str) and resolved_user_id:
                facade.bot_user_id = resolved_user_id
            else:
                facade.logger.error("Matrix user ID is missing or invalid.")
                await facade._close_matrix_client_after_failure(
                    client, "connect_matrix setup"
                )
                return None

        if e2ee_enabled:
            await facade._maybe_upload_e2ee_keys(client)

        facade.logger.debug("Performing initial sync to initialize rooms...")
        sync_response = await _perform_initial_sync(client, facade.matrix_homeserver)
        await _post_sync_setup(
            client,
            sync_response,
            facade.config,
            facade.matrix_rooms,
            facade.matrix_homeserver,
            facade.bot_user_id,
            e2ee_enabled,
        )
    except BaseException:
        await facade._close_matrix_client_after_failure(client, "connect_matrix setup")
        raise
    else:
        async with facade._MATRIX_STARTUP_SYNC_LOCK:
            if facade.matrix_client is not None:
                await facade._close_matrix_client_after_failure(
                    client, "connect_matrix duplicate setup"
                )
                return facade.matrix_client
            facade.matrix_client = client
            return client


async def login_matrix_bot(
    homeserver: str | None = None,
    username: str | None = None,
    password: str | None = None,
    logout_others: bool | None = None,
    config_for_paths: dict[str, Any] | None = None,
) -> bool:
    """
    Perform an interactive login to a Matrix homeserver, persist the obtained session credentials, and prepare an optional E2EE store when enabled.

    Parameters:
        homeserver (str | None): Homeserver URL to use; when None the user will be prompted.
        username (str | None): Bot username or full MXID; when None the user will be prompted and the value will be normalized to a full MXID.
        password (str | None): Account password; when None the user will be prompted.
        logout_others (bool | None): If True attempt to log out other sessions; if False do not; if None and running interactively the user will be prompted (treated as False for non-interactive calls).
        config_for_paths (dict[str, Any] | None): Optional in-memory configuration used to resolve credential and E2EE file paths without reloading configuration from disk.

    Returns:
        bool: `True` if login succeeded and credentials were saved, `False` otherwise.
    """
    client = None
    try:
        if os.getenv("MMRELAY_DEBUG_NIO") == "1":
            logging.getLogger("nio").setLevel(logging.DEBUG)
            logging.getLogger("nio.client").setLevel(logging.DEBUG)
            logging.getLogger("nio.http_client").setLevel(logging.DEBUG)
            logging.getLogger("nio.responses").setLevel(logging.DEBUG)
            logging.getLogger("aiohttp").setLevel(logging.DEBUG)

        prompted_for_credentials = False
        username_included_serverpart = False

        if not homeserver:
            homeserver = facade.input(
                "Enter Matrix homeserver URL (e.g., https://matrix.org): "
            )
            prompted_for_credentials = True

        if not homeserver.startswith(("https://", "http://")):
            homeserver = "https://" + homeserver

        parsed = urlparse(homeserver)
        original_domain = parsed.hostname or urlparse(f"//{homeserver}").hostname
        if not original_domain:
            host = homeserver.split("://")[-1].split("/", 1)[0]
            original_domain = re.sub(r":\d+$", "", host)

        facade.logger.info(f"Performing server discovery for {homeserver}...")

        ssl_context = facade._create_ssl_context()
        if ssl_context is None:
            facade.logger.warning(
                "Failed to create SSL context for server discovery; falling back to default system SSL"
            )
        else:
            facade.logger.debug(f"SSL context created successfully: {ssl_context}")
            facade.logger.debug(f"SSL context protocol: {ssl_context.protocol}")
            facade.logger.debug(f"SSL context verify_mode: {ssl_context.verify_mode}")

        temp_client = facade.AsyncClient(homeserver, "", ssl=cast(Any, ssl_context))
        try:
            discovery_response = await asyncio.wait_for(
                temp_client.discovery_info(), timeout=MATRIX_LOGIN_TIMEOUT
            )

            try:
                if isinstance(discovery_response, facade.DiscoveryInfoResponse):
                    actual_homeserver = discovery_response.homeserver_url
                    facade.logger.info(
                        f"Server discovery successful: {actual_homeserver}"
                    )
                    homeserver = actual_homeserver
                elif isinstance(discovery_response, facade.DiscoveryInfoError):
                    facade.logger.info(
                        f"Server discovery failed, using original URL: {homeserver}"
                    )
                else:
                    if hasattr(discovery_response, "homeserver_url"):
                        actual_homeserver = discovery_response.homeserver_url
                        facade.logger.info(
                            f"Server discovery successful: {actual_homeserver}"
                        )
                        homeserver = actual_homeserver
                    else:
                        facade.logger.warning(
                            f"Server discovery returned unexpected response type, using original URL: {homeserver}"
                        )
            except TypeError as e:
                facade.logger.warning(
                    f"Server discovery error: {e}, using original URL: {homeserver}"
                )

        except asyncio.TimeoutError:
            facade.logger.warning(
                f"Server discovery timed out, using original URL: {homeserver}"
            )
        except Exception as e:
            facade.logger.warning(
                f"Server discovery error: {e}, using original URL: {homeserver}"
            )
        finally:
            await temp_client.close()

        if not username:
            username = facade.input(
                "Enter Matrix username (localpart, e.g., bot) or full user ID (e.g., @bot:example.com): "
            )
            prompted_for_credentials = True
        raw_username = username.strip() if isinstance(username, str) else ""
        username_included_serverpart = ":" in raw_username.lstrip("@")

        if original_domain:
            username = facade._normalize_bot_user_id(original_domain, username)
        else:
            username = facade._normalize_bot_user_id(homeserver, username)

        if not username:
            facade.logger.error("Username normalization failed")
            return False

        facade.logger.info(f"Using username: {username}")

        if not username.startswith("@"):
            facade.logger.warning(f"Username doesn't start with @: {username}")
        if username.count(":") != 1:
            facade.logger.warning(
                f"Username has unexpected colon count: {username.count(':')}"
            )

        username_special_chars = set(username or "") - set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@:.-_"
        )
        if username_special_chars:
            facade.logger.warning(
                f"Username contains unusual characters: {username_special_chars}"
            )

        if not password:
            password = getpass.getpass("Enter Matrix password: ")
            prompted_for_credentials = True

        if password:
            facade.logger.debug("Password provided for login")
        else:
            facade.logger.warning("No password provided")

        if logout_others is None and prompted_for_credentials:
            logout_others_input = facade.input(
                "Log out other sessions? (Y/n) [Default: Yes]: "
            ).lower()
            logout_others = (
                not logout_others_input.startswith("n") if logout_others_input else True
            )
        if logout_others is None:
            logout_others = False

        from mmrelay.config import load_config

        loaded_config_for_paths = config_for_paths
        e2ee_enabled = False
        if loaded_config_for_paths is None:
            try:
                loaded_config_for_paths = await asyncio.to_thread(load_config)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
                facade.logger.debug("Could not load config for credentials path: %s", e)

        existing_device_id = None
        credentials_path = None
        try:
            credentials_path = await asyncio.to_thread(
                facade._resolve_credentials_save_path, loaded_config_for_paths
            )

            if credentials_path:
                path_exists = await asyncio.to_thread(os.path.exists, credentials_path)
            else:
                path_exists = False
            existing_credentials_path = credentials_path if path_exists else None
            if existing_credentials_path is not None:

                def _load_direct(path: str) -> dict[str, Any] | None:
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            return cast(dict[str, Any], json.load(f))
                    except (OSError, json.JSONDecodeError, TypeError, ValueError):
                        return None

                existing_creds = await asyncio.to_thread(
                    _load_direct, existing_credentials_path
                )
                if existing_creds:
                    existing_user_id = facade._first_nonblank_str(
                        existing_creds.get(CONFIG_KEY_USER_ID),
                        existing_creds.get(CONFIG_KEY_BOT_USER_ID),
                    )
                    if existing_user_id:
                        existing_user_id = facade._normalize_bot_user_id(
                            original_domain or homeserver, existing_user_id
                        )
                    user_id_match = existing_user_id == username
                    if user_id_match:
                        existing_device_id = facade._get_valid_device_id(
                            existing_creds.get(CONFIG_KEY_DEVICE_ID)
                        )
                        if existing_device_id:
                            facade.logger.info(
                                "Reusing existing device_id: %s", existing_device_id
                            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            facade.logger.debug(f"Could not load existing credentials: {e}")

        if loaded_config_for_paths is not None:
            try:
                e2ee_enabled = facade.is_e2ee_enabled(loaded_config_for_paths)
            except (KeyError, TypeError, ValueError) as e:
                facade.logger.debug(f"Could not load config for E2EE check: {e}")
                e2ee_enabled = False
        else:
            facade.logger.debug(
                "Could not load config for E2EE check: config load failed"
            )
            e2ee_enabled = False

        facade.logger.debug(f"E2EE enabled in config: {e2ee_enabled}")

        store_path = None
        if e2ee_enabled:
            try:
                store_path = str(await asyncio.to_thread(facade.get_e2ee_store_dir))
            except E2EENotSupportedError as e:
                facade.logger.warning(
                    "E2EE is not supported on this platform; "
                    "disabling E2EE for this login session: %s",
                    e,
                )
                e2ee_enabled = False
                store_path = None
            except OSError as e:
                facade.logger.warning(
                    "Could not resolve E2EE store path; "
                    "disabling E2EE for this login session: %s",
                    e,
                )
                e2ee_enabled = False
                store_path = None

            if store_path is not None:
                await asyncio.to_thread(os.makedirs, store_path, exist_ok=True)
                facade.logger.debug(f"Using E2EE store path: {store_path}")
        else:
            facade.logger.debug("E2EE disabled in configuration, not using store path")

        client_config = AsyncClientConfig(
            store_sync_tokens=True, encryption_enabled=e2ee_enabled
        )

        localpart = facade._extract_localpart_from_mxid(username) or ""
        facade.logger.debug("Creating AsyncClient with:")
        facade.logger.debug(f"  homeserver: {homeserver}")
        facade.logger.debug(f"  username (MXID): {username}")
        facade.logger.debug(f"  localpart: {localpart}")
        facade.logger.debug(f"  device_id: {existing_device_id}")
        facade.logger.debug(f"  store_path: {store_path}")
        facade.logger.debug(f"  e2ee_enabled: {e2ee_enabled}")

        client = facade.AsyncClient(
            homeserver,
            localpart,
            device_id=existing_device_id,
            store_path=store_path,
            config=client_config,
            ssl=cast(Any, ssl_context),
        )

        facade.logger.debug("AsyncClient created successfully")

        facade.logger.info(f"Logging in as {username} to {homeserver}...")

        device_name = "mmrelay-e2ee" if e2ee_enabled else "mmrelay"
        try:
            if existing_device_id:
                client.device_id = existing_device_id

            facade.logger.debug(f"Attempting login to {homeserver} as {username}")
            facade.logger.debug("Login parameters:")
            facade.logger.debug(f"  device_name: {device_name}")
            facade.logger.debug(f"  client.user: {client.user}")
            facade.logger.debug(f"  client.homeserver: {client.homeserver}")

            try:
                from nio.api import Api

                method, path, data = Api.login(
                    user=localpart,
                    password=password,
                    device_name=device_name,
                    device_id=existing_device_id,
                )
                facade.logger.debug("Matrix API call details:")
                facade.logger.debug(f"  method: {method}")
                facade.logger.debug(f"  path: {path}")
                facade.logger.debug(f"  data length: {len(data) if data else 0}")

                parsed_data = json.loads(data)
                safe_data = {
                    k: (v if k != "password" else f"[{len(v)} chars]")
                    for k, v in parsed_data.items()
                }
                facade.logger.debug(f"  parsed data: {safe_data}")

            except Exception as e:
                facade.logger.error(f"Failed to test API call: {e}")

            response = await asyncio.wait_for(
                client.login(password, device_name=device_name),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            facade.logger.debug(f"Login response type: {type(response).__name__}")

            for attr in [
                "user_id",
                "device_id",
                "access_token",
                "status_code",
                "message",
            ]:
                if hasattr(response, attr):
                    value = getattr(response, attr)
                    if attr == "access_token":
                        facade.logger.debug(
                            f"Response.{attr}: {'present' if value else 'not present'} (type: {type(value).__name__})"
                        )
                    else:
                        facade.logger.debug(
                            f"Response.{attr}: {value} (type: {type(value).__name__})"
                        )
                else:
                    facade.logger.debug(f"Response.{attr}: NOT PRESENT")
        except asyncio.TimeoutError:
            facade.logger.exception(
                f"Login timed out after {MATRIX_LOGIN_TIMEOUT} seconds"
            )
            facade.logger.error(
                "This may indicate network connectivity issues or a slow Matrix server"
            )
            await client.close()
            return False
        except TypeError as e:
            if "'>=' not supported between instances of 'str' and 'int'" in str(e):
                facade.logger.error(
                    "Matrix-nio library error during login (known issue)"
                )
                facade.logger.error(
                    "This typically indicates invalid credentials or server response format issues"
                )
                facade.logger.error("Troubleshooting steps:")
                facade.logger.error("1. Verify your username and password are correct")
                facade.logger.error("2. Check if your account is locked or suspended")
                facade.logger.error("3. Try logging in through a web browser first")
                facade.logger.error(
                    "4. Ensure your Matrix server supports the login API"
                )
                facade.logger.error(
                    "5. Try using a different homeserver URL format (e.g., with https://)"
                )
            else:
                facade.logger.exception("Type error during login")
            await client.close()
            return False
        except Exception as e:
            error_type = type(e).__name__
            facade.logger.exception(f"Login failed with {error_type}")

            if isinstance(e, (ConnectionError, asyncio.TimeoutError)):
                facade.logger.error("Network connectivity issue detected.")
                facade.logger.error("Troubleshooting steps:")
                facade.logger.error("1. Check your internet connection")
                facade.logger.error(
                    f"2. Verify the homeserver URL is correct: {homeserver}"
                )
                facade.logger.error("3. Check if the Matrix server is online")
            elif isinstance(e, (ssl.SSLError, ssl.CertificateError)):
                facade.logger.error("SSL/TLS certificate issue detected.")
                facade.logger.error(
                    "This may indicate a problem with the server's SSL certificate."
                )
            elif "DNSError" in error_type or "NameResolutionError" in error_type:
                facade.logger.error("DNS resolution failed.")
                facade.logger.error(f"Cannot resolve hostname: {homeserver}")
                facade.logger.error("Check your DNS settings and internet connection.")
            elif "'user_id' is a required property" in str(e):
                facade.logger.error("Matrix server response validation failed.")
                facade.logger.error("This typically indicates:")
                facade.logger.error("1. Invalid username or password")
                facade.logger.error("2. Server response format not as expected")
                facade.logger.error("3. Matrix server compatibility issues")
                facade.logger.error("Troubleshooting steps:")
                facade.logger.error(
                    "1. Verify credentials by logging in via web browser"
                )
                facade.logger.error(
                    "2. Try using the full homeserver URL (e.g., https://matrix.org)"
                )
                facade.logger.error(
                    "3. Check if your Matrix server is compatible with matrix-nio"
                )
                facade.logger.error("4. Try a different Matrix server if available")

            else:
                facade.logger.error("Unexpected error during login.")

            await client.close()
            return False

        access_token = getattr(response, "access_token", None)
        if access_token:
            facade.logger.info("Login successful!")

            actual_user_id = None
            response_user_id = facade._first_nonblank_str(
                getattr(response, "user_id", None)
            )
            try:
                whoami_response = await client.whoami()
                whoami_user_id = facade._first_nonblank_str(
                    getattr(whoami_response, "user_id", None)
                )
                if whoami_user_id:
                    actual_user_id = whoami_user_id
                    facade.logger.debug("Got user_id from whoami: %s", actual_user_id)
                elif response_user_id:
                    actual_user_id = response_user_id
                    facade.logger.warning(
                        "whoami response did not include user_id; using login response user_id"
                    )
                else:
                    facade.logger.warning(
                        "whoami response did not include user_id and login response had no user_id; "
                        "saving credentials without user_id"
                    )
            except WHOAMI_USER_ID_FALLBACK_EXCEPTIONS as e:
                if response_user_id:
                    actual_user_id = response_user_id
                    facade.logger.warning(
                        "whoami call failed: %s; using login response user_id", e
                    )
                else:
                    facade.logger.warning(
                        "whoami call failed: %s; login response had no user_id, "
                        "saving credentials without user_id",
                        e,
                    )

            response_device_id = facade._get_valid_device_id(
                getattr(response, "device_id", None)
            )
            resolved_device_id = response_device_id or existing_device_id
            credentials = {
                CONFIG_KEY_HOMESERVER: homeserver,
                CONFIG_KEY_ACCESS_TOKEN: getattr(response, "access_token", None),
                CONFIG_KEY_DEVICE_ID: resolved_device_id,
            }
            if actual_user_id:
                credentials[CONFIG_KEY_USER_ID] = actual_user_id

            credentials_path = await asyncio.to_thread(
                facade._resolve_credentials_save_path, loaded_config_for_paths
            )
            if not credentials_path:
                facade.logger.error("Could not resolve credentials save path")
                await client.close()
                return False
            await asyncio.to_thread(
                facade.save_credentials, credentials, credentials_path=credentials_path
            )
            facade.logger.info("Credentials saved to %s", credentials_path)

            if logout_others:
                facade.logger.info("Logging out other sessions...")
                facade.logger.warning("Logout others not yet implemented")

            await client.close()
            return True
        else:
            status_code = getattr(response, "status_code", None)
            error_message = getattr(response, "message", None)
            if status_code is not None and error_message is not None:
                facade.logger.error(f"Login failed: {type(response).__name__}")
                facade.logger.error(f"Error message: {error_message}")
                facade.logger.error(f"HTTP status code: {status_code}")

                numeric_status: int | None = None
                try:
                    numeric_status = int(status_code)
                except (TypeError, ValueError):
                    numeric_status = None
                status_text = str(status_code).strip().upper()

                if (
                    numeric_status == 401
                    or status_text == "M_FORBIDDEN"
                    or "M_FORBIDDEN" in str(error_message).upper()
                ):
                    facade.logger.error(
                        "Authentication failed - invalid username or password."
                    )
                    facade.logger.error("Troubleshooting steps:")
                    facade.logger.error(
                        "1. Verify your username and password are correct"
                    )
                    facade.logger.error(
                        "2. Check if your account is locked or suspended"
                    )
                    facade.logger.error("3. Try logging in through a web browser first")
                    facade.logger.error(
                        "4. Use 'mmrelay auth login' to set up new credentials"
                    )
                    if not username_included_serverpart:
                        facade.logger.error(
                            "5. If needed, retry with a full Matrix ID (e.g., @user:example.com)."
                        )
                elif numeric_status == 404:
                    facade.logger.error("User not found or homeserver not found.")
                    facade.logger.error(
                        f"Check that the homeserver URL is correct: {homeserver}"
                    )
                elif numeric_status == 429:
                    facade.logger.error("Rate limited - too many login attempts.")
                    facade.logger.error("Wait a few minutes before trying again.")
                elif numeric_status is not None and numeric_status >= 500:
                    facade.logger.error(
                        "Matrix server error - the server is experiencing issues."
                    )
                    facade.logger.error(
                        "Try again later or contact your server administrator."
                    )
                else:
                    facade.logger.error("Login failed for unknown reason.")
                    facade.logger.error(
                        "Try using 'mmrelay auth login' for interactive setup."
                    )
            else:
                facade.logger.error(
                    f"Unexpected login response: {type(response).__name__}"
                )
                facade.logger.error(
                    "This may indicate a matrix-nio library issue or server problem."
                )

            await client.close()
            return False

    except (*facade.NIO_COMM_EXCEPTIONS, ssl.SSLError, OSError):
        facade.logger.exception("Error during login")
        try:
            if client is not None:
                maybe_coro = client.close()
                if asyncio.iscoroutine(maybe_coro):
                    await maybe_coro
        except (OSError, RuntimeError, ConnectionError) as cleanup_e:
            facade.logger.debug(f"Ignoring error during client cleanup: {cleanup_e}")
        return False


async def join_matrix_room(
    matrix_client: facade.AsyncClient, room_id_or_alias: str
) -> None:
    """
    Join the bot to a Matrix room identified by a room ID or room alias.

    If `room_id_or_alias` is a room alias (starts with '#'), resolve it to a canonical room ID and update the in-memory `matrix_rooms` mapping with the resolved ID when available. If the client is already joined to the resolved room no join is attempted. Errors during alias resolution or joining are logged and do not raise exceptions.

    Parameters:
        room_id_or_alias (str): A Matrix room identifier, either a canonical room ID (e.g. "!abc:server")
            or a room alias (e.g. "#room:server"). When an alias is provided it will be resolved and the
            resulting room ID will be used for joining and recorded in the module-level `matrix_rooms` mapping.
    """
    if not isinstance(room_id_or_alias, str):
        facade.logger.error(
            "join_matrix_room expected a string room ID, received %r",
            room_id_or_alias,
        )
        return

    room_id: Optional[str] = room_id_or_alias

    if room_id_or_alias.startswith("#"):
        try:
            response = await matrix_client.room_resolve_alias(room_id_or_alias)
        except NIO_COMM_EXCEPTIONS:
            facade.logger.exception("Error resolving alias '%s'", room_id_or_alias)
            return

        room_id = getattr(response, "room_id", None) if response else None
        if not room_id:
            error_details = (
                getattr(response, "message", response)
                if response
                else "No response from server"
            )
            facade.logger.error(
                "Failed to resolve alias '%s': %s",
                room_id_or_alias,
                error_details,
            )
            return

        try:
            mapping = facade.matrix_rooms
        except NameError:
            mapping = None

        if mapping:
            try:
                facade._update_room_id_in_mapping(mapping, room_id_or_alias, room_id)
            except Exception:
                facade.logger.debug(
                    "Non-fatal error updating matrix_rooms for alias '%s'",
                    room_id_or_alias,
                    exc_info=True,
                )

        facade.logger.info("Resolved alias '%s' -> '%s'", room_id_or_alias, room_id)

    if room_id is None:
        facade.logger.error("Resolved room_id is None, cannot join room.")
        return

    try:
        if room_id not in matrix_client.rooms:
            response = await matrix_client.join(room_id)
            joined_room_id = getattr(response, "room_id", None) if response else None
            if joined_room_id:
                facade.logger.info(f"Joined room '{joined_room_id}' successfully")
            else:
                error_details = (
                    getattr(response, "message", response)
                    if response
                    else "No response from server"
                )
                facade.logger.error(
                    "Failed to join room '%s': %s",
                    room_id,
                    error_details,
                )
        else:
            facade.logger.debug(
                "Bot is already in room '%s', no action needed.",
                room_id,
            )
    except NIO_COMM_EXCEPTIONS:
        facade.logger.exception(f"Error joining room '{room_id}'")
    except Exception:
        facade.logger.exception(f"Unexpected error joining room '{room_id}'")
