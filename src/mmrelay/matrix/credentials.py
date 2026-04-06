from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional, cast

import mmrelay.matrix_utils as facade
from mmrelay.cli_utils import msg_require_auth_login
from mmrelay.config import (
    InvalidCredentialsPathTypeError,
)
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_DEVICE_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_KEY_PASSWORD,
    CONFIG_KEY_USER_ID,
    REQUIRED_CREDENTIALS_KEYS,
)

__all__ = [
    "_resolve_credentials_save_path",
    "_missing_credentials_keys",
    "_resolve_and_load_credentials",
]


def _resolve_credentials_save_path(config_data: dict[str, Any] | None) -> str | None:
    """
    Resolve the filesystem path to save Matrix credentials.

    Checks for an explicit credentials path in `config_data` and returns it with `~` expanded;
    if none is provided, returns the canonical credentials path. Returns `None` when the path
    cannot be resolved due to invalid types or filesystem errors.

    Parameters:
        config_data (dict[str, Any] | None): Configuration data that may contain an explicit
            credentials path; may be None.

    Returns:
        str | None: Absolute path to use for saving credentials, or `None` if resolution fails.
    """
    try:
        explicit_path = facade.get_explicit_credentials_path(config_data)
        if explicit_path:
            return os.path.expanduser(explicit_path)
        return str(facade.get_credentials_path())
    except (InvalidCredentialsPathTypeError, TypeError, OSError, ValueError) as exc:
        facade.logger.debug("Failed to resolve credentials path: %s", exc)
        return None


def _missing_credentials_keys(credentials: dict[str, Any]) -> list[str]:
    """
    Identify which required credential keys are missing or invalid in the provided mapping.

    Parameters:
        credentials (dict[str, Any]): Mapping containing credential keys and their values.

    Returns:
        list[str]: List of required keys that are not present or are empty in `credentials`.
    """
    missing_keys: list[str] = []
    for key in REQUIRED_CREDENTIALS_KEYS:
        value = credentials.get(key)
        if not isinstance(value, str) or not value.strip():
            missing_keys.append(key)
    return missing_keys


