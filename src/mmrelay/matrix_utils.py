import asyncio
import getpass
import html
import importlib
import inspect
import io
import json
import logging
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from json import JSONDecodeError
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Generator,
    Optional,
    Tuple,
    Type,
    cast,
)
from urllib.parse import urlparse

if TYPE_CHECKING:
    pass

# matrix-nio is not marked py.typed in our environment, so mypy treats it as untyped.
from nio import (
    AsyncClient,
    AsyncClientConfig,
    DiscoveryInfoError,
    DiscoveryInfoResponse,
    MatrixRoom,
    MegolmEvent,
    ProfileGetDisplayNameError,
    ProfileGetDisplayNameResponse,
    ReactionEvent,
    RoomMessageEmote,
    RoomMessageNotice,
    RoomMessageText,
    SyncError,
    UploadError,
    UploadResponse,
)
from nio.events.room_events import (
    RoomMemberEvent,
)

# Import InviteMemberEvent separately to avoid submodule import issues
try:
    from nio import InviteMemberEvent  # pyright: ignore[reportMissingImports]
except ImportError:
    from nio.events.invite_events import (
        InviteMemberEvent,
    )

from PIL import Image

import mmrelay.config as config_module

# Local imports
from mmrelay.cli_utils import (
    _create_ssl_context,
    msg_require_auth_login,
    msg_retry_auth_login,
)
from mmrelay.config import (
    get_e2ee_store_dir,
    get_explicit_credentials_path,
    get_meshtastic_config_value,
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
    PORTNUM_DETECTION_SENSOR_APP,
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
from mmrelay.meshtastic_utils import connect_meshtastic, send_text_reply

# Import meshtastic protobuf for port numbers when needed
from mmrelay.message_queue import get_message_queue, queue_message
from mmrelay.paths import get_credentials_path

# Avoid mypy attr-defined complaints when config symbols move across refactors.
_config_any = cast(Any, config_module)
async_load_credentials = cast(
    Callable[[], Awaitable[dict[str, Any] | None]],
    _config_any.async_load_credentials,
)
InvalidCredentialsPathTypeError = cast(
    type[Exception], _config_any.InvalidCredentialsPathTypeError
)

# Import nio exception types with error handling for test environments.
# matrix-nio is not marked py.typed in our env; keep import-untyped for mypy --strict.
try:
    nio_exceptions = importlib.import_module("nio.exceptions")
    nio_responses = importlib.import_module("nio.responses")

    NioLocalProtocolError = nio_exceptions.LocalProtocolError
    NioLocalTransportError = nio_exceptions.LocalTransportError
    NioRemoteProtocolError = nio_exceptions.RemoteProtocolError
    NioRemoteTransportError = nio_exceptions.RemoteTransportError
    NioLoginError = nio_responses.LoginError
    NioLogoutError = nio_responses.LogoutError
except (ImportError, AttributeError):
    # Fallback for test environments where nio imports might fail
    class _NioStubError(Exception):
        """Stub exception for nio errors in test mode"""

        pass

    NioLoginError = _NioStubError
    NioLogoutError = _NioStubError
    NioLocalProtocolError = _NioStubError
    NioRemoteProtocolError = _NioStubError
    NioLocalTransportError = _NioStubError
    NioRemoteTransportError = _NioStubError

NIO_COMM_EXCEPTIONS: tuple[type[BaseException], ...] = (
    NioLocalProtocolError,
    NioRemoteProtocolError,
    NioLocalTransportError,
    NioRemoteTransportError,
    asyncio.TimeoutError,
)
# jsonschema is a matrix-nio dependency but keep import guarded for safety.
jsonschema: Any = None
try:
    import jsonschema as _jsonschema  # pyright: ignore[reportMissingImports]  # type: ignore[import-untyped]

    jsonschema = _jsonschema
except ImportError:  # pragma: no cover - jsonschema is expected in runtime
    pass
# Provide a concrete ValidationError type for explicit exception handling.
if jsonschema is not None:
    from jsonschema.exceptions import ValidationError as _ValidationError

    JSONSCHEMA_VALIDATION_ERROR: Type[BaseException] = _ValidationError
else:

    class _JsonSchemaValidationError(Exception):
        """Fallback when jsonschema is unavailable."""

    JSONSCHEMA_VALIDATION_ERROR = _JsonSchemaValidationError
# Exception handling strategy:
# Catch only expected nio/network/timeouts so programming errors surface during testing.

logger = get_logger(name="Matrix")


class MissingMatrixRoomsError(ValueError):
    """Raised when matrix_rooms configuration is missing."""

    def __init__(self) -> None:
        super().__init__("Missing required matrix_rooms configuration")


class MatrixSyncTimeoutError(ConnectionError):
    """Raised when initial Matrix sync times out."""

    def __init__(self) -> None:
        super().__init__("Matrix sync timed out")


class MatrixSyncFailedError(ConnectionError):
    """Raised when Matrix sync fails."""

    def __init__(self) -> None:
        super().__init__("Matrix sync failed")


class MatrixSyncFailedDetailsError(ConnectionError):
    """Raised when Matrix sync fails with detailed error info."""

    def __init__(self, error_type: str, error_details: str) -> None:
        super().__init__(f"Matrix sync failed: {error_type} - {error_details}")


_MIME_TYPE_MAP: Dict[str, str] = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
    "BMP": "image/bmp",
    "TIFF": "image/tiff",
}


def _is_room_alias(value: Any) -> bool:
    """
    Determine whether a value is a Matrix room alias.

    Returns:
        `True` if `value` is a string that begins with '#', `False` otherwise.
    """

    return isinstance(value, str) and value.startswith("#")


def _get_valid_device_id(device_id_value: Any) -> Optional[str]:
    """
    Normalize and validate a device ID value.

    Parameters:
        device_id_value (Any): Value to validate; expected to be a string or other type.

    Returns:
        Optional[str]: The input string with surrounding whitespace removed if it is non-empty, `None` otherwise.
    """
    if isinstance(device_id_value, str):
        value = device_id_value.strip()
        return value or None
    return None


def _extract_localpart_from_mxid(mxid: str | None) -> str | None:
    """
    Extract the localpart from a Matrix MXID.

    Parameters:
        mxid (str | None): A Matrix user ID (e.g., "@user:server") or localpart.

    Returns:
        str | None: The localpart portion of the MXID (without @ and server),
        or the original value if it's already a localpart, or None if input is None.
    """
    if not mxid:
        return mxid
    if mxid.startswith("@"):
        return mxid[1:].split(":", 1)[0]
    return mxid


def _is_room_mapped(mapping: Any, room_id_or_alias: str) -> bool:
    """
    Determine whether a room ID or alias exists in a matrix_rooms configuration.

    Parameters:
        mapping (list|dict): The matrix_rooms configuration (accepted as a list or dict form).
        room_id_or_alias (str): Room ID (e.g., "!abc:server") or room alias (e.g., "#room:server").

    Returns:
        bool: `True` if the room ID or alias is present in the mapping, `False` otherwise.
    """
    if not isinstance(mapping, (list, dict)):
        return False

    return any(
        alias_or_id == room_id_or_alias
        for alias_or_id, _ in _iter_room_alias_entries(mapping)
    )


def _iter_room_alias_entries(
    mapping: Any,
) -> Generator[Tuple[str, Callable[[str], None]], None, None]:
    """
    Yield (alias_or_id, setter) pairs for entries in a Matrix room mapping.

    Each yielded tuple contains:
    - alias_or_id (str): the room alias or room ID found in the entry (may be an alias starting with '#' or a canonical room ID). If a dict entry has no "id" key, an empty string is yielded.
    - setter (callable): a single-argument function new_id -> None that updates the original mapping in-place to replace the entry with the resolved room ID.

    Parameters:
        mapping (list|dict): A collection of room entries in one of two shapes:
            - list: items may be strings (alias or ID) or dicts with an "id" key.
            - dict: values may be strings (alias or ID) or dicts with an "id" key.

    Yields:
        tuple[str, Callable[[str], None]]: (alias_or_id, setter) for each entry in the mapping.
    """

    def _make_entry_setter(entry: dict[str, Any]) -> Callable[[str], None]:
        # Capture the current entry via default args to avoid loop-variable reuse.
        """
        Create and return a setter function that updates the given entry's "id" field in place.

        Parameters:
            entry (dict[str, Any]): The dictionary whose "id" key will be updated by the returned setter.

        Returns:
            Callable[[str], None]: A function that sets entry["id"] to the provided string.
        """

        def _set_entry_id(new_id: str, target: dict[str, Any] = entry) -> None:
            target["id"] = new_id

        return _set_entry_id

    def _make_list_setter(index: int, collection: list[Any]) -> Callable[[str], None]:
        """
        Create a callable that replaces the element at a fixed index in a given list.

        Parameters:
            index (int): The index in the list whose value the returned callable will replace.
            collection (list[Any]): The list to be modified by the returned callable.

        Returns:
            Callable[[str], None]: A function that accepts a single `new_id` string and sets `collection[index]` to `new_id`.
        """

        def _set_list_entry_value(
            new_id: str, idx: int = index, target: list[Any] = collection
        ) -> None:
            target[idx] = new_id

        return _set_list_entry_value

    def _make_dict_setter(
        key: Any, collection: dict[Any, Any]
    ) -> Callable[[str], None]:
        """
        Create a setter function that assigns a new string ID to a specific key in a dictionary.

        Parameters:
            key (Any): The dictionary key whose value the returned setter will replace.
            collection (dict[Any, Any]): The dictionary to be modified by the returned setter.

        Returns:
            setter (Callable[[str], None]): A function that sets collection[key] to the provided `new_id`.
        """

        def _set_dict_entry_value(
            new_id: str,
            target_key: Any = key,
            target: dict[Any, Any] = collection,
        ) -> None:
            target[target_key] = new_id

        return _set_dict_entry_value

    if isinstance(mapping, list):
        for index, entry in enumerate(mapping):
            if isinstance(entry, dict):
                yield (entry.get("id", ""), _make_entry_setter(entry))
            else:
                yield (entry, _make_list_setter(index, mapping))
    elif isinstance(mapping, dict):
        for key, entry in list(mapping.items()):
            if isinstance(entry, dict):
                yield (entry.get("id", ""), _make_entry_setter(entry))
            else:
                yield (entry, _make_dict_setter(key, mapping))


