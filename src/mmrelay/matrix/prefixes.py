import re
from typing import Any
from urllib.parse import urlparse

import mmrelay.matrix_utils as facade
from mmrelay.constants.config import (
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_KEY_PASSWORD,
    CONFIG_KEY_USER_ID,
    CONFIG_SECTION_DATABASE,
    CONFIG_SECTION_DATABASE_LEGACY,
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
)
from mmrelay.constants.database import DEFAULT_MSGS_TO_KEEP
from mmrelay.constants.formats import (
    DEFAULT_MATRIX_PREFIX,
    DEFAULT_MESHTASTIC_PREFIX,
    DEFAULT_TEXT_ENCODING,
    HTML_TAG_REGEX,
    MARKDOWN_ESCAPE_REGEX,
    OBJECT_REPR_REGEX,
    PREFIX_DEFINITION_REGEX,
)
from mmrelay.constants.messages import (
    DISPLAY_NAME_DEFAULT_LENGTH,
    MAX_TRUNCATION_LENGTH,
)

__all__ = [
    "_first_nonblank_str",
    "_can_auto_create_credentials",
    "_normalize_bot_user_id",
    "_get_msgs_to_keep_config",
    "_get_detailed_matrix_error_message",
    "get_interaction_settings",
    "message_storage_enabled",
    "_add_truncated_vars",
    "_escape_leading_prefix_for_markdown",
    "validate_prefix_format",
    "get_meshtastic_prefix",
    "get_matrix_prefix",
]


def _first_nonblank_str(*values: Any) -> str | None:
    """Return the first non-blank string value after stripping whitespace."""
    for value in values:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _can_auto_create_credentials(matrix_config: dict[str, Any] | None) -> bool:
    """
    Determine whether the Matrix configuration contains the fields required to create credentials automatically.

    Parameters:
        matrix_config (dict): The `matrix` section from config.yaml.

    Returns:
        True if `homeserver`, a user id (`bot_user_id` or `user_id`), and `password` are present as non-empty strings, False otherwise.
    """
    if not isinstance(matrix_config, dict):
        return False
    homeserver = matrix_config.get(CONFIG_KEY_HOMESERVER)
    user = _first_nonblank_str(
        matrix_config.get(CONFIG_KEY_BOT_USER_ID),
        matrix_config.get(CONFIG_KEY_USER_ID),
    )
    password = matrix_config.get(CONFIG_KEY_PASSWORD)
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
    config = facade.config
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

    msg_map_config = _get_msg_map_config(effective_config.get(CONFIG_SECTION_DATABASE))

    # If not found in database config, check legacy db config
    if msg_map_config is None:
        msg_map_config = _get_msg_map_config(
            effective_config.get(CONFIG_SECTION_DATABASE_LEGACY)
        )
        if msg_map_config is not None:
            facade.logger.warning(
                "Using 'db.msg_map' configuration (legacy). 'database.msg_map' is now the preferred format and 'db.msg_map' will be deprecated in a future version."
            )

    if msg_map_config is None:
        msg_map_config = {}

    msgs_to_keep = msg_map_config.get("msgs_to_keep", DEFAULT_MSGS_TO_KEEP)
    if isinstance(msgs_to_keep, bool) or not isinstance(msgs_to_keep, int):
        return DEFAULT_MSGS_TO_KEEP
    return msgs_to_keep


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
            OBJECT_REPR_REGEX.search(error_str) is not None
            or HTML_TAG_REGEX.search(error_str) is not None
            or "unknown error" in error_str.lower()
        )

    try:
        # Handle bytes/bytearray types by converting to string
        if isinstance(matrix_response, (bytes, bytearray)):
            try:
                matrix_response = matrix_response.decode(DEFAULT_TEXT_ENCODING)
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
                    message = message.decode(DEFAULT_TEXT_ENCODING)
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
            facade.logger.debug(
                "Failed to convert matrix_response to string", exc_info=True
            )
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
        facade.logger.debug(
            "Failed to extract matrix error details from %r: %s", matrix_response, e
        )
        # If we can't extract error details, provide a generic but helpful message
        return (
            "Unable to determine specific error - likely a network connectivity issue"
        )


