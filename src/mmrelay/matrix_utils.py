# mypy: disable-error-code=import-untyped

import asyncio
import getpass
import html
import importlib
import io
import json
import logging
import mimetypes  # For dynamic content type detection and format handling
import os
import re
import ssl
import sys
import time
from typing import Any, Dict, Optional, Union, cast
from urllib.parse import urlparse

from nio import (  # type: ignore[import-untyped]
    AsyncClient,
    AsyncClientConfig,
    DiscoveryInfoError,
    DiscoveryInfoResponse,
    MatrixRoom,
    MegolmEvent,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    UploadError,
    UploadResponse,
)
from nio.events.room_events import RoomMemberEvent  # type: ignore[import-untyped]
from PIL import Image

# Local imports
from mmrelay.cli_utils import (
    _create_ssl_context,
    msg_require_auth_login,
    msg_retry_auth_login,
)
from mmrelay.config import (
    get_base_dir,
    get_e2ee_store_dir,
    get_meshtastic_config_value,
    load_credentials,
    save_credentials,
)
from mmrelay.constants.app import WINDOWS_PLATFORM
from mmrelay.constants.config import (
    CONFIG_SECTION_MATRIX,
    DEFAULT_BROADCAST_ENABLED,
    DEFAULT_DETECTION_SENSOR,
    E2EE_KEY_SHARING_DELAY_SECONDS,
)
from mmrelay.constants.database import DEFAULT_MSGS_TO_KEEP
from mmrelay.constants.formats import (
    DEFAULT_MATRIX_PREFIX,
    DEFAULT_MESHTASTIC_PREFIX,
    DETECTION_SENSOR_APP,
)
from mmrelay.constants.messages import (
    DEFAULT_MESSAGE_TRUNCATE_BYTES,
    DISPLAY_NAME_DEFAULT_LENGTH,
    MAX_TRUNCATION_LENGTH,
    MESHNET_NAME_ABBREVIATION_LENGTH,
    MESSAGE_PREVIEW_LENGTH,
    SHORTNAME_FALLBACK_LENGTH,
    TRUNCATION_LOG_LIMIT,
)
from mmrelay.constants.network import (
    MATRIX_EARLY_SYNC_TIMEOUT,
    MATRIX_LOGIN_TIMEOUT,
    MATRIX_ROOM_SEND_TIMEOUT,
    MATRIX_SYNC_OPERATION_TIMEOUT,
    MILLISECONDS_PER_SECOND,
)
from mmrelay.db_utils import (
    async_prune_message_map,
    async_store_message_map,
    get_message_map_by_matrix_event_id,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic, sendTextReply
from mmrelay.message_queue import get_message_queue, queue_message

# Import nio exception types with error handling for test environments
try:
    from nio.exceptions import (
        LocalProtocolError as NioLocalProtocolError,  # type: ignore[import-untyped]
    )
    from nio.exceptions import (
        LocalTransportError as NioLocalTransportError,  # type: ignore[import-untyped]
    )
    from nio.exceptions import (
        RemoteProtocolError as NioRemoteProtocolError,  # type: ignore[import-untyped]
    )
    from nio.exceptions import (
        RemoteTransportError as NioRemoteTransportError,  # type: ignore[import-untyped]
    )
    from nio.responses import (
        LoginError as NioLoginError,  # type: ignore[import-untyped]
    )
    from nio.responses import (
        LogoutError as NioLogoutError,  # type: ignore[import-untyped]
    )
except ImportError:
    # Fallback for test environments where nio imports might fail
    NioLoginError = Exception
    NioLogoutError = Exception
    NioLocalProtocolError = Exception
    NioRemoteProtocolError = Exception
    NioLocalTransportError = Exception
    NioRemoteTransportError = Exception

NIO_COMM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    NioLocalProtocolError,
    NioRemoteProtocolError,
    NioLocalTransportError,
    NioRemoteTransportError,
    asyncio.TimeoutError,
)
# Exception handling strategy:
# Catch only expected nio/network/timeouts so programming errors surface during testing.

logger = get_logger(name="Matrix")


def _is_room_alias(value: Any) -> bool:
    """Return True when value looks like a Matrix room alias (string starting with '#')."""

    return isinstance(value, str) and value.startswith("#")


def _get_valid_device_id(device_id_value: Any) -> Optional[str]:
    """Return device_id if it's a non-empty string, else None."""
    return (
        device_id_value
        if isinstance(device_id_value, str) and device_id_value.strip()
        else None
    )


def _iter_room_alias_entries(mapping):
    """
    Yield (alias_or_id, setter) pairs for entries in a Matrix room mapping.

    Each yielded tuple contains:
    - alias_or_id (str): the room alias or room ID found in the entry (may be an alias starting with '#' or a canonical room ID). If a dict entry has no `"id"` key, an empty string is yielded.
    - setter (callable): a function accepting a single argument `new_id` which updates the underlying mapping in-place to replace the alias with the resolved room ID.

    Supports two mapping shapes:
    - list: items may be strings (alias/ID) or dicts with an `"id"` key.
    - dict: values may be strings (alias/ID) or dicts with an `"id"` key.

    The setter updates the original collection (list element or dict value) so callers can resolve aliases and persist resolved IDs back into the provided mapping.
    """

    if isinstance(mapping, list):
        for index, entry in enumerate(mapping):
            if isinstance(entry, dict):
                yield (
                    entry.get("id", ""),
                    lambda new_id, target=entry: target.__setitem__("id", new_id),
                )
            else:
                yield (
                    entry,
                    lambda new_id, idx=index, collection=mapping: collection.__setitem__(
                        idx, new_id
                    ),
                )
    elif isinstance(mapping, dict):
        for key, entry in list(mapping.items()):
            if isinstance(entry, dict):
                yield (
                    entry.get("id", ""),
                    lambda new_id, target=entry: target.__setitem__("id", new_id),
                )
            else:
                yield (
                    entry,
                    lambda new_id, target_key=key, collection=mapping: collection.__setitem__(
                        target_key, new_id
                    ),
                )


async def _resolve_aliases_in_mapping(mapping, resolver):
    """
    Resolve Matrix room alias entries found in a mapping (list or dict) by calling an async resolver and replacing aliases with resolved room IDs in-place.

    This function iterates entries produced by _iter_room_alias_entries(mapping). For each entry whose key/value looks like a Matrix room alias (a string starting with '#'), it awaits the provided resolver coroutine with the alias; if the resolver returns a truthy room ID, the corresponding entry in the original mapping is updated via the entry's setter. If mapping is not a list or dict, the function logs a warning and returns without modifying anything.

    Parameters:
        mapping (list|dict): A mapping of Matrix rooms where some entries may be aliases (e.g., "#room:example.org").
        resolver (Callable[[str], Awaitable[Optional[str]]]): Async callable that accepts an alias and returns a resolved room ID (or falsy on failure).

    Returns:
        None
    """

    if not isinstance(mapping, (list, dict)):
        logger.warning(
            "matrix_rooms is expected to be a list or dict, got %s",
            type(mapping).__name__,
        )
        return

    for alias, setter in _iter_room_alias_entries(mapping):
        if _is_room_alias(alias):
            resolved_id = await resolver(alias)
            if resolved_id:
                setter(resolved_id)


def _update_room_id_in_mapping(mapping, alias, resolved_id) -> bool:
    """
    Replace a room alias with its resolved room ID in a mapping.

    Parameters:
        mapping (list|dict): A matrix_rooms mapping represented as a list of aliases or a dict of entries; only list and dict types are supported.
        alias (str): The room alias to replace (e.g., "#room:server").
        resolved_id (str): The canonical room ID to substitute for the alias (e.g., "!abcdef:server").

    Returns:
        bool: True if the alias was found and replaced with resolved_id; False if the mapping type is unsupported or the alias was not present.
    """

    if not isinstance(mapping, (list, dict)):
        return False

    for existing_alias, setter in _iter_room_alias_entries(mapping):
        if existing_alias == alias:
            setter(resolved_id)
            return True
    return False


def _display_room_channel_mappings(
    rooms: Dict[str, Any], config: Dict[str, Any], e2ee_status: Dict[str, Any]
) -> None:
    """
    Log Matrix rooms grouped by Meshtastic channel, showing mapping counts and E2EE/encryption indicators.

    Reads the "matrix_rooms" entry from config (accepting either dict or list form), builds a mapping from room ID to the configured "meshtastic_channel", then groups and logs rooms ordered by channel number. For each room logs an emoji/status depending on the room's encryption flag and the provided e2ee_status["overall_status"] (common values: "ready", "unavailable", "disabled").

    Parameters:
        rooms (dict): Mapping of room_id -> room object (room objects should expose at least `display_name` and `encrypted` attributes or fall back to the room_id).
        config (dict): Configuration dict containing a "matrix_rooms" section; entries should include "id" and "meshtastic_channel" when using dict/list room formats.
        e2ee_status (dict): E2EE status information; function expects an "overall_status" key used to determine messaging/encryption indicators.

    Returns:
        None
    """
    if not rooms:
        logger.info("Bot is not in any Matrix rooms")
        return

    # Get matrix_rooms configuration
    matrix_rooms_config = config.get("matrix_rooms", [])
    if not matrix_rooms_config:
        logger.info("No matrix_rooms configuration found")
        return

    # Normalize matrix_rooms configuration to list format
    if isinstance(matrix_rooms_config, dict):
        # Convert dict format to list format
        matrix_rooms_list = list(matrix_rooms_config.values())
    else:
        # Already in list format
        matrix_rooms_list = matrix_rooms_config

    # Create mapping of room_id -> channel number
    room_to_channel = {}
    for room_config in matrix_rooms_list:
        if isinstance(room_config, dict):
            room_id = room_config.get("id")
            channel = room_config.get("meshtastic_channel")
            if room_id and channel is not None:
                room_to_channel[room_id] = channel

    # Group rooms by channel
    channels: dict[int, list[tuple[str, Any]]] = {}

    for room_id, room in rooms.items():
        if room_id in room_to_channel:
            channel = room_to_channel[room_id]
            if channel not in channels:
                channels[channel] = []
            channels[channel].append((room_id, room))

    # Display header
    mapped_rooms = sum(len(room_list) for room_list in channels.values())
    logger.info(f"Matrix Rooms → Meshtastic Channels ({mapped_rooms} configured):")

    # Display rooms organized by channel (sorted by channel number)
    for channel in sorted(channels.keys()):
        room_list = channels[channel]
        logger.info(f"  Channel {channel}:")

        for room_id, room in room_list:
            room_name = getattr(room, "display_name", room_id)
            encrypted = getattr(room, "encrypted", False)

            # Format with encryption status
            if e2ee_status["overall_status"] == "ready":
                if encrypted:
                    logger.info(f"    🔒 {room_name}")
                else:
                    logger.info(f"    ✅ {room_name}")
            else:
                if encrypted:
                    if e2ee_status["overall_status"] == "unavailable":
                        logger.info(
                            f"    ⚠️ {room_name} (E2EE not supported - messages blocked)"
                        )
                    elif e2ee_status["overall_status"] == "disabled":
                        logger.info(
                            f"    ⚠️ {room_name} (E2EE disabled - messages blocked)"
                        )
                    else:
                        logger.info(
                            f"    ⚠️ {room_name} (E2EE incomplete - messages may be blocked)"
                        )
                else:
                    logger.info(f"    ✅ {room_name}")


def _can_auto_create_credentials(matrix_config: dict) -> bool:
    """
    Return True if the Matrix config provides non-empty strings for homeserver, a user id (bot_user_id or user_id), and password.

    Checks that the `matrix_config` contains the required fields to perform an automatic login flow by ensuring each value exists and is a non-blank string.

    Parameters:
        matrix_config (dict): The `matrix` section from config.yaml.

    Returns:
        bool: True when homeserver, (bot_user_id or user_id), and password are all present and non-empty strings; otherwise False.
    """
    homeserver = matrix_config.get("homeserver")
    user = matrix_config.get("bot_user_id") or matrix_config.get("user_id")
    password = matrix_config.get("password")
    return all(isinstance(v, str) and v.strip() for v in (homeserver, user, password))


def _normalize_bot_user_id(homeserver: str, bot_user_id: str) -> str:
    """
    Normalize a bot user identifier into a full Matrix MXID.

    Accepts several common input forms and returns a normalized Matrix ID of the form
    "@localpart:server". Behavior:
    - If bot_user_id is falsy, it is returned unchanged.
    - If bot_user_id already contains a server part (e.g. "@user:server.com" or "user:server.com"),
      the existing server is preserved (any trailing numeric port is removed).
    - If bot_user_id lacks a server part (e.g. "@user" or "user"), the server domain is derived
      from the provided homeserver and appended.
    - The homeserver argument is tolerant of missing URL scheme and will extract the hostname
      portion (handles inputs like "example.com", "https://example.com:8448", or
      "[::1]:8448/path").

    Parameters:
        homeserver (str): The Matrix homeserver URL or host used to derive a server domain.
        bot_user_id (str): A bot identifier in one of several forms (with or without leading "@"
            and with or without a server part).

    Returns:
        str: A normalized Matrix user ID in the form "@localpart:server".
    """
    if not bot_user_id:
        return bot_user_id

    def _canonical_server(value: str | None) -> str | None:
        if not value:
            return value
        value = value.strip()
        if value.startswith("[") and "]" in value:
            closing_index = value.find("]")
            value = value[1:closing_index]
        if value.count(":") == 1 and re.search(r":\d+$", value):
            value = value.rsplit(":", 1)[0]
        if ":" in value and not value.startswith("["):
            value = f"[{value}]"
        return value

    # Derive domain from homeserver (tolerate missing scheme; drop brackets/port/paths)
    parsed = urlparse(homeserver)
    domain = parsed.hostname or urlparse(f"//{homeserver}").hostname
    if not domain:
        # Last-ditch fallback for malformed inputs; drop any trailing :port
        host = homeserver.split("://")[-1].split("/", 1)[0]
        domain = re.sub(r":\d+$", "", host)

    domain = _canonical_server(domain) or ""

    # Normalize user ID
    localpart, *serverpart = bot_user_id.lstrip("@").split(":", 1)
    if serverpart and serverpart[0]:
        # Already has a server part; drop any brackets/port consistently
        raw_server = serverpart[0]
        server = urlparse(f"//{raw_server}").hostname or re.sub(
            r":\d+$",
            "",
            raw_server,
        )
        canonical_server = _canonical_server(server)
        return f"@{localpart}:{canonical_server or domain}"

    # No server part, add the derived domain
    return f"@{localpart.rstrip(':')}:{domain}"