async def _resolve_and_load_credentials(
    config_data: dict[str, Any] | None,
    matrix_section: Any,
) -> facade.MatrixAuthInfo | None:
    """
    Resolve Matrix authentication information from saved credentials, automatic login, or provided configuration.

    Attempts to load and validate credentials.json; if valid credentials are found returns a MatrixAuthInfo populated from them. If no valid credentials file exists but the provided matrix section contains a password, performs an automatic login to create and load credentials. If neither a credentials file nor usable configuration are available, returns None.

    Parameters:
        config_data (dict[str, Any] | None): Full application configuration; required when falling back to non-file-based config or when prompting for interactive flows.
        matrix_section (Any): The parsed `matrix` section from the configuration (expected to be a mapping when present); may contain access_token, homeserver, bot_user_id/user_id, or password.

    Returns:
        MatrixAuthInfo | None: A MatrixAuthInfo populated with homeserver, access token, user id, optional device id, original credentials dict, and credentials_path when available; otherwise `None` when authentication cannot be resolved.
    """
    credentials: dict[str, Any] | None = None
    e2ee_device_id: Optional[str] = None
    credentials_path: str | None = None

    candidate_path = await asyncio.to_thread(
        _resolve_credentials_save_path, config_data
    )

    try:
        credentials = await facade.async_load_credentials()
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        facade.logger.warning("Error loading credentials: %s", exc)
        credentials = None

    if credentials is None:
        if candidate_path and await asyncio.to_thread(os.path.isfile, candidate_path):  # type: ignore[arg-type]
            facade.logger.warning(
                "Ignoring invalid credentials file: %s", candidate_path
            )

    if credentials:
        missing_keys = _missing_credentials_keys(credentials)
        if missing_keys:
            facade.logger.warning(
                "Ignoring credentials.json missing required keys: %s",
                ", ".join(missing_keys),
            )
            credentials = None
        else:
            credentials_path = candidate_path
            matrix_homeserver = credentials[CONFIG_KEY_HOMESERVER]
            matrix_access_token = credentials[CONFIG_KEY_ACCESS_TOKEN]
            raw_user_id = facade._first_nonblank_str(
                credentials.get(CONFIG_KEY_USER_ID),
                credentials.get(CONFIG_KEY_BOT_USER_ID),
            )
            normalized_user_id = (
                facade._normalize_bot_user_id(matrix_homeserver, raw_user_id)
                if raw_user_id
                else None
            )
            bot_user_id = normalized_user_id or ""
            e2ee_device_id = facade._get_valid_device_id(
                credentials.get(CONFIG_KEY_DEVICE_ID)
            )

            facade.logger.debug(f"Using Matrix credentials (device: {e2ee_device_id})")

            if (
                isinstance(matrix_section, dict)
                and CONFIG_KEY_ACCESS_TOKEN in matrix_section
            ):
                facade.logger.info(
                    "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
                )

            return facade.MatrixAuthInfo(
                homeserver=matrix_homeserver,
                access_token=matrix_access_token,
                user_id=bot_user_id,
                device_id=e2ee_device_id,
                credentials=credentials,
                credentials_path=credentials_path,
            )

    if facade._can_auto_create_credentials(matrix_section):
        matrix_section = cast(dict[str, Any], matrix_section)
        facade.logger.info(
            "No credentials.json found, but config.yaml has password field. Attempting automatic login..."
        )

        homeserver = matrix_section[CONFIG_KEY_HOMESERVER]
        username = facade._first_nonblank_str(
            matrix_section.get(CONFIG_KEY_BOT_USER_ID),
            matrix_section.get(CONFIG_KEY_USER_ID),
        )
        if username:
            username = facade._normalize_bot_user_id(homeserver, username)
        password = matrix_section[CONFIG_KEY_PASSWORD]

        try:
            success = await facade.login_matrix_bot(
                homeserver=homeserver,
                username=username,
                password=password,
                logout_others=False,
                config_for_paths=config_data if isinstance(config_data, dict) else None,
            )

            if success:
                facade.logger.info(
                    "Automatic login successful! Credentials saved to credentials.json"
                )
                credentials = await facade.async_load_credentials()
                if not credentials:
                    facade.logger.error("Failed to load newly created credentials")
                    return None

                missing_keys = _missing_credentials_keys(credentials)
                if missing_keys:
                    facade.logger.error(
                        "Newly created credentials.json missing required keys: %s",
                        ", ".join(missing_keys),
                    )
                    return None

                credentials_path = await asyncio.to_thread(
                    _resolve_credentials_save_path, config_data
                )
                matrix_homeserver = credentials[CONFIG_KEY_HOMESERVER]
                matrix_access_token = credentials[CONFIG_KEY_ACCESS_TOKEN]
                raw_user_id = credentials.get(CONFIG_KEY_USER_ID)
                bot_user_id = (
                    raw_user_id.strip() if isinstance(raw_user_id, str) else ""
                )
                e2ee_device_id = facade._get_valid_device_id(
                    credentials.get(CONFIG_KEY_DEVICE_ID)
                )

                return facade.MatrixAuthInfo(
                    homeserver=matrix_homeserver,
                    access_token=matrix_access_token,
                    user_id=bot_user_id,
                    device_id=e2ee_device_id,
                    credentials=credentials,
                    credentials_path=credentials_path,
                )
            else:
                facade.logger.error(
                    "Automatic login failed. Please check your credentials or use 'mmrelay auth login'"
                )
                return None
        except asyncio.CancelledError:
            raise
        except (OSError, IOError) as e:
            facade.logger.exception(
                "I/O error during automatic login (%s). Please use 'mmrelay auth login' for interactive setup",
                type(e).__name__,
            )
            return None

    if config_data is None:
        facade.logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    if "matrix" not in config_data:
        facade.logger.error(
            "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
        )
        facade.logger.error(msg_require_auth_login())
        return None

    if not isinstance(matrix_section, dict):
        facade.logger.error(
            "Matrix configuration section is empty or invalid (expected a mapping under 'matrix')."
        )
        facade.logger.error(msg_require_auth_login())
        return None

    matrix_access_token = matrix_section.get(CONFIG_KEY_ACCESS_TOKEN)
    if not isinstance(matrix_access_token, str) or not matrix_access_token.strip():
        auth_keys = (
            CONFIG_KEY_ACCESS_TOKEN,
            CONFIG_KEY_PASSWORD,
            CONFIG_KEY_HOMESERVER,
            CONFIG_KEY_BOT_USER_ID,
            CONFIG_KEY_USER_ID,
        )
        present_auth_keys: list[str] = []
        for key in auth_keys:
            value = matrix_section.get(key)
            if isinstance(value, str) and value.strip():
                present_auth_keys.append(key)
        if present_auth_keys:
            facade.logger.error(
                "Matrix section is missing required field: '%s'",
                CONFIG_KEY_ACCESS_TOKEN,
            )
        else:
            facade.logger.error(
                "Matrix section contains non-auth settings only (for example E2EE options), "
                "and no credentials.json was found."
            )
        facade.logger.error(msg_require_auth_login())
        return None

    matrix_homeserver = matrix_section.get(CONFIG_KEY_HOMESERVER)
    if not isinstance(matrix_homeserver, str) or not matrix_homeserver.strip():
        facade.logger.error(
            "Matrix section is missing required field: '%s'",
            CONFIG_KEY_HOMESERVER,
        )
        return None

    raw_user_id = facade._first_nonblank_str(
        matrix_section.get(CONFIG_KEY_BOT_USER_ID),
        matrix_section.get(CONFIG_KEY_USER_ID),
    )
    if isinstance(raw_user_id, str) and raw_user_id.strip():
        normalized_user_id = facade._normalize_bot_user_id(
            matrix_homeserver, raw_user_id
        )
        if normalized_user_id is None:
            facade.logger.error("Matrix section has invalid bot_user_id")
            return None
        bot_user_id = normalized_user_id
    else:
        facade.logger.warning(
            "Matrix section missing bot_user_id; continuing with access_token-only configuration"
        )
        bot_user_id = ""

    return facade.MatrixAuthInfo(
        homeserver=matrix_homeserver,
        access_token=matrix_access_token,
        user_id=bot_user_id,
        device_id=None,
        credentials=None,
        credentials_path=None,
    )