def get_interaction_settings(config: dict[str, Any] | None) -> dict[str, bool]:
    """
    Determine whether message reactions and replies are enabled according to the configuration.

    Checks the new `meshtastic.message_interactions` mapping first; if present, uses its `reactions` and `replies` values. If absent, falls back to the legacy `meshtastic.relay_reactions` flag (deprecated) which enables only reactions. If `config` is None or no relevant keys are present, both features are disabled.

    Parameters:
        config (dict[str, Any] | None): The loaded configuration mapping or None.

    Returns:
        dict[str, bool]: A mapping with keys `"reactions"` and `"replies"`. `"reactions"` is `True` when reactions are enabled, `"replies"` is `True` when replies are enabled; both are `False` by default.
    """
    if not isinstance(config, dict):
        if config is not None:
            facade.logger.warning(
                "Invalid top-level config type (%s); disabling reactions and replies.",
                type(config).__name__,
            )
        return {"reactions": False, "replies": False}

    meshtastic_config = config.get(CONFIG_SECTION_MESHTASTIC, {})
    if not isinstance(meshtastic_config, dict):
        facade.logger.warning(
            "Invalid '%s' configuration type (%s); disabling reactions and replies.",
            CONFIG_SECTION_MESHTASTIC,
            type(meshtastic_config).__name__,
        )
        return {"reactions": False, "replies": False}

    # Check for new structured configuration first
    interactions = meshtastic_config.get("message_interactions")
    if interactions is not None:
        if not isinstance(interactions, dict):
            facade.logger.warning(
                "Invalid '%s.message_interactions' value (%s); disabling reactions and replies.",
                CONFIG_SECTION_MESHTASTIC,
                type(interactions).__name__,
            )
            return {"reactions": False, "replies": False}

        reactions = interactions.get("reactions", False)
        replies = interactions.get("replies", False)
        if "reactions" in interactions and not isinstance(reactions, bool):
            facade.logger.warning(
                "Invalid '%s.message_interactions.reactions' value (%s); treating as False.",
                CONFIG_SECTION_MESHTASTIC,
                type(reactions).__name__,
            )
        if "replies" in interactions and not isinstance(replies, bool):
            facade.logger.warning(
                "Invalid '%s.message_interactions.replies' value (%s); treating as False.",
                CONFIG_SECTION_MESHTASTIC,
                type(replies).__name__,
            )
        return {
            "reactions": reactions if isinstance(reactions, bool) else False,
            "replies": replies if isinstance(replies, bool) else False,
        }

    # Fall back to legacy relay_reactions setting
    if "relay_reactions" in meshtastic_config:
        enabled_value = meshtastic_config.get("relay_reactions")
        enabled = enabled_value if isinstance(enabled_value, bool) else False
        if not isinstance(enabled_value, bool):
            facade.logger.warning(
                "Invalid '%s.relay_reactions' value (%s); treating as False.",
                CONFIG_SECTION_MESHTASTIC,
                type(enabled_value).__name__,
            )
        facade.logger.warning(
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
    for i in range(
        1, MAX_TRUNCATION_LENGTH + 1
    ):  # Support up to MAX_TRUNCATION_LENGTH chars, always add all variants
        truncated_value = text[:i]
        format_vars[f"{prefix}{i}"] = truncated_value


def _escape_leading_prefix_for_markdown(message: str) -> tuple[str, bool]:
    """
    Prevent a leading reference-style Markdown link definition from being interpreted by escaping its bracketed prefix.

    If the message begins with a bracketed prefix followed by a colon (for example, "[name]: "), returns a version of the message where characters that would trigger Markdown link-definition parsing inside the leading brackets are backslash-escaped. If no such prefix is present the input is returned unchanged.

    Returns:
        tuple[str, bool]: `(safe_message, escaped)` where `safe_message` is the possibly-escaped message and `escaped` is `True` if an escape was performed, `False` otherwise.
    """
    match = PREFIX_DEFINITION_REGEX.match(message)
    if not match:
        return message, False

    prefix_text = match.group(1)
    spacing = match.group(2)
    escaped_prefix = MARKDOWN_ESCAPE_REGEX.sub(r"\\\1", prefix_text)
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
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
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
    if not isinstance(config, dict):
        return ""
    meshtastic_config = config.get(CONFIG_SECTION_MESHTASTIC)
    if not isinstance(meshtastic_config, dict):
        meshtastic_config = {}

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
    if user_id and user_id.startswith("@") and ":" in user_id:
        # Extract username and server from @username:server.com format
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
        result = prefix_format.format(**format_vars)
        facade.logger.debug(
            "Meshtastic prefix generated (%s): %s",
            (
                "custom format"
                if prefix_format != DEFAULT_MESHTASTIC_PREFIX
                else "default format"
            ),
            result,
        )
        return result
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
        # Fallback to default format if custom format is invalid
        facade.logger.warning(
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
    if not isinstance(config, dict):
        return ""
    matrix_config = config.get(CONFIG_SECTION_MATRIX)
    if not isinstance(matrix_config, dict):
        matrix_config = {}

    # Check if prefixes are enabled for Matrix direction
    if not matrix_config.get("prefix_enabled", True):
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
        facade.logger.debug(
            "Matrix prefix generated (%s): %s",
            (
                "custom format"
                if matrix_prefix_format != DEFAULT_MATRIX_PREFIX
                else "default format"
            ),
            result,
        )
        return result
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
        # Fallback to default format if custom format is invalid
        facade.logger.warning(
            f"Invalid matrix prefix_format '{matrix_prefix_format}': {e}. Using default format."
        )
        # The default format only uses 'long' and 'mesh', which are safe
        return DEFAULT_MATRIX_PREFIX.format(
            long=longname or "", mesh=meshnet_name or ""
        )