def _get_msgs_to_keep_config():
    """
    Return the configured number of Meshtastic–Matrix message mappings to retain.

    Reads the global `config` and prefers the new location `database.msg_map.msgs_to_keep`.
    If that section is absent, falls back to the legacy `db.msg_map.msgs_to_keep` and emits a deprecation warning.
    If no configuration is available or `msgs_to_keep` is not set, returns DEFAULT_MSGS_TO_KEEP.

    Returns:
        int: Number of message mappings to keep.
    """
    global config
    if not config:
        return DEFAULT_MSGS_TO_KEEP

    msg_map_config = config.get("database", {}).get("msg_map", {})

    # If not found in database config, check legacy db config
    if not msg_map_config:
        legacy_msg_map_config = config.get("db", {}).get("msg_map", {})

        if legacy_msg_map_config:
            msg_map_config = legacy_msg_map_config
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )

    return msg_map_config.get("msgs_to_keep", DEFAULT_MSGS_TO_KEEP)


def _get_detailed_sync_error_message(sync_response) -> str:
    """
    Return a concise, user-facing explanation for why an initial Matrix sync failed.

    Given a sync response or error object (commonly a nio ErrorResponse, an HTTP/transport error, raw bytes, or any object exposing `message`, `status_code`, or `transport_response`), extract the most specific human-readable reason available and map common HTTP/transport conditions to short, actionable messages (e.g., authentication failure, forbidden, not found, rate limited, server error). Falls back to a generic network/connectivity message when no specific detail can be reliably extracted.

    Parameters:
        sync_response: The sync response or error object to summarize. May be bytes/bytearray, a nio ErrorResponse-like object, or any object with `message`, `status_code`, or `transport_response` attributes.

    Returns:
        str: A short, user-focused error description suitable for logs and brief troubleshooting hints.
    """
    try:
        # Handle bytes/bytearray types by converting to string
        if isinstance(sync_response, (bytes, bytearray)):
            try:
                sync_response = sync_response.decode("utf-8")
            except UnicodeDecodeError:
                return "Network connectivity issue or server unreachable (binary data)"

        # Try to extract specific error information
        message_attr = getattr(sync_response, "message", None)
        if message_attr:
            message = message_attr
            # Handle if message is bytes/bytearray
            if isinstance(message, (bytes, bytearray)):
                try:
                    message = message.decode("utf-8")
                except UnicodeDecodeError:
                    return "Network connectivity issue or server unreachable"
            return message
        status_code_attr = getattr(sync_response, "status_code", None)
        if status_code_attr:
            status_code = status_code_attr
            # Handle if status_code is not an int
            try:
                status_code = int(status_code)
            except (ValueError, TypeError):
                return "Network connectivity issue or server unreachable"

            if status_code == 401:
                return "Authentication failed - invalid or expired credentials"
            elif status_code == 403:
                return "Access forbidden - check user permissions"
            elif status_code == 404:
                return "Server not found - check homeserver URL"
            elif status_code == 429:
                return "Rate limited - too many requests"
            elif status_code >= 500:
                return f"Server error (HTTP {status_code}) - the Matrix server is experiencing issues"
            else:
                return f"HTTP error {status_code}"
        elif hasattr(sync_response, "transport_response"):
            # Check for transport-level errors
            transport = getattr(sync_response, "transport_response", None)
            if transport and hasattr(transport, "status_code"):
                try:
                    status_code = int(transport.status_code)
                    return f"Transport error: HTTP {status_code}"
                except (ValueError, TypeError):
                    return "Network connectivity issue or server unreachable"

        # Fallback to string representation with safety checks
        try:
            error_str = str(sync_response)
        except Exception:
            return "Network connectivity issue or server unreachable"

        # Clean up object repr strings that contain angle brackets
        if error_str and error_str != "None":
            # Remove object repr patterns like <object at 0x...>
            if "<" in error_str and ">" in error_str and " at 0x" in error_str:
                return "Network connectivity issue or server unreachable"
            # Remove HTML/XML-like content
            elif "<" in error_str and ">" in error_str:
                return "Network connectivity issue or server unreachable"
            elif "unknown error" in error_str.lower():
                return "Network connectivity issue or server unreachable"
            else:
                return error_str
        else:
            return "Network connectivity issue or server unreachable"

    except (AttributeError, ValueError, TypeError) as e:
        logger.debug(
            "Failed to extract sync error details from %r: %s", sync_response, e
        )
        # If we can't extract error details, provide a generic but helpful message
        return (
            "Unable to determine specific error - likely a network connectivity issue"
        )


def _create_mapping_info(
    matrix_event_id, room_id, text, meshnet=None, msgs_to_keep=None
):
    """
    Create metadata linking a Matrix event to a Meshtastic message for cross-network mapping.

    Strips quoted lines from `text` and populates a mapping dict containing identifiers and retention settings. If `msgs_to_keep` is None the value is obtained from _get_msgs_to_keep_config(). Returns None when any of `matrix_event_id`, `room_id`, or `text` is missing or falsy.

    Returns:
        dict or None: Mapping with keys:
            - matrix_event_id: original Matrix event id
            - room_id: Matrix room id
            - text: cleaned text with quoted lines removed
            - meshnet: optional meshnet name (may be None)
            - msgs_to_keep: number of message mappings to retain
    """
    if not matrix_event_id or not room_id or not text:
        return None

    if msgs_to_keep is None:
        msgs_to_keep = _get_msgs_to_keep_config()

    return {
        "matrix_event_id": matrix_event_id,
        "room_id": room_id,
        "text": strip_quoted_lines(text),
        "meshnet": meshnet,
        "msgs_to_keep": msgs_to_keep,
    }


def get_interaction_settings(config):
    """
    Determine if message reactions and replies are enabled in the configuration.

    Checks for both the new `message_interactions` structure and the legacy `relay_reactions` flag for backward compatibility. Returns a dictionary with boolean values for `reactions` and `replies`, defaulting to both disabled if not specified.
    """
    if config is None:
        return {"reactions": False, "replies": False}

    meshtastic_config = config.get("meshtastic", {})

    # Check for new structured configuration first
    if "message_interactions" in meshtastic_config:
        interactions = meshtastic_config["message_interactions"]
        return {
            "reactions": interactions.get("reactions", False),
            "replies": interactions.get("replies", False),
        }

    # Fall back to legacy relay_reactions setting
    if "relay_reactions" in meshtastic_config:
        enabled = meshtastic_config["relay_reactions"]
        logger.warning(
            "Configuration setting 'relay_reactions' is deprecated. "
            "Please use 'message_interactions: {reactions: bool, replies: bool}' instead. "
            "Legacy mode: enabling reactions only."
        )
        return {
            "reactions": enabled,
            "replies": False,
        }  # Only reactions for legacy compatibility

    # Default to privacy-first (both disabled)
    return {"reactions": False, "replies": False}


def message_storage_enabled(interactions):
    """
    Determine if message storage is needed based on enabled message interactions.

    Returns:
        True if either reactions or replies are enabled in the interactions dictionary; otherwise, False.
    """
    return interactions["reactions"] or interactions["replies"]


def _add_truncated_vars(format_vars, prefix, text):
    """Helper function to add variable length truncation variables to format_vars dict."""
    # Always add truncated variables, even for empty text (to prevent KeyError)
    text = text or ""  # Convert None to empty string
    logger.debug(f"Adding truncated vars for prefix='{prefix}', text='{text}'")
    for i in range(
        1, MAX_TRUNCATION_LENGTH + 1
    ):  # Support up to MAX_TRUNCATION_LENGTH chars, always add all variants
        truncated_value = text[:i]
        format_vars[f"{prefix}{i}"] = truncated_value
        if i <= TRUNCATION_LOG_LIMIT:  # Only log first few to avoid spam
            logger.debug(f"  {prefix}{i} = '{truncated_value}'")


def validate_prefix_format(format_string, available_vars):
    """Validate prefix format string against available variables.

    Args:
        format_string (str): The format string to validate.
        available_vars (dict): Dictionary of available variables with test values.

    Returns:
        tuple: (is_valid: bool, error_message: str or None)
    """
    try:
        # Test format with dummy data
        format_string.format(**available_vars)
        return True, None
    except (KeyError, ValueError) as e:
        return False, str(e)


def get_meshtastic_prefix(config, display_name, user_id=None):
    """
    Generate a Meshtastic message prefix using the configured format, supporting variable-length truncation and user-specific variables.

    If prefix formatting is enabled in the configuration, returns a formatted prefix string for Meshtastic messages using the user's display name and optional Matrix user ID. Supports custom format strings with placeholders for the display name, truncated display name segments (e.g., `{display5}`), and user ID components. Falls back to a default format if the custom format is invalid or missing. Returns an empty string if prefixing is disabled.

    Args:
        config (dict): The application configuration dictionary.
        display_name (str): The user's display name (room-specific or global).
        user_id (str, optional): The user's Matrix ID (@user:server.com).

    Returns:
        str: The formatted prefix string if enabled, empty string otherwise.

    Examples:
        Basic usage:
            get_meshtastic_prefix(config, "Alice Smith")
            # Returns: "Alice[M]: " (with default format)

        Custom format:
            config = {"meshtastic": {"prefix_format": "{display8}> "}}
            get_meshtastic_prefix(config, "Alice Smith")
            # Returns: "Alice Sm> "
    """
    meshtastic_config = config.get("meshtastic", {})

    # Check if prefixes are enabled
    if not meshtastic_config.get("prefix_enabled", True):
        return ""

    # Get custom format or use default
    prefix_format = meshtastic_config.get("prefix_format", DEFAULT_MESHTASTIC_PREFIX)

    # Parse username and server from user_id if available
    username = ""
    server = ""
    if user_id:
        # Extract username and server from @username:server.com format
        if user_id.startswith("@") and ":" in user_id:
            parts = user_id[1:].split(":", 1)  # Remove @ and split on first :
            username = parts[0]
            server = parts[1] if len(parts) > 1 else ""

    # Available variables for formatting with variable length support
    format_vars = {
        "display": display_name or "",
        "user": user_id or "",
        "username": username,
        "server": server,
    }

    # Add variable length display name truncation (display1, display2, display3, etc.)
    _add_truncated_vars(format_vars, "display", display_name)

    try:
        return prefix_format.format(**format_vars)
    except (KeyError, ValueError) as e:
        # Fallback to default format if custom format is invalid
        logger.warning(
            f"Invalid prefix_format '{prefix_format}': {e}. Using default format."
        )
        # The default format only uses 'display5', which is safe to format
        return DEFAULT_MESHTASTIC_PREFIX.format(
            display5=display_name[:DISPLAY_NAME_DEFAULT_LENGTH] if display_name else ""
        )


def get_matrix_prefix(config, longname, shortname, meshnet_name):
    """
    Generates a formatted prefix string for Meshtastic messages relayed to Matrix, based on configuration settings and sender/mesh network names.

    The prefix format supports variable-length truncation for the sender and mesh network names using template variables (e.g., `{long4}` for the first 4 characters of the sender name). Returns an empty string if prefixing is disabled in the configuration.

    Parameters:
        longname (str): Full Meshtastic sender name.
        shortname (str): Short Meshtastic sender name.
        meshnet_name (str): Name of the mesh network.

    Returns:
        str: The formatted prefix string, or an empty string if prefixing is disabled.
    """
    matrix_config = config.get(CONFIG_SECTION_MATRIX, {})

    # Enhanced debug logging for configuration troubleshooting
    logger.debug(
        f"get_matrix_prefix called with longname='{longname}', shortname='{shortname}', meshnet_name='{meshnet_name}'"
    )
    logger.debug(f"Matrix config section: {matrix_config}")

    # Check if prefixes are enabled for Matrix direction
    if not matrix_config.get("prefix_enabled", True):
        logger.debug("Matrix prefixes are disabled, returning empty string")
        return ""

    # Get custom format or use default
    matrix_prefix_format = matrix_config.get("prefix_format", DEFAULT_MATRIX_PREFIX)
    logger.debug(
        f"Using matrix prefix format: '{matrix_prefix_format}' (default: '{DEFAULT_MATRIX_PREFIX}')"
    )

    # Available variables for formatting with variable length support
    format_vars = {
        "long": longname,
        "short": shortname,
        "mesh": meshnet_name,
    }

    # Add variable length truncation for longname and mesh name
    _add_truncated_vars(format_vars, "long", longname)
    _add_truncated_vars(format_vars, "mesh", meshnet_name)

    try:
        result = matrix_prefix_format.format(**format_vars)
        logger.debug(
            f"Matrix prefix generated: '{result}' using format '{matrix_prefix_format}' with vars {format_vars}"
        )
        # Additional debug to help identify the issue
        if result == f"[{longname}/{meshnet_name}]: ":
            logger.debug(
                "Generated prefix matches default format - check if custom configuration is being loaded correctly"
            )
        return result
    except (KeyError, ValueError) as e:
        # Fallback to default format if custom format is invalid
        logger.warning(
            f"Invalid matrix prefix_format '{matrix_prefix_format}': {e}. Using default format."
        )
        # The default format only uses 'long' and 'mesh', which are safe
        return DEFAULT_MATRIX_PREFIX.format(
            long=longname or "", mesh=meshnet_name or ""
        )


