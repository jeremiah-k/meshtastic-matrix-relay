"""
Centralized E2EE (End-to-End Encryption) utilities for consistent status detection and messaging.

This module provides a unified approach to E2EE status detection, warning messages, and room
formatting across all components of the meshtastic-matrix-relay application.
"""

import os
import sys
from typing import Any, Dict, List, Literal, Optional, TypedDict

from mmrelay.cli_utils import get_command
from mmrelay.constants.app import (
    CREDENTIALS_FILENAME,
    MATRIX_DIRNAME,
    WINDOWS_PLATFORM,
)
from mmrelay.constants.config import CONFIG_SECTION_MATRIX
from mmrelay.constants.messages import (
    MSG_E2EE_DISABLED,
    MSG_E2EE_DISABLED_SHORT,
    MSG_E2EE_NO_AUTH,
    MSG_E2EE_WINDOWS_UNSUPPORTED,
    MSG_E2EE_WINDOWS_UNSUPPORTED_DETAIL,
    MSG_E2EE_WINDOWS_UNSUPPORTED_SHORT,
)
from mmrelay.log_utils import get_logger
from mmrelay.matrix.compat import (
    format_e2ee_install_command,
    format_e2ee_unavailable_message,
    get_matrix_capabilities,
)
from mmrelay.paths import is_deprecation_window_active, resolve_all_paths

logger = get_logger("E2EE")

_E2EE_INCOMPLETE_STATUS = "E2EE setup is incomplete"


class E2EEStatus(TypedDict):
    """Type definition for E2EE status dictionary."""

    enabled: bool
    available: bool
    configured: bool
    platform_supported: bool
    dependencies_installed: bool
    credentials_available: bool
    overall_status: Literal["ready", "disabled", "unavailable", "incomplete"]
    issues: List[str]


def get_e2ee_status(
    config: Dict[str, Any], config_path: Optional[str] = None
) -> E2EEStatus:
    """
    Consolidates End-to-End Encryption (E2EE) readiness by checking platform support, required crypto dependencies, configuration flags, and presence of Matrix credentials.

    Parameters:
        config (Dict[str, Any]): Parsed application configuration; used to read `matrix.e2ee.enabled` and legacy `matrix.encryption.enabled`.
        config_path (Optional[str]): Path to the application config file. When provided, credentials are first searched next to this config directory and then in standard locations; when omitted, only the standard credentials locations are probed.

    Returns:
        E2EEStatus: Dictionary describing E2EE readiness with these keys:
          - enabled (bool): E2EE enabled in configuration.
          - available (bool): Platform and dependencies allow E2EE.
          - configured (bool): Authentication/credentials are present.
          - platform_supported (bool): False when running on unsupported platforms (e.g., Windows/msys/cygwin).
          - dependencies_installed (bool): True if required olm/nio components are importable.
          - credentials_available (bool): True if a Matrix `credentials.json` file was discovered in searched locations.
          - overall_status (str): One of "ready", "disabled", "unavailable", or "incomplete".
          - issues (List[str]): Human-readable issues that prevent full E2EE readiness.
    """
    status: E2EEStatus = {
        "enabled": False,
        "available": False,
        "configured": False,
        "platform_supported": True,
        "dependencies_installed": False,
        "credentials_available": False,
        "overall_status": "incomplete",
        "issues": [],
    }

    # Check platform support
    if sys.platform == WINDOWS_PLATFORM or sys.platform.startswith(("msys", "cygwin")):
        status["platform_supported"] = False
        status["issues"].append(MSG_E2EE_WINDOWS_UNSUPPORTED)
        logger.debug("E2EE platform check: Windows/msys/cygwin not supported")

    # Check dependencies
    matrix_capabilities = get_matrix_capabilities()
    if matrix_capabilities.encryption_available:
        status["dependencies_installed"] = True
        logger.debug(
            "E2EE dependency check: %s crypto available via %s",
            matrix_capabilities.crypto_backend,
            matrix_capabilities.provider_name,
        )
    else:
        status["dependencies_installed"] = False
        status["issues"].append(format_e2ee_unavailable_message(matrix_capabilities))
        logger.debug(
            "E2EE dependency check failed: provider=%s version=%s backend=%s "
            "olm=%s vodozemac=%s nio_crypto=%s sqlite_store=%s encryption_enabled=%s",
            matrix_capabilities.provider_name,
            matrix_capabilities.provider_version,
            matrix_capabilities.crypto_backend,
            matrix_capabilities.olm_available,
            matrix_capabilities.vodozemac_available,
            matrix_capabilities.nio_crypto_available,
            matrix_capabilities.sqlite_store_available,
            matrix_capabilities.nio_crypto_encryption_enabled,
        )

    # Check configuration
    matrix_section = config.get(CONFIG_SECTION_MATRIX, {})
    e2ee_config = matrix_section.get("e2ee", {})
    encryption_config = matrix_section.get("encryption", {})  # Legacy support
    status["enabled"] = e2ee_config.get("enabled", False) or encryption_config.get(
        "enabled", False
    )

    if not status["enabled"]:
        status["issues"].append(MSG_E2EE_DISABLED)
        logger.debug("E2EE config check: not enabled in configuration")

    # Check credentials
    paths_info = resolve_all_paths()
    status["credentials_available"] = _check_credentials_available(
        config_path, paths_info
    )

    if not status["credentials_available"]:
        status["issues"].append(MSG_E2EE_NO_AUTH)
        logger.debug("E2EE credentials check: no credentials.json found")

    # Determine overall availability and status
    status["available"] = (
        status["platform_supported"] and status["dependencies_installed"]
    )
    status["configured"] = status["credentials_available"]

    # Determine overall status
    if not status["platform_supported"]:
        status["overall_status"] = "unavailable"
    elif status["enabled"] and status["available"] and status["configured"]:
        status["overall_status"] = "ready"
    elif not status["enabled"]:
        status["overall_status"] = "disabled"
    else:
        status["overall_status"] = "incomplete"

    logger.debug(
        "E2EE status determined: %s (enabled=%s, available=%s, configured=%s)",
        status["overall_status"],
        status["enabled"],
        status["available"],
        status["configured"],
    )

    return status