async def _resolve_aliases_in_mapping(
    mapping: Any,
    resolver: Callable[[str], Awaitable[str | None]],
) -> None:
    """
    Resolve Matrix room aliases found in a list or dict by replacing them in-place with resolved room IDs.

    Parameters:
        mapping (list|dict): A list or dict containing room identifiers or alias entries; entries that look like room aliases (strings starting with '#') will be replaced in-place when resolved.
        resolver (Callable[[str], Awaitable[str | None]]): Async callable that accepts a room alias and returns a resolved room ID (truthy) or None on failure.

    Returns:
        None

    Notes:
        If `mapping` is not a list or dict, the function logs a warning and makes no changes.
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


def _update_room_id_in_mapping(
    mapping: Any,
    alias: str,
    resolved_id: str,
) -> bool:
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
    Log Matrix rooms grouped by Meshtastic channel and show encryption/E2EE status indicators.

    Reads the "matrix_rooms" entry from config (accepting dict or list form), builds a mapping from room ID to its configured "meshtastic_channel", groups the provided rooms by channel, and logs each room with an icon indicating whether the room is encrypted and the supplied E2EE overall status.

    Parameters:
        rooms (dict): Mapping of room_id -> room object. Room objects should expose `display_name` and `encrypted` attributes; falls back to the room_id when `display_name` is missing.
        config (dict): Configuration containing a "matrix_rooms" section with entries that include "id" and "meshtastic_channel".
        e2ee_status (dict): E2EE status information; expects an "overall_status" key used to determine status messages (common values: "ready", "unavailable", "disabled").
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
    logger.info(f"Meshtastic Channels ↔ Matrix Rooms ({mapped_rooms} configured):")

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


def _can_auto_create_credentials(matrix_config: Dict[str, Any] | None) -> bool:
    """
    Determine whether the Matrix configuration contains the fields required to create credentials automatically.

    Parameters:
        matrix_config (dict): The `matrix` section from config.yaml.

    Returns:
        True if `homeserver`, a user id (`bot_user_id` or `user_id`), and `password` are present as non-empty strings, False otherwise.
    """
    if not isinstance(matrix_config, dict):
        return False
    homeserver = matrix_config.get("homeserver")
    user = matrix_config.get("bot_user_id") or matrix_config.get("user_id")
    password = matrix_config.get("password")
    return all(isinstance(v, str) and v.strip() for v in (homeserver, user, password))


def _normalize_bot_user_id(homeserver: str, bot_user_id: str | None) -> str | None:
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
        str | None: A normalized Matrix user ID in the form "@localpart:server",
        or None if bot_user_id is falsy.
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


def _get_msgs_to_keep_config(config_override: dict[str, Any] | None = None) -> int:
    """
    Return the configured number of Meshtastic-Matrix message mappings to retain.

    Looks up `database.msg_map.msgs_to_keep` in the provided configuration (or the module-level config when none is provided), falls back to legacy `db.msg_map.msgs_to_keep` with a deprecation warning, and returns DEFAULT_MSGS_TO_KEEP when the value is missing or not an integer.

    Parameters:
        config_override (dict[str, Any] | None): Optional config to consult instead of the module-level `config`.

    Returns:
        int: The configured number of mappings to keep, or DEFAULT_MSGS_TO_KEEP if unspecified or invalid.
    """
    global config
    effective_config = config_override if config_override is not None else config
    if not isinstance(effective_config, dict) or not effective_config:
        return DEFAULT_MSGS_TO_KEEP

    def _get_msg_map_config(section: dict[str, Any] | None) -> dict[str, Any] | None:
        """
        Extract the "msg_map" subsection from a configuration section if present and valid.

        Parameters:
            section (dict[str, Any] | None): Configuration section to inspect.

        Returns:
            dict[str, Any] | None: The value of `section["msg_map"]` if it exists and is a dict, otherwise `None`.
        """
        if not isinstance(section, dict):
            return None
        candidate = section.get("msg_map")
        return candidate if isinstance(candidate, dict) else None

    msg_map_config = _get_msg_map_config(effective_config.get("database"))

    # If not found in database config, check legacy db config
    if msg_map_config is None:
        msg_map_config = _get_msg_map_config(effective_config.get("db"))
        if msg_map_config is not None:
            logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )

    if msg_map_config is None:
        msg_map_config = {}

    msgs_to_keep = msg_map_config.get("msgs_to_keep", DEFAULT_MSGS_TO_KEEP)
    return msgs_to_keep if isinstance(msgs_to_keep, int) else DEFAULT_MSGS_TO_KEEP


def _get_detailed_matrix_error_message(matrix_response: Any) -> str:
    """
    Summarize a Matrix SDK response or error into a short, user-facing message.

    Accepts a bytes/bytearray, string, or an object exposing attributes such as `message`, `status_code`, or `transport_response`, and returns a concise, actionable description suitable for logs or user feedback (examples: authentication failure, forbidden access, rate limiting, server error, or a generic network/connectivity issue). The function prefers explicit message or HTTP status information when available, falls back to a safe string representation, and avoids exposing unhelpful object reprs or HTML fragments.

    Parameters:
        matrix_response: The response or error to summarize. May be raw bytes, a human-readable string, or an exception/response object with `message`, `status_code`, or `transport_response` attributes.

    Returns:
        A short descriptive error string (e.g., "Authentication failed - invalid or expired credentials", "Access forbidden - check user permissions", "Rate limited - too many requests", "Server error (HTTP <code>) - the Matrix server is experiencing issues", or "Network connectivity issue or server unreachable").
    """

    def _is_unhelpful_error_string(error_str: str) -> bool:
        """
        Detect whether an error message string is unhelpful (e.g., an object repr, bare HTML-like tag, or generic "unknown error").

        Parameters:
            error_str (str): The error message text to evaluate.

        Returns:
            bool: `true` if the string appears to be an unhelpful error message (contains an object memory-address repr, a lone HTML-like tag, or the phrase "unknown error"), `false` otherwise.
        """
        return (
            re.search(r"<.+? object at 0x[0-9a-fA-F]+>", error_str) is not None
            or re.search(r"<[a-zA-Z/][^>]*>", error_str) is not None
            or "unknown error" in error_str.lower()
        )

    try:
        # Handle bytes/bytearray types by converting to string
        if isinstance(matrix_response, (bytes, bytearray)):
            try:
                matrix_response = matrix_response.decode("utf-8")
            except UnicodeDecodeError:
                return "Network connectivity issue or server unreachable (binary data)"

        # If already a string, decide whether to return or fall back
        if isinstance(matrix_response, str):
            # Clean up object/HTML/unknown placeholders
            if _is_unhelpful_error_string(matrix_response):
                return "Network connectivity issue or server unreachable"
            return matrix_response

        # Try to extract specific error information from an object
        message_attr = getattr(matrix_response, "message", None)
        if message_attr:
            message = message_attr
            # Handle if message is bytes/bytearray
            if isinstance(message, (bytes, bytearray)):
                try:
                    message = message.decode("utf-8")
                except UnicodeDecodeError:
                    return "Network connectivity issue or server unreachable"
            if isinstance(message, str):
                return message
        status_code_attr = getattr(matrix_response, "status_code", None)
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
        elif hasattr(matrix_response, "transport_response"):
            # Check for transport-level errors
            transport = getattr(matrix_response, "transport_response", None)
            if transport and hasattr(transport, "status_code"):
                try:
                    status_code = int(transport.status_code)
                    return f"Transport error: HTTP {status_code}"
                except (ValueError, TypeError):
                    return "Network connectivity issue or server unreachable"

        # Fallback to string representation with safety checks
        try:
            error_str = str(matrix_response)
        except Exception:  # noqa: BLE001 — keep bridge alive on hostile __str__()
            # Keep broad here: custom nio/error objects can raise arbitrary exceptions in __str__;
            # returning a generic connectivity message prevents sync loop crashes and keeps handling consistent.
            logger.debug("Failed to convert matrix_response to string", exc_info=True)
            return "Network connectivity issue or server unreachable"

        if (
            error_str
            and error_str != "None"
            and not _is_unhelpful_error_string(error_str)
        ):
            return error_str
        else:
            return "Network connectivity issue or server unreachable"

    except (AttributeError, ValueError, TypeError) as e:
        logger.debug(
            "Failed to extract matrix error details from %r: %s", matrix_response, e
        )
        # If we can't extract error details, provide a generic but helpful message
        return (
            "Unable to determine specific error - likely a network connectivity issue"
        )


def _create_mapping_info(
    matrix_event_id: str,
    room_id: str,
    text: str,
    meshnet: str | None = None,
    msgs_to_keep: int | None = None,
) -> dict[str, Any] | None:
    """
    Create a mapping dictionary that links a Matrix event to a Meshtastic message.

    If `msgs_to_keep` is None, the value is obtained from _get_msgs_to_keep_config(). The `text` value in the mapping has quoted lines removed. Returns None when `matrix_event_id`, `room_id`, or `text` is missing or empty.

    Parameters:
        matrix_event_id: The Matrix event ID to map from.
        room_id: The Matrix room ID where the event was posted.
        text: The message text to store (quoted lines will be stripped).
        meshnet: Optional meshnet name to record for the mapping.
        msgs_to_keep: Optional override for how many message mappings to retain; if omitted, the configured default is used.

    Returns:
        A dict with keys `matrix_event_id`, `room_id`, `text`, `meshnet`, and `msgs_to_keep`, or `None` if required inputs are missing.
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