# Global config variable that will be set from config.py
config = None

# These will be set in connect_matrix()
matrix_homeserver = None
matrix_rooms = None
matrix_access_token = None
bot_user_id = None
bot_user_name = None  # Detected upon logon
bot_start_time = int(
    time.time() * MILLISECONDS_PER_SECOND
)  # Timestamp when the bot starts, used to filter out old messages


matrix_client = None


def bot_command(command, event):
    """
    Checks if the given command is directed at the bot,
    accounting for variations in different Matrix clients.
    """
    full_message = event.body.strip()
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    # Check for simple !command format first
    if full_message.startswith(f"!{command}") or text_content.startswith(f"!{command}"):
        return True

    starts_with_id = bot_user_id and (
        full_message.startswith(bot_user_id) or text_content.startswith(bot_user_id)
    )
    starts_with_name = bot_user_name and (
        full_message.startswith(bot_user_name) or text_content.startswith(bot_user_name)
    )
    is_mention = starts_with_id or starts_with_name

    if is_mention:
        # Construct a regex pattern to match variations of bot mention and command
        parts = []
        if bot_user_id:
            parts.append(re.escape(bot_user_id))
        if bot_user_name:
            parts.append(re.escape(bot_user_name))
        # Only include explicit bot identifiers to avoid false positives with any @mention or #reference
        pattern = rf"^(?:{'|'.join(parts)})[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    return False


async def _connect_meshtastic():
    """Helper function to connect to Meshtastic, using executor in non-test environments."""
    if os.getenv("MMRELAY_TESTING") == "1" or "PYTEST_CURRENT_TEST" in os.environ:
        return connect_meshtastic()
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, connect_meshtastic)


async def connect_matrix(passed_config=None):
    """
    Initialize and return a configured matrix-nio AsyncClient connected to the configured Matrix homeserver.

    Creates or restores client credentials (prefers credentials.json, falls back to automatic login using username/password from config, then to direct tokens in config), optionally enables End-to-End Encryption when configured and dependencies are available, performs an initial full-state sync to populate rooms, resolves room aliases found in configuration, and sets module-level connection state used by other functions.

    Parameters:
        passed_config (dict | None): Optional configuration to use for this connection attempt; when provided it overrides the module-level config for this call.

    Returns:
        AsyncClient | None: A ready-to-use matrix-nio AsyncClient on success, or `None` if connection or credentials are unavailable.

    Raises:
        ValueError: If the required top-level "matrix_rooms" configuration is missing.
        ConnectionError: If the initial Matrix sync fails or times out.
    """
    global matrix_client, bot_user_name, matrix_homeserver, matrix_rooms, matrix_access_token, bot_user_id, config

    # Update the global config if a config is passed
    if passed_config is not None:
        config = passed_config

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    # Check if client already exists
    if matrix_client:
        return matrix_client

    # Check for credentials.json first
    credentials = None
    e2ee_device_id: Optional[str] = None
    credentials_path = None

    # Try to find credentials.json in the config directory
    try:
        from mmrelay.config import get_base_dir

        config_dir = get_base_dir()
        credentials_path = os.path.join(config_dir, "credentials.json")

        if os.path.exists(credentials_path):
            with open(credentials_path, "r", encoding="utf-8") as f:
                credentials = json.load(f)
    except Exception as e:
        logger.warning(f"Error loading credentials: {e}")

    # If credentials.json exists, use it
    if credentials:
        matrix_homeserver = credentials["homeserver"]
        matrix_access_token = credentials["access_token"]
        bot_user_id = credentials["user_id"]
        e2ee_device_id = _get_valid_device_id(credentials.get("device_id"))

        # Log consolidated credentials info
        logger.debug(f"Using Matrix credentials (device: {e2ee_device_id})")

        # If device_id is missing, warn but proceed; we'll learn and persist it after restore_login().
        if e2ee_device_id is None:
            logger.warning(
                "credentials.json has no valid device_id; proceeding to restore session and discover device_id."
            )

        # If config also has Matrix login info, let the user know we're ignoring it
        if config and "matrix" in config and "access_token" in config["matrix"]:
            logger.info(
                "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
            )
    # Check if we can automatically create credentials from config.yaml
    elif (
        config and "matrix" in config and _can_auto_create_credentials(config["matrix"])
    ):
        logger.info(
            "No credentials.json found, but config.yaml has password field. Attempting automatic login..."
        )

        matrix_section = config["matrix"]
        homeserver = matrix_section["homeserver"]
        username = matrix_section.get("bot_user_id") or matrix_section.get("user_id")
        # Normalize the username to ensure it's a full MXID
        if username:
            username = _normalize_bot_user_id(homeserver, username)
        password = matrix_section["password"]

        # Attempt automatic login
        try:
            success = await login_matrix_bot(
                homeserver=homeserver,
                username=username,
                password=password,
                logout_others=False,
            )

            if success:
                logger.info(
                    "Automatic login successful! Credentials saved to credentials.json"
                )
                # Load the newly created credentials and set up for credentials flow
                credentials = load_credentials()
                if not credentials:
                    logger.error("Failed to load newly created credentials")
                    return None

                # Set up variables for credentials-based connection
                matrix_homeserver = credentials["homeserver"]
                matrix_access_token = credentials["access_token"]
                bot_user_id = credentials["user_id"]
                e2ee_device_id = _get_valid_device_id(credentials.get("device_id"))
            else:
                logger.error(
                    "Automatic login failed. Please check your credentials or use 'mmrelay auth login'"
                )
                return None
        except Exception as e:
            logger.exception(f"Error during automatic login: {type(e).__name__}")
            logger.error("Please use 'mmrelay auth login' for interactive setup")
            return None
    else:
        # Check if config is available
        if config is None:
            logger.error("No configuration available. Cannot connect to Matrix.")
            return None

        # Check if matrix section exists in config
        if "matrix" not in config:
            logger.error(
                "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
            )
            logger.error(msg_require_auth_login())
            return None

        matrix_section = config["matrix"]

        # Check for required fields in matrix section
        required_fields = ["homeserver", "access_token", "bot_user_id"]
        missing_fields = [
            field for field in required_fields if field not in matrix_section
        ]

        if missing_fields:
            logger.error(f"Matrix section is missing required fields: {missing_fields}")
            logger.error(msg_require_auth_login())
            return None

        # Extract Matrix configuration from config
        matrix_homeserver = matrix_section["homeserver"]
        matrix_access_token = matrix_section["access_token"]
        bot_user_id = _normalize_bot_user_id(
            matrix_homeserver, matrix_section["bot_user_id"]
        )

        # Manual method does not support device_id - use auth system for E2EE
        e2ee_device_id = None

    # Get matrix rooms from config
    if "matrix_rooms" not in config:
        logger.error("Configuration is missing 'matrix_rooms' section")
        logger.error(
            "Please ensure your config.yaml includes matrix_rooms configuration"
        )
        raise ValueError("Missing required 'matrix_rooms' configuration")
    matrix_rooms = config["matrix_rooms"]

    # Create SSL context using certifi's certificates with system default fallback
    ssl_context = _create_ssl_context()
    if ssl_context is None:
        logger.warning(
            "Failed to create certifi/system SSL context; proceeding with AsyncClient defaults"
        )

    # Check if E2EE is enabled
    e2ee_enabled = False
    e2ee_store_path = None
    # Only initialize e2ee_device_id if not already set from credentials
    if "e2ee_device_id" not in locals():
        e2ee_device_id = None

    try:
        from mmrelay.config import is_e2ee_enabled

        # Check if E2EE is enabled using the helper function
        e2ee_enabled = is_e2ee_enabled(config)

        # Debug logging for E2EE detection
        logger.debug(
            f"E2EE detection: matrix config section present: {'matrix' in config}"
        )
        logger.debug(f"E2EE detection: e2ee enabled = {e2ee_enabled}")

        if e2ee_enabled:
            # Check if running on Windows
            if sys.platform == WINDOWS_PLATFORM:
                logger.error(
                    "E2EE is not supported on Windows due to library limitations."
                )
                logger.error(
                    "The python-olm library requires native C libraries that are difficult to install on Windows."
                )
                logger.error(
                    "Please disable E2EE in your configuration or use a Linux/macOS system for E2EE support."
                )
                e2ee_enabled = False
            else:
                # Check if python-olm is installed
                try:
                    importlib.import_module("olm")

                    # Also check for other required E2EE dependencies unless tests skip them
                    if os.getenv("MMRELAY_TESTING") != "1":
                        try:
                            nio_crypto = importlib.import_module("nio.crypto")
                            if not hasattr(nio_crypto, "OlmDevice"):
                                raise ImportError("nio.crypto.OlmDevice is unavailable")

                            nio_store = importlib.import_module("nio.store")
                            if not hasattr(nio_store, "SqliteStore"):
                                raise ImportError(
                                    "nio.store.SqliteStore is unavailable"
                                )

                            logger.debug("All E2EE dependencies are available")
                        except ImportError:
                            logger.exception("Missing E2EE dependency")
                            logger.error(
                                "Please reinstall with: pipx install 'mmrelay[e2e]'"
                            )
                            logger.warning("E2EE will be disabled for this session.")
                            e2ee_enabled = False
                        else:
                            # Dependencies are available, keep the config-determined value
                            if e2ee_enabled:
                                logger.info("End-to-End Encryption (E2EE) is enabled")
                            else:
                                logger.debug(
                                    "E2EE dependencies available but E2EE is disabled in configuration"
                                )
                    else:
                        logger.debug(
                            "Skipping additional E2EE dependency imports in test mode"
                        )

                    if e2ee_enabled:
                        # Ensure nio still receives a store path even when dependency
                        # checks are skipped (e.g. production runs without MMRELAY_TESTING);
                        # without this the client will not load encryption state.
                        # Get store path from config or use default
                        if (
                            "encryption" in config["matrix"]
                            and "store_path" in config["matrix"]["encryption"]
                        ):
                            e2ee_store_path = os.path.expanduser(
                                config["matrix"]["encryption"]["store_path"]
                            )
                        elif (
                            "e2ee" in config["matrix"]
                            and "store_path" in config["matrix"]["e2ee"]
                        ):
                            e2ee_store_path = os.path.expanduser(
                                config["matrix"]["e2ee"]["store_path"]
                            )
                        else:
                            e2ee_store_path = get_e2ee_store_dir()

                        # Create store directory if it doesn't exist
                        os.makedirs(e2ee_store_path, exist_ok=True)

                        # Check if store directory contains database files
                        store_files = (
                            os.listdir(e2ee_store_path)
                            if os.path.exists(e2ee_store_path)
                            else []
                        )
                        db_files = [f for f in store_files if f.endswith(".db")]
                        if db_files:
                            logger.debug(
                                f"Found existing E2EE store files: {', '.join(db_files)}"
                            )
                        else:
                            logger.warning(
                                "No existing E2EE store files found. Encryption may not work correctly."
                            )

                        logger.debug(f"Using E2EE store path: {e2ee_store_path}")

                        # If device_id is not present in credentials, we can attempt to learn it later.
                        if not e2ee_device_id:
                            logger.debug(
                                "No device_id in credentials; will retrieve from store/whoami later if available"
                            )
                except ImportError:
                    logger.warning(
                        "E2EE is enabled in config but python-olm is not installed."
                    )
                    logger.warning("Install 'mmrelay[e2e]' to use E2EE features.")
                    e2ee_enabled = False
    except (KeyError, TypeError):
        # E2EE not configured
        pass

    # Initialize the Matrix client with custom SSL context
    # Use the same AsyncClientConfig pattern as working E2EE examples
    client_config = AsyncClientConfig(
        max_limit_exceeded=0,
        max_timeouts=0,
        store_sync_tokens=True,
        encryption_enabled=e2ee_enabled,
    )

    # Log the device ID being used
    if e2ee_device_id:
        logger.debug(f"Device ID from credentials: {e2ee_device_id}")

    matrix_client = AsyncClient(
        homeserver=matrix_homeserver,
        user=bot_user_id,
        device_id=e2ee_device_id,  # Will be None if not specified in config or credentials
        store_path=e2ee_store_path if e2ee_enabled else None,
        config=client_config,
        ssl=cast(Any, ssl_context),
    )

    # Set the access_token and user_id using restore_login for better session management
    if credentials:
        # Use restore_login method for proper session restoration only when we have a valid device_id.
        # nio will handle loading the store automatically if store_path was provided
        # to the client constructor.
        if e2ee_device_id:
            device_id_for_restore = cast(Any, e2ee_device_id)
            matrix_client.restore_login(
                user_id=bot_user_id,
                device_id=device_id_for_restore,
                access_token=matrix_access_token,
            )
            logger.info(
                f"Restored login session for {bot_user_id} with device {e2ee_device_id}"
            )
        else:
            # First-run E2EE setup: discover device_id using whoami
            logger.info("First-run E2EE setup: discovering device_id via whoami")
            matrix_client.access_token = matrix_access_token
            matrix_client.user_id = bot_user_id

            # Call whoami to discover device_id from server
            try:
                whoami_response = await matrix_client.whoami()
                discovered_device_id = getattr(whoami_response, "device_id", None)
                if discovered_device_id:
                    e2ee_device_id = discovered_device_id
                    logger.info(f"Discovered device_id from whoami: {e2ee_device_id}")

                    # Save the discovered device_id to credentials for future use
                    try:
                        if credentials is not None:
                            credentials["device_id"] = e2ee_device_id
                            save_credentials(credentials)
                            logger.info(
                                "Updated credentials.json with discovered device_id"
                            )
                    except Exception as e:
                        logger.debug(f"Failed to persist discovered device_id: {e}")
                else:
                    logger.warning("whoami response did not contain device_id")
            except Exception as e:
                logger.warning(f"Failed to discover device_id via whoami: {e}")
                logger.warning("E2EE may not work properly without a device_id")
    else:
        # Fallback to direct assignment for legacy token-based auth
        matrix_client.access_token = matrix_access_token
        matrix_client.user_id = bot_user_id

    # If E2EE is enabled, upload keys if necessary.
    # nio will have loaded the store automatically if store_path was provided.
    if e2ee_enabled:
        try:
            if matrix_client.should_upload_keys:
                logger.info("Uploading encryption keys...")
                await matrix_client.keys_upload()
                logger.info("Encryption keys uploaded successfully")
            else:
                logger.debug("No key upload needed - keys already present")
        except Exception:
            logger.exception("Failed to upload E2EE keys")
            # E2EE might still work, so we don't disable it here
            logger.error("Consider regenerating credentials with: mmrelay auth login")

    # Perform initial sync to populate rooms (needed for message delivery)
    logger.debug("Performing initial sync to initialize rooms...")
    try:
        # A full_state=True sync is required to get room encryption state
        sync_response = await asyncio.wait_for(
            matrix_client.sync(timeout=MATRIX_EARLY_SYNC_TIMEOUT, full_state=True),
            timeout=MATRIX_SYNC_OPERATION_TIMEOUT,
        )
        # Check if sync failed by looking for error class name
        if (
            hasattr(sync_response, "__class__")
            and "Error" in sync_response.__class__.__name__
        ):
            # Provide more detailed error information
            error_type = sync_response.__class__.__name__
            error_details = _get_detailed_sync_error_message(sync_response)
            logger.error(f"Initial sync failed: {error_type}")
            logger.error(f"Error details: {error_details}")

            # Provide user-friendly troubleshooting guidance
            if "SyncError" in error_type:
                logger.error(
                    "This usually indicates a network connectivity issue or server problem."
                )
                logger.error("Troubleshooting steps:")
                logger.error("1. Check your internet connection")
                logger.error(
                    f"2. Verify the homeserver URL is correct: {matrix_homeserver}"
                )
                logger.error("3. Ensure the Matrix server is online and accessible")
                logger.error("4. Check if your credentials are still valid")

            try:
                await matrix_client.close()
            except Exception:
                logger.debug("Ignoring error while closing client after sync failure")
            finally:
                matrix_client = None
            raise ConnectionError(f"Matrix sync failed: {error_type} - {error_details}")
        else:
            logger.info(
                f"Initial sync completed. Found {len(matrix_client.rooms)} rooms."
            )

            # List all rooms with unified E2EE status display
            from mmrelay.config import config_path
            from mmrelay.e2ee_utils import (
                get_e2ee_status,
                get_room_encryption_warnings,
            )

            # Get comprehensive E2EE status
            e2ee_status = get_e2ee_status(config or {}, config_path)

            # Resolve room aliases in config (supports list[str|dict] and dict[str->str|dict])
            async def _resolve_alias(alias: str) -> Optional[str]:
                """
                Resolve a Matrix room alias to its canonical room ID.

                Attempts to resolve the provided room alias using the module's Matrix client. Returns the resolved room ID string on success; returns None if the alias cannot be resolved or if an error/timeout occurs (errors from the underlying nio client are caught and handled internally).
                """
                if not matrix_client:
                    logger.warning(
                        f"Cannot resolve alias {alias}: Matrix client is not available"
                    )
                    return None

                logger.debug(f"Resolving alias from config: {alias}")
                try:
                    response = await matrix_client.room_resolve_alias(alias)
                    room_id = getattr(response, "room_id", None)
                    if room_id:
                        logger.debug(f"Resolved alias {alias} to {room_id}")
                        return room_id
                    error_details = (
                        getattr(response, "message", response)
                        if response
                        else "No response from server"
                    )
                    logger.warning(f"Could not resolve alias {alias}: {error_details}")
                except NIO_COMM_EXCEPTIONS:
                    logger.exception(f"Error resolving alias {alias}")
                return None

            await _resolve_aliases_in_mapping(matrix_rooms, _resolve_alias)

            # Display rooms with channel mappings
            _display_room_channel_mappings(
                matrix_client.rooms, config, dict(e2ee_status)
            )

            # Show warnings for encrypted rooms when E2EE is not ready
            warnings = get_room_encryption_warnings(
                matrix_client.rooms, dict(e2ee_status)
            )
            for warning in warnings:
                logger.warning(warning)

            # Debug information
            encrypted_count = sum(
                1
                for room in matrix_client.rooms.values()
                if getattr(room, "encrypted", False)
            )
            logger.debug(
                f"Found {encrypted_count} encrypted rooms out of {len(matrix_client.rooms)} total rooms"
            )
            logger.debug(f"E2EE status: {e2ee_status['overall_status']}")

            # Additional debugging for E2EE enabled case
            if e2ee_enabled and encrypted_count == 0 and len(matrix_client.rooms) > 0:
                logger.debug("No encrypted rooms detected - all rooms are plaintext")
    except asyncio.TimeoutError:
        logger.exception(
            f"Initial sync timed out after {MATRIX_SYNC_OPERATION_TIMEOUT} seconds"
        )
        logger.error(
            "This indicates a network connectivity issue or slow Matrix server."
        )
        logger.error("Troubleshooting steps:")
        logger.error("1. Check your internet connection")
        logger.error(f"2. Verify the homeserver is accessible: {matrix_homeserver}")
        logger.error(
            "3. Try again in a few minutes - the server may be temporarily overloaded"
        )
        logger.error(
            "4. Consider using a different Matrix homeserver if the problem persists"
        )
        try:
            if matrix_client:
                await matrix_client.close()
        except Exception:
            logger.debug("Ignoring error while closing client after sync timeout")
        finally:
            matrix_client = None
        raise ConnectionError(
            f"Matrix sync timed out after {MATRIX_SYNC_OPERATION_TIMEOUT} seconds - check network connectivity and server status"
        ) from None

    # Add a delay to allow for key sharing to complete
    # This addresses a race condition where the client attempts to send encrypted messages
    # before it has received and processed room key sharing messages from other devices.
    # The initial sync() call triggers key sharing requests, but the actual key exchange
    # happens asynchronously. Without this delay, outgoing messages may be sent unencrypted
    # even to encrypted rooms. While not ideal, this timing-based approach is necessary
    # because matrix-nio doesn't provide event-driven alternatives to detect when key
    # sharing is complete.
    if e2ee_enabled:
        logger.debug(
            f"Waiting for {E2EE_KEY_SHARING_DELAY_SECONDS} seconds to allow for key sharing..."
        )
        await asyncio.sleep(E2EE_KEY_SHARING_DELAY_SECONDS)

    # Fetch the bot's display name
    try:
        response = await matrix_client.get_displayname(bot_user_id)
        displayname = getattr(response, "displayname", None)
        if displayname:
            bot_user_name = displayname
        else:
            bot_user_name = bot_user_id  # Fallback if display name is not set
    except NIO_COMM_EXCEPTIONS as e:
        logger.debug(f"Failed to get bot display name for {bot_user_id}: {e}")
        bot_user_name = bot_user_id  # Fallback on network error

    # Store E2EE status on the client for other functions to access
    setattr(matrix_client, "e2ee_enabled", e2ee_enabled)  # type: ignore[attr-defined]
    return matrix_client