def _check_credentials_available(
    config_path: Optional[str] = None, paths_info: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Check whether a Matrix credentials file exists in any standard or legacy locations.

    Searches (in order) beside an optional config file's directory, the primary HOME-based credentials location, a legacy same-home location, and legacy source locations while the deprecation window is active.

    Parameters:
        config_path (Optional[str]): Path to the application's configuration file; when provided, the config file's directory is searched for credentials.
        paths_info (Optional[Dict[str, Any]]): Pre-resolved paths mapping (as returned by resolve_all_paths()); used instead of resolving paths inside the function.

    Returns:
        bool: `True` if a credentials file is found in any checked location, `False` otherwise.
    """
    # Check config directory first
    if config_path:
        config_dir = os.path.dirname(config_path)
        config_candidates = (
            os.path.join(config_dir, CREDENTIALS_FILENAME),
            os.path.join(config_dir, MATRIX_DIRNAME, CREDENTIALS_FILENAME),
        )
        if any(os.path.exists(path) for path in config_candidates):
            return True

    # Resolve paths if not provided
    if paths_info is None:
        paths_info = resolve_all_paths()

    # Check HOME location (primary)
    primary_credentials_path = paths_info["credentials_path"]
    if os.path.exists(primary_credentials_path):
        return True

    # Compatibility fallback for pre-1.3 same-home credentials location.
    home_root = paths_info.get("home")
    if isinstance(home_root, str):
        legacy_same_home_path = os.path.join(home_root, CREDENTIALS_FILENAME)
        if os.path.exists(legacy_same_home_path):
            return True

    # Check legacy sources during deprecation window
    if is_deprecation_window_active():
        for legacy_root in paths_info.get("legacy_sources", []):
            legacy_candidates = (
                os.path.join(legacy_root, CREDENTIALS_FILENAME),
                os.path.join(legacy_root, MATRIX_DIRNAME, CREDENTIALS_FILENAME),
            )
            if any(os.path.exists(path) for path in legacy_candidates):
                return True

    # No credentials found
    return False


def get_room_encryption_warnings(
    rooms: Dict[str, Any], e2ee_status: Dict[str, Any]
) -> List[str]:
    """
    Produce user-facing warnings for encrypted rooms when E2EE is not fully configured.

    Inspects the provided rooms mapping for items with a truthy `encrypted` attribute and, when E2EE is not ready, returns one or two formatted warning lines describing the issue and whether messages will be blocked.

    Parameters:
        rooms (Dict[str, Any]): Mapping of room_id to room object. Room objects should expose an `encrypted` attribute and may provide `display_name`; `room_id` is used as a fallback name.
        e2ee_status (Dict[str, Any]): E2EE status dictionary (as returned by `get_e2ee_status`) — this function reads the `overall_status` key to determine warning text.

    Returns:
        List[str]: Formatted warning lines. Returns an empty list if E2EE is ready, there are no encrypted rooms, or the `rooms` input is invalid.
    """
    warnings: List[str] = []

    if e2ee_status["overall_status"] == "ready":
        # No warnings needed when E2EE is fully ready
        return warnings

    # Check for encrypted rooms
    encrypted_rooms = []

    # Handle invalid rooms input
    if not rooms or not hasattr(rooms, "items"):
        return warnings

    for room_id, room in rooms.items():
        if getattr(room, "encrypted", False):
            room_name = getattr(room, "display_name", room_id)
            encrypted_rooms.append(room_name)

    if encrypted_rooms:
        overall = e2ee_status["overall_status"]
        if overall == "unavailable":
            warnings.append(
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but {MSG_E2EE_WINDOWS_UNSUPPORTED}"
            )
        elif overall == "disabled":
            warnings.append(
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but {MSG_E2EE_DISABLED}"
            )
        else:
            warnings.append(
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but {_E2EE_INCOMPLETE_STATUS}"
            )

        # Tail message depends on readiness
        if overall == "incomplete":
            warnings.append("   Messages to encrypted rooms may be blocked")
        else:
            warnings.append("   Messages to encrypted rooms will be blocked")

    return warnings


def format_room_list(rooms: Dict[str, Any], e2ee_status: Dict[str, Any]) -> List[str]:
    """
    Format a list of human-readable room lines with encryption indicators and status-specific warnings.

    Given a mapping of room_id -> room-like objects, produce one display string per room:
    - If E2EE overall_status == "ready": encrypted rooms are marked "🔒 {name} - Encrypted"; non-encrypted rooms are "✅ {name}".
    - If not ready: encrypted rooms are prefixed with "⚠️" and include a short reason derived from overall_status ("unavailable" -> not supported on Windows, "disabled" -> disabled in config, otherwise "incomplete"); non-encrypted rooms remain "✅ {name}".

    Parameters:
        rooms: Mapping of room_id to a room-like object. Each room may have attributes:
            - display_name (str): human-friendly name (fallback: room_id)
            - encrypted (bool): whether the room is encrypted (default: False)
        e2ee_status: E2EE status dictionary (as returned by get_e2ee_status()). Only e2ee_status["overall_status"] is used.

    Returns:
        List[str]: One formatted line per room suitable for user display.
    """
    room_lines: List[str] = []

    # Handle invalid rooms input
    if not rooms or not hasattr(rooms, "items"):
        return room_lines

    for room_id, room in rooms.items():
        room_name = getattr(room, "display_name", room_id)
        encrypted = getattr(room, "encrypted", False)

        if e2ee_status["overall_status"] == "ready":
            # Show detailed status when E2EE is fully ready
            if encrypted:
                room_lines.append(f"   🔒 {room_name} - Encrypted")
            else:
                room_lines.append(f"   ✅ {room_name}")
        else:
            # Show warnings for encrypted rooms when E2EE is not ready
            if encrypted:
                if e2ee_status["overall_status"] == "unavailable":
                    room_lines.append(
                        f"   ⚠️ {room_name} - Encrypted ({MSG_E2EE_WINDOWS_UNSUPPORTED_SHORT} - messages will be blocked)"
                    )
                elif e2ee_status["overall_status"] == "disabled":
                    room_lines.append(
                        f"   ⚠️ {room_name} - Encrypted ({MSG_E2EE_DISABLED_SHORT} - messages will be blocked)"
                    )
                else:
                    room_lines.append(
                        f"   ⚠️ {room_name} - Encrypted ({_E2EE_INCOMPLETE_STATUS} - messages may be blocked)"
                    )
            else:
                room_lines.append(f"   ✅ {room_name}")

    return room_lines


# Standard warning message templates
def get_e2ee_warning_messages() -> dict[str, str]:
    """
    Provide standard user-facing E2EE warning messages.

    Returns:
        dict[str, str]: Mapping of status keys to ready-to-display messages. Keys include
            "unavailable", "disabled", "incomplete", "missing_deps", "missing_auth",
            and "missing_config".
    """
    matrix_capabilities = get_matrix_capabilities()
    return {
        "unavailable": f"{MSG_E2EE_WINDOWS_UNSUPPORTED} - messages to encrypted rooms will be blocked",
        "disabled": f"{MSG_E2EE_DISABLED} - messages to encrypted rooms will be blocked",
        "incomplete": f"{_E2EE_INCOMPLETE_STATUS} - messages to encrypted rooms may be blocked",
        "missing_deps": format_e2ee_unavailable_message(matrix_capabilities),
        "missing_auth": f"{MSG_E2EE_NO_AUTH} - run: {get_command('auth_login')}",
        "missing_config": "E2EE not enabled in configuration - add 'e2ee: enabled: true' under matrix section",
    }


def get_e2ee_error_message(e2ee_status: Dict[str, Any]) -> str:
    """
    Selects one actionable E2EE warning or instruction based on the provided E2EE status.

    If the status indicates "ready", returns an empty string. Otherwise chooses a single message in priority order for the first failing condition: platform unsupported, E2EE disabled in config, missing E2EE dependencies, missing Matrix credentials, or general incomplete setup.

    Parameters:
        e2ee_status (dict): Status dictionary produced by get_e2ee_status(); expected keys used are
            "overall_status", "platform_supported", "enabled", "dependencies_installed", and
            "credentials_available".

    Returns:
        str: The chosen warning or instruction message, or an empty string when no action is required.
    """
    if e2ee_status.get("overall_status") == "ready":
        return ""  # No error

    # Get current warning messages
    warning_messages = get_e2ee_warning_messages()

    # Build error message based on specific issues
    if not e2ee_status.get("platform_supported", True):
        return warning_messages["unavailable"]
    elif not e2ee_status.get("enabled", False):
        return warning_messages["disabled"]
    elif not e2ee_status.get("dependencies_installed", False):
        return warning_messages["missing_deps"]
    elif not e2ee_status.get("credentials_available", False):
        return warning_messages["missing_auth"]
    else:
        return warning_messages["incomplete"]


def get_e2ee_fix_instructions(e2ee_status: E2EEStatus) -> List[str]:
    """
    Provide ordered, user-facing instructions to resolve E2EE setup issues.

    When E2EE is ready, returns a single confirmation line. If the platform is unsupported, returns concise platform guidance. Otherwise returns a numbered sequence of actionable steps to install dependencies, provision Matrix credentials, enable E2EE in configuration, and verify the setup; related commands and config snippets are returned as additional indented lines.

    Parameters:
        e2ee_status (E2EEStatus): Status mapping used to select which steps to include. The function reads these keys:
            - "overall_status"
            - "platform_supported"
            - "dependencies_installed"
            - "credentials_available"
            - "enabled"

    Returns:
        List[str]: Ordered, human-readable instruction lines. Each step is a separate string; indented strings contain commands or configuration snippets.
    """
    if e2ee_status["overall_status"] == "ready":
        return ["✅ E2EE is fully configured and ready"]

    instructions = []

    if not e2ee_status["platform_supported"]:
        instructions.append(MSG_E2EE_WINDOWS_UNSUPPORTED)
        instructions.append(MSG_E2EE_WINDOWS_UNSUPPORTED_DETAIL)
        return instructions

    step = 1
    if not e2ee_status["dependencies_installed"]:
        instructions.append(f"{step}. Install E2EE dependencies:")
        instructions.append(f"   {format_e2ee_install_command()}")
        step += 1

    if not e2ee_status["credentials_available"]:
        instructions.append(f"{step}. Set up Matrix authentication:")
        instructions.append(f"   {get_command('auth_login')}")
        step += 1

    if not e2ee_status["enabled"]:
        instructions.append(f"{step}. Enable E2EE in configuration:")
        instructions.append("   Edit config.yaml and add under matrix section:")
        instructions.append("   e2ee:")
        instructions.append("     enabled: true")
        step += 1

    instructions.append(f"{step}. Verify configuration:")
    instructions.append(f"   {get_command('check_config')}")

    return instructions
