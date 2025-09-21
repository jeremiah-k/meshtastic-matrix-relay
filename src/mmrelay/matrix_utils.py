import asyncio
import getpass
import html
import io
import json
import logging
import os
import re
import ssl
import sys
import time
from typing import Any, Dict, Union
from urllib.parse import urlparse

import meshtastic.protobuf.portnums_pb2
from nio import (
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
    UploadResponse,
)
from nio.events.room_events import RoomMemberEvent
from PIL import Image

# Import nio exception types with error handling for test environments
try:
    from nio.exceptions import LocalProtocolError as NioLocalProtocolError
    from nio.exceptions import LocalTransportError as NioLocalTransportError
    from nio.exceptions import RemoteProtocolError as NioRemoteProtocolError
    from nio.exceptions import RemoteTransportError as NioRemoteTransportError
    from nio.responses import ErrorResponse as NioErrorResponse
    from nio.responses import LoginError as NioLoginError
    from nio.responses import LogoutError as NioLogoutError
except ImportError:
    # Fallback for test environments where nio imports might fail
    NioLoginError = Exception
    NioLogoutError = Exception
    NioErrorResponse = Exception
    NioLocalProtocolError = Exception
    NioRemoteProtocolError = Exception
    NioLocalTransportError = Exception
    NioRemoteTransportError = Exception

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
    get_message_map_by_matrix_event_id,
    prune_message_map,
    store_message_map,
)
from mmrelay.log_utils import get_logger

# Do not import plugin_loader here to avoid circular imports
from mmrelay.meshtastic_utils import connect_meshtastic, sendTextReply
from mmrelay.message_queue import get_message_queue, queue_message