async def login_matrix_bot(
    homeserver=None, username=None, password=None, logout_others=False
):
    """
    Perform an interactive Matrix login for the bot and persist credentials for later use.

    This coroutine attempts server discovery for the provided homeserver, logs in as the given username, optionally initializes an encrypted client store (if E2EE is enabled in configuration), and saves resulting credentials (homeserver, user_id, access_token, device_id) to credentials.json so the relay can restore the session non-interactively. If an existing credentials.json contains a matching user_id, the device_id will be reused when available.

    Parameters:
        homeserver (str | None): Homeserver URL to use. If None, the user is prompted.
        username (str | None): Matrix username (without or with leading "@"). If None, the user is prompted.
        password (str | None): Password for the account. If None, the user is prompted securely.
        logout_others (bool | None): If True, attempts to log out other sessions after login. If None, the user is prompted. (Note: full "logout others" behavior may be limited.)

    Returns:
        bool: True on successful login and credentials persisted; False on failure. The function handles errors internally and returns False rather than raising.
    """
    client = None  # Initialize to avoid unbound variable errors
    try:
        # Optionally enable verbose nio/aiohttp debug logging
        if os.getenv("MMRELAY_DEBUG_NIO") == "1":
            logging.getLogger("nio").setLevel(logging.DEBUG)
            logging.getLogger("nio.client").setLevel(logging.DEBUG)
            logging.getLogger("nio.http_client").setLevel(logging.DEBUG)
            logging.getLogger("nio.responses").setLevel(logging.DEBUG)
            logging.getLogger("aiohttp").setLevel(logging.DEBUG)

        # Get homeserver URL
        if not homeserver:
            homeserver = input(
                "Enter Matrix homeserver URL (e.g., https://matrix.org): "
            )

        # Ensure homeserver URL has the correct format
        if not (homeserver.startswith("https://") or homeserver.startswith("http://")):
            homeserver = "https://" + homeserver

        # Step 1: Perform server discovery to get the actual homeserver URL
        logger.info(f"Performing server discovery for {homeserver}...")

        # Create SSL context using certifi's certificates
        ssl_context = _create_ssl_context()
        if ssl_context is None:
            logger.warning(
                "Failed to create SSL context for server discovery; falling back to default system SSL"
            )
        else:
            logger.debug(f"SSL context created successfully: {ssl_context}")
            logger.debug(f"SSL context protocol: {ssl_context.protocol}")
            logger.debug(f"SSL context verify_mode: {ssl_context.verify_mode}")

        # Create a temporary client for discovery
        temp_client = AsyncClient(homeserver, "", ssl=cast(Any, ssl_context))
        try:
            discovery_response = await asyncio.wait_for(
                temp_client.discovery_info(), timeout=MATRIX_LOGIN_TIMEOUT
            )

            try:
                if isinstance(discovery_response, DiscoveryInfoResponse):
                    actual_homeserver = discovery_response.homeserver_url
                    logger.info(f"Server discovery successful: {actual_homeserver}")
                    homeserver = actual_homeserver
                elif isinstance(discovery_response, DiscoveryInfoError):
                    logger.info(
                        f"Server discovery failed, using original URL: {homeserver}"
                    )
                    # Continue with original homeserver URL
                else:
                    # Fallback for test environments or unexpected response types
                    if hasattr(discovery_response, "homeserver_url"):
                        actual_homeserver = discovery_response.homeserver_url
                        logger.info(f"Server discovery successful: {actual_homeserver}")
                        homeserver = actual_homeserver
                    else:
                        logger.warning(
                            f"Server discovery returned unexpected response type, using original URL: {homeserver}"
                        )
            except TypeError as e:
                logger.warning(
                    f"Server discovery error: {e}, using original URL: {homeserver}"
                )

        except asyncio.TimeoutError:
            logger.warning(
                f"Server discovery timed out, using original URL: {homeserver}"
            )
            # Continue with original homeserver URL
        except Exception as e:
            logger.warning(
                f"Server discovery error: {e}, using original URL: {homeserver}"
            )
            # Continue with original homeserver URL
        finally:
            await temp_client.close()

        # Get username
        if not username:
            username = input("Enter Matrix username (without @): ")

        # Format username correctly
        username = _normalize_bot_user_id(homeserver, username)

        logger.info(f"Using username: {username}")

        # Validate username format
        if not username.startswith("@"):
            logger.warning(f"Username doesn't start with @: {username}")
        if username.count(":") != 1:
            logger.warning(
                f"Username has unexpected colon count: {username.count(':')}"
            )

        # Check for special characters in username that might cause issues
        username_special_chars = set(username) - set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@:.-_"
        )
        if username_special_chars:
            logger.warning(
                f"Username contains unusual characters: {username_special_chars}"
            )

        # Get password
        if not password:
            password = getpass.getpass("Enter Matrix password: ")

        # Simple password validation without logging sensitive information
        if password:
            logger.debug("Password provided for login")
        else:
            logger.warning("No password provided")

        # Ask about logging out other sessions
        if logout_others is None:
            logout_others_input = input(
                "Log out other sessions? (Y/n) [Default: Yes]: "
            ).lower()
            logout_others = (
                not logout_others_input.startswith("n") if logout_others_input else True
            )

        # Check for existing credentials to reuse device_id
        existing_device_id = None
        try:
            import json

            config_dir = get_base_dir()
            credentials_path = os.path.join(config_dir, "credentials.json")

            if os.path.exists(credentials_path):
                with open(credentials_path, "r", encoding="utf-8") as f:
                    existing_creds = json.load(f)
                    if (
                        "device_id" in existing_creds
                        and existing_creds["user_id"] == username
                    ):
                        existing_device_id = existing_creds["device_id"]
                        logger.info(f"Reusing existing device_id: {existing_device_id}")
        except Exception as e:
            logger.debug(f"Could not load existing credentials: {e}")

        # Check if E2EE is enabled in configuration
        from mmrelay.config import is_e2ee_enabled, load_config

        try:
            config = load_config()
            e2ee_enabled = is_e2ee_enabled(config)
        except Exception as e:
            logger.debug(f"Could not load config for E2EE check: {e}")
            e2ee_enabled = False

        logger.debug(f"E2EE enabled in config: {e2ee_enabled}")

        # Get the E2EE store path only if E2EE is enabled
        store_path = None
        if e2ee_enabled:
            store_path = get_e2ee_store_dir()
            os.makedirs(store_path, exist_ok=True)
            logger.debug(f"Using E2EE store path: {store_path}")
        else:
            logger.debug("E2EE disabled in configuration, not using store path")

        # Create client config with E2EE based on configuration
        client_config = AsyncClientConfig(
            store_sync_tokens=True, encryption_enabled=e2ee_enabled
        )

        # Use the same SSL context as discovery client
        # ssl_context was created above for discovery

        # Initialize client with E2EE support
        # Use most common pattern from matrix-nio examples: positional homeserver and user
        logger.debug("Creating AsyncClient with:")
        logger.debug(f"  homeserver: {homeserver}")
        logger.debug(f"  username: {username}")
        logger.debug(f"  device_id: {existing_device_id}")
        logger.debug(f"  store_path: {store_path}")
        logger.debug(f"  e2ee_enabled: {e2ee_enabled}")

        client = AsyncClient(
            homeserver,
            username,
            device_id=existing_device_id,
            store_path=store_path,
            config=client_config,
            ssl=cast(Any, ssl_context),
        )

        logger.debug("AsyncClient created successfully")

        logger.info(f"Logging in as {username} to {homeserver}...")

        # Login with consistent device name and timeout
        # Use appropriate device name based on E2EE configuration
        device_name = "mmrelay-e2ee" if e2ee_enabled else "mmrelay"
        try:
            # Set device_id on client if we have an existing one
            if existing_device_id:
                client.device_id = existing_device_id

            logger.debug(f"Attempting login to {homeserver} as {username}")
            logger.debug("Login parameters:")
            logger.debug(f"  device_name: {device_name}")
            logger.debug(f"  password length: {len(password) if password else 0}")
            logger.debug(f"  client.user: {client.user}")
            logger.debug(f"  client.homeserver: {client.homeserver}")

            # Test the API call that matrix-nio will make
            try:
                from nio.api import Api  # type: ignore[import-untyped]

                method, path, data = Api.login(
                    user=username,
                    password=password,
                    device_name=device_name,
                    device_id=existing_device_id,
                )
                logger.debug("Matrix API call details:")
                logger.debug(f"  method: {method}")
                logger.debug(f"  path: {path}")
                logger.debug(f"  data length: {len(data) if data else 0}")

                # Parse the JSON to see the structure (without logging the password)
                import json

                parsed_data = json.loads(data)
                safe_data = {
                    k: (v if k != "password" else f"[{len(v)} chars]")
                    for k, v in parsed_data.items()
                }
                logger.debug(f"  parsed data: {safe_data}")

            except Exception as e:
                logger.error(f"Failed to test API call: {e}")

            response = await asyncio.wait_for(
                client.login(password, device_name=device_name),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )

            # Debug: Log the response type and safe attributes only
            logger.debug(f"Login response type: {type(response).__name__}")

            # Check specific attributes that should be present, masking sensitive data
            for attr in [
                "user_id",
                "device_id",
                "access_token",
                "status_code",
                "message",
            ]:
                if hasattr(response, attr):
                    value = getattr(response, attr)
                    if attr == "access_token" and value:
                        # Mask access token for security
                        masked_value = (
                            f"{value[:8]}...{value[-4:]}"
                            if len(value) > 12
                            else "***masked***"
                        )
                        logger.debug(
                            f"Response.{attr}: {masked_value} (type: {type(value).__name__})"
                        )
                    else:
                        logger.debug(
                            f"Response.{attr}: {value} (type: {type(value).__name__})"
                        )
                else:
                    logger.debug(f"Response.{attr}: NOT PRESENT")
        except asyncio.TimeoutError:
            logger.exception(f"Login timed out after {MATRIX_LOGIN_TIMEOUT} seconds")
            logger.error(
                "This may indicate network connectivity issues or a slow Matrix server"
            )
            await client.close()
            return False
        except TypeError as e:
            # Handle the specific ">=" comparison error that can occur in matrix-nio
            if "'>=' not supported between instances of 'str' and 'int'" in str(e):
                logger.error("Matrix-nio library error during login (known issue)")
                logger.error(
                    "This typically indicates invalid credentials or server response format issues"
                )
                logger.error("Troubleshooting steps:")
                logger.error("1. Verify your username and password are correct")
                logger.error("2. Check if your account is locked or suspended")
                logger.error("3. Try logging in through a web browser first")
                logger.error("4. Ensure your Matrix server supports the login API")
                logger.error(
                    "5. Try using a different homeserver URL format (e.g., with https://)"
                )
            else:
                logger.exception("Type error during login")
            await client.close()
            return False
        except Exception as e:
            # Handle other exceptions during login (e.g., network errors)
            error_type = type(e).__name__
            logger.exception(f"Login failed with {error_type}")

            # Provide specific guidance based on error type
            if isinstance(e, (ConnectionError, asyncio.TimeoutError)):
                logger.error("Network connectivity issue detected.")
                logger.error("Troubleshooting steps:")
                logger.error("1. Check your internet connection")
                logger.error(f"2. Verify the homeserver URL is correct: {homeserver}")
                logger.error("3. Check if the Matrix server is online")
            elif isinstance(e, (ssl.SSLError, ssl.CertificateError)):
                logger.error("SSL/TLS certificate issue detected.")
                logger.error(
                    "This may indicate a problem with the server's SSL certificate."
                )
            elif "DNSError" in error_type or "NameResolutionError" in error_type:
                logger.error("DNS resolution failed.")
                logger.error(f"Cannot resolve hostname: {homeserver}")
                logger.error("Check your DNS settings and internet connection.")
            elif "'user_id' is a required property" in str(e):
                logger.error("Matrix server response validation failed.")
                logger.error("This typically indicates:")
                logger.error("1. Invalid username or password")
                logger.error("2. Server response format not as expected")
                logger.error("3. Matrix server compatibility issues")
                logger.error("Troubleshooting steps:")
                logger.error("1. Verify credentials by logging in via web browser")
                logger.error(
                    "2. Try using the full homeserver URL (e.g., https://matrix.org)"
                )
                logger.error(
                    "3. Check if your Matrix server is compatible with matrix-nio"
                )
                logger.error("4. Try a different Matrix server if available")

            else:
                logger.error("Unexpected error during login.")

            # Additional details already included in the message above.
            await client.close()
            return False

        # Handle login response - check for access_token first (most reliable indicator)
        access_token = getattr(response, "access_token", None)
        if access_token:
            logger.info("Login successful!")

            # Get the actual user_id from whoami() - this is the proper way
            try:
                whoami_response = await client.whoami()
                user_id = getattr(whoami_response, "user_id", None)
                if user_id:
                    actual_user_id = user_id
                    logger.debug(f"Got user_id from whoami: {actual_user_id}")
                else:
                    # Fallback to response user_id or username
                    actual_user_id = getattr(response, "user_id", username)
                    logger.warning(
                        f"whoami failed, using fallback user_id: {actual_user_id}"
                    )
            except Exception as e:
                logger.warning(f"whoami call failed: {e}, using fallback")
                actual_user_id = getattr(response, "user_id", username)

            # Save credentials to credentials.json
            credentials = {
                "homeserver": homeserver,
                "user_id": actual_user_id,
                "access_token": getattr(response, "access_token", None),
                "device_id": getattr(response, "device_id", existing_device_id),
            }

            config_dir = get_base_dir()
            credentials_path = os.path.join(config_dir, "credentials.json")
            save_credentials(credentials)
            logger.info(f"Credentials saved to {credentials_path}")

            # Logout other sessions if requested
            if logout_others:
                logger.info("Logging out other sessions...")
                # Note: This would require additional implementation
                logger.warning("Logout others not yet implemented")

            await client.close()
            return True
        else:
            # Handle login failure
            status_code = getattr(response, "status_code", None)
            error_message = getattr(response, "message", None)
            if status_code is not None and error_message is not None:
                logger.error(f"Login failed: {type(response).__name__}")
                logger.error(f"Error message: {error_message}")
                logger.error(f"HTTP status code: {status_code}")

                # Provide specific troubleshooting guidance
                if status_code == 401 or "M_FORBIDDEN" in str(error_message):
                    logger.error(
                        "Authentication failed - invalid username or password."
                    )
                    logger.error("Troubleshooting steps:")
                    logger.error("1. Verify your username and password are correct")
                    logger.error("2. Check if your account is locked or suspended")
                    logger.error("3. Try logging in through a web browser first")
                    logger.error(
                        "4. Use 'mmrelay auth login' to set up new credentials"
                    )
                elif status_code == 404:
                    logger.error("User not found or homeserver not found.")
                    logger.error(
                        f"Check that the homeserver URL is correct: {homeserver}"
                    )
                elif status_code == 429:
                    logger.error("Rate limited - too many login attempts.")
                    logger.error("Wait a few minutes before trying again.")
                elif status_code and int(status_code) >= 500:
                    logger.error(
                        "Matrix server error - the server is experiencing issues."
                    )
                    logger.error(
                        "Try again later or contact your server administrator."
                    )
                else:
                    logger.error("Login failed for unknown reason.")
                    logger.error(
                        "Try using 'mmrelay auth login' for interactive setup."
                    )
            else:
                logger.error(f"Unexpected login response: {type(response).__name__}")
                logger.error(
                    "This may indicate a matrix-nio library issue or server problem."
                )

            await client.close()
            return False

    except Exception:
        logger.exception("Error during login")
        try:
            if client:
                await client.close()
        except Exception as e:
            # Ignore errors during client cleanup - connection may already be closed
            logger.debug(f"Ignoring error during client cleanup: {e}")
        return False


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """
    Join the bot to a Matrix room by ID or alias.

    Resolves a room alias (e.g. "#room:server") to its canonical room ID, updates the in-memory
    matrix_rooms mapping with the resolved ID (if available), and attempts to join the resolved
    room ID. No-op if the client is already joined to the room. Errors during alias resolution
    or join are caught and logged; the function does not raise exceptions.

    Parameters documented only where meaning is not obvious:
        room_id_or_alias (str): A Matrix room identifier, either a canonical room ID (e.g. "!abc:server")
            or a room alias (starts with '#'). When an alias is provided, it will be resolved and
            the resolved room ID will be used for joining and recorded in the module's matrix_rooms mapping.

    Returns:
        None
    """

    if not isinstance(room_id_or_alias, str):
        logger.error(
            "join_matrix_room expected a string room ID, received %r",
            room_id_or_alias,
        )
        return

    room_id: Optional[str] = room_id_or_alias

    if room_id_or_alias.startswith("#"):
        try:
            response = await matrix_client.room_resolve_alias(room_id_or_alias)
        except NIO_COMM_EXCEPTIONS:
            logger.exception("Error resolving alias '%s'", room_id_or_alias)
            return

        room_id = getattr(response, "room_id", None) if response else None
        if not room_id:
            error_details = (
                getattr(response, "message", response)
                if response
                else "No response from server"
            )
            logger.error(
                "Failed to resolve alias '%s': %s",
                room_id_or_alias,
                error_details,
            )
            return

        try:
            mapping = matrix_rooms
        except NameError:
            mapping = None

        if mapping:
            try:
                _update_room_id_in_mapping(mapping, room_id_or_alias, room_id)
            except Exception:
                logger.debug(
                    "Non-fatal error updating matrix_rooms for alias '%s'",
                    room_id_or_alias,
                    exc_info=True,
                )

        logger.info("Resolved alias '%s' -> '%s'", room_id_or_alias, room_id)

    if room_id is None:
        logger.error("Resolved room_id is None, cannot join room.")
        return

    try:
        if room_id not in matrix_client.rooms:
            response = await matrix_client.join(room_id)
            joined_room_id = getattr(response, "room_id", None) if response else None
            if joined_room_id:
                logger.info(f"Joined room '{joined_room_id}' successfully")
            else:
                error_details = (
                    getattr(response, "message", response)
                    if response
                    else "No response from server"
                )
                logger.error(
                    "Failed to join room '%s': %s",
                    room_id,
                    error_details,
                )
        else:
            logger.debug(
                "Bot is already in room '%s', no action needed.",
                room_id,
            )
    except NIO_COMM_EXCEPTIONS:
        logger.exception(f"Error joining room '{room_id}'")