def get_interaction_settings(config: dict[str, Any] | None) -> dict[str, bool]:
    """
    Determine whether message reactions and replies are enabled according to the configuration.

    Checks the new `meshtastic.message_interactions` mapping first; if present, uses its `reactions` and `replies` values. If absent, falls back to the legacy `meshtastic.relay_reactions` flag (deprecated) which enables only reactions. If `config` is None or no relevant keys are present, both features are disabled.

    Parameters:
        config (dict[str, Any] | None): The loaded configuration mapping or None.

    Returns:
        dict[str, bool]: A mapping with keys `"reactions"` and `"replies"`. `"reactions"` is `True` when reactions are enabled, `"replies"` is `True` when replies are enabled; both are `False` by default.
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


def message_storage_enabled(interactions: dict[str, bool]) -> bool:
    """
    Determine if message storage is needed based on enabled message interactions.

    Returns:
        True if either reactions or replies are enabled in the interactions dictionary; otherwise, False.
    """
    return interactions["reactions"] or interactions["replies"]


def _add_truncated_vars(
    format_vars: dict[str, str], prefix: str, text: str | None
) -> None:
    """
    Populate format_vars with truncated variants of text using keys prefix1 … prefix{MAX_TRUNCATION_LENGTH}.

    Each generated key maps to the first N characters of text (or an empty string when text is None). This function mutates format_vars in place to ensure all truncation keys exist.

    Parameters:
        format_vars (dict[str, str]): Mapping to populate; mutated in place.
        prefix (str): Base name for keys; numeric suffixes 1..MAX_TRUNCATION_LENGTH are appended.
        text (str | None): Source string to truncate; treated as empty string when None.
    """
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


_PREFIX_DEFINITION_PATTERN = re.compile(r"^\[(.+?)\]:(\s*)")
# Escape underscores, asterisks, backticks, tildes, backslashes, and brackets inside prefixes
_MARKDOWN_ESCAPE_PATTERN = re.compile(r"([*_`~\\\[\]])")


def _escape_leading_prefix_for_markdown(message: str) -> tuple[str, bool]:
    """
    Prevent a leading reference-style Markdown link definition from being interpreted by escaping its bracketed prefix.

    If the message begins with a bracketed prefix followed by a colon (for example, "[name]: "), returns a version of the message where characters that would trigger Markdown link-definition parsing inside the leading brackets are backslash-escaped. If no such prefix is present the input is returned unchanged.

    Returns:
        tuple[str, bool]: `(safe_message, escaped)` where `safe_message` is the possibly-escaped message and `escaped` is `True` if an escape was performed, `False` otherwise.
    """
    match = _PREFIX_DEFINITION_PATTERN.match(message)
    if not match:
        return message, False

    prefix_text = match.group(1)
    spacing = match.group(2)
    escaped_prefix = _MARKDOWN_ESCAPE_PATTERN.sub(r"\\\1", prefix_text)
    escaped = f"\\[{escaped_prefix}]:{spacing}"
    return escaped + message[match.end() :], True


def validate_prefix_format(
    format_string: str, available_vars: dict[str, Any]
) -> tuple[bool, str | None]:
    """
    Validate that a str.format-compatible format string can be formatted using the provided test variables.

    Parameters:
        format_string (str): The format string to validate (uses str.format syntax).
        available_vars (dict): Mapping of placeholder names to sample values used to test formatting.

    Returns:
        tuple: (is_valid, error_message). is_valid is True if formatting succeeds, False otherwise. error_message is the exception message when invalid, or None when valid.
    """
    try:
        # Test format with dummy data
        format_string.format(**available_vars)
        return True, None
    except (KeyError, ValueError) as e:
        return False, str(e)


def get_meshtastic_prefix(
    config: dict[str, Any], display_name: str, user_id: str | None = None
) -> str:
    """
    Generate the Meshtastic message prefix according to configuration.

    When prefixing is enabled, return a formatted prefix that may include the user's display name and parts of their Matrix ID. The format string can reference these variables: `{display}` (full display name), `{displayN}` (truncated display name where N is a positive integer), `{user}` (full MXID), `{username}` (localpart without leading `@`), and `{server}` (homeserver domain). If the configured format is invalid, a safe default prefix is returned. If prefixing is disabled in the config, return an empty string.

    Parameters:
        user_id (str | None): Optional Matrix ID in the form `@localpart:server`; when provided, `username` and `server` variables are derived from it.

    Returns:
        str: The formatted prefix string when enabled, or an empty string if prefixing is disabled.
    """
    meshtastic_config = config.get("meshtastic", {})

    # Check if prefixes are enabled
    if not meshtastic_config.get("prefix_enabled", True):
        return ""

    # Get custom format or use default
    prefix_format_value = meshtastic_config.get(
        "prefix_format", DEFAULT_MESHTASTIC_PREFIX
    )
    prefix_format = (
        str(prefix_format_value)
        if prefix_format_value is not None
        else DEFAULT_MESHTASTIC_PREFIX
    )

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


def get_matrix_prefix(
    config: dict[str, Any], longname: str, shortname: str, meshnet_name: str
) -> str:
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
    matrix_prefix_format_value = matrix_config.get(
        "prefix_format", DEFAULT_MATRIX_PREFIX
    )
    matrix_prefix_format = (
        str(matrix_prefix_format_value)
        if matrix_prefix_format_value is not None
        else DEFAULT_MATRIX_PREFIX
    )
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


async def get_displayname(user_id: str) -> str | None:
    """
    Get the display name for a given user ID.

    Parameters:
        user_id (str): The Matrix user ID.

    Returns:
        str | None: The display name, or None if not available.
    """
    if not matrix_client:
        return None
    response = await matrix_client.get_displayname(user_id)
    return getattr(response, "displayname", None)


def bot_command(
    command: str,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    require_mention: bool = False,
) -> bool:
    """
    Determine whether a Matrix event addresses the bot with the given command.

    Checks the event's plain and HTML-formatted bodies. Matches when the message either starts with `!<command>` (only allowed when `require_mention` is False) or begins with an explicit mention of the bot (bot MXID or display name) optionally followed by punctuation/whitespace and then `!<command>`.

    Parameters:
        command (str): Command name to detect (without the leading `!`).
        event: Matrix event object expected to provide a plain `body` and a `source`/`content` with optional `formatted_body`.
        require_mention (bool): If True, only accept commands that explicitly mention the bot; if False, accept bare `!<command>` messages as well.

    Returns:
        bool: `True` if the message addresses the bot with the given command, `False` otherwise.
    """
    full_message = (getattr(event, "body", "") or "").strip()
    if not command:
        return False
    content = event.source.get("content", {})
    formatted_body = content.get("formatted_body", "")

    # Remove HTML tags and extract the text content
    text_content = re.sub(r"<[^>]+>", "", formatted_body).strip()

    bodies = [full_message, text_content]

    bare_pattern = rf"^!{re.escape(command)}(?:\s|$)"

    if not require_mention and any(
        re.match(bare_pattern, body, re.IGNORECASE) for body in bodies if body
    ):
        return True

    mention_parts: list[str] = []
    for ident in (bot_user_id, bot_user_name):
        if ident:
            try:
                mention_parts.append(re.escape(str(ident)))
            except Exception:
                logger.debug(
                    "Failed to escape identifier %r for bot_command pattern", ident
                )
                continue

    if not mention_parts:
        return False

    pattern = (
        rf"^(?:{'|'.join(mention_parts)})[,:;]?\s*!" rf"{re.escape(command)}(?:\s|$)"
    )

    return any(re.match(pattern, body, re.IGNORECASE) for body in bodies if body)


async def _connect_meshtastic() -> Any:
    """
    Obtain a Meshtastic interface usable from asynchronous code.

    Returns:
        meshtastic_iface: The Meshtastic interface or proxy object produced by the synchronous connector.
    """
    return await asyncio.to_thread(connect_meshtastic)


async def _get_meshtastic_interface_and_channel(
    room_config: dict[str, Any], purpose: str
) -> tuple[Any | None, int | None]:
    """
    Return a connected Meshtastic interface and the room's validated Meshtastic channel.

    Parameters:
        room_config (dict): Room configuration; must contain a non-negative integer under "meshtastic_channel".
        purpose (str): Short description of the caller's intent used in logged error messages.

    Returns:
        tuple: (meshtastic_interface, channel)
            - meshtastic_interface (Any | None): A connected Meshtastic interface object, or `None` if a connection could not be made.
            - channel (int | None): The validated non-negative channel number from the room config, or `None` if missing or invalid.
    """
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    meshtastic_interface = await _connect_meshtastic()
    if not meshtastic_interface:
        meshtastic_logger.error(f"Failed to connect to Meshtastic. Cannot {purpose}.")
        return None, None

    meshtastic_channel = room_config.get("meshtastic_channel")
    if meshtastic_channel is None:
        meshtastic_logger.error(
            f"Room config missing 'meshtastic_channel'; cannot {purpose}."
        )
        return None, None
    if not isinstance(meshtastic_channel, int) or meshtastic_channel < 0:
        meshtastic_logger.error(
            f"Invalid meshtastic_channel value {meshtastic_channel!r} in room config; must be a non-negative integer."
        )
        return None, None

    return meshtastic_interface, meshtastic_channel


async def _handle_detection_sensor_packet(
    config: dict[str, Any],
    room_config: dict[str, Any],
    full_display_name: str,
    text: str,
) -> None:
    """
    Relay detection-sensor text from Matrix to Meshtastic as a DETECTION_SENSOR_APP payload when enabled.

    If both global broadcast and detection_sensor processing are enabled, queue the provided text on the room's configured Meshtastic channel using the DETECTION_SENSOR_APP port; otherwise do nothing. Logs outcomes and returns silently on failures to obtain a Meshtastic interface or channel.

    Parameters:
        config (dict[str, Any]): Global configuration used to determine feature flags.
        room_config (dict[str, Any]): Room-specific configuration; must include "meshtastic_channel".
        full_display_name (str): Matrix sender display name used in the queued message description.
        text (str): Plain-text payload to send.
    """
    detection_enabled = get_meshtastic_config_value(
        config, "detection_sensor", DEFAULT_DETECTION_SENSOR
    )
    broadcast_enabled = get_meshtastic_config_value(
        config,
        "broadcast_enabled",
        DEFAULT_BROADCAST_ENABLED,
        required=False,
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

    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await _get_meshtastic_interface_and_channel(room_config, "relay detection data")
    if not meshtastic_interface:
        return

    import meshtastic.protobuf.portnums_pb2

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
        meshtastic_logger.error("Failed to relay detection sensor data to Meshtastic")


@dataclass
class MatrixAuthInfo:
    homeserver: str
    access_token: str
    user_id: str
    device_id: Optional[str]
    credentials: dict[str, Any] | None
    credentials_path: str | None


def _resolve_credentials_save_path(config_data: dict[str, Any] | None) -> str | None:
    """
    Determine the filesystem path where credentials should be saved.

    Attempts to resolve an explicit credentials path from configuration or falls back to the
    canonical credentials path; expands a leading `~` to the user home when present.

    Returns:
        The absolute path to use for saving credentials, or `None` if the path cannot be resolved.
    """
    try:
        explicit_path = get_explicit_credentials_path(config_data)
        if explicit_path:
            return os.path.expanduser(explicit_path)
        return str(get_credentials_path())
    except (InvalidCredentialsPathTypeError, TypeError, OSError, ValueError) as exc:
        logger.debug("Failed to resolve credentials path: %s", exc)
        return None


def _missing_credentials_keys(credentials: dict[str, Any]) -> list[str]:
    """Return required credentials keys missing from the provided mapping."""
    return [
        key
        for key in ("homeserver", "access_token", "user_id")
        if key not in credentials
    ]


async def _resolve_and_load_credentials(
    config_data: dict[str, Any] | None,
    matrix_section: Any,
) -> MatrixAuthInfo | None:
    """
    Resolve Matrix auth info from credentials, auto-login, or config.

    Returns:
        MatrixAuthInfo | None: Auth info when available, otherwise None.
    """
    credentials: dict[str, Any] | None = None
    e2ee_device_id: Optional[str] = None
    credentials_path: str | None = None

    try:
        credentials = await async_load_credentials()
    except asyncio.CancelledError:
        raise
    except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
        logger.warning("Error loading credentials: %s", exc)
        credentials = None

    if credentials is None:
        candidate_path = _resolve_credentials_save_path(config_data)
        if candidate_path and await asyncio.to_thread(os.path.isfile, candidate_path):  # type: ignore[arg-type]
            logger.warning("Ignoring invalid credentials file: %s", candidate_path)

    if credentials:
        missing_keys = _missing_credentials_keys(credentials)
        if missing_keys:
            logger.warning(
                "Ignoring credentials.json missing required keys: %s",
                ", ".join(missing_keys),
            )
            credentials = None
        else:
            credentials_path = _resolve_credentials_save_path(config_data)
            matrix_homeserver = credentials["homeserver"]
            matrix_access_token = credentials["access_token"]
            bot_user_id = credentials["user_id"]
            e2ee_device_id = _get_valid_device_id(credentials.get("device_id"))

            logger.debug(f"Using Matrix credentials (device: {e2ee_device_id})")

            if e2ee_device_id is None:
                logger.warning(
                    "credentials.json has no valid device_id; proceeding to restore session and discover device_id."
                )

            if isinstance(matrix_section, dict) and "access_token" in matrix_section:
                logger.info(
                    "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
                )

            return MatrixAuthInfo(
                homeserver=matrix_homeserver,
                access_token=matrix_access_token,
                user_id=bot_user_id,
                device_id=e2ee_device_id,
                credentials=credentials,
                credentials_path=credentials_path,
            )

    if _can_auto_create_credentials(matrix_section):
        matrix_section = cast(dict[str, Any], matrix_section)
        logger.info(
            "No credentials.json found, but config.yaml has password field. Attempting automatic login..."
        )

        homeserver = matrix_section["homeserver"]
        username = matrix_section.get("bot_user_id") or matrix_section.get("user_id")
        if username:
            username = _normalize_bot_user_id(homeserver, username)
        password = matrix_section["password"]

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
                credentials = await async_load_credentials()
                if not credentials:
                    logger.error("Failed to load newly created credentials")
                    return None

                missing_keys = _missing_credentials_keys(credentials)
                if missing_keys:
                    logger.error(
                        "Newly created credentials.json missing required keys: %s",
                        ", ".join(missing_keys),
                    )
                    return None

                credentials_path = _resolve_credentials_save_path(config_data)
                matrix_homeserver = credentials["homeserver"]
                matrix_access_token = credentials["access_token"]
                bot_user_id = credentials["user_id"]
                e2ee_device_id = _get_valid_device_id(credentials.get("device_id"))

                return MatrixAuthInfo(
                    homeserver=matrix_homeserver,
                    access_token=matrix_access_token,
                    user_id=bot_user_id,
                    device_id=e2ee_device_id,
                    credentials=credentials,
                    credentials_path=credentials_path,
                )
            else:
                logger.error(
                    "Automatic login failed. Please check your credentials or use 'mmrelay auth login'"
                )
                return None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "Error during automatic login (%s). Please use 'mmrelay auth login' for interactive setup",
                type(e).__name__,
            )
            return None

    if config_data is None:
        logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    if "matrix" not in config_data:
        logger.error(
            "No Matrix authentication available. Neither credentials.json nor matrix section in config found."
        )
        logger.error(msg_require_auth_login())
        return None

    if not isinstance(matrix_section, dict):
        logger.error(
            "Matrix configuration section is empty or invalid (expected a mapping under 'matrix')."
        )
        logger.error(msg_require_auth_login())
        return None

    matrix_access_token = matrix_section.get("access_token")
    if not isinstance(matrix_access_token, str) or not matrix_access_token.strip():
        logger.error("Matrix section is missing required field: access_token")
        logger.error(msg_require_auth_login())
        return None

    matrix_homeserver = matrix_section.get("homeserver")
    if not isinstance(matrix_homeserver, str) or not matrix_homeserver.strip():
        logger.error("Matrix section is missing required field: homeserver")
        return None

    raw_user_id = matrix_section.get("bot_user_id") or matrix_section.get("user_id")
    if isinstance(raw_user_id, str) and raw_user_id.strip():
        bot_user_id = _normalize_bot_user_id(matrix_homeserver, raw_user_id)
        if bot_user_id is None:
            logger.error("Matrix section has invalid bot_user_id")
            return None
    else:
        logger.warning(
            "Matrix section missing bot_user_id; continuing with access_token-only configuration"
        )
        bot_user_id = ""

    return MatrixAuthInfo(
        homeserver=matrix_homeserver,
        access_token=matrix_access_token,
        user_id=bot_user_id,
        device_id=None,
        credentials=None,
        credentials_path=None,
    )


async def _configure_e2ee(
    config_data: dict[str, Any],
    matrix_section: Any,
    e2ee_device_id: Optional[str],
) -> tuple[bool, str | None]:
    """Determine E2EE enablement and store path."""
    e2ee_enabled = False
    e2ee_store_path = None
    try:
        from mmrelay.config import is_e2ee_enabled

        e2ee_enabled = is_e2ee_enabled(config_data)
        logger.debug(
            f"E2EE detection: matrix config section present: {'matrix' in config_data}"
        )
        logger.debug(f"E2EE detection: e2ee enabled = {e2ee_enabled}")

        if e2ee_enabled:
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
                try:
                    importlib.import_module("olm")

                    try:
                        nio_crypto = importlib.import_module("nio.crypto")
                        if not hasattr(nio_crypto, "OlmDevice"):
                            raise ImportError("nio.crypto.OlmDevice is unavailable")

                        nio_store = importlib.import_module("nio.store")
                        if not hasattr(nio_store, "SqliteStore"):
                            raise ImportError("nio.store.SqliteStore is unavailable")

                        logger.debug("All E2EE dependencies are available")
                    except ImportError:
                        logger.exception("Missing E2EE dependency")
                        logger.error(
                            "Please reinstall with: pipx install 'mmrelay[e2e]'"
                        )
                        logger.warning("E2EE will be disabled for this session.")
                        e2ee_enabled = False
                    else:
                        logger.info("End-to-End Encryption (E2EE) is enabled")

                    if e2ee_enabled:
                        store_override = None
                        if isinstance(matrix_section, dict):
                            encryption_section = matrix_section.get("encryption")
                            e2ee_section = matrix_section.get("e2ee")
                            if isinstance(encryption_section, dict):
                                store_override = encryption_section.get("store_path")
                            if not store_override and isinstance(e2ee_section, dict):
                                store_override = e2ee_section.get("store_path")

                        if isinstance(store_override, str) and store_override:
                            e2ee_store_path = os.path.expanduser(store_override)
                        else:
                            e2ee_store_path = str(get_e2ee_store_dir())

                        try:
                            await asyncio.to_thread(
                                os.makedirs, e2ee_store_path, exist_ok=True
                            )
                        except OSError as e:
                            logger.warning(
                                "Could not create E2EE store directory %s: %s",
                                e2ee_store_path,
                                e,
                            )

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
                            logger.debug(
                                f"Found existing E2EE store files: {', '.join(db_files)}"
                            )
                        else:
                            logger.warning(
                                "No existing E2EE store files found. Encryption may not work correctly."
                            )

                        logger.debug(f"Using E2EE store path: {e2ee_store_path}")

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
        logger.debug("E2EE configuration not found or malformed, E2EE disabled")

    return e2ee_enabled, e2ee_store_path


def _initialize_matrix_client(
    homeserver: str,
    user_id: str,
    device_id: Optional[str],
    e2ee_enabled: bool,
    e2ee_store_path: str | None,
    ssl_context: ssl.SSLContext | None,
) -> AsyncClient:
    """Initialize AsyncClient with the unified configuration."""
    client_config = AsyncClientConfig(
        max_limit_exceeded=0,
        max_timeouts=0,
        store_sync_tokens=True,
        encryption_enabled=e2ee_enabled,
    )

    if device_id:
        logger.debug(f"Device ID from credentials: {device_id}")

    return AsyncClient(
        homeserver=homeserver,
        user=user_id,
        device_id=device_id,
        store_path=e2ee_store_path if e2ee_enabled else None,
        config=client_config,
        ssl=cast(Any, ssl_context),
    )


async def _perform_matrix_login(
    client: AsyncClient,
    auth_info: MatrixAuthInfo,
) -> Optional[str]:
    """Restore or configure Matrix login session."""
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
            logger.info(
                f"Restored login session for {user_id} with device {e2ee_device_id}"
            )
        else:
            logger.info("First-run E2EE setup: discovering device_id via whoami")
            client.access_token = access_token
            client.user_id = user_id

            try:
                whoami_response = await client.whoami()
                credentials_updated = False

                # Verify user_id matching
                discovered_user_id = getattr(whoami_response, "user_id", None)
                if discovered_user_id and user_id and discovered_user_id != user_id:
                    logger.warning(
                        f"Matrix user_id mismatch: credentials say {user_id} but whoami says {discovered_user_id}. "
                        "Updating credentials to match whoami."
                    )
                    user_id = discovered_user_id
                    client.user_id = user_id
                    if auth_info.credentials is not None:
                        auth_info.credentials["user_id"] = user_id
                        credentials_updated = True

                discovered_device_id = getattr(whoami_response, "device_id", None)
                if discovered_device_id and discovered_device_id != e2ee_device_id:
                    e2ee_device_id = discovered_device_id
                    client.device_id = e2ee_device_id
                    logger.info(f"Discovered device_id from whoami: {e2ee_device_id}")

                    if auth_info.credentials is not None:
                        auth_info.credentials["device_id"] = e2ee_device_id
                        credentials_updated = True

                if credentials_updated and auth_info.credentials is not None:
                    try:
                        await asyncio.to_thread(
                            save_credentials,
                            auth_info.credentials,
                            credentials_path=auth_info.credentials_path,
                        )
                        logger.info(
                            "Updated credentials.json with discovered information"
                        )
                    except OSError as e:
                        logger.warning(f"Failed to persist discovered device_id: {e}")

                    if e2ee_device_id and user_id:
                        client.restore_login(
                            user_id=user_id,
                            device_id=e2ee_device_id,
                            access_token=access_token,
                        )
                    logger.info(
                        f"Restored login session for {user_id} with device {e2ee_device_id}"
                    )
                else:
                    logger.warning("whoami response did not contain device_id")
            except NIO_COMM_EXCEPTIONS as e:
                logger.warning(f"Failed to discover device_id via whoami: {e}")
                logger.warning("E2EE may not work properly without a device_id")
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
                    logger.debug(
                        "Discovered user_id via whoami: %s", discovered_user_id
                    )
                else:
                    logger.warning("whoami response did not contain user_id")
            except NIO_COMM_EXCEPTIONS as e:
                logger.warning("Failed to discover user_id via whoami: %s", e)

    return e2ee_device_id


async def _maybe_upload_e2ee_keys(client: AsyncClient) -> None:
    """Upload E2EE keys if needed."""
    try:
        if client.should_upload_keys:
            logger.info("Uploading encryption keys...")
            await client.keys_upload()
            logger.info("Encryption keys uploaded successfully")
        else:
            logger.debug("No key upload needed - keys already present")
    except NIO_COMM_EXCEPTIONS:
        logger.exception(
            "Failed to upload E2EE keys. Consider regenerating credentials with: mmrelay auth login"
        )


async def _close_matrix_client_after_failure(
    client: AsyncClient | None, context: str
) -> None:
    global matrix_client
    if not client:
        return
    try:
        await client.close()
    except asyncio.CancelledError:
        raise
    except NIO_COMM_EXCEPTIONS:
        logger.debug(
            "Ignoring error while closing client after %s", context, exc_info=True
        )
    finally:
        if matrix_client is client:
            matrix_client = None


async def _perform_initial_sync(
    client: AsyncClient, matrix_homeserver: str
) -> Any | None:
    """Perform initial sync and handle server quirks."""
    invite_safe_filter: dict[str, Any] = {"room": {"invite": {"limit": 0}}}
    sync_response: Any | None = None

    try:
        sync_response = await asyncio.wait_for(
            client.sync(timeout=MATRIX_EARLY_SYNC_TIMEOUT, full_state=True),
            timeout=MATRIX_SYNC_OPERATION_TIMEOUT,
        )
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
        await _close_matrix_client_after_failure(client, "sync timeout")
        raise MatrixSyncTimeoutError() from None
    except asyncio.CancelledError:
        logger.exception("Initial sync cancelled")
        await _close_matrix_client_after_failure(client, "sync cancellation")
        raise
    except JSONSCHEMA_VALIDATION_ERROR as exc:
        logger.exception("Initial sync response failed schema validation.")
        logger.warning(
            "This usually indicates a non-compliant homeserver or proxy response."
        )
        logger.warning(
            "Retrying initial sync without invites to tolerate invalid invite_state payloads."
        )
        try:
            sync_response = await asyncio.wait_for(
                client.sync(
                    timeout=MATRIX_EARLY_SYNC_TIMEOUT,
                    full_state=False,
                    sync_filter=invite_safe_filter,
                ),
                timeout=MATRIX_SYNC_OPERATION_TIMEOUT,
            )
            cast(Any, client).mmrelay_sync_filter = invite_safe_filter
            cast(Any, client).mmrelay_first_sync_filter = invite_safe_filter
            logger.info(
                "Initial sync completed after invite-safe retry. "
                "Invite handling is disabled for subsequent syncs."
            )
        except JSONSCHEMA_VALIDATION_ERROR:
            logger.exception("Invite-safe sync retry failed")
            logger.warning(
                "Invite-safe sync retry failed schema validation; "
                "attempting to ignore invalid invite_state payloads."
            )

            async def _sync_ignore_invalid_invites() -> Any:
                """
                Perform a Matrix sync using an invite-safe filter while treating malformed or schema-invalid invite_state payloads as empty.
                """
                import nio.responses as nio_responses

                original_descriptor = vars(nio_responses.SyncResponse).get(
                    "_get_invite_state"
                )
                original_callable = getattr(
                    nio_responses.SyncResponse, "_get_invite_state", None
                )

                def _safe_get_invite_state(invite_state_dict: Any) -> list[Any]:
                    if (
                        not isinstance(invite_state_dict, dict)
                        or "events" not in invite_state_dict
                    ):
                        return []
                    try:
                        if callable(original_callable):
                            return cast(
                                list[Any],
                                original_callable(invite_state_dict),
                            )
                    except JSONSCHEMA_VALIDATION_ERROR:
                        logger.warning(
                            "Invalid invite_state payload; ignoring invite_state events."
                        )
                        return []
                    return []

                try:
                    nio_responses.SyncResponse._get_invite_state = staticmethod(  # pyright: ignore[reportAttributeAccessIssue]  # type: ignore[misc]
                        _safe_get_invite_state
                    )
                    return await asyncio.wait_for(
                        client.sync(
                            timeout=MATRIX_EARLY_SYNC_TIMEOUT,
                            full_state=False,
                            sync_filter=invite_safe_filter,
                        ),
                        timeout=MATRIX_SYNC_OPERATION_TIMEOUT,
                    )
                finally:
                    if original_descriptor is not None:
                        nio_responses.SyncResponse._get_invite_state = (
                            original_descriptor
                        )
                    else:
                        # Descriptor didn't exist in class dict; remove it if we added it.
                        # This avoids leaking the patch if it was shadowing a base class.
                        try:
                            del nio_responses.SyncResponse._get_invite_state
                        except (AttributeError, TypeError):
                            pass

            try:
                sync_response = await _sync_ignore_invalid_invites()
                cast(Any, client).mmrelay_sync_filter = invite_safe_filter
                cast(Any, client).mmrelay_first_sync_filter = invite_safe_filter
                logger.info(
                    "Initial sync completed after invite-safe retry "
                    "with invalid invite_state payloads ignored."
                )
            except (ImportError, AttributeError):
                logger.debug("Invite-safe sync retry handler failed", exc_info=True)
            except asyncio.CancelledError:
                logger.exception("Invite-ignoring sync retry cancelled")
                await _close_matrix_client_after_failure(client, "sync cancellation")
                raise
            except (
                *NIO_COMM_EXCEPTIONS,
                JSONSCHEMA_VALIDATION_ERROR,
            ):
                logger.exception("Invite-ignoring sync retry failed")
        except asyncio.TimeoutError:
            logger.exception(
                "Invite-safe sync retry timed out after %s seconds",
                MATRIX_SYNC_OPERATION_TIMEOUT,
            )
        except asyncio.CancelledError:
            logger.exception("Invite-safe sync retry cancelled")
            await _close_matrix_client_after_failure(client, "sync cancellation")
            raise
        except NIO_COMM_EXCEPTIONS:
            logger.exception("Invite-safe sync retry failed")

        if sync_response is None:
            logger.exception("Matrix sync failed")
            await _close_matrix_client_after_failure(client, "sync failure")
            raise MatrixSyncFailedError() from exc
    except NIO_COMM_EXCEPTIONS as exc:
        logger.exception("Matrix sync failed")
        await _close_matrix_client_after_failure(client, "sync failure")
        raise MatrixSyncFailedError() from exc

    return sync_response


async def _post_sync_setup(
    client: AsyncClient,
    sync_response: Any | None,
    config_data: dict[str, Any],
    matrix_rooms: Any,
    matrix_homeserver: str,
    bot_user_id: str,
    e2ee_enabled: bool,
) -> None:
    """Handle post-sync room setup, E2EE status, and display name resolution."""
    global bot_user_name

    if isinstance(sync_response, SyncError):
        error_type = sync_response.__class__.__name__
        error_details = _get_detailed_matrix_error_message(sync_response)
        logger.error(f"Initial sync failed: {error_type}")
        logger.error(f"Error details: {error_details}")

        logger.error(
            "This usually indicates a network connectivity issue or server problem."
        )
        logger.error("Troubleshooting steps:")
        logger.error("1. Check your internet connection")
        logger.error(f"2. Verify the homeserver URL is correct: {matrix_homeserver}")
        logger.error("3. Ensure the Matrix server is online and accessible")
        logger.error("4. Check if your credentials are still valid")

        await _close_matrix_client_after_failure(client, "sync failure")
        raise MatrixSyncFailedDetailsError(error_type, error_details)

    logger.info(f"Initial sync completed. Found {len(client.rooms)} rooms.")

    from mmrelay.e2ee_utils import (
        get_e2ee_status,
        get_room_encryption_warnings,
    )

    e2ee_status = get_e2ee_status(config_data or {}, config_module.config_path)

    async def _resolve_alias(alias: str) -> str | None:
        if not client:
            logger.warning(
                f"Cannot resolve alias {alias}: Matrix client is not available"
            )
            return None

        logger.debug(f"Resolving alias from config: {alias}")
        try:
            response = await client.room_resolve_alias(alias)
            room_id = getattr(response, "room_id", None)
            if room_id:
                logger.debug(f"Resolved alias {alias} to {room_id}")
                return cast(str, room_id)
            error_details = (
                getattr(response, "message", response)
                if response
                else "No response from server"
            )
            logger.warning(f"Could not resolve alias {alias}: {error_details}")
        except NIO_COMM_EXCEPTIONS:
            logger.exception(f"Error resolving alias {alias}")
        except (TypeError, ValueError, AttributeError):
            logger.exception(f"Error resolving alias {alias}")
        except Exception:
            logger.exception(f"Error resolving alias {alias}")
        return None

    await _resolve_aliases_in_mapping(matrix_rooms, _resolve_alias)

    _display_room_channel_mappings(client.rooms, config_data, dict(e2ee_status))

    warnings = get_room_encryption_warnings(client.rooms, dict(e2ee_status))
    for warning in warnings:
        logger.warning(warning)

    encrypted_count = sum(
        1 for room in client.rooms.values() if getattr(room, "encrypted", False)
    )
    logger.debug(
        f"Found {encrypted_count} encrypted rooms out of {len(client.rooms)} total rooms"
    )
    logger.debug(f"E2EE status: {e2ee_status['overall_status']}")

    if e2ee_enabled and encrypted_count == 0 and len(client.rooms) > 0:
        logger.debug("No encrypted rooms detected - all rooms are plaintext")

    if e2ee_enabled:
        logger.debug(
            f"Waiting for {E2EE_KEY_SHARING_DELAY_SECONDS} seconds to allow for key sharing..."
        )
        await asyncio.sleep(E2EE_KEY_SHARING_DELAY_SECONDS)

    try:
        response = await client.get_displayname(bot_user_id)
        displayname = getattr(response, "displayname", None)
        if displayname:
            bot_user_name = displayname
        else:
            bot_user_name = bot_user_id
    except NIO_COMM_EXCEPTIONS as e:
        logger.debug(f"Failed to get bot display name for {bot_user_id}: {e}")
        bot_user_name = bot_user_id

    cast(Any, client).e2ee_enabled = e2ee_enabled


async def connect_matrix(
    passed_config: dict[str, Any] | None = None,
) -> AsyncClient | None:
    """
    Initialize and configure a Matrix AsyncClient using available credentials and configuration.

    Attempts to authenticate (via credentials.json, automatic login, or config), enable end-to-end encryption when configured, perform an initial sync to populate room state, and return a ready-to-use AsyncClient.

    Parameters:
        passed_config (dict[str, Any] | None): Optional configuration override for this connection attempt; when provided it is used instead of the module-level config for this call.

    Returns:
        AsyncClient | None: A configured and initialized Matrix AsyncClient on success, or `None` if connection or credential setup failed.

    Raises:
        ValueError: If the required top-level "matrix_rooms" configuration is missing.
        ConnectionError: If the initial Matrix sync fails or times out.
    """
    global matrix_client, bot_user_name, matrix_homeserver, matrix_rooms, matrix_access_token, bot_user_id, config

    if passed_config is not None:
        config = passed_config
        config_module.relay_config = passed_config

    if config is None:
        logger.error("No configuration available. Cannot connect to Matrix.")
        return None

    if matrix_client:
        return matrix_client

    matrix_section = config.get("matrix") if isinstance(config, dict) else None

    auth_info = await _resolve_and_load_credentials(
        config if isinstance(config, dict) else None,
        matrix_section,
    )
    if auth_info is None:
        return None

    matrix_homeserver = auth_info.homeserver
    matrix_access_token = auth_info.access_token
    bot_user_id = auth_info.user_id
    e2ee_device_id = auth_info.device_id

    if "matrix_rooms" not in config:
        logger.error("Configuration is missing 'matrix_rooms' section")
        logger.error(
            "Please ensure your config.yaml includes matrix_rooms configuration"
        )
        raise MissingMatrixRoomsError()
    matrix_rooms = config["matrix_rooms"]

    ssl_context = _create_ssl_context()
    if ssl_context is None:
        logger.warning(
            "Failed to create certifi/system SSL context; proceeding with AsyncClient defaults"
        )

    e2ee_enabled, e2ee_store_path = await _configure_e2ee(
        config, matrix_section, e2ee_device_id
    )

    if not isinstance(matrix_homeserver, str) or not matrix_homeserver:
        logger.error("Matrix homeserver is missing or invalid.")
        return None
    if not isinstance(matrix_access_token, str) or not matrix_access_token:
        logger.error("Matrix access token is missing or invalid.")
        return None
    if not isinstance(bot_user_id, str) or not bot_user_id:
        logger.warning(
            "Matrix user ID is missing or invalid; will attempt whoami after login."
        )
        bot_user_id = ""

    matrix_client = _initialize_matrix_client(
        homeserver=matrix_homeserver,
        user_id=bot_user_id,
        device_id=e2ee_device_id,
        e2ee_enabled=e2ee_enabled,
        e2ee_store_path=e2ee_store_path,
        ssl_context=ssl_context,
    )

    await _perform_matrix_login(matrix_client, auth_info)
    if not bot_user_id:
        resolved_user_id = matrix_client.user_id
        if isinstance(resolved_user_id, str) and resolved_user_id:
            bot_user_id = resolved_user_id
            globals()["bot_user_id"] = resolved_user_id
        else:
            logger.error("Matrix user ID is missing or invalid.")
            return None

    if e2ee_enabled:
        await _maybe_upload_e2ee_keys(matrix_client)

    logger.debug("Performing initial sync to initialize rooms...")
    sync_response = await _perform_initial_sync(matrix_client, matrix_homeserver)
    await _post_sync_setup(
        matrix_client,
        sync_response,
        config,
        matrix_rooms,
        matrix_homeserver,
        bot_user_id,
        e2ee_enabled,
    )
    return matrix_client


async def login_matrix_bot(
    homeserver: str | None = None,
    username: str | None = None,
    password: str | None = None,
    logout_others: bool | None = None,
) -> bool:
    """
    Authenticate the bot with a Matrix homeserver and persist the resulting session credentials.

    If any of homeserver, username, or password are None, the function will prompt interactively for them. On success the acquired credentials (homeserver, user_id, access_token, device_id) are saved to the configured credentials path.

    Parameters:
        logout_others (bool | None): If True, attempt to log out other sessions; if False, do not; if None and running interactively, the user will be prompted (treated as False in non-interactive calls).

    Returns:
        bool: `True` if login succeeded and credentials were saved, `False` otherwise.
    """
    import json  # Local import to avoid Pyright false positive in nested scope

    client = None  # Initialize to avoid unbound variable errors
    try:
        # Optionally enable verbose nio/aiohttp debug logging
        if os.getenv("MMRELAY_DEBUG_NIO") == "1":
            logging.getLogger("nio").setLevel(logging.DEBUG)
            logging.getLogger("nio.client").setLevel(logging.DEBUG)
            logging.getLogger("nio.http_client").setLevel(logging.DEBUG)
            logging.getLogger("nio.responses").setLevel(logging.DEBUG)
            logging.getLogger("aiohttp").setLevel(logging.DEBUG)

        prompted_for_credentials = False

        # Get homeserver URL
        if not homeserver:
            homeserver = input(
                "Enter Matrix homeserver URL (e.g., https://matrix.org): "
            )
            prompted_for_credentials = True

        # Ensure homeserver URL has the correct format
        if not (homeserver.startswith("https://") or homeserver.startswith("http://")):
            homeserver = "https://" + homeserver

        # Store the original homeserver domain for username normalization
        # This ensures the username uses the domain the user expects, not the discovered one
        parsed = urlparse(homeserver)
        original_domain = parsed.hostname or urlparse(f"//{homeserver}").hostname
        if not original_domain:
            host = homeserver.split("://")[-1].split("/", 1)[0]
            original_domain = re.sub(r":\d+$", "", host)

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
            prompted_for_credentials = True

        # Format username correctly using the original homeserver domain
        # This ensures the username uses the domain the user expects, not the discovered one
        if original_domain:
            username = _normalize_bot_user_id(original_domain, username)
        else:
            username = _normalize_bot_user_id(homeserver, username)

        if not username:
            logger.error("Username normalization failed")
            return False

        logger.info(f"Using username: {username}")

        # Validate username format
        if not username.startswith("@"):
            logger.warning(f"Username doesn't start with @: {username}")
        if username.count(":") != 1:
            logger.warning(
                f"Username has unexpected colon count: {username.count(':')}"
            )

        # Check for special characters in username that might cause issues
        username_special_chars = set(username or "") - set(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@:.-_"
        )
        if username_special_chars:
            logger.warning(
                f"Username contains unusual characters: {username_special_chars}"
            )

        # Get password
        if not password:
            password = getpass.getpass("Enter Matrix password: ")
            prompted_for_credentials = True

        # Simple password validation without logging sensitive information
        if password:
            logger.debug("Password provided for login")
        else:
            logger.warning("No password provided")

        # Ask about logging out other sessions when running interactively
        if logout_others is None and prompted_for_credentials:
            logout_others_input = input(
                "Log out other sessions? (Y/n) [Default: Yes]: "
            ).lower()
            logout_others = (
                not logout_others_input.startswith("n") if logout_others_input else True
            )
        if logout_others is None:
            logout_others = False

        from mmrelay.config import is_e2ee_enabled, load_config

        config_for_paths: dict[str, Any] | None = None
        e2ee_enabled = False
        try:
            config_for_paths = await asyncio.to_thread(load_config)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
            logger.debug("Could not load config for credentials path: %s", e)

        # Check for existing credentials to reuse device_id
        existing_device_id = None
        credentials_path = None
        try:
            explicit_path = get_explicit_credentials_path(config_for_paths)
            if explicit_path:
                credentials_path = os.path.expanduser(explicit_path)
            else:
                from mmrelay.paths import resolve_all_paths

                credentials_path = resolve_all_paths()["credentials_path"]

            if credentials_path and await asyncio.to_thread(
                os.path.exists, credentials_path
            ):

                def _load_existing_creds(path: str) -> dict[str, Any]:
                    with open(path, "r", encoding="utf-8") as f:
                        return cast(dict[str, Any], json.load(f))

                existing_creds = await asyncio.to_thread(
                    _load_existing_creds, credentials_path
                )
                if (
                    "device_id" in existing_creds
                    and existing_creds["user_id"] == username
                ):
                    existing_device_id = existing_creds["device_id"]
                    logger.info(f"Reusing existing device_id: {existing_device_id}")
        except (OSError, JSONDecodeError, KeyError, TypeError) as e:
            logger.debug(f"Could not load existing credentials: {e}")

        # Check if E2EE is enabled in configuration
        if config_for_paths is not None:
            try:
                e2ee_enabled = is_e2ee_enabled(config_for_paths)
            except (KeyError, TypeError, ValueError) as e:
                logger.debug(f"Could not load config for E2EE check: {e}")
                e2ee_enabled = False
        else:
            logger.debug("Could not load config for E2EE check: config load failed")
            e2ee_enabled = False

        logger.debug(f"E2EE enabled in config: {e2ee_enabled}")

        # Get the E2EE store path only if E2EE is enabled
        store_path = None
        if e2ee_enabled:
            store_path = str(await asyncio.to_thread(get_e2ee_store_dir))
            await asyncio.to_thread(os.makedirs, store_path, exist_ok=True)
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
        # Extract localpart from full MXID for AsyncClient compatibility
        localpart = _extract_localpart_from_mxid(username) or ""
        logger.debug("Creating AsyncClient with:")
        logger.debug(f"  homeserver: {homeserver}")
        logger.debug(f"  username (MXID): {username}")
        logger.debug(f"  localpart: {localpart}")
        logger.debug(f"  device_id: {existing_device_id}")
        logger.debug(f"  store_path: {store_path}")
        logger.debug(f"  e2ee_enabled: {e2ee_enabled}")

        client = AsyncClient(
            homeserver,
            localpart,
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
            logger.debug(f"  client.user: {client.user}")
            logger.debug(f"  client.homeserver: {client.homeserver}")

            # Test the API call that matrix-nio will make
            try:
                from nio.api import Api

                method, path, data = Api.login(
                    user=localpart,
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

            # Check specific attributes that should be present
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
                        # Only log presence of access token, never the value
                        logger.debug(
                            f"Response.{attr}: {'present' if value else 'not present'} (type: {type(value).__name__})"
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

            # save_credentials() now uses unified HOME location
            explicit_path = get_explicit_credentials_path(config_for_paths)
            if explicit_path:
                credentials_path = os.path.expanduser(explicit_path)
            else:
                from mmrelay.paths import resolve_all_paths

                credentials_path = resolve_all_paths()["credentials_path"]
            await asyncio.to_thread(
                save_credentials, credentials, credentials_path=credentials_path
            )
            logger.info("Credentials saved to %s", credentials_path)

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

    except (
        NioLocalProtocolError,
        NioRemoteProtocolError,
        NioLocalTransportError,
        NioRemoteTransportError,
        asyncio.TimeoutError,
        ssl.SSLError,
        OSError,
    ):
        logger.exception("Error during login")
        try:
            if client:
                await client.close()
        except (OSError, RuntimeError, ConnectionError) as cleanup_e:
            # Ignore errors during client cleanup - connection may already be closed
            logger.debug(f"Ignoring error during client cleanup: {cleanup_e}")
        return False


async def join_matrix_room(matrix_client: AsyncClient, room_id_or_alias: str) -> None:
    """
    Join the bot to a Matrix room identified by a room ID or room alias.

    If given a room alias (starts with '#'), resolve it to a canonical room ID, update the in-memory
    matrix_rooms mapping with the resolved ID when available, and attempt to join the resolved room.
    No action is taken if the client is already joined. Errors during alias resolution or join are
    logged; the function does not raise exceptions.
    Parameters:
        room_id_or_alias (str): A Matrix room identifier, either a canonical room ID (e.g. "!abc:server")
            or a room alias (e.g. "#room:server"). When an alias is provided it will be resolved and the
            resulting room ID will be used for joining and recorded in the module-level matrix_rooms mapping.
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
                # Keep the bridge alive for unexpected mapping update errors
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
    except Exception:
        # Handle truly unexpected errors during room joining
        logger.exception(f"Unexpected error joining room '{room_id}'")


