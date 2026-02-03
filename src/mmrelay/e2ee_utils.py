"""
Centralized E2EE (End-to-End Encryption) utilities for consistent status detection and messaging.

This module provides a unified approach to E2EE status detection, warning messages, and room
formatting across all components of the meshtastic-matrix-relay application.
"""

import importlib
import os
import sys
from typing import Any, Dict, List, Literal, Optional, TypedDict

from mmrelay.cli_utils import get_command
from mmrelay.constants.app import (
    CREDENTIALS_FILENAME,
    PACKAGE_NAME_E2E,
    PYTHON_OLM_PACKAGE,
    WINDOWS_PLATFORM,
)


class E2EEStatus(TypedDict):
    """Type definition for E2EE status dictionary."""

    enabled: bool
    available: bool
    configured: bool
    platform_supported: bool
    dependencies_installed: bool
    credentials_available: bool
    overall_status: Literal["ready", "disabled", "unavailable", "incomplete", "unknown"]
    issues: List[str]


def get_e2ee_status(
    config: Dict[str, Any], config_path: Optional[str] = None
) -> E2EEStatus:
    """
    Consolidates E2EE readiness information by inspecting the runtime platform, required crypto dependencies, configuration, and presence of Matrix credentials.

    Parameters:
        config (Dict[str, Any]): Parsed application configuration; used to read `matrix.e2ee.enabled` and legacy `matrix.encryption.enabled`.
        config_path (Optional[str]): Optional path to the configuration file; when provided the directory containing this path is checked first for `credentials.json`, otherwise the application's base directory is used.

    Returns:
        E2EEStatus: A dict with the following keys:
          - enabled (bool): E2EE enabled in configuration.
          - available (bool): Platform and dependencies allow E2EE.
          - configured (bool): Authentication/credentials are present.
          - platform_supported (bool): True unless running on Windows/msys/cygwin.
          - dependencies_installed (bool): True if required olm/nio components are importable.
          - credentials_available (bool): True if `credentials.json` is discovered.
          - overall_status (str): One of "ready", "disabled", "unavailable", "incomplete", or "unknown".
          - issues (List[str]): Human-readable issues found that prevent full E2EE readiness.
    """
    status: E2EEStatus = {
        "enabled": False,
        "available": False,
        "configured": False,
        "platform_supported": True,
        "dependencies_installed": False,
        "credentials_available": False,
        "overall_status": "unknown",
        "issues": [],
    }

    # Check platform support
    if sys.platform == WINDOWS_PLATFORM or sys.platform.startswith(("msys", "cygwin")):
        status["platform_supported"] = False
        status["issues"].append("E2EE is not supported on Windows")

    # Check dependencies
    try:
        importlib.import_module("olm")

        nio_crypto = importlib.import_module("nio.crypto")
        if not hasattr(nio_crypto, "OlmDevice"):
            raise ImportError("nio.crypto.OlmDevice is unavailable")

        nio_store = importlib.import_module("nio.store")
        if not hasattr(nio_store, "SqliteStore"):
            raise ImportError("nio.store.SqliteStore is unavailable")

        status["dependencies_installed"] = True
    except ImportError:
        status["dependencies_installed"] = False
        status["issues"].append(
            f"E2EE dependencies not installed ({PYTHON_OLM_PACKAGE})"
        )

    # Check configuration
    matrix_section = config.get("matrix", {})
    e2ee_config = matrix_section.get("e2ee", {})
    encryption_config = matrix_section.get("encryption", {})  # Legacy support
    status["enabled"] = e2ee_config.get("enabled", False) or encryption_config.get(
        "enabled", False
    )

    if not status["enabled"]:
        status["issues"].append("E2EE is disabled in configuration")

    # Check credentials
    if config_path:
        status["credentials_available"] = _check_credentials_available(config_path)
    else:
        # Check HOME location and legacy sources
        from mmrelay.paths import resolve_all_paths

        # Check primary credentials location (HOME)
        paths_info = resolve_all_paths()
        primary_credentials_path = paths_info["credentials_path"]
        status["credentials_available"] = os.path.exists(primary_credentials_path)

        # If not found in HOME, search legacy locations
        if not status["credentials_available"]:
            for legacy_root in paths_info.get("legacy_sources", []):
                legacy_credentials_path = os.path.join(
                    legacy_root, CREDENTIALS_FILENAME
                )
                if os.path.exists(legacy_credentials_path):
                    status["credentials_available"] = True
                    break

    if not status["credentials_available"]:
        status["issues"].append("Matrix authentication not configured")

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

    return status