def _get_e2ee_error_message():
    """
    Return a user-facing string explaining why End-to-End Encryption (E2EE) is not enabled.

    This queries the unified E2EE status (using the module-level config and config path)
    and converts that status into a concise error message suitable for logging or UI display.

    Returns:
        str: A short, human-readable explanation of the current E2EE problem (empty or generic
        message if no specific issue is detected).
    """
    from mmrelay.config import config_path
    from mmrelay.e2ee_utils import get_e2ee_error_message, get_e2ee_status

    # Get unified E2EE status
    e2ee_status = get_e2ee_status(config or {}, config_path)

    # Return unified error message
    return get_e2ee_error_message(dict(e2ee_status))


async def matrix_relay(
    room_id,
    message,
    longname,
    shortname,
    meshnet_name,
    portnum,
    meshtastic_id=None,
    meshtastic_replyId=None,
    meshtastic_text=None,
    emote=False,
    emoji=False,
    reply_to_event_id=None,
):
    """
    Relay a Meshtastic message into a Matrix room and optionally persist a Meshtastic⇄Matrix mapping for later interactions.

    Formats the Meshtastic text into plain and HTML-safe formatted Matrix content, applies Matrix reply framing when reply_to_event_id is provided, enforces E2EE restrictions, sends the event via the configured Matrix client, and—when message-interactions are enabled—stores a mapping from the Meshtastic message to the created Matrix event for use by cross-network replies and reactions. Errors are logged; the function does not raise on send or storage failures.

    Parameters:
        room_id (str): Matrix room ID or alias to send the message into.
        message (str): Text of the Meshtastic message to relay.
        longname (str): Sender long display name from Meshtastic.
        shortname (str): Sender short display name from Meshtastic.
        meshnet_name (str): Remote meshnet name associated with the incoming message.
        portnum (int): Meshtastic application/port number for the message.
        meshtastic_id (optional): Meshtastic message identifier; when provided and storage is enabled, used to persist a mapping to the created Matrix event.
        meshtastic_replyId (optional): Original Meshtastic message ID being replied to; included as metadata on the Matrix event.
        meshtastic_text (optional): Original Meshtastic text to store with the mapping; if omitted, the relayed message text is used.
        emote (bool, optional): If True, send the Matrix event as `m.emote` instead of `m.text`.
        emoji (bool, optional): If True, mark the event with an emoji flag used by downstream logic.
        reply_to_event_id (str, optional): Matrix event_id being replied to; if provided the outgoing event will include an `m.in_reply_to` relation and quoted/HTML reply content when the original mapping can be found.
    """
    global config

    # Log the current state of the config
    logger.debug(f"matrix_relay: config is {'available' if config else 'None'}")

    matrix_client = await connect_matrix()

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot relay message to Matrix.")
        return

    # Get interaction settings
    interactions = get_interaction_settings(config)
    storage_enabled = message_storage_enabled(interactions)

    # Retrieve db config for message_map pruning
    # Check database config for message map settings (preferred format)
    database_config = config.get("database", {})
    msg_map_config = database_config.get("msg_map", {})

    # If not found in database config, check legacy db config
    if not msg_map_config:
        db_config = config.get("db", {})
        legacy_msg_map_config = db_config.get("msg_map", {})

        if legacy_msg_map_config:
            msg_map_config = legacy_msg_map_config
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )
    msgs_to_keep = msg_map_config.get(
        "msgs_to_keep", DEFAULT_MSGS_TO_KEEP
    )  # Default from constants

    try:
        # Always use our own local meshnet_name for outgoing events
        local_meshnet_name = config["meshtastic"]["meshnet_name"]

        # Check if message contains HTML tags or markdown formatting
        has_html = bool(re.search(r"</?[a-zA-Z][^>]*>", message))
        has_markdown = bool(re.search(r"[*_`~]", message))  # Basic markdown indicators

        # Process markdown/HTML if available; otherwise, safe fallback
        if has_markdown or has_html:
            try:
                import bleach  # type: ignore[import-untyped]  # lazy import
                import markdown  # type: ignore[import-untyped]  # lazy import

                raw_html = markdown.markdown(message)
                formatted_body = bleach.clean(
                    raw_html,
                    tags=[
                        "b",
                        "strong",
                        "i",
                        "em",
                        "code",
                        "pre",
                        "br",
                        "blockquote",
                        "a",
                        "ul",
                        "ol",
                        "li",
                        "p",
                    ],
                    attributes={"a": ["href"]},
                    strip=True,
                )
                plain_body = re.sub(r"</?[^>]*>", "", formatted_body)
            except ImportError:
                formatted_body = html.escape(message).replace("\n", "<br/>")
                plain_body = message
        else:
            formatted_body = html.escape(message).replace("\n", "<br/>")
            plain_body = message

        content = {
            "msgtype": "m.text" if not emote else "m.emote",
            "body": plain_body,
            "meshtastic_longname": longname,
            "meshtastic_shortname": shortname,
            "meshtastic_meshnet": local_meshnet_name,
            "meshtastic_portnum": portnum,
        }

        # Always add format and formatted_body to avoid nio validation errors
        # where formatted_body becomes None and fails schema validation.
        content["format"] = "org.matrix.custom.html"
        content["formatted_body"] = formatted_body
        if meshtastic_id is not None:
            content["meshtastic_id"] = meshtastic_id
        if meshtastic_replyId is not None:
            content["meshtastic_replyId"] = meshtastic_replyId
        if meshtastic_text is not None:
            content["meshtastic_text"] = meshtastic_text
        if emoji:
            content["meshtastic_emoji"] = 1

        # Add Matrix reply formatting if this is a reply
        if reply_to_event_id:
            content["m.relates_to"] = {"m.in_reply_to": {"event_id": reply_to_event_id}}
            # For Matrix replies, we need to format the body with quoted content
            # Get the original message details for proper quoting
            try:
                orig = get_message_map_by_matrix_event_id(reply_to_event_id)
                if orig:
                    # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
                    _, _, original_text, original_meshnet = orig

                    # Use the relay bot's user ID for attribution (this is correct for relay messages)
                    bot_user_id = (
                        getattr(matrix_client, "user_id", None)
                        if matrix_client
                        else None
                    )
                    original_sender_display = f"{longname}/{original_meshnet}"

                    # Create the quoted reply format
                    safe_original = html.escape(original_text or "")
                    safe_sender_display = re.sub(
                        r"([\\`*_{}[\]()#+.!-])", r"\\\1", original_sender_display
                    )
                    quoted_text = (
                        f"> <{bot_user_id}> [{safe_sender_display}]: {safe_original}"
                    )
                    content["body"] = f"{quoted_text}\n\n{plain_body}"

                    # Always use HTML formatting for replies since we need the mx-reply structure
                    content["format"] = "org.matrix.custom.html"
                    reply_link = f"https://matrix.to/#/{room_id}/{reply_to_event_id}"
                    bot_link = f"https://matrix.to/#/{bot_user_id}"
                    blockquote_content = (
                        f'<a href="{reply_link}">In reply to</a> '
                        f'<a href="{bot_link}">{bot_user_id}</a><br>'
                        f"[{html.escape(original_sender_display)}]: {safe_original}"
                    )
                    content["formatted_body"] = (
                        f"<mx-reply><blockquote>{blockquote_content}</blockquote></mx-reply>{formatted_body}"
                    )
                else:
                    logger.warning(
                        f"Could not find original message for reply_to_event_id: {reply_to_event_id}"
                    )
            except Exception as e:
                logger.error(f"Error formatting Matrix reply: {e}")

        try:
            # Ensure matrix_client is not None
            if not matrix_client:
                logger.error("Matrix client is None. Cannot send message.")
                return

            # Send the message with a timeout
            # For encrypted rooms, use ignore_unverified_devices=True
            # After checking working implementations, always use ignore_unverified_devices=True
            # for text messages to ensure encryption works properly
            room = (
                matrix_client.rooms.get(room_id)
                if matrix_client and hasattr(matrix_client, "rooms")
                else None
            )

            # Debug logging for encryption status
            if room:
                encrypted_status = getattr(room, "encrypted", "unknown")
                logger.debug(
                    f"Room {room_id} encryption status: encrypted={encrypted_status}"
                )

                # Additional E2EE debugging
                if encrypted_status is True:
                    logger.debug(f"Sending encrypted message to room {room_id}")
                elif encrypted_status is False:
                    logger.debug(f"Sending unencrypted message to room {room_id}")
                else:
                    logger.warning(
                        f"Room {room_id} encryption status is unknown - this may indicate E2EE issues"
                    )
            else:
                logger.warning(
                    f"Room {room_id} not found in client.rooms - cannot determine encryption status"
                )

            # Always use ignore_unverified_devices=True for text messages (like matrix-nio-send)
            logger.debug(
                "Sending message with ignore_unverified_devices=True (always for text messages)"
            )

            # Final check: Do not send to encrypted rooms if E2EE is not enabled
            if (
                room
                and getattr(room, "encrypted", False)
                and not getattr(matrix_client, "e2ee_enabled", False)
            ):
                room_name = getattr(room, "display_name", room_id)
                error_message = _get_e2ee_error_message()
                logger.error(
                    f"🔒 BLOCKED: Cannot send message to encrypted room '{room_name}' ({room_id})"
                )
                logger.error(f"Reason: {error_message}")
                logger.info(
                    "💡 Tip: Run 'mmrelay config check' to validate your E2EE setup"
                )
                return

            response = await asyncio.wait_for(
                matrix_client.room_send(
                    room_id=room_id,
                    message_type="m.room.message",
                    content=content,
                    ignore_unverified_devices=True,
                ),
                timeout=MATRIX_ROOM_SEND_TIMEOUT,  # Increased timeout
            )

            # Log at info level, matching one-point-oh pattern
            logger.info(f"Sent inbound radio message to matrix room: {room_id}")
            # Additional details at debug level
            event_id = getattr(response, "event_id", None)
            if event_id:
                logger.debug(f"Message event_id: {event_id}")

        except asyncio.TimeoutError:
            logger.error(f"Timeout sending message to Matrix room {room_id}")
            return
        except Exception:
            logger.exception(f"Error sending message to Matrix room {room_id}")
            return

        # Only store message map if any interactions are enabled and conditions are met
        # This enables reactions and/or replies functionality based on configuration
        if (
            storage_enabled
            and meshtastic_id is not None
            and not emote
            and getattr(response, "event_id", None) is not None
        ):
            try:
                await async_store_message_map(
                    meshtastic_id,
                    getattr(response, "event_id", None),
                    room_id,
                    meshtastic_text if meshtastic_text else message,
                    meshtastic_meshnet=local_meshnet_name,
                )
                logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")

                # If msgs_to_keep > 0, prune old messages after inserting a new one
                if msgs_to_keep > 0:
                    await async_prune_message_map(msgs_to_keep)
            except Exception as e:
                logger.error(f"Error storing message map: {e}")

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception:
        logger.exception(f"Error sending radio message to matrix room {room_id}")