def _get_e2ee_error_message() -> str:
    """
    Provide a short, user-facing explanation for why End-to-End Encryption (E2EE) is not enabled.

    Maps the unified E2EE status to a concise, human-readable message suitable for logging or UI display.

    Returns:
        str: A short explanation of the current E2EE problem, or an empty string if no specific issue is detected.
    """
    from mmrelay.e2ee_utils import get_e2ee_error_message, get_e2ee_status

    # Get unified E2EE status
    e2ee_status = get_e2ee_status(config or {}, config_module.config_path)

    # Return unified error message
    return get_e2ee_error_message(dict(e2ee_status))


async def matrix_relay(
    room_id: str,
    message: str,
    longname: str,
    shortname: str,
    meshnet_name: str,
    portnum: int,
    meshtastic_id: int | None = None,
    meshtastic_replyId: int | None = None,
    meshtastic_text: str | None = None,
    emote: bool = False,
    emoji: bool = False,
    reply_to_event_id: str | None = None,
) -> None:
    """
    Relay a Meshtastic-originated message into a Matrix room and optionally persist a Meshtastic↔Matrix mapping.

    Formats the provided Meshtastic text for Matrix (plain and HTML/quoted forms as appropriate), sends it to the specified Matrix room with the chosen msgtype, and when message storage is enabled, records a mapping from the Meshtastic message to the created Matrix event to support cross-network replies and reactions. The function respects room encryption/E2EE constraints and logs send/storage failures without raising.

    Parameters:
        room_id (str): Matrix room ID or alias to send the message into.
        message (str): Text content from Meshtastic to relay.
        longname (str): Sender long display name from Meshtastic for attribution/metadata.
        shortname (str): Sender short display name from Meshtastic for metadata.
        meshnet_name (str): Meshnet name for the incoming message; if empty, the configured local meshnet is used.
        portnum (int): Meshtastic application/port number for the message.
        meshtastic_id (int | None): Optional Meshtastic message identifier; used to persist a mapping when storage is enabled.
        meshtastic_replyId (int | None): Optional Meshtastic message ID that this message replies to; included as metadata.
        meshtastic_text (str | None): Optional Meshtastic-origin text to store with the mapping; if omitted the relayed `message` is used.
        emote (bool): If True, send as `m.emote` instead of `m.text`.
        emoji (bool): If True, include an emoji flag in the outbound metadata for downstream handling.
        reply_to_event_id (str | None): Optional Matrix event_id to reply to; if provided and the original mapping is resolvable, the outgoing event includes an `m.in_reply_to` relation and a quoted formatted body.
    """
    global config

    # Log the current state of the config
    logger.debug(f"matrix_relay: config is {'available' if config else 'None'}")

    matrix_client = await connect_matrix()

    if matrix_client is None:
        logger.error("Matrix client is None. Cannot send message.")
        return

    # Check if config is available
    if config is None:
        logger.error("No configuration available. Cannot relay message to Matrix.")
        return

    # Get interaction settings
    interactions = get_interaction_settings(config)
    storage_enabled = message_storage_enabled(interactions)
    msgs_to_keep = _get_msgs_to_keep_config(config)

    try:
        if room_id not in matrix_client.rooms:
            await join_matrix_room(matrix_client, room_id)

        relay_meshnet_name = meshnet_name or config["meshtastic"]["meshnet_name"]

        # Check if message contains HTML tags or markdown formatting, or a prefix that could be parsed as a link definition
        has_html = bool(re.search(r"</?[a-zA-Z][^>]*>", message))
        safe_message, has_prefix = _escape_leading_prefix_for_markdown(message)
        has_markdown = bool(re.search(r"[*_`~]", message)) or has_prefix

        # Process markdown/HTML if available; otherwise, safe fallback
        if has_markdown or has_html:
            try:
                # bleach lacks type stubs in our env; keep import-untyped for strict mypy.
                import bleach  # type: ignore[import-untyped]  # lazy import

                # markdown has stubs in our env; avoid import-untyped to keep mypy clean.
                # If that changes, prefer installing types-Markdown over adding ignores.
                import markdown  # lazy import

                raw_html = markdown.markdown(safe_message)
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
                # Without markdown/bleach, preserve the original text to avoid showing escape characters
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
            "meshtastic_meshnet": relay_meshnet_name,
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
            logger.exception("Timeout sending message to Matrix room %s", room_id)
            return
        except NIO_COMM_EXCEPTIONS:
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
                event_id = getattr(response, "event_id", None)
                if isinstance(event_id, str):
                    await async_store_message_map(
                        meshtastic_id,
                        event_id,
                        room_id,
                        meshtastic_text if meshtastic_text else message,
                        meshtastic_meshnet=relay_meshnet_name,
                    )
                    logger.debug(
                        f"Stored message map for meshtastic_id: {meshtastic_id}"
                    )

                # If msgs_to_keep > 0, prune old messages after inserting a new one
                if msgs_to_keep > 0:
                    await async_prune_message_map(msgs_to_keep)
            except Exception as e:
                logger.error(f"Error storing message map: {e}")

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception:
        # Keep the bridge alive for unexpected Meshtastic send errors
        logger.exception(f"Error sending radio message to matrix room {room_id}")