logger = get_logger(name="Matrix")


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
    channels = {}

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
    Return a concise, user-facing description of why a Matrix initial sync failed.

    Given a sync response object (commonly a nio ErrorResponse or an HTTP/transport error object), extract the most specific, human-readable failure reason available. The helper maps common HTTP status codes (401, 403, 404, 429, 5xx) to friendly messages, inspects nio error messages and transport responses, and falls back to a generic network/connectivity message when necessary.

    Parameters:
        sync_response: The sync response or error object returned by the Matrix client; can be a nio ErrorResponse, an HTTP/transport error, or any object carrying `message`, `status_code`, or `transport_response` attributes.

    Returns:
        str: A short, user-focused error description suitable for logs and user-facing troubleshooting hints.
    """
    try:
        # Handle bytes/bytearray types by converting to string
        if isinstance(sync_response, (bytes, bytearray)):
            try:
                sync_response = sync_response.decode("utf-8")
            except UnicodeDecodeError:
                return "Network connectivity issue or server unreachable (binary data)"

        # Try to extract specific error information
        if hasattr(sync_response, "message") and sync_response.message:
            message = sync_response.message
            # Handle if message is bytes/bytearray
            if isinstance(message, (bytes, bytearray)):
                try:
                    message = message.decode("utf-8")
                except UnicodeDecodeError:
                    return "Network connectivity issue or server unreachable"
            return message
        elif hasattr(sync_response, "status_code") and sync_response.status_code:
            status_code = sync_response.status_code
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
            transport = sync_response.transport_response
            if hasattr(transport, "status_code"):
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

    # Check if the message starts with bot_user_id or bot_user_name
    if full_message.startswith(bot_user_id) or text_content.startswith(bot_user_id):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    elif full_message.startswith(bot_user_name) or text_content.startswith(
        bot_user_name
    ):
        # Construct a regex pattern to match variations of bot mention and command
        pattern = rf"^(?:{re.escape(bot_user_id)}|{re.escape(bot_user_name)}|[#@].+?)[,:;]?\s*!{command}"
        return bool(re.match(pattern, full_message)) or bool(
            re.match(pattern, text_content)
        )
    else:
        return False


async def connect_matrix(passed_config=None):
    """
    Establish and initialize a Matrix AsyncClient connected to the configured homeserver, with optional End-to-End Encryption (E2EE) support.

    This function will:
    - Prefer credentials.json (E2EE-enabled session) when present; otherwise use the "matrix" section in the provided global configuration.
    - Validate required configuration (including a required top-level "matrix_rooms" mapping).
    - Create an AsyncClient with a certifi-backed SSL context.
    - When E2EE is enabled and supported, prepare the encryption store, load keys, and upload device keys if needed.
    - Perform an initial sync (full_state) to populate room state and then fetch the bot's display name.
    - Return the initialized AsyncClient instance (and set several module-level globals used elsewhere).

    Parameters:
        passed_config (dict | None): Optional configuration to use instead of the module-level config. If provided, it replaces the global config for this connection attempt.

    Returns:
        AsyncClient: An initialized matrix-nio AsyncClient instance ready for use, or None when connection cannot be established due to missing credentials/configuration.

    Raises:
        ValueError: If the top-level "matrix_rooms" configuration is missing.
        ConnectionError: If the initial sync reports a sync error or times out.
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
        e2ee_device_id = credentials.get("device_id")

        # Log consolidated credentials info
        logger.debug(f"Using Matrix credentials (device: {e2ee_device_id})")

        # If device_id is missing, warn but proceed; we'll learn and persist it after restore_login().
        if not isinstance(e2ee_device_id, str) or not e2ee_device_id.strip():
            logger.warning(
                "credentials.json has no valid device_id; proceeding to restore session and discover device_id."
            )
            e2ee_device_id = None

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
                e2ee_device_id = credentials.get("device_id")
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
        bot_user_id = matrix_section["bot_user_id"]

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
                    import olm  # noqa: F401

                    # Also check for other required E2EE dependencies
                    try:
                        from nio.crypto import OlmDevice  # noqa: F401
                        from nio.store import SqliteStore  # noqa: F401

                        logger.debug("All E2EE dependencies are available")
                    except ImportError as e:
                        logger.error(f"Missing E2EE dependency: {e}")
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
                            from mmrelay.config import get_e2ee_store_dir

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
        ssl=ssl_context,
    )

    # Set the access_token and user_id using restore_login for better session management
    if credentials:
        # Use restore_login method for proper session restoration.
        # nio will handle loading the store automatically if store_path was provided
        # to the client constructor.
        matrix_client.restore_login(
            user_id=bot_user_id,
            device_id=e2ee_device_id,
            access_token=matrix_access_token,
        )
        logger.info(
            f"Restored login session for {bot_user_id} with device {e2ee_device_id}"
        )

        # If the device_id was not known up-front, capture what nio has after restore.
        if not e2ee_device_id and getattr(matrix_client, "device_id", None):
            e2ee_device_id = matrix_client.device_id
            logger.debug(f"Device ID established after restore_login: {e2ee_device_id}")
            try:
                if credentials is not None:
                    credentials["device_id"] = e2ee_device_id
                    save_credentials(credentials)
                    logger.info("Updated credentials.json with discovered device_id")
            except Exception as e:
                logger.debug(f"Failed to persist discovered device_id: {e}")
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
            e2ee_status = get_e2ee_status(config, config_path)

            # Display rooms with channel mappings
            _display_room_channel_mappings(matrix_client.rooms, config, e2ee_status)

            # Show warnings for encrypted rooms when E2EE is not ready
            warnings = get_room_encryption_warnings(matrix_client.rooms, e2ee_status)
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
    response = await matrix_client.get_displayname(bot_user_id)
    if hasattr(response, "displayname"):
        bot_user_name = response.displayname
    else:
        bot_user_name = bot_user_id  # Fallback if display name is not set

    # Set E2EE status on the client for other functions to access
    matrix_client.e2ee_enabled = e2ee_enabled

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
    try:
        # Enable nio debug logging for detailed connection analysis
        # Enable ALL logging to debug the validation issue
        logging.getLogger("nio").setLevel(logging.DEBUG)
        logging.getLogger("nio.client").setLevel(logging.DEBUG)
        logging.getLogger("nio.http_client").setLevel(logging.DEBUG)
        logging.getLogger("nio.responses").setLevel(
            logging.DEBUG
        )  # Enable validation logging
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
        temp_client = AsyncClient(homeserver, "", ssl=ssl_context)
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
        if not username.startswith("@"):
            username = f"@{username}"

        server_name = urlparse(homeserver).netloc
        logger.debug(f"Extracted server_name from homeserver: {server_name}")
        logger.debug(f"Original username: {username}")

        if ":" not in username:
            username = f"{username}:{server_name}"
            logger.debug(f"Added server to username: {username}")

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

        # Debug password handling (without logging the actual password)
        logger.debug(f"Password length: {len(password) if password else 0}")
        logger.debug(f"Password type: {type(password).__name__}")
        if password:
            # Check for special characters that might cause issues
            special_chars = set(password) - set(
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
            )
            if special_chars:
                logger.debug(
                    f"Password contains special characters: {len(special_chars)} unique chars"
                )
                # Log the character codes without revealing the password
                char_codes = [ord(c) for c in special_chars]
                logger.debug(f"Special character codes: {char_codes}")

                # Test JSON serialization with special characters
                try:
                    import json

                    test_dict = {"password": password}
                    json_str = json.dumps(test_dict)
                    parsed_back = json.loads(json_str)
                    if parsed_back["password"] != password:
                        logger.error(
                            "Password JSON round-trip failed - special characters not handled correctly"
                        )
                        logger.error(
                            "This may cause login failures with matrix-nio library"
                        )
                        logger.error(
                            "Consider using a password with only alphanumeric characters"
                        )
                except Exception as json_e:
                    logger.error(f"Password JSON serialization failed: {json_e}")
                    logger.error(
                        "Password contains characters that cannot be properly serialized"
                    )
                    logger.error("This will likely cause login to fail")
                    logger.error(
                        "Please use a password with only alphanumeric characters"
                    )
            else:
                logger.debug("Password contains only alphanumeric characters")

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
            ssl=ssl_context,
        )

        logger.debug("AsyncClient created successfully")

        # Test JSON encoding of password to see if special characters are handled correctly
        if password:
            import json

            test_dict = {"password": password}
            try:
                json_str = json.dumps(test_dict, separators=(",", ":"))
                logger.debug(
                    f"Password JSON encoding test successful, length: {len(json_str)}"
                )
                # Check if the JSON contains the expected password length
                parsed_back = json.loads(json_str)
                if len(parsed_back["password"]) != len(password):
                    logger.error(
                        "Password length mismatch after JSON encoding/decoding!"
                    )
                else:
                    logger.debug("Password JSON round-trip successful")
            except Exception as e:
                logger.error(f"Password JSON encoding failed: {e}")

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
                from nio.api import Api

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

            # Debug: Log the actual response received
            logger.debug(f"Login response type: {type(response).__name__}")
            logger.debug(f"Login response attributes: {dir(response)}")
            if hasattr(response, "__dict__"):
                logger.debug(f"Login response dict: {response.__dict__}")

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
        if hasattr(response, "access_token") and response.access_token:
            logger.info("Login successful!")

            # Get the actual user_id from whoami() - this is the proper way
            try:
                whoami_response = await client.whoami()
                if hasattr(whoami_response, "user_id"):
                    actual_user_id = whoami_response.user_id
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
                "access_token": response.access_token,
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
            if hasattr(response, "status_code") and hasattr(response, "message"):
                status_code = response.status_code
                error_message = response.message

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
            await client.close()
        except Exception as e:
            # Ignore errors during client cleanup - connection may already be closed
            logger.debug(f"Ignoring error during client cleanup: {e}")
        return False


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """
    Join a Matrix room by ID or alias, resolving aliases and updating the local matrix_rooms mapping.

    If given a room alias (starts with '#'), the alias is resolved to a room ID and any entry in the global matrix_rooms list that referenced that alias will be replaced with the resolved room ID. If the bot is not already in the resolved room (or provided room ID), the function attempts to join it. Successes and failures are logged; exceptions are caught and handled internally (the function does not raise).

    Parameters:
        room_id_or_alias (str): Room ID (e.g. "!abcdef:server") or alias (e.g. "#room:server") to join.
    """
    try:
        if room_id_or_alias.startswith("#"):
            # If it's a room alias, resolve it to a room ID
            response = await matrix_client.room_resolve_alias(room_id_or_alias)
            if not hasattr(response, "room_id") or not response.room_id:
                logger.error(
                    f"Failed to resolve room alias '{room_id_or_alias}': {getattr(response, 'message', str(response))}"
                )
                return
            room_id = response.room_id
            # Update the room ID in the matrix_rooms list
            for room_config in matrix_rooms:
                if room_config["id"] == room_id_or_alias:
                    room_config["id"] = room_id
                    break
        else:
            room_id = room_id_or_alias

        # Attempt to join the room if not already joined
        if room_id not in matrix_client.rooms:
            response = await matrix_client.join(room_id)
            if response and hasattr(response, "room_id"):
                logger.info(f"Joined room '{room_id_or_alias}' successfully")
            else:
                logger.error(
                    f"Failed to join room '{room_id_or_alias}': {getattr(response, 'message', str(response))}"
                )
        else:
            logger.debug(f"Bot is already in room '{room_id_or_alias}'")
    except Exception:
        logger.exception(f"Error joining room '{room_id_or_alias}'")