def truncate_message(text, max_bytes=DEFAULT_MESSAGE_TRUNCATE_BYTES):
    """
    Truncate a string so its UTF-8 encoding fits within max_bytes.

    Returns a substring whose UTF-8 byte length is at most `max_bytes`. If
    `max_bytes` falls in the middle of a multi-byte UTF-8 character, the
    incomplete character is dropped (decoding uses 'ignore').

    Parameters:
        text (str): Input text to truncate.
        max_bytes (int): Maximum allowed size in bytes for the UTF-8 encoded result
            (defaults to DEFAULT_MESSAGE_TRUNCATE_BYTES).

    Returns:
        str: Truncated string.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Removes lines starting with '>' from the input text.

    This is typically used to exclude quoted content from Matrix replies, such as when processing reaction text.
    """
    lines = text.splitlines()
    filtered = [line.strip() for line in lines if not line.strip().startswith(">")]
    return " ".join(line for line in filtered if line).strip()


async def get_user_display_name(room, event):
    """
    Return the display name for the event sender, preferring a room-specific name.

    If the room provides a per-room display name for the sender, that name is returned.
    Otherwise the function performs an asynchronous lookup against the homeserver for the
    user's global display name and returns it if present. If no display name is available,
    the sender's Matrix ID (MXID) is returned.

    Returns:
        str: A human-readable display name or the sender's MXID.
    """
    room_display_name = room.user_name(event.sender)
    if room_display_name:
        return room_display_name

    if matrix_client:
        try:
            display_name_response = await matrix_client.get_displayname(event.sender)
            return getattr(display_name_response, "displayname", None) or event.sender
        except NIO_COMM_EXCEPTIONS as e:
            logger.debug(f"Failed to get display name for {event.sender}: {e}")
            return event.sender
    return event.sender


def format_reply_message(
    config,
    full_display_name,
    text,
    *,
    longname=None,
    shortname=None,
    meshnet_name=None,
    local_meshnet_name=None,
    mesh_text_override=None,
):
    """
    Format a reply message by prefixing a truncated display name and removing quoted lines.

    The resulting message is prefixed with the first five characters of the user's display name followed by "[M]: ", has quoted lines removed, and is truncated to fit within the allowed message length.

    Parameters:
        full_display_name (str): The user's full display name to be truncated for the prefix.
        text (str): The reply text, possibly containing quoted lines.

    Returns:
        str: The formatted and truncated reply message.
    """
    # Determine the base text to use (prefer the raw Meshtastic payload when present)
    base_text = mesh_text_override if mesh_text_override else text

    clean_text = strip_quoted_lines(base_text).strip()

    # Handle remote meshnet replies by using the remote sender's prefix format
    if meshnet_name and local_meshnet_name and meshnet_name != local_meshnet_name:
        sender_long = longname or full_display_name or shortname or "???"
        sender_short = shortname or sender_long[:SHORTNAME_FALLBACK_LENGTH] or "???"
        short_meshnet_name = meshnet_name[:MESHNET_NAME_ABBREVIATION_LENGTH]

        prefix_candidates = [
            f"[{sender_long}/{meshnet_name}]: ",
            f"[{sender_long}/{short_meshnet_name}]: ",
            f"{sender_long}/{meshnet_name}: ",
            f"{sender_long}/{short_meshnet_name}: ",
            f"{sender_short}/{meshnet_name}: ",
            f"{sender_short}/{short_meshnet_name}: ",
        ]

        matrix_prefix_full = get_matrix_prefix(
            config, sender_long, sender_short, meshnet_name
        )
        matrix_prefix_short = get_matrix_prefix(
            config, sender_long, sender_short, short_meshnet_name
        )
        prefix_candidates.extend([matrix_prefix_full, matrix_prefix_short])

        for candidate in prefix_candidates:
            if candidate and clean_text.startswith(candidate):
                clean_text = clean_text[len(candidate) :].lstrip()
                break

        if not clean_text and mesh_text_override:
            clean_text = strip_quoted_lines(mesh_text_override).strip()

        mesh_prefix = f"{sender_short}/{short_meshnet_name}:"
        reply_body = f" {clean_text}" if clean_text else ""
        reply_message = f"{mesh_prefix}{reply_body}"
        return truncate_message(reply_message.strip())

    # Default behavior for local Matrix users (retain existing prefix logic)
    prefix = get_meshtastic_prefix(config, full_display_name)
    reply_message = f"{prefix}{clean_text}" if clean_text else prefix.rstrip()
    return truncate_message(reply_message)