def truncate_message(text: str, max_bytes: int = DEFAULT_MESSAGE_TRUNCATE_BYTES) -> str:
    """
    Truncate text so its UTF-8 encoding occupies no more than the given byte limit.

    If `max_bytes` cuts a multi-byte UTF-8 character, the partial character is discarded so the result is valid UTF-8.

    Parameters:
        text (str): Input text to truncate.
        max_bytes (int): Maximum allowed size in bytes for the UTF-8 encoded result.

    Returns:
        str: A string whose UTF-8 encoding is at most `max_bytes` bytes.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Strip quoted lines (lines starting with '>') from a block of text.

    Parameters:
        text (str): Input text possibly containing quoted lines.

    Returns:
        str: The remaining non-quoted lines joined with single spaces and trimmed of leading/trailing whitespace.
    """
    lines = text.splitlines()
    filtered = [line.strip() for line in lines if not line.strip().startswith(">")]
    return " ".join(line for line in filtered if line).strip()


async def get_user_display_name(
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
) -> str:
    """
    Get the display name for an event sender, preferring a room-specific name.

    If the room defines a per-room display name for the sender, that name is returned.
    Otherwise the global display name from the homeserver is returned when available.
    If no display name can be determined, the sender's Matrix ID (MXID) is returned.

    Returns:
        str: The sender's display name or their MXID.
    """
    room_display_name = room.user_name(event.sender)
    if room_display_name:
        return cast(str, room_display_name)

    # Some environments may not expose the nio response classes; guard isinstance to avoid TypeError.
    response_types = tuple(
        t for t in (ProfileGetDisplayNameResponse,) if isinstance(t, type)
    )
    error_types = tuple(t for t in (ProfileGetDisplayNameError,) if isinstance(t, type))

    if matrix_client:
        try:
            display_name_response = await matrix_client.get_displayname(event.sender)
            if response_types and isinstance(display_name_response, response_types):
                return cast(
                    str,
                    getattr(display_name_response, "displayname", None) or event.sender,
                )
            if error_types and isinstance(display_name_response, error_types):
                logger.debug(
                    "Failed to get display name for %s: %s",
                    event.sender,
                    getattr(display_name_response, "message", display_name_response),
                )
            else:
                logger.debug(
                    "Unexpected display name response type %s for %s",
                    type(display_name_response),
                    event.sender,
                )
            # Fallback: if the response exposes a displayname attribute, use it.
            display_attr = getattr(display_name_response, "displayname", None)
            if display_attr:
                return cast(str, display_attr)
        except NIO_COMM_EXCEPTIONS as e:
            logger.debug(f"Failed to get display name for {event.sender}: {e}")
            return cast(str, event.sender)
    return cast(str, event.sender)


