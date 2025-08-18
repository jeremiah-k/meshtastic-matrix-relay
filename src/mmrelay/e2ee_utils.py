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
    Get comprehensive E2EE status information.

    Analyzes the current environment, configuration, and dependencies to determine
    the complete E2EE status. This is the single source of truth for E2EE capabilities.

    Args:
        config: Parsed configuration dictionary
        config_path: Path to config file (used for credential detection)

    Returns:
        Dict containing:
        - enabled: Whether E2EE is enabled in configuration
        - available: Whether E2EE is available (platform + dependencies)
        - configured: Whether authentication is properly set up
        - platform_supported: Whether current platform supports E2EE
        - dependencies_installed: Whether required dependencies are available
        - credentials_available: Whether credentials.json exists
        - overall_status: Summary status (ready/disabled/unavailable/incomplete)
        - issues: List of specific issues preventing E2EE
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
        import olm  # noqa: F401
        from nio.crypto import OlmDevice  # noqa: F401
        from nio.store import SqliteStore  # noqa: F401

        status["dependencies_installed"] = True
    except ImportError:
        status["dependencies_installed"] = False
        status["issues"].append(f"E2EE dependencies not installed ({PYTHON_OLM_PACKAGE})")

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
        # Fallback to base directory check only
        from mmrelay.config import get_base_dir

        base_credentials_path = os.path.join(get_base_dir(), CREDENTIALS_FILENAME)
        status["credentials_available"] = os.path.exists(base_credentials_path)

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
    Check if credentials.json is available in standard locations.

    Checks both the config directory and the base directory for credentials.json,
    following the same pattern as other credential checking functions.

    Args:
        config_path: Path to the configuration file

    Returns:
        True if credentials.json exists in either location
    """
    # Check config directory first
    config_dir = os.path.dirname(config_path)
    config_credentials_path = os.path.join(config_dir, CREDENTIALS_FILENAME)

    if os.path.exists(config_credentials_path):
        return True

    # Fallback to base directory
    try:
        from mmrelay.config import get_base_dir

        base_credentials_path = os.path.join(get_base_dir(), CREDENTIALS_FILENAME)
        return os.path.exists(base_credentials_path)
    except (ImportError, OSError):
        # If we can't determine base directory, assume no credentials
        return False


def get_room_encryption_warnings(
    rooms: Dict[str, Any], e2ee_status: Dict[str, Any]
) -> List[str]:
    """
    Generate warnings for encrypted rooms when E2EE is not properly configured.

    Args:
        rooms: Dictionary of Matrix rooms
        e2ee_status: E2EE status from get_e2ee_status()

    Returns:
        List of warning messages for problematic rooms
    """
    warnings = []

    if e2ee_status["overall_status"] == "ready":
        # No warnings needed when E2EE is fully ready
        return warnings

    # Check for encrypted rooms
    encrypted_rooms = []
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
    Format room list with appropriate encryption status indicators.

    Args:
        rooms: Dictionary of Matrix rooms
        e2ee_status: E2EE status from get_e2ee_status()

    Returns:
        List of formatted room strings
    """
    room_lines = []

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
def get_e2ee_warning_messages():
    """Get E2EE warning messages with current CLI commands."""
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
    Get appropriate error message for E2EE issues.

    Args:
        e2ee_status: E2EE status from get_e2ee_status()

    Returns:
        Formatted error message explaining the issue and how to fix it
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


def get_e2ee_fix_instructions(e2ee_status: Dict[str, Any]) -> List[str]:
    """
    Get step-by-step instructions to fix E2EE issues.

    Args:
        e2ee_status: E2EE status from get_e2ee_status()

    Returns:
        List of instruction strings
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