async def send_reply_to_meshtastic(
    reply_message,
    full_display_name,
    room_config,
    room,
    event,
    text,
    storage_enabled,
    local_meshnet_name,
    reply_id=None,
):
    """
    Enqueue a Matrix reply to be delivered over Meshtastic as either a structured reply or a regular broadcast.

    If broadcasting is disabled this function does nothing. When storage_enabled is True, it constructs a mapping record that links the originating Matrix event to the Meshtastic message and attaches it to the queued message so replies and reactions can be correlated later. Errors are logged; the function does not raise.

    Parameters:
        reply_message (str): Text payload already formatted for Meshtastic.
        full_display_name (str): Sender display name used in queue descriptions and logs.
        room_config (dict): Room-specific configuration; must contain "meshtastic_channel" (integer channel index).
        room: Matrix room object; its room_id is used for mapping metadata.
        event: Matrix event object; its event_id is used for mapping metadata.
        text (str): Original Matrix message text used when building mapping metadata.
        storage_enabled (bool): If True, create and attach a message-mapping record to the queued Meshtastic message.
        local_meshnet_name (str | None): Local meshnet name included in mapping metadata when present.
        reply_id (int | None): If provided, send as a structured Meshtastic reply targeting this Meshtastic message ID; otherwise send a regular broadcast.
    """
    meshtastic_interface = await _connect_meshtastic()
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    meshtastic_channel = room_config["meshtastic_channel"]

    broadcast_enabled = get_meshtastic_config_value(
        config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
    )
    logger.debug(f"broadcast_enabled = {broadcast_enabled}")

    if broadcast_enabled:
        try:
            # Create mapping info once if storage is enabled
            mapping_info = None
            if storage_enabled:
                # Get message map configuration
                msgs_to_keep = _get_msgs_to_keep_config()

                mapping_info = _create_mapping_info(
                    event.event_id, room.room_id, text, local_meshnet_name, msgs_to_keep
                )

            if reply_id is not None:
                # Send as a structured reply using our custom function
                # Queue the reply message
                success = queue_message(
                    sendTextReply,
                    meshtastic_interface,
                    text=reply_message,
                    reply_id=reply_id,
                    channelIndex=meshtastic_channel,
                    description=f"Reply from {full_display_name} to message {reply_id}",
                    mapping_info=mapping_info,
                )

                if success:
                    # Get queue size to determine logging approach
                    queue_size = get_message_queue().get_queue_size()

                    if queue_size > 1:
                        meshtastic_logger.info(
                            f"Relaying Matrix reply from {full_display_name} to radio broadcast as structured reply (queued: {queue_size} messages)"
                        )
                    else:
                        meshtastic_logger.info(
                            f"Relaying Matrix reply from {full_display_name} to radio broadcast as structured reply"
                        )
                else:
                    meshtastic_logger.error(
                        "Failed to relay structured reply to Meshtastic"
                    )
                    return
            else:
                # Send as regular message (fallback for when no reply_id is available)
                success = queue_message(
                    meshtastic_interface.sendText,
                    text=reply_message,
                    channelIndex=meshtastic_channel,
                    description=f"Reply from {full_display_name} (fallback to regular message)",
                    mapping_info=mapping_info,
                )

                if success:
                    # Get queue size to determine logging approach
                    queue_size = get_message_queue().get_queue_size()

                    if queue_size > 1:
                        meshtastic_logger.info(
                            f"Relaying Matrix reply from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                        )
                    else:
                        meshtastic_logger.info(
                            f"Relaying Matrix reply from {full_display_name} to radio broadcast"
                        )
                else:
                    meshtastic_logger.error(
                        "Failed to relay reply message to Meshtastic"
                    )
                    return

            # Message mapping is now handled automatically by the queue system

        except Exception:
            meshtastic_logger.exception("Error sending Matrix reply to Meshtastic")


async def handle_matrix_reply(
    room,
    event,
    reply_to_event_id,
    text,
    room_config,
    storage_enabled,
    local_meshnet_name,
    config,
    *,
    mesh_text_override=None,
    longname=None,
    shortname=None,
    meshnet_name=None,
):
    """
    Forward a Matrix reply to Meshtastic when the replied-to Matrix event maps to a Meshtastic message.

    If the Matrix event identified by reply_to_event_id has an associated Meshtastic mapping, format a Meshtastic reply that preserves sender attribution and enqueue it referencing the original Meshtastic message ID. If no mapping exists, do nothing.

    Parameters:
        room: Matrix room object where the reply originated.
        event: Matrix event object representing the reply.
        reply_to_event_id (str): Matrix event ID being replied to; used to locate the Meshtastic mapping.
        text (str): The reply text from Matrix.
        room_config (dict): Per-room relay configuration used when sending to Meshtastic.
        storage_enabled (bool): Whether message mapping/storage is enabled.
        local_meshnet_name (str): Local meshnet name used to determine cross-meshnet formatting.
        config (dict): Global relay configuration passed to formatting routines.
        mesh_text_override (str | None): Optional override text to send instead of the derived text.
        longname (str | None): Sender long display name used for prefixing.
        shortname (str | None): Sender short display name used for prefixing.
        meshnet_name (str | None): Remote meshnet name associated with the original mapping, if any.

    Returns:
        bool: `True` if a mapping was found and the reply was queued to Meshtastic, `False` otherwise.
    """
    # Look up the original message in the message map
    loop = asyncio.get_running_loop()
    orig = await loop.run_in_executor(
        None, get_message_map_by_matrix_event_id, reply_to_event_id
    )
    if not orig:
        logger.debug(
            f"Original message for Matrix reply not found in DB: {reply_to_event_id}"
        )
        return False  # Continue processing as normal message if original not found

    # Extract the original meshtastic_id to use as reply_id
    # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
    original_meshtastic_id = orig[0]

    # Get user display name
    full_display_name = await get_user_display_name(room, event)

    # Format the reply message
    reply_message = format_reply_message(
        config,
        full_display_name,
        text,
        longname=longname,
        shortname=shortname,
        meshnet_name=meshnet_name,
        local_meshnet_name=local_meshnet_name,
        mesh_text_override=mesh_text_override,
    )

    logger.info(
        f"Relaying Matrix reply from {full_display_name} to Meshtastic as reply to message {original_meshtastic_id}"
    )

    # Send the reply to Meshtastic with the original message ID as reply_id
    await send_reply_to_meshtastic(
        reply_message,
        full_display_name,
        room_config,
        room,
        event,
        text,
        storage_enabled,
        local_meshnet_name,
        reply_id=original_meshtastic_id,
    )

    return True  # Reply was handled, stop further processing


async def on_decryption_failure(room: MatrixRoom, event: MegolmEvent) -> None:
    """
    Handle a MegolmEvent that failed to decrypt by requesting the needed session keys.

    If a received encrypted event cannot be decrypted, this callback logs an error and attempts to request the missing keys from the device that sent them by creating and sending a to-device key request via the module-level Matrix client. The function will:
    - Set event.room_id to the room's id (monkey-patch) so the key request is properly scoped.
    - Create a key request from the event and send it with matrix_client.to_device().
    - Log success or any errors encountered.

    If the module-level Matrix client is not available, the function logs an error and returns without sending a request.
    """
    logger.error(
        f"Failed to decrypt event '{event.event_id}' in room '{room.room_id}'! "
        f"This is usually temporary and resolves on its own. "
        f"If this persists, the bot's session may be corrupt. "
        f"{msg_retry_auth_login()}."
    )

    # Attempt to request the keys for the failed event
    try:
        if not matrix_client:
            logger.error("Matrix client not available, cannot request keys.")
            return

        # Monkey-patch the event object with the correct room_id
        event.room_id = room.room_id

        if not matrix_client.device_id:
            logger.error(
                "Cannot request keys for event %s: client has no device_id",
                event.event_id,
            )
            return
        request = event.as_key_request(matrix_client.user_id, matrix_client.device_id)
        await matrix_client.to_device(request)
        logger.info(f"Requested keys for failed decryption of event {event.event_id}")
    except Exception:
        logger.exception(f"Failed to request keys for event {event.event_id}")


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: Union[
        RoomMessageText,
        RoomMessageNotice,
        ReactionEvent,
        RoomMessageEmote,
    ],
) -> None:
    """
    Handle an incoming Matrix room event and relay it to Meshtastic when applicable.

    Processes text, notice, emote, and reaction events for configured rooms: ignores events from before the bot started and events sent by the bot itself; respects per-room configuration and global interaction settings; routes reactions back to the originating Meshtastic message when a mapping exists (including forwarding remote-meshnet emote reactions as radio text); bridges Matrix replies to Meshtastic replies when a corresponding mapping is found and replies are enabled; relays regular Matrix messages to Meshtastic using configured prefix and truncation rules; and honours detection-sensor forwarding when enabled. Integrates with the plugin system and treats recognized bot commands as non-relayed.

    Side effects:
    - May enqueue Meshtastic send operations (text or data) via the internal queue.
    - May read and write persistent message mappings to support reply/reaction bridging.
    - May call Matrix APIs (e.g., to fetch display names) and connect to Meshtastic.
    """
    # DEBUG: Log all Matrix message events to trace reception
    logger.debug(
        f"Received Matrix event in room {room.room_id}: {type(event).__name__}"
    )
    logger.debug(
        f"Event details - sender: {event.sender}, timestamp: {event.server_timestamp}"
    )

    # Importing here to avoid circular imports and to keep logic consistent
    # Note: We do not call store_message_map directly here for inbound matrix->mesh messages.
    from mmrelay.message_queue import get_message_queue

    # That logic occurs inside matrix_relay if needed.
    full_display_name = "Unknown user"
    message_timestamp = event.server_timestamp

    # We do not relay messages that occurred before the bot started
    if message_timestamp < bot_start_time:
        return

    # Do not process messages from the bot itself
    if event.sender == bot_user_id:
        return

    # Note: MegolmEvent (encrypted) messages are handled by the `on_decryption_failure`
    # callback if they fail to decrypt. Successfully decrypted messages are automatically
    # converted to RoomMessageText/RoomMessageNotice/etc. by matrix-nio and handled normally.

    # Find the room_config that matches this room, if any
    room_config = None
    iterable = (
        matrix_rooms.values()
        if matrix_rooms and isinstance(matrix_rooms, dict)
        else (matrix_rooms or [])
    )
    for room_conf in iterable:
        if isinstance(room_conf, dict) and room_conf.get("id") == room.room_id:
            room_config = room_conf
            break

    # Only proceed if the room is supported
    if not room_config:
        return

    relates_to = event.source["content"].get("m.relates_to")
    global config

    # Check if config is available
    if not config:
        logger.error("No configuration available for Matrix message processing.")

    is_reaction = False
    reaction_emoji = None
    original_matrix_event_id = None

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot process Matrix message.")
        return

    # Get interaction settings
    interactions = get_interaction_settings(config)
    storage_enabled = message_storage_enabled(interactions)

    # Check if this is a Matrix ReactionEvent (usually m.reaction)
    if isinstance(event, ReactionEvent):
        # This is a reaction event
        is_reaction = True
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        if relates_to and "event_id" in relates_to and "key" in relates_to:
            # Extract the reaction emoji and the original event it relates to
            reaction_emoji = relates_to["key"]
            original_matrix_event_id = relates_to["event_id"]
            logger.debug(
                f"Original matrix event ID: {original_matrix_event_id}, Reaction emoji: {reaction_emoji}"
            )

    # Check if this is a Matrix RoomMessageEmote (m.emote)
    if isinstance(event, RoomMessageEmote):
        logger.debug(f"Processing Matrix emote event: {event.source}")
        # For RoomMessageEmote, treat as remote reaction only if it has reaction indicators
        content = event.source.get("content", {})
        reaction_body = content.get("body", "")
        meshtastic_replyId = content.get("meshtastic_replyId")
        relates_to = event.source.get("relates_to", {})

        # Only treat as reaction if it has meshtastic_replyId or relates_to metadata
        is_reaction = bool(meshtastic_replyId or relates_to)

        if is_reaction:
            # We need to manually extract the reaction emoji from the body
            reaction_match = re.search(r"reacted (.+?) to", reaction_body)
            reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"

    # Some Matrix relays (especially Meshtastic bridges) provide the raw mesh
    # payload alongside the formatted body. Prefer that when available so we do
    # not lose content if the formatted text is empty or stripped unexpectedly.
    mesh_text_override = event.source["content"].get("meshtastic_text")
    if isinstance(mesh_text_override, str):
        mesh_text_override = mesh_text_override.strip()
        if not mesh_text_override:
            mesh_text_override = None
    else:
        mesh_text_override = None

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

    # Initialize text to empty string to prevent UnboundLocalError
    text = ""

    # Establish baseline text content for non-reaction messages
    if not is_reaction or mesh_text_override:
        body_text = getattr(event, "body", "")
        content_body = event.source["content"].get("body", "")
        text = mesh_text_override or body_text or content_body or ""
        text = text.strip()

    # If a message has suppress flag, do not process
    if suppress:
        return

    # If this is a reaction and reactions are disabled, do nothing
    if is_reaction and not interactions["reactions"]:
        logger.debug(
            "Reaction event encountered but reactions are disabled. Doing nothing."
        )
        return

    local_meshnet_name = config["meshtastic"]["meshnet_name"]

    # Check if this is a Matrix reply (not a reaction)
    is_reply = False
    reply_to_event_id = None
    if not is_reaction and relates_to and "m.in_reply_to" in relates_to:
        reply_to_event_id = relates_to["m.in_reply_to"].get("event_id")
        if reply_to_event_id:
            is_reply = True
            logger.debug(f"Processing Matrix reply to event: {reply_to_event_id}")

    # If this is a reaction and reactions are enabled, attempt to relay it
    if is_reaction and interactions["reactions"]:
        # Check if we need to relay a reaction from a remote meshnet to our local meshnet.
        # If meshnet_name != local_meshnet_name and meshtastic_replyId is present and this is an emote,
        # it's a remote reaction that needs to be forwarded as a text message describing the reaction.
        if (
            meshnet_name
            and meshnet_name != local_meshnet_name
            and meshtastic_replyId
            and isinstance(event, RoomMessageEmote)
        ):
            logger.info(f"Relaying reaction from remote meshnet: {meshnet_name}")

            short_meshnet_name = meshnet_name[:MESHNET_NAME_ABBREVIATION_LENGTH]

            # Format the reaction message for relaying to the local meshnet.
            # The necessary information is in the m.emote event
            if not shortname:
                shortname = longname[:SHORTNAME_FALLBACK_LENGTH] if longname else "???"

            meshtastic_text_db = event.source["content"].get("meshtastic_text", "")
            # Strip out any quoted lines from the text
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:MESSAGE_PREVIEW_LENGTH] + "..."
                if len(meshtastic_text_db) > MESSAGE_PREVIEW_LENGTH
                else meshtastic_text_db
            )

            reaction_message = f'{shortname}/{short_meshnet_name} reacted {reaction_emoji} to "{abbreviated_text}"'

            # Relay the remote reaction to the local meshnet.
            meshtastic_interface = await _connect_meshtastic()
            if not meshtastic_interface:
                logger.error(
                    "Failed to connect to Meshtastic for remote reaction relay"
                )
                return
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if get_meshtastic_config_value(
                config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
            ):
                meshtastic_logger.info(
                    f"Relaying reaction from remote meshnet {meshnet_name} to radio broadcast"
                )
                logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                success = queue_message(
                    meshtastic_interface.sendText,
                    text=reaction_message,
                    channelIndex=meshtastic_channel,
                    description=f"Remote reaction from {meshnet_name}",
                )

                if success:
                    logger.debug(
                        f"Queued remote reaction to Meshtastic: {reaction_message}"
                    )
                else:
                    logger.error("Failed to relay remote reaction to Meshtastic")
                    return
            # We've relayed the remote reaction to our local mesh, so we're done.
            return

        # If original_matrix_event_id is set, this is a reaction to some other matrix event
        if original_matrix_event_id:
            orig = get_message_map_by_matrix_event_id(original_matrix_event_id)
            if not orig:
                # If we don't find the original message in the DB, we suspect it's a reaction-to-reaction scenario
                logger.debug(
                    "Original message for reaction not found in DB. Possibly a reaction-to-reaction scenario. Not forwarding."
                )
                return

            # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
            meshtastic_id, matrix_room_id, meshtastic_text_db, meshtastic_meshnet_db = (
                orig
            )
            # Get room-specific display name if available, fallback to global display name
            full_display_name = await get_user_display_name(room, event)

            # If not from a remote meshnet, proceed as normal to relay back to the originating meshnet
            prefix = get_meshtastic_prefix(config, full_display_name)

            # Remove quoted lines so we don't bring in the original '>' lines from replies
            meshtastic_text_db = strip_quoted_lines(meshtastic_text_db)
            meshtastic_text_db = meshtastic_text_db.replace("\n", " ").replace(
                "\r", " "
            )

            abbreviated_text = (
                meshtastic_text_db[:MESSAGE_PREVIEW_LENGTH] + "..."
                if len(meshtastic_text_db) > MESSAGE_PREVIEW_LENGTH
                else meshtastic_text_db
            )

            # Always use our local meshnet_name for outgoing events
            reaction_message = (
                f'{prefix}reacted {reaction_emoji} to "{abbreviated_text}"'
            )
            meshtastic_interface = await _connect_meshtastic()
            if not meshtastic_interface:
                logger.error("Failed to connect to Meshtastic for local reaction relay")
                return
            from mmrelay.meshtastic_utils import logger as meshtastic_logger

            meshtastic_channel = room_config["meshtastic_channel"]

            if get_meshtastic_config_value(
                config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
            ):
                meshtastic_logger.info(
                    f"Relaying reaction from {full_display_name} to radio broadcast"
                )
                logger.debug(
                    f"Sending reaction to Meshtastic with meshnet={local_meshnet_name}: {reaction_message}"
                )
                success = queue_message(
                    meshtastic_interface.sendText,
                    text=reaction_message,
                    channelIndex=meshtastic_channel,
                    description=f"Local reaction from {full_display_name}",
                )

                if success:
                    logger.debug(
                        f"Queued local reaction to Meshtastic: {reaction_message}"
                    )
                else:
                    logger.error("Failed to relay local reaction to Meshtastic")
                    return
            return

    # Handle Matrix replies to Meshtastic messages (only if replies are enabled)
    if is_reply and reply_to_event_id and interactions["replies"]:
        reply_handled = await handle_matrix_reply(
            room,
            event,
            reply_to_event_id,
            text,
            room_config,
            storage_enabled,
            local_meshnet_name,
            config,
            mesh_text_override=mesh_text_override,
            longname=longname,
            shortname=shortname,
            meshnet_name=meshnet_name,
        )
        if reply_handled:
            return

    # For Matrix->Mesh messages from a remote meshnet, rewrite the message format
    if longname and meshnet_name:
        # Always include the meshnet_name in the full display name.
        full_display_name = f"{longname}/{meshnet_name}"

        if meshnet_name != local_meshnet_name:
            # A message from a remote meshnet relayed into Matrix, now going back out
            logger.info(f"Processing message from remote meshnet: {meshnet_name}")
            short_meshnet_name = meshnet_name[:MESHNET_NAME_ABBREVIATION_LENGTH]
            # If shortname is not available, derive it from the longname
            if shortname is None:
                shortname = longname[:SHORTNAME_FALLBACK_LENGTH] if longname else "???"
            if mesh_text_override:
                text = mesh_text_override
            # Remove the original prefix to avoid double-tagging
            # Get the prefix that would have been used for this message
            original_prefix = get_matrix_prefix(
                config, longname, shortname, meshnet_name
            )
            if original_prefix and text.startswith(original_prefix):
                text = text[len(original_prefix) :]
                logger.debug(
                    f"Removed original prefix '{original_prefix}' from remote meshnet message"
                )
            if not text and mesh_text_override:
                text = mesh_text_override
            text = truncate_message(text)
            # Use the configured prefix format for remote meshnet messages
            prefix = get_matrix_prefix(config, longname, shortname, short_meshnet_name)
            full_message = f"{prefix}{text}"
            if not text:
                logger.warning(
                    "Remote meshnet message from %s had empty text after formatting; skipping relay",
                    meshnet_name,
                )
                return
        else:
            # If this message is from our local meshnet (loopback), we ignore it
            return
    else:
        # Normal Matrix message from a Matrix user
        full_display_name = await get_user_display_name(room, event)
        prefix = get_meshtastic_prefix(config, full_display_name, event.sender)
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        full_message = truncate_message(full_message)

    # Extract portnum for potential detection sensor handling
    portnum = event.source["content"].get("meshtastic_portnum")

    # Plugin functionality
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                found_matching_plugin = await plugin.handle_room_message(
                    room, event, full_message
                )
                if found_matching_plugin:
                    logger.info(
                        f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                    )
            except Exception as e:
                logger.error(
                    f"Error processing message with plugin {plugin.plugin_name}: {e}"
                )

    # Check if the message is a command directed at the bot
    is_command = False
    for plugin in plugins:
        for command in plugin.get_matrix_commands():
            if bot_command(command, event):
                is_command = True
                break
        if is_command:
            break

    # If this is a command, we do not send it to the mesh
    if is_command:
        logger.debug("Message is a command, not sending to mesh")
        return

    # Check if this is a detection sensor packet (before connecting to Meshtastic)
    is_detection_packet = portnum == DETECTION_SENSOR_APP

    if is_detection_packet:
        detection_enabled = get_meshtastic_config_value(
            config, "detection_sensor", DEFAULT_DETECTION_SENSOR
        )
        broadcast_enabled = get_meshtastic_config_value(
            config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
        )
        from mmrelay.meshtastic_utils import logger as meshtastic_logger

        if not broadcast_enabled:
            meshtastic_logger.debug(
                f"Detection sensor packet received from {full_display_name}, but broadcast is disabled."
            )
            return

        if not detection_enabled:
            meshtastic_logger.debug(
                f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
            )
            return

        meshtastic_interface = await _connect_meshtastic()

        if not meshtastic_interface:
            logger.error(
                "Failed to connect to Meshtastic. Cannot relay detection data."
            )
            return

        meshtastic_channel = room_config["meshtastic_channel"]

        import meshtastic.protobuf.portnums_pb2  # type: ignore[import-untyped]

        success = queue_message(
            meshtastic_interface.sendData,
            data=text.encode("utf-8"),
            channelIndex=meshtastic_channel,
            portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
            description=f"Detection sensor data from {full_display_name}",
        )

        if success:
            queue_size = get_message_queue().get_queue_size()
            if queue_size > 1:
                meshtastic_logger.info(
                    f"Relaying detection sensor data from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                )
            else:
                meshtastic_logger.info(
                    f"Relaying detection sensor data from {full_display_name} to radio broadcast"
                )
        else:
            meshtastic_logger.error(
                "Failed to relay detection sensor data to Meshtastic"
            )
        return

    # Connect to Meshtastic for regular messages
    meshtastic_interface = await _connect_meshtastic()
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not meshtastic_interface:
        logger.error("Failed to connect to Meshtastic. Cannot relay message.")
        return

    meshtastic_channel = room_config["meshtastic_channel"]

    # If message is from Matrix and broadcast_enabled is True, relay to Meshtastic
    # Note: If relay_reactions is False, we won't store message_map, but we can still relay.
    # The lack of message_map storage just means no reaction bridging will occur.
    if not found_matching_plugin:
        if get_meshtastic_config_value(
            config, "broadcast_enabled", DEFAULT_BROADCAST_ENABLED, required=False
        ):
            mapping_info = None
            if storage_enabled:
                # Check database config for message map settings (preferred format)
                msgs_to_keep = _get_msgs_to_keep_config()

                mapping_info = _create_mapping_info(
                    event.event_id,
                    room.room_id,
                    text,
                    local_meshnet_name,
                    msgs_to_keep,
                )

            success = queue_message(
                meshtastic_interface.sendText,
                text=full_message,
                channelIndex=meshtastic_channel,
                description=f"Message from {full_display_name}",
                mapping_info=mapping_info,
            )

            if success:
                # Get queue size to determine logging approach
                queue_size = get_message_queue().get_queue_size()

                if queue_size > 1:
                    meshtastic_logger.info(
                        f"Relaying message from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                    )
                else:
                    meshtastic_logger.info(
                        f"Relaying message from {full_display_name} to radio broadcast"
                    )
            else:
                meshtastic_logger.error("Failed to relay message to Meshtastic")
                return
            # Message mapping is now handled automatically by the queue system
        else:
            logger.debug(
                f"broadcast_enabled is False - not relaying message from {full_display_name} to Meshtastic"
            )


class ImageUploadError(RuntimeError):
    """Raised when Matrix image upload fails."""

    def __init__(self, upload_response: UploadError | UploadResponse | None):
        message = getattr(upload_response, "message", "Unknown error")
        super().__init__(f"Image upload failed: {message}")
        self.upload_response = upload_response


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> Union[UploadResponse, UploadError]:
    """
    Upload an image to Matrix and return response.

    Returns:
        UploadResponse with content_uri on success, or UploadError on failure.
    """
    # Determine image format from filename
    image_format = os.path.splitext(filename)[1][1:].upper() or "PNG"
    if image_format == "JPG":
        image_format = "JPEG"

    # Standard MIME type mapping for common image formats
    MIME_TYPE_MAP = {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "GIF": "image/gif",
        "WEBP": "image/webp",
        "BMP": "image/bmp",
        "TIFF": "image/tiff",
    }

    buffer = io.BytesIO()
    try:
        image.save(buffer, format=image_format)
        # If save succeeds, determine content type from the format we used
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type or not content_type.startswith("image/"):
            # Use proper MIME type mapping for fallback
            content_type = MIME_TYPE_MAP.get(image_format, "image/png")
    except (ValueError, KeyError):
        # Fallback to PNG if format is unsupported
        buffer.seek(0)
        buffer.truncate(0)
        image.save(buffer, format="PNG")
        content_type = "image/png"

    image_data = buffer.getvalue()

    try:
        response, _ = await client.upload(
            io.BytesIO(image_data),
            content_type=content_type,
            filename=filename,
            filesize=len(image_data),
        )
    except NIO_COMM_EXCEPTIONS as e:
        # Convert nio communication exceptions to an UploadError instance
        logger.error(f"Image upload failed due to a network error: {e}")
        upload_error = UploadError(message=str(e))
        return upload_error
    else:
        return response


async def send_room_image(
    client: AsyncClient,
    room_id: str,
    upload_response: Union[UploadResponse, UploadError, None],
    filename: str = "image.png",
):
    """
    Sends an already uploaded image to specified room.

    Args:
        client: Matrix AsyncClient instance
        room_id: Target room ID
        upload_response: Upload response from upload_image
        filename: Filename to use in message body (default: "image.png")
    """
    content_uri = getattr(upload_response, "content_uri", None)
    if content_uri:
        await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.image",
                "url": content_uri,
                "body": filename,
            },
        )
    else:
        logger.error(
            f"Upload failed: {getattr(upload_response, 'message', 'Unknown error')}"
        )
        raise ImageUploadError(upload_response)


async def send_image(
    client: AsyncClient, room_id: str, image: Image.Image, filename: str = "image.png"
):
    """
    Upload and send an image to a Matrix room.

    This is a convenience function that combines upload_image and send_room_image.

    Args:
        client: Matrix AsyncClient instance
        room_id: Target room ID
        image: PIL Image to send
        filename: Filename to use for upload and message body (default: "image.png")
    """
    response = await upload_image(client=client, image=image, filename=filename)
    await send_room_image(client, room_id, upload_response=response, filename=filename)


async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
    """
    Callback to handle room member events, specifically tracking room-specific display name changes.
    This ensures we detect when users update their display names in specific rooms.

    Note: This callback doesn't need to do any explicit processing since matrix-nio
    automatically updates the room state and room.user_name() will return the
    updated room-specific display name immediately after this event.
    """
    # The callback is registered to ensure matrix-nio processes the event,
    # but no explicit action is needed since room.user_name() automatically
    # handles room-specific display names after the room state is updated.
    pass