def format_reply_message(
    config: dict[str, Any],
    full_display_name: str,
    text: str,
    *,
    longname: str | None = None,
    shortname: str | None = None,
    meshnet_name: str | None = None,
    local_meshnet_name: str | None = None,
    mesh_text_override: str | None = None,
) -> str:
    """
    Format a Meshtastic-style reply, applying an appropriate sender prefix and truncating the result to the configured maximum length.

    Parameters:
        config (dict[str, Any]): Runtime configuration used to build prefix formats.
        full_display_name (str): Sender's full display name used when constructing local prefixes.
        text (str): Original reply text; quoted lines (leading '>') will be removed.
        longname (str | None): Optional long form of the sender name for remote-meshnet prefixes.
        shortname (str | None): Optional short form of the sender name for remote-meshnet prefixes.
        meshnet_name (str | None): Remote meshnet name; when provided and different from local_meshnet_name, remote-prefix rules are applied.
        local_meshnet_name (str | None): Local meshnet name used to determine whether a reply is remote.
        mesh_text_override (str | None): Optional raw Meshtastic payload preferred over `text` when generating the reply body.

    Returns:
        str: The formatted reply message with quoted lines removed, the appropriate prefix applied (remote or local), and truncated to the configured maximum length.
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
    reply_message: str,
    full_display_name: str,
    room_config: dict[str, Any],
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    text: str,
    storage_enabled: bool,
    local_meshnet_name: str,
    reply_id: int | None = None,
    relay_config: dict[str, Any] | None = None,
) -> bool:
    """
    Queue a Meshtastic delivery for a Matrix reply, optionally sending it as a structured reply that targets a specific Meshtastic message.

    Creates and attaches message-mapping metadata when storage is enabled, respects the channel from room_config, and honors an optional relay_config override. Enqueues either a structured reply (when reply_id is provided) or a regular broadcast and logs outcomes; the function handles errors internally and does not raise.

    Parameters:
        reply_message (str): Meshtastic-ready text payload to send.
        full_display_name (str): Sender display name used in queue descriptions and logs.
        room_config (dict): Room-specific configuration; must include "meshtastic_channel" (integer channel index).
        room (MatrixRoom): Matrix room object; room.room_id is used in mapping metadata.
        event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): Matrix event object; event.event_id is used in mapping metadata.
        text (str): Original Matrix message text used when building mapping metadata.
        storage_enabled (bool): If True, create and attach a message-mapping record for correlation of future replies/reactions.
        local_meshnet_name (str): Local meshnet name to include in mapping metadata when present.
        reply_id (int | None): If provided, send as a structured Meshtastic reply targeting this Meshtastic message ID; if None, send as a regular broadcast.
        relay_config (dict[str, Any] | None): Optional config override to control Meshtastic broadcast and message-map settings.

    Returns:
        bool: `True` if the message was successfully queued for delivery to Meshtastic, `False` otherwise.
    """
    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await _get_meshtastic_interface_and_channel(room_config, "relay reply")
    from mmrelay.meshtastic_utils import logger as meshtastic_logger

    if not meshtastic_interface or meshtastic_channel is None:
        return False

    effective_config = relay_config if relay_config is not None else config
    if effective_config is None:
        effective_config = {}
    broadcast_enabled = get_meshtastic_config_value(
        effective_config,
        "broadcast_enabled",
        DEFAULT_BROADCAST_ENABLED,
        required=False,
    )
    logger.debug(f"broadcast_enabled = {broadcast_enabled}")

    if not broadcast_enabled:
        return False

    try:
        # Create mapping info once if storage is enabled
        mapping_info = None
        if storage_enabled:
            # Get message map configuration
            msgs_to_keep = _get_msgs_to_keep_config(effective_config)

            mapping_info = _create_mapping_info(
                event.event_id, room.room_id, text, local_meshnet_name, msgs_to_keep
            )

        if reply_id is not None:
            # Send as a structured reply using our custom function
            # Queue structured reply message for delivery to Meshtastic.
            success = queue_message(
                send_text_reply,
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
                return True
            else:
                meshtastic_logger.error(
                    "Failed to relay structured reply to Meshtastic"
                )
                return False
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
                return True
            else:
                meshtastic_logger.error("Failed to relay reply message to Meshtastic")
                return False

        # Message mapping is now handled automatically by the queue system

    except Exception:  # noqa: BLE001 - error boundary for Meshtastic send path
        # Keep the bridge alive for unexpected Meshtastic send errors
        meshtastic_logger.exception("Error sending Matrix reply to Meshtastic")
        return False


async def handle_matrix_reply(
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
    reply_to_event_id: str,
    text: str,
    room_config: dict[str, Any],
    storage_enabled: bool,
    local_meshnet_name: str,
    config: dict[str, Any],
    *,
    mesh_text_override: str | None = None,
    longname: str | None = None,
    shortname: str | None = None,
    meshnet_name: str | None = None,
) -> bool:
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

    # Extract the original meshtastic_id to use as reply_id.
    # orig = (meshtastic_id, matrix_room_id, meshtastic_text, meshtastic_meshnet)
    # message_map may return legacy string IDs; normalize when numeric, otherwise
    # fall back to a broadcast reply to preserve compatibility.
    original_meshtastic_id_raw = orig[0]
    if isinstance(original_meshtastic_id_raw, int):
        original_meshtastic_id = original_meshtastic_id_raw
    elif isinstance(original_meshtastic_id_raw, str):
        if original_meshtastic_id_raw.isdigit():
            original_meshtastic_id = int(original_meshtastic_id_raw)
        else:
            logger.warning(
                "Message map meshtastic_id %r is not numeric; sending broadcast reply",
                original_meshtastic_id_raw,
            )
            original_meshtastic_id = None
    else:
        logger.warning(
            "Message map meshtastic_id has unexpected type %s; sending broadcast reply",
            type(original_meshtastic_id_raw).__name__,
        )
        original_meshtastic_id = None

    # Get user display name
    full_display_name = await get_user_display_name(room, event)

    # Prefer the meshnet stored with the original mapping when available; the
    # Matrix event often lacks this field for replies.
    orig_meshnet_name = orig[3]
    reply_meshnet_name = meshnet_name or orig_meshnet_name

    # Format the reply message
    reply_message = format_reply_message(
        config,
        full_display_name,
        text,
        longname=longname,
        shortname=shortname,
        meshnet_name=reply_meshnet_name,
        local_meshnet_name=local_meshnet_name,
        mesh_text_override=mesh_text_override,
    )

    if original_meshtastic_id is not None:
        logger.info(
            f"Relaying Matrix reply from {full_display_name} to Meshtastic as reply to message {original_meshtastic_id}"
        )
    else:
        logger.info(
            f"Relaying Matrix reply from {full_display_name} to Meshtastic as broadcast reply"
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
        relay_config=config,
    )

    return True  # Reply was handled, stop further processing


async def on_decryption_failure(room: MatrixRoom, event: MegolmEvent) -> None:
    """
    Handle a MegolmEvent that could not be decrypted by requesting missing session keys.

    If the module-level Matrix client is available, this sets the event's room_id, constructs a to-device key request for the missing Megolm session, and sends it to the device that holds the keys. Logs outcomes and returns without action if no matrix client or device id is available.

    Parameters:
        room (MatrixRoom): The room where the decryption failure occurred.
        event (MegolmEvent): The encrypted event that failed to decrypt; its `room_id` may be updated as part of the request side effect.
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
    except NIO_COMM_EXCEPTIONS:
        logger.exception(f"Failed to request keys for event {event.event_id}")