def _get_e2ee_error_message():
    """
    Return a specific error message for why E2EE is not properly enabled.
    Uses the unified E2EE status system for consistent messaging.
    """
    from mmrelay.config import config_path
    from mmrelay.e2ee_utils import get_e2ee_error_message, get_e2ee_status

    # Get unified E2EE status
    e2ee_status = get_e2ee_status(config, config_path)

    # Return unified error message
    return get_e2ee_error_message(e2ee_status)


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
    Relay a Meshtastic message into a Matrix room, optionally as an emote, emoji-marked message, or as a reply, and record a Meshtastic↔Matrix mapping when configured.

    Builds a Matrix message payload (plain and HTML/markdown-safe formatted bodies), applies Matrix reply framing when reply_to_event_id is provided, enforces E2EE restrictions (will block sending to encrypted rooms when client E2EE is not enabled), sends the event via the global Matrix client, and — if message-interactions are enabled and a Meshtastic message ID is provided — stores a mapping for future cross-network replies/reactions. Handles timeouts and errors by logging and returning without raising.

    Parameters:
        room_id (str): Matrix room ID or alias to send to.
        message (str): Message text to relay; may contain Markdown or HTML which will be converted/stripped as needed.
        longname (str): Sender long display name from Meshtastic used for attribution in formatted output.
        shortname (str): Sender short display name from Meshtastic.
        meshnet_name (str): Originating meshnet name (used for metadata/attribution).
        portnum (int): Meshtastic port number the message originated from.
        meshtastic_id (str, optional): Meshtastic message ID; when provided and interactions/storage are enabled, a mapping from this Meshtastic ID to the resulting Matrix event will be persisted.
        meshtastic_replyId (str, optional): Meshtastic message ID being replied to; included as metadata on the Matrix event.
        meshtastic_text (str, optional): Original Meshtastic message text used when creating stored mappings.
        emote (bool, optional): If True, send as an m.emote (emote) message instead of regular text.
        emoji (bool, optional): If True, add emoji metadata to the Matrix event (used to mark emoji-like messages).
        reply_to_event_id (str, optional): Matrix event ID to which this message should be formatted as an m.in_reply_to reply.

    Side effects:
        - Sends a message to Matrix using the global matrix client.
        - May persist a Meshtastic↔Matrix mapping for replies/reactions when storage is enabled.
        - Logs errors and warnings; does not raise on send failures or storage errors (errors are caught and logged).

    Returns:
        None
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
                import bleach  # lazy import
                import markdown  # lazy import

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
                    bot_user_id = matrix_client.user_id
                    original_sender_display = f"{longname}/{original_meshnet}"

                    # Create the quoted reply format
                    safe_original = html.escape(original_text or "")
                    safe_sender_display = re.sub(
                        r"([\\`*_{}[\]()#+.!-])", r"\\\1", original_sender_display
                    )
                    quoted_text = (
                        f"> <@{bot_user_id}> [{safe_sender_display}]: {safe_original}"
                    )
                    content["body"] = f"{quoted_text}\n\n{plain_body}"

                    # Always use HTML formatting for replies since we need the mx-reply structure
                    content["format"] = "org.matrix.custom.html"
                    reply_link = f"https://matrix.to/#/{room_id}/{reply_to_event_id}"
                    bot_link = f"https://matrix.to/#/@{bot_user_id}"
                    blockquote_content = (
                        f'<a href="{reply_link}">In reply to</a> '
                        f'<a href="{bot_link}">@{bot_user_id}</a><br>'
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
            if hasattr(response, "event_id"):
                logger.debug(f"Message event_id: {response.event_id}")

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
            and hasattr(response, "event_id")
        ):
            try:
                # Store the message map
                store_message_map(
                    meshtastic_id,
                    response.event_id,
                    room_id,
                    meshtastic_text if meshtastic_text else message,
                    meshtastic_meshnet=local_meshnet_name,
                )
                logger.debug(f"Stored message map for meshtastic_id: {meshtastic_id}")

                # If msgs_to_keep > 0, prune old messages after inserting a new one
                if msgs_to_keep > 0:
                    prune_message_map(msgs_to_keep)
            except Exception as e:
                logger.error(f"Error storing message map: {e}")

    except asyncio.TimeoutError:
        logger.error("Timed out while waiting for Matrix response")
    except Exception:
        logger.exception(f"Error sending radio message to matrix room {room_id}")