def _check_credentials_available(config_path: str) -> bool:
    """
    Check whether Matrix credentials file exists in standard locations.

    Searches for CREDENTIALS_FILENAME in HOME directory and config directory.
    Checks legacy_sources for compatibility during deprecation window.

    Parameters:
        config_path (str): Filesystem path to configuration file whose directory should be checked.

    Returns:
        bool: True if credentials file exists in HOME or config directory; otherwise False.
    """
    # Check config directory first
    config_dir = os.path.dirname(config_path)
    config_credentials_path = os.path.join(config_dir, CREDENTIALS_FILENAME)

    if os.path.exists(config_credentials_path):
        return True

    # Check HOME location (primary)
    from mmrelay.paths import resolve_all_paths

    paths_info = resolve_all_paths()
    primary_credentials_path = paths_info["credentials_path"]

    if os.path.exists(primary_credentials_path):
        return True

    # Check legacy sources during deprecation window
    from mmrelay.paths import is_deprecation_window_active

    if is_deprecation_window_active():
        for legacy_root in paths_info.get("legacy_sources", []):
            legacy_credentials_path = os.path.join(legacy_root, CREDENTIALS_FILENAME)
            if os.path.exists(legacy_credentials_path):
                return True

    # No credentials found
    return False


def get_room_encryption_warnings(
    rooms: Dict[str, Any], e2ee_status: Dict[str, Any]
) -> List[str]:
    """
    Return user-facing warnings for encrypted rooms when E2EE is not fully ready.

    If the provided E2EE status has overall_status == "ready", returns an empty list.
    Scans the given rooms mapping for items whose `encrypted` attribute is truthy and
    produces one or two warning lines per situation:
    - A line noting how many encrypted rooms were detected and the reason (platform unsupported,
      disabled, or incomplete).
    - A follow-up line indicating whether messages to those rooms will be blocked or may be blocked.

    Parameters:
        rooms: Mapping of room_id -> room object. Room objects are expected to expose
            an `encrypted` attribute and optionally a `display_name` attribute; room_id is
            used as a fallback name.
        e2ee_status: E2EE status dictionary as returned by get_e2ee_status(); this function
            reads the `overall_status` key to decide warning text.

    Returns:
        List[str]: Formatted warning lines (empty if no relevant warnings).
    """
    warnings: list[str] = []

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
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but E2EE is not supported on Windows"
            )
        elif overall == "disabled":
            warnings.append(
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but E2EE is disabled"
            )
        else:
            warnings.append(
                f"⚠️ {len(encrypted_rooms)} encrypted room(s) detected but E2EE setup is incomplete"
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
    room_lines: list[str] = []

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
                        f"   ⚠️ {room_name} - Encrypted (E2EE not supported on Windows - messages will be blocked)"
                    )
                elif e2ee_status["overall_status"] == "disabled":
                    room_lines.append(
                        f"   ⚠️ {room_name} - Encrypted (E2EE disabled - messages will be blocked)"
                    )
                else:
                    room_lines.append(
                        f"   ⚠️ {room_name} - Encrypted (E2EE incomplete - messages may be blocked)"
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
    return {
        "unavailable": "E2EE is not supported on Windows - messages to encrypted rooms will be blocked",
        "disabled": "E2EE is disabled in configuration - messages to encrypted rooms will be blocked",
        "incomplete": "E2EE setup is incomplete - messages to encrypted rooms may be blocked",
        "missing_deps": f"E2EE dependencies not installed - run: pipx install {PACKAGE_NAME_E2E}",
        "missing_auth": f"Matrix authentication not configured - run: {get_command('auth_login')}",
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
        instructions.append("❌ E2EE is not supported on Windows")
        instructions.append("   Use Linux or macOS for E2EE support")
        return instructions

    step = 1
    if not e2ee_status["dependencies_installed"]:
        instructions.append(f"{step}. Install E2EE dependencies:")
        instructions.append(f"   pipx install {PACKAGE_NAME_E2E}")
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