# Callback for new messages in Matrix room
async def on_room_message(
    room: MatrixRoom,
    event: RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote,
) -> None:
    """
    Handle an incoming Matrix room event and bridge eligible events to Meshtastic.

    Processes RoomMessageText, RoomMessageNotice, RoomMessageEmote, and ReactionEvent events for configured rooms. Filters out events older than the bot start time and messages sent by the bot. Respects per-room and global interaction settings (reactions and replies), delegates command handling to plugins (preventing relay when handled), and forwards eligible reactions, replies, detection-sensor packets, remote-meshnet messages, and ordinary Matrix messages to Meshtastic. When configured, creates and attaches message mapping metadata for reply/reaction correlation.

    Parameters:
        room (MatrixRoom): The Matrix room where the event was received.
        event (RoomMessageText | RoomMessageNotice | ReactionEvent | RoomMessageEmote): The received room event.
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
    from mmrelay.meshtastic_utils import logger as meshtastic_logger
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
        emote_relates_to = content.get("m.relates_to") or {}

        # Only treat as a reaction if it is explicitly an annotation or has a legacy meshtastic_replyId.
        # Replies also carry m.relates_to, so checking rel_type keeps replies from being misclassified as reactions.
        is_reaction = bool(
            meshtastic_replyId or emote_relates_to.get("rel_type") == "m.annotation"
        )

        if is_reaction:
            # We need to manually extract the reaction emoji from the body
            reaction_match = re.search(r"reacted (.+?) to", reaction_body)
            reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"
            if emote_relates_to and "event_id" in emote_relates_to:
                original_matrix_event_id = emote_relates_to["event_id"]

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
            (
                meshtastic_interface,
                meshtastic_channel,
            ) = await _get_meshtastic_interface_and_channel(
                room_config, "relay reaction"
            )
            # _get_meshtastic_interface_and_channel validates channel and returns None on failure.
            if not meshtastic_interface:
                return

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
            (
                meshtastic_interface,
                meshtastic_channel,
            ) = await _get_meshtastic_interface_and_channel(
                room_config, "relay reaction"
            )
            # _get_meshtastic_interface_and_channel validates channel and returns None on failure.
            if not meshtastic_interface:
                return

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
    # Normalize legacy string values to support tests/older senders
    if isinstance(portnum, str):
        if portnum.isdigit():
            try:
                portnum = int(portnum)
            except ValueError:
                pass
        elif portnum == DETECTION_SENSOR_APP:
            portnum = PORTNUM_DETECTION_SENSOR_APP

    # Plugin functionality
    from mmrelay.plugin_loader import load_plugins

    plugins = load_plugins()

    found_matching_plugin = False
    for plugin in plugins:
        if not found_matching_plugin:
            try:
                # Support both async and sync plugins for backward compatibility
                handler_result = plugin.handle_room_message(room, event, text)
                if inspect.isawaitable(handler_result):
                    found_matching_plugin = await handler_result
                else:
                    found_matching_plugin = bool(handler_result)

                if found_matching_plugin:
                    logger.info(
                        f"Processed command with plugin: {plugin.plugin_name} from {event.sender}"
                    )
            except Exception:
                # Keep the bridge alive for unexpected plugin errors and capture traceback for debugging.
                # Both error and exception are logged to satisfy error-boundary expectations and provide tracebacks.
                logger.error(
                    "Error processing message with plugin %s", plugin.plugin_name
                )
                logger.exception(
                    "Error processing message with plugin %s", plugin.plugin_name
                )

    # If a plugin handled the message, don't relay it to Meshtastic
    if found_matching_plugin:
        logger.debug("Message handled by plugin, not sending to mesh")
        return

    def _matches_command(plugin_obj: Any) -> bool:
        """
        Return whether a plugin should handle the currently processed Matrix event.

        Checks the plugin for a `matches(event)` predicate first; if present and truthy, the plugin matches.
        If `matches` is absent but `get_matrix_commands()` is present, the function invokes `bot_command`
        for each command returned, respecting an optional `get_require_bot_mention()` flag on the plugin.

        Parameters:
            plugin_obj (Any): Plugin instance that may implement `matches(event)`, `get_matrix_commands()`,
                and optionally `get_require_bot_mention()`.

        Returns:
            bool: `True` if the plugin should handle the current event, `False` otherwise.

        Notes:
            If the plugin raises an exception while evaluating predicates or commands, the exception is logged
            and the plugin is treated as not matching.
        """
        if hasattr(plugin_obj, "matches"):
            try:
                return bool(plugin_obj.matches(event))
            except Exception:
                # Broad catch keeps a faulty plugin from crashing the bridge; we log details for diagnostics.
                logger.exception(
                    "Error checking plugin match for %s",
                    getattr(plugin_obj, "plugin_name", plugin_obj),
                )
                return False
        if hasattr(plugin_obj, "get_matrix_commands"):
            try:
                require_mention_attr = getattr(
                    plugin_obj, "get_require_bot_mention", lambda: False
                )
                require_mention = bool(
                    require_mention_attr()
                    if callable(require_mention_attr)
                    else require_mention_attr
                )
                return any(
                    bot_command(cmd, event, require_mention=require_mention)
                    for cmd in plugin_obj.get_matrix_commands()
                )
            except Exception:
                # Intentional: isolate plugin errors while surfacing them via logs.
                logger.exception(
                    "Error checking plugin commands for %s",
                    getattr(plugin_obj, "plugin_name", plugin_obj),
                )
                return False

        return False

    if any(_matches_command(plugin) for plugin in plugins):
        logger.debug("Message is a command, not sending to mesh")
        return

    # Check if this is a detection sensor packet (before connecting to Meshtastic)
    is_detection_packet = portnum == PORTNUM_DETECTION_SENSOR_APP

    if is_detection_packet:
        await _handle_detection_sensor_packet(
            config, room_config, full_display_name, text
        )
        return

    # Connect to Meshtastic and validate channel for regular messages
    (
        meshtastic_interface,
        meshtastic_channel,
    ) = await _get_meshtastic_interface_and_channel(room_config, "relay message")

    if not meshtastic_interface:
        # The helper function already logs the specific error
        return

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
                msgs_to_keep = _get_msgs_to_keep_config(config)

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

    def __init__(
        self,
        upload_response: UploadError | UploadResponse | SimpleNamespace | None,
    ):
        """
        Create an ImageUploadError and attach the underlying upload response or error.

        Parameters:
            upload_response: The underlying upload error or response object (or None). If present, its `message`
                attribute will be included in the exception text and the object will be stored on the instance as
                `upload_response`.

        """
        message = getattr(upload_response, "message", "Unknown error")
        super().__init__(f"Image upload failed: {message}")
        self.upload_response = upload_response


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> UploadResponse | UploadError | SimpleNamespace:
    """
    Upload an image to the Matrix content repository and return the upload result.

    Parameters:
        client (AsyncClient): Matrix nio client used to perform the upload.
        image (PIL.Image.Image): Pillow image to upload.
        filename (str): Filename used to infer the image MIME type and as the uploaded filename.

    Returns:
        UploadResponse on success (contains `content_uri`).
        On failure, a SimpleNamespace-like object with `message` and optional `status_code` attributes describing the error.
    """
    # Determine image format from filename
    image_format = os.path.splitext(filename)[1][1:].upper() or "PNG"
    if image_format == "JPG":
        image_format = "JPEG"

    buffer = io.BytesIO()
    try:
        image.save(buffer, format=image_format)
        content_type = _MIME_TYPE_MAP.get(image_format, "image/png")
    except (ValueError, KeyError, OSError):
        # Fallback to PNG if format is unsupported
        logger.warning(
            f"Unsupported image format '{image_format}' for {filename}. Falling back to PNG."
        )
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
        # Convert nio communication exceptions to an UploadError-like instance
        logger.exception("Image upload failed due to a network error")
        # Use a simple object with the attributes our callers expect
        upload_error = SimpleNamespace(message=str(e), status_code=None)
        return upload_error
    else:
        return response


async def send_room_image(
    client: AsyncClient,
    room_id: str,
    upload_response: UploadResponse | UploadError | SimpleNamespace | None,
    filename: str = "image.png",
) -> None:
    """
    Send an uploaded image to a Matrix room.

    If `upload_response` exposes a `content_uri`, sends an `m.image` message referencing that URI and using `filename` as the body. If `content_uri` is missing, logs an error and raises ImageUploadError.

    Parameters:
        client (AsyncClient): Matrix client used to send the message.
        room_id (str): Target Matrix room ID.
        upload_response (UploadResponse | UploadError | SimpleNamespace | None): Result from an upload operation; must provide a `content_uri` attribute on success.
        filename (str): Filename to include as the message body (defaults to "image.png").

    Raises:
        ImageUploadError: If `upload_response` does not contain a `content_uri`.
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
) -> None:
    """
    Upload a Pillow Image to the Matrix content repository and send it to a room.

    Uploads the provided PIL Image, stores it in the client's content repository, and sends it to the specified room as an `m.image` message using the given filename.

    Raises:
        ImageUploadError: If the upload or send operation fails.
    """
    response = await upload_image(client=client, image=image, filename=filename)
    await send_room_image(client, room_id, upload_response=response, filename=filename)