def truncate_message(text, max_bytes=DEFAULT_MESSAGE_TRUNCATE_BYTES):
    """
    Truncate the given text to fit within the specified byte size.

    :param text: The text to truncate.
    :param max_bytes: The maximum allowed byte size for the truncated text.
    :return: The truncated text.
    """
    truncated_text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
    return truncated_text


def strip_quoted_lines(text: str) -> str:
    """
    Removes lines starting with '>' from the input text.

    This is typically used to exclude quoted content from Matrix replies, such as when processing reaction text.
    """
    lines = text.splitlines()
    filtered = [line for line in lines if not line.strip().startswith(">")]
    return " ".join(filtered).strip()


async def get_user_display_name(room, event):
    """
    Retrieve the display name of a Matrix user, preferring the room-specific name if available.

    Returns:
        str: The user's display name, or their Matrix ID if no display name is set.
    """
    room_display_name = room.user_name(event.sender)
    if room_display_name:
        return room_display_name

    display_name_response = await matrix_client.get_displayname(event.sender)
    return display_name_response.displayname or event.sender


def format_reply_message(config, full_display_name, text):
    """
    Format a reply message by prefixing a truncated display name and removing quoted lines.

    The resulting message is prefixed with the first five characters of the user's display name followed by "[M]: ", has quoted lines removed, and is truncated to fit within the allowed message length.

    Parameters:
        full_display_name (str): The user's full display name to be truncated for the prefix.
        text (str): The reply text, possibly containing quoted lines.

    Returns:
        str: The formatted and truncated reply message.
    """
    prefix = get_meshtastic_prefix(config, full_display_name)

    # Strip quoted content from the reply text
    clean_text = strip_quoted_lines(text)
    reply_message = f"{prefix}{clean_text}"
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
    Queue a Matrix-origin reply for transmission over Meshtastic, optionally as a structured reply targeting a specific Meshtastic message.

    If Meshtastic broadcasting is disabled in configuration, the function does nothing. When broadcasting is enabled, it enqueues either a structured reply (if reply_id is provided and supported) or a regular text broadcast. If storage_enabled is True, a message-mapping metadata record is created so the Meshtastic message can be correlated back to the originating Matrix event for future replies/reactions; the mapping retention uses the configured msgs_to_keep value.

    Parameters:
        reply_message (str): Message text already formatted for Meshtastic.
        full_display_name (str): Human-readable sender name to include in message descriptions.
        room_config (dict): Room-specific configuration; must contain "meshtastic_channel".
        room: Matrix room object where the original event occurred (used for event and room IDs).
        event: Matrix event object being replied to (its event_id is used for mapping metadata).
        text (str): Original Matrix event text (used when creating mapping metadata).
        storage_enabled (bool): If True, attach mapping metadata to the queued Meshtastic message.
        local_meshnet_name (str | None): Optional meshnet identifier to include in mapping metadata.
        reply_id (int | None): Meshtastic message ID to target for a structured reply; if None, a regular broadcast is sent.

    Returns:
        None

    Notes:
        - The function logs errors and does not raise; actual transmission is handled asynchronously by the Meshtastic queue system.
        - Mapping creation uses configured limits (msgs_to_keep) and _create_mapping_info; if mapping creation fails, the message is still attempted without mapping.
    """
    meshtastic_interface = connect_meshtastic()
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
):
    """
    Relays a Matrix reply to the corresponding Meshtastic message if a mapping exists.

    Looks up the original Meshtastic message using the Matrix event ID being replied to. If found, formats and sends the reply to Meshtastic, preserving conversational context. Returns True if the reply was successfully handled; otherwise, returns False to allow normal message processing.

    Returns:
        bool: True if the reply was relayed to Meshtastic, False otherwise.
    """
    # Look up the original message in the message map
    orig = get_message_map_by_matrix_event_id(reply_to_event_id)
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
    reply_message = format_reply_message(config, full_display_name, text)

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
    Handle an incoming Matrix room event and relay appropriate content to Meshtastic.

    Processes inbound Matrix events (text, notice, emote, reaction, encrypted events, and reply structures) for supported rooms and, depending on configuration, forwards messages, reactions, and replies to the Meshtastic network. Behavior summary:
    - Ignores events from before the bot started and events sent by the bot itself.
    - Logs and notes room encryption changes; encrypted message decryption is handled elsewhere.
    - Uses per-room configuration to decide whether to process the event; unsupported rooms are ignored.
    - Honors interaction settings (reactions and replies) and a broadcast_enabled gate for whether Matrix->Meshtastic forwarding occurs.
    - For reactions: looks up mapped Meshtastic messages and forwards reactions back to the originating mesh when configured; supports special handling for remote-meshnet reactions and emote-derived reactions.
    - For replies: attempts to find the corresponding Meshtastic message mapping and queue a reply to Meshtastic when enabled.
    - For regular messages: applies configured prefix formatting, truncation, and special handling for messages originating from remote meshnets; supports detection-sensor forwarding when the port indicates detection data.
    - Integrates with the plugin system: plugins can handle or consume messages/commands; messages identified as commands directed at the bot are not relayed to Meshtastic.

    Side effects:
    - May enqueue messages or data to be sent via Meshtastic (via the internal queue system).
    - May read and consult persistent message mapping storage to support reaction and reply bridging.
    - May call Matrix APIs to fetch display names.

    Returns:
    - None
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
    for room_conf in matrix_rooms:
        if room_conf["id"] == room.room_id:
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
        logger.debug(f"Processing Matrix reaction event: {event.source}")
        # For RoomMessageEmote, treat as remote reaction if meshtastic_replyId exists
        is_reaction = True
        # We need to manually extract the reaction emoji from the body
        reaction_body = event.source["content"].get("body", "")
        reaction_match = re.search(r"reacted (.+?) to", reaction_body)
        reaction_emoji = reaction_match.group(1).strip() if reaction_match else "?"

    text = event.body.strip() if (not is_reaction and hasattr(event, "body")) else ""

    longname = event.source["content"].get("meshtastic_longname")
    shortname = event.source["content"].get("meshtastic_shortname", None)
    meshnet_name = event.source["content"].get("meshtastic_meshnet")
    meshtastic_replyId = event.source["content"].get("meshtastic_replyId")
    suppress = event.source["content"].get("mmrelay_suppress")

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
            meshtastic_interface = connect_meshtastic()
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
            room_display_name = room.user_name(event.sender)
            if room_display_name:
                full_display_name = room_display_name
            else:
                # Fallback to global display name if room-specific name is not available
                display_name_response = await matrix_client.get_displayname(
                    event.sender
                )
                full_display_name = display_name_response.displayname or event.sender

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
            meshtastic_interface = connect_meshtastic()
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
            text = truncate_message(text)
            # Use the configured prefix format for remote meshnet messages
            prefix = get_matrix_prefix(config, longname, shortname, short_meshnet_name)
            full_message = f"{prefix}{text}"
        else:
            # If this message is from our local meshnet (loopback), we ignore it
            return
    else:
        # Normal Matrix message from a Matrix user
        # Get room-specific display name if available, fallback to global display name
        room_display_name = room.user_name(event.sender)
        if room_display_name:
            full_display_name = room_display_name
        else:
            # Fallback to global display name if room-specific name is not available
            display_name_response = await matrix_client.get_displayname(event.sender)
            full_display_name = display_name_response.displayname or event.sender
        prefix = get_meshtastic_prefix(config, full_display_name, event.sender)
        logger.debug(f"Processing matrix message from [{full_display_name}]: {text}")
        full_message = f"{prefix}{text}"
        full_message = truncate_message(full_message)

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

    # Connect to Meshtastic
    meshtastic_interface = connect_meshtastic()
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
            portnum = event.source["content"].get("meshtastic_portnum")
            if portnum == DETECTION_SENSOR_APP:
                # If detection_sensor is enabled, forward this data as detection sensor data
                if get_meshtastic_config_value(
                    config, "detection_sensor", DEFAULT_DETECTION_SENSOR
                ):
                    success = queue_message(
                        meshtastic_interface.sendData,
                        data=full_message.encode("utf-8"),
                        channelIndex=meshtastic_channel,
                        portNum=meshtastic.protobuf.portnums_pb2.PortNum.DETECTION_SENSOR_APP,
                        description=f"Detection sensor data from {full_display_name}",
                    )

                    if success:
                        # Get queue size to determine logging approach
                        queue_size = get_message_queue().get_queue_size()

                        if queue_size > 1:
                            meshtastic_logger.info(
                                f"Relaying detection sensor data from {full_display_name} to radio broadcast (queued: {queue_size} messages)"
                            )
                        else:
                            meshtastic_logger.info(
                                f"Relaying detection sensor data from {full_display_name} to radio broadcast"
                            )
                        # Note: Detection sensor messages are not stored in message_map because they are never replied to
                        # Only TEXT_MESSAGE_APP messages need to be stored for reaction handling
                    else:
                        meshtastic_logger.error(
                            "Failed to relay detection sensor data to Meshtastic"
                        )
                        return
                else:
                    meshtastic_logger.debug(
                        f"Detection sensor packet received from {full_display_name}, but detection sensor processing is disabled."
                    )
            else:
                # Regular text message - logging will be handled by queue success handler
                pass

                # Create mapping info if storage is enabled
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


async def upload_image(
    client: AsyncClient, image: Image.Image, filename: str
) -> UploadResponse:
    """
    Uploads an image to Matrix and returns the UploadResponse containing the content URI.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_data = buffer.getvalue()

    response, maybe_keys = await client.upload(
        io.BytesIO(image_data),
        content_type="image/png",
        filename=filename,
        filesize=len(image_data),
    )

    return response


async def send_room_image(
    client: AsyncClient, room_id: str, upload_response: UploadResponse
):
    """
    Sends an already uploaded image to the specified room.
    """
    await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content={"msgtype": "m.image", "url": upload_response.content_uri, "body": ""},
    )


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
