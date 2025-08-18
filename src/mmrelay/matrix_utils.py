import asyncio
import getpass
import io
import json
import logging
import os
import re
import ssl
import sys
import time
from typing import Union
from urllib.parse import urlparse

import certifi
import markdown
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

from mmrelay.cli_utils import (
    msg_regenerate_credentials,
    msg_require_auth_login,
    msg_retry_auth_login,
)
from mmrelay.config import (
    get_base_dir,
    get_e2ee_store_dir,
    get_meshtastic_config_value,
    save_credentials,
)
from mmrelay.constants.app import WINDOWS_PLATFORM
from mmrelay.constants.config import (
    CONFIG_SECTION_MATRIX,
    DEFAULT_BROADCAST_ENABLED,
    DEFAULT_DETECTION_SENSOR,
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


def _create_mapping_info(
    matrix_event_id, room_id, text, meshnet=None, msgs_to_keep=None
):
    """
    Create a metadata dictionary linking a Matrix event to a Meshtastic message for message mapping.

    Removes quoted lines from the message text and includes identifiers and message retention settings. Returns `None` if any required parameter is missing.

    Returns:
        dict: Mapping information for the message queue, or `None` if required fields are missing.
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
        ConnectionError: If creating the SSL context fails or the initial sync reports a sync error.
        asyncio.TimeoutError: If the initial sync times out.
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
            logger.info(f"Found credentials at {credentials_path}")
            with open(credentials_path, "r") as f:
                credentials = json.load(f)
    except Exception as e:
        logger.warning(f"Error loading credentials: {e}")

    # If credentials.json exists, use it
    if credentials:
        matrix_homeserver = credentials["homeserver"]
        matrix_access_token = credentials["access_token"]
        bot_user_id = credentials["user_id"]
        e2ee_device_id = credentials.get("device_id")

        # Log credentials loading
        logger.info(f"Using credentials from {credentials_path}")
        logger.info(f"Loaded device_id: {e2ee_device_id}")

        # Check if device_id is missing or None
        if not isinstance(e2ee_device_id, str) or not e2ee_device_id.strip():
            if not e2ee_device_id:
                logger.error("Device ID is missing from credentials.json!")
                logger.error("E2EE requires a valid device_id for session persistence")
                # Log available keys for debugging without exposing sensitive data
                logger.debug(
                    f"credentials.json keys present: {list(credentials.keys())}"
                )
                error_msg = "E2EE requires a valid device_id in credentials.json"
            else:
                logger.error(
                    f"Invalid device_id format in credentials.json: {repr(e2ee_device_id)}"
                )
                logger.error("Device ID must be a non-empty string")
                error_msg = "Invalid device_id format in credentials.json"

            logger.error(msg_regenerate_credentials())
            raise RuntimeError(error_msg)

        # If config also has Matrix login info, let the user know we're ignoring it
        if config and "matrix" in config and "access_token" in config["matrix"]:
            logger.info(
                "NOTE: Ignoring Matrix login details in config.yaml in favor of credentials.json"
            )
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

        # Set device_id for E2EE - only use config value if specified
        e2ee_device_id = matrix_section.get("device_id")

    # Get matrix rooms from config
    if "matrix_rooms" not in config:
        logger.error("Configuration is missing 'matrix_rooms' section")
        logger.error(
            "Please ensure your config.yaml includes matrix_rooms configuration"
        )
        raise ValueError("Missing required 'matrix_rooms' configuration")
    matrix_rooms = config["matrix_rooms"]

    # Create SSL context using certifi's certificates
    try:
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception as e:
        logger.error(f"Failed to create SSL context: {e}")
        raise ConnectionError(f"SSL context creation failed: {e}") from e

    # Check if E2EE is enabled
    e2ee_enabled = False
    e2ee_store_path = None
    # Only initialize e2ee_device_id if not already set from credentials
    if "e2ee_device_id" not in locals():
        e2ee_device_id = None

    try:
        # Check both 'encryption' and 'e2ee' keys for backward compatibility
        matrix_cfg = config.get("matrix", {}) or {}
        encryption_enabled = matrix_cfg.get("encryption", {}).get("enabled", False)
        e2ee_enabled = matrix_cfg.get("e2ee", {}).get("enabled", False)
        if encryption_enabled or e2ee_enabled:
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

                        logger.info("All E2EE dependencies are available")
                    except ImportError as e:
                        logger.error(f"Missing E2EE dependency: {e}")
                        logger.error("Please reinstall with: pip install mmrelay[e2e]")
                        raise RuntimeError(
                            "Missing E2EE dependency (Olm/SqliteStore)"
                        ) from e

                    e2ee_enabled = True
                    logger.info("End-to-End Encryption (E2EE) is enabled")

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
                        logger.info(
                            f"Found existing E2EE store files: {', '.join(db_files)}"
                        )
                    else:
                        logger.warning(
                            "No existing E2EE store files found. Encryption may not work correctly."
                        )

                    logger.info(f"Using E2EE store path: {e2ee_store_path}")

                    # If device_id is not present in credentials, we can attempt to learn it later.
                    if not e2ee_device_id:
                        logger.debug(
                            "No device_id in credentials; will retrieve from store/whoami later if available"
                        )
                except ImportError:
                    logger.warning(
                        "E2EE is enabled in config but python-olm is not installed."
                    )
                    logger.warning("Install mmrelay[e2e] to use E2EE features.")
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
                logger.info("No key upload needed - keys already present")
        except Exception as e:
            logger.error(f"Failed to upload E2EE keys: {e}")
            # E2EE might still work, so we don't disable it here
            logger.error("Consider regenerating credentials with: mmrelay auth login")

    # Perform initial sync to populate rooms (needed for message delivery)
    logger.info("Performing initial sync to initialize rooms...")
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
            logger.error(f"Initial sync failed: {sync_response}")
            raise ConnectionError(f"Matrix sync failed: {sync_response}")
        else:
            logger.info(
                f"Initial sync completed. Found {len(matrix_client.rooms)} rooms."
            )

            # List all rooms with unified E2EE status display
            from mmrelay.e2ee_utils import get_e2ee_status, format_room_list, get_room_encryption_warnings
            from mmrelay.config import config_path

            # Get comprehensive E2EE status
            e2ee_status = get_e2ee_status(config, config_path)

            logger.info("Bot is in the following rooms:")

            # Format room list with appropriate encryption indicators
            room_lines = format_room_list(matrix_client.rooms, e2ee_status)
            for line in room_lines:
                logger.info(line)

            # Show warnings for encrypted rooms when E2EE is not ready
            warnings = get_room_encryption_warnings(matrix_client.rooms, e2ee_status)
            for warning in warnings:
                logger.warning(warning)

            # Debug information
            encrypted_count = sum(1 for room in matrix_client.rooms.values() if getattr(room, 'encrypted', False))
            logger.debug(
                f"Found {encrypted_count} encrypted rooms out of {len(matrix_client.rooms)} total rooms"
            )
            logger.debug(f"E2EE status: {e2ee_status['overall_status']}")

            # Additional debugging for E2EE enabled case
            if e2ee_enabled and encrypted_count == 0 and len(matrix_client.rooms) > 0:
                logger.debug("No encrypted rooms detected - all rooms are plaintext")
    except asyncio.TimeoutError:
        logger.error(
            f"Initial sync timed out after {MATRIX_SYNC_OPERATION_TIMEOUT} seconds"
        )
        raise

    # Add a delay to allow for key sharing to complete
    # This addresses a race condition where the client attempts to send encrypted messages
    # before it has received and processed room key sharing messages from other devices.
    # The initial sync() call triggers key sharing requests, but the actual key exchange
    # happens asynchronously. Without this delay, outgoing messages may be sent unencrypted
    # even to encrypted rooms. While not ideal, this timing-based approach is necessary
    # because matrix-nio doesn't provide event-driven alternatives to detect when key
    # sharing is complete. The delay can be configured via matrix.e2ee.key_sharing_delay_seconds.
    if e2ee_enabled:
        # Make the delay configurable, default to 5 seconds
        delay = (
            config.get("matrix", {}).get("e2ee", {}).get("key_sharing_delay_seconds", 5)
        )
        logger.debug(f"Waiting for {delay} seconds to allow for key sharing...")
        await asyncio.sleep(delay)

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
    Login to Matrix as a bot and save the access token for E2EE use.

    This function creates a new Matrix session with E2EE support and saves
    the credentials to credentials.json for use by the relay.

    Args:
        homeserver: The Matrix homeserver URL
        username: The Matrix username
        password: The Matrix password
        logout_others: Whether to log out other sessions

    Returns:
        bool: True if login was successful, False otherwise
    """
    try:
        # Enable nio debug logging for detailed connection analysis
        logging.getLogger("nio").setLevel(logging.DEBUG)
        logging.getLogger("nio.client").setLevel(logging.DEBUG)
        logging.getLogger("nio.http_client").setLevel(logging.DEBUG)
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

        # Create a temporary client for discovery
        temp_client = AsyncClient(homeserver, "")
        try:
            discovery_response = await asyncio.wait_for(
                temp_client.discovery_info(), timeout=30.0
            )

            if isinstance(discovery_response, DiscoveryInfoResponse):
                actual_homeserver = discovery_response.homeserver_url
                logger.info(f"Server discovery successful: {actual_homeserver}")
                homeserver = actual_homeserver
            elif isinstance(discovery_response, DiscoveryInfoError):
                logger.info(
                    f"Server discovery failed, using original URL: {homeserver}"
                )
                # Continue with original homeserver URL

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
        if ":" not in username:
            username = f"{username}:{server_name}"

        logger.info(f"Using username: {username}")

        # Get password
        if not password:
            password = getpass.getpass("Enter Matrix password: ")

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
                with open(credentials_path, "r") as f:
                    existing_creds = json.load(f)
                    if (
                        "device_id" in existing_creds
                        and existing_creds["user_id"] == username
                    ):
                        existing_device_id = existing_creds["device_id"]
                        logger.info(f"Reusing existing device_id: {existing_device_id}")
        except Exception as e:
            logger.debug(f"Could not load existing credentials: {e}")

        # Get the E2EE store path
        store_path = get_e2ee_store_dir()
        os.makedirs(store_path, exist_ok=True)
        logger.info(f"Using E2EE store path: {store_path}")

        # Create client config for E2EE
        client_config = AsyncClientConfig(
            store_sync_tokens=True, encryption_enabled=True
        )

        # Try default SSL context first (like matrix-commander)
        # If that fails, we'll try with certifi SSL context
        ssl_context = None  # Use aiohttp default SSL context

        # Initialize client with E2EE support
        # Use most common pattern from matrix-nio examples: positional homeserver and user
        client = AsyncClient(
            homeserver,
            username,
            device_id=existing_device_id,
            store_path=store_path,
            config=client_config,
            ssl=ssl_context,
        )

        logger.info(f"Logging in as {username} to {homeserver}...")

        # Login with consistent device name and timeout
        # Use the original working device name
        device_name = "mmrelay-e2ee"
        try:
            # Set device_id on client if we have an existing one
            if existing_device_id:
                client.device_id = existing_device_id

            response = await asyncio.wait_for(
                client.login(password, device_name=device_name),
                timeout=MATRIX_LOGIN_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"Login timed out after {MATRIX_LOGIN_TIMEOUT} seconds")
            logger.error(
                "This may indicate network connectivity issues or a slow Matrix server"
            )
            await client.close()
            return False
        except Exception as e:
            # Handle other exceptions during login (e.g., network errors)
            logger.error(f"Login exception: {e}")
            logger.error(f"Exception type: {type(e)}")
            if hasattr(e, "message"):
                logger.error(f"Exception message: {e.message}")
            await client.close()
            return False

        if hasattr(response, "access_token"):
            logger.info("Login successful!")

            # Save credentials to credentials.json
            credentials = {
                "homeserver": homeserver,
                "user_id": username,
                "access_token": response.access_token,
                "device_id": response.device_id,
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
            # Better error logging
            logger.error(f"Login failed: {response}")
            if hasattr(response, "message"):
                logger.error(f"Error message: {response.message}")
            if hasattr(response, "status_code"):
                logger.error(f"Status code: {response.status_code}")
            await client.close()
            return False

    except Exception as e:
        logger.error(f"Error during login: {e}")
        return False


async def join_matrix_room(matrix_client, room_id_or_alias: str) -> None:
    """
    Join a Matrix room by room ID or alias, resolving aliases and updating the local room mapping.

    If room_id_or_alias is a room alias (starts with '#'), the alias is resolved to a room ID and any entry in the global `matrix_rooms` list that referenced the alias will be updated to the resolved room ID. The function will attempt to join the resolved room (or the given room ID) if the bot is not already a member. Success and failure are logged; errors are caught and logged internally.
    Parameters:
        room_id_or_alias (str): A Matrix room ID (e.g. "!abcdef:server") or room alias (e.g. "#room:server") to join.
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
                    f"Failed to join room '{room_id_or_alias}': {response.message}"
                )
        else:
            logger.debug(f"Bot is already in room '{room_id_or_alias}'")
    except Exception as e:
        logger.error(f"Error joining room '{room_id_or_alias}': {e}")


def _get_e2ee_error_message():
    """
    Return a specific error message for why E2EE is not properly enabled.
    Provides actionable guidance for different failure scenarios.
    """
    if sys.platform == WINDOWS_PLATFORM:
        return (
            "E2EE is not supported on Windows due to library limitations. "
            "Messages will not be sent to encrypted rooms. "
            "Use Linux/macOS for E2EE support, or ask room admins to disable encryption."
        )

    try:
        import olm  # noqa: F401
    except ImportError:
        return (
            "E2EE dependencies are not installed. "
            "Install with: pip install mmrelay[e2e] "
            "Then run 'mmrelay config check' to verify setup."
        )

    # Check for credentials.json (check both locations like _validate_credentials_json)
    from mmrelay.config import get_base_dir, get_config_paths

    credentials_found = False

    # Try to find credentials.json in standard locations
    try:
        # First check base directory
        base_credentials_path = os.path.join(get_base_dir(), "credentials.json")
        if os.path.exists(base_credentials_path):
            credentials_found = True
        else:
            # Also check config directory if we can determine it
            try:
                config_paths = get_config_paths()
                for config_path in config_paths:
                    if os.path.exists(config_path):
                        config_dir = os.path.dirname(config_path)
                        config_credentials_path = os.path.join(
                            config_dir, "credentials.json"
                        )
                        if os.path.exists(config_credentials_path):
                            credentials_found = True
                            break
            except (OSError, ImportError, AttributeError):
                # Config path detection failed - this is expected in some environments
                # Fall back to base directory check only
                pass
    except (OSError, ImportError):
        # File system or import errors - safe to assume no credentials available
        # This handles cases where get_base_dir() or file operations fail
        pass

    if not credentials_found:
        return (
            "E2EE is not configured. "
            "Run 'mmrelay auth login' to set up E2EE authentication, "
            "then use 'mmrelay config check' to verify."
        )

    return (
        "E2EE is not enabled in your configuration. "
        "Add 'e2ee: enabled: true' under the matrix section in config.yaml, "
        "then run 'mmrelay config check' to validate."
    )


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
    Relay a Meshtastic message into a Matrix room, optionally as an emote, emoji reaction, or reply, and record cross-network mappings when configured.

    Formats content (plain and HTML/markdown), builds Matrix reply framing when reply_to_event_id is provided, sends the message to the specified Matrix room, and—if message-interactions are enabled—stores a mapping between the Meshtastic message ID and the resulting Matrix event for future replies/reactions. Respects room encryption and will block sending to encrypted rooms if E2EE is not properly enabled.

    Parameters:
        room_id (str): Matrix room ID or alias to send the message to.
        message (str): Message text to relay.
        longname (str): Sender's long display name from Meshtastic (used in formatted output).
        shortname (str): Sender's short display name from Meshtastic.
        meshnet_name (str): Originating meshnet name (used for metadata/attribution).
        portnum (int): Meshtastic port number the message originated from.
        meshtastic_id (str, optional): Meshtastic message ID; when provided and interactions are enabled, a mapping to the Matrix event will be stored.
        meshtastic_replyId (str, optional): Meshtastic message ID being replied to (included as metadata).
        meshtastic_text (str, optional): Original Meshtastic message text (used when storing mappings).
        emote (bool, optional): If True, send as an emote (m.emote) instead of regular text.
        emoji (bool, optional): If True, mark the message as an emoji reaction in metadata.
        reply_to_event_id (str, optional): Matrix event ID to format this message as an m.relates_to reply to.

    Side effects:
        - Sends a message to Matrix via the global Matrix client.
        - May persist a Meshtastic ↔ Matrix message mapping (for reactions/replies) depending on configuration.
        - Observes encryption configuration and will not send to encrypted rooms when E2EE is not enabled.
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

        # Process markdown to HTML if needed (like base plugin does)
        if has_markdown or has_html:
            formatted_body = markdown.markdown(message)
            plain_body = re.sub(r"</?[^>]*>", "", formatted_body)  # Strip all HTML tags
        else:
            formatted_body = message
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
                    quoted_text = f"> <@{bot_user_id}> [{original_sender_display}]: {original_text}"
                    content["body"] = f"{quoted_text}\n\n{plain_body}"

                    # Always use HTML formatting for replies since we need the mx-reply structure
                    content["format"] = "org.matrix.custom.html"
                    reply_link = f"https://matrix.to/#/{room_id}/{reply_to_event_id}"
                    bot_link = f"https://matrix.to/#/@{bot_user_id}"
                    blockquote_content = f'<a href="{reply_link}">In reply to</a> <a href="{bot_link}">@{bot_user_id}</a><br>[{original_sender_display}]: {original_text}'
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
        except Exception as e:
            logger.error(f"Error sending message to Matrix room {room_id}: {e}")
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
    except Exception as e:
        logger.error(f"Error sending radio message to matrix room {room_id}: {e}")


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

        except Exception as e:
            meshtastic_logger.error(f"Error sending Matrix reply to Meshtastic: {e}")


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
    except Exception as e:
        logger.error(f"Failed to request keys for event {event.event_id}: {e}")


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
        text = truncate_message(text)

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