async def on_room_member(room: MatrixRoom, event: RoomMemberEvent) -> None:
    """
    Handle room member events to observe room-specific display name changes.

    This callback is registered so the Matrix client processes member state updates; no explicit action is required here because room-specific display names are available via the room state immediately after this event.
    """
    # The callback is registered to ensure matrix-nio processes the event,
    # but no explicit action is needed since room.user_name() automatically
    # handles room-specific display names after the room state is updated.
    pass


async def on_invite(room: MatrixRoom, event: InviteMemberEvent) -> None:
    """
    Handle an invite targeted at the bot and join the room when it is configured in matrix_rooms.

    Attempts to join the invited room via the global matrix_client when all of the following are true: the event's state_key matches the bot's user id, the membership is "invite", and the room is present in the matrix_rooms configuration. Logs outcomes and failures; performs no return value.

    Parameters:
        room (MatrixRoom): The Matrix room associated with the invite.
        event (InviteMemberEvent): The invite event containing membership and state_key information.
    """
    global bot_user_id, matrix_rooms, matrix_client

    if not bot_user_id:
        logger.warning("bot_user_id is not set, cannot process invites.")
        return

    # Only process invites directed at the bot
    if event.state_key != bot_user_id:
        logger.debug(
            f"Ignoring invite for {event.state_key} (not for bot {bot_user_id})"
        )
        return

    # Only process "invite" membership events
    if event.membership != "invite":
        logger.debug(f"Ignoring non-invite membership event: {event.membership}")
        return

    room_id = room.room_id

    # Check if room is in matrix_rooms configuration
    if not _is_room_mapped(matrix_rooms, room_id):
        logger.info(
            f"Room '{room_id}' is not in matrix_rooms configuration, ignoring invite"
        )
        return
    logger.info(f"Room '{room_id}' is in matrix_rooms configuration, accepting invite")

    if not matrix_client:
        logger.error("matrix_client is None, cannot join room")
        return

    # Join the room if mapped and we're not already in it
    try:
        if room_id not in matrix_client.rooms:
            logger.info(f"Joining mapped room '{room_id}'...")
            response = await matrix_client.join(room_id)
            joined_room_id = getattr(response, "room_id", None) if response else None
            if joined_room_id:
                logger.info(f"Successfully joined room '{joined_room_id}'")
            else:
                error_details = _get_detailed_matrix_error_message(response)
                logger.error(f"Failed to join room '{room_id}': {error_details}")
        else:
            logger.debug(f"Bot is already in room '{room_id}', no action needed")
    except NIO_COMM_EXCEPTIONS:
        logger.exception(f"Error joining room '{room_id}'")
    except Exception:
        logger.exception(f"Unexpected error joining room '{room_id}'")
