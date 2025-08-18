"""
Centralized E2EE (End-to-End Encryption) utilities for consistent status detection and messaging.

This module provides a unified approach to E2EE status detection, warning messages, and room
formatting across all components of the meshtastic-matrix-relay application.
"""

import os
import sys
from typing import Dict, Any, Optional, Tuple, List


# Platform constants
WINDOWS_PLATFORM = "win32"


def get_e2ee_status(config: Dict[str, Any], config_path: Optional[str] = None) -> Dict[str, Any]:
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
    status = {
        "enabled": False,
        "available": False,
        "configured": False,
        "platform_supported": True,
        "dependencies_installed": False,
        "credentials_available": False,
        "overall_status": "unknown",
        "issues": []
    }
    
    # Check platform support
    if sys.platform == WINDOWS_PLATFORM:
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
        status["issues"].append("E2EE dependencies not installed (python-olm)")
    
    # Check configuration
    matrix_section = config.get("matrix", {})
    e2ee_config = matrix_section.get("e2ee", {})
    encryption_config = matrix_section.get("encryption", {})  # Legacy support
    status["enabled"] = e2ee_config.get("enabled", False) or encryption_config.get("enabled", False)
    
    if not status["enabled"]:
        status["issues"].append("E2EE is disabled in configuration")
    
    # Check credentials
    if config_path:
        status["credentials_available"] = _check_credentials_available(config_path)
    else:
        # Fallback to base directory check only
        from mmrelay.config import get_base_dir
        base_credentials_path = os.path.join(get_base_dir(), "credentials.json")
        status["credentials_available"] = os.path.exists(base_credentials_path)
    
    if not status["credentials_available"]:
        status["issues"].append("Matrix authentication not configured")
    
    # Determine overall availability and status
    status["available"] = status["platform_supported"] and status["dependencies_installed"]
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
    config_credentials_path = os.path.join(config_dir, "credentials.json")
    
    if os.path.exists(config_credentials_path):
        return True
    
    # Fallback to base directory
    try:
        from mmrelay.config import get_base_dir
        base_credentials_path = os.path.join(get_base_dir(), "credentials.json")
        return os.path.exists(base_credentials_path)
    except (ImportError, OSError):
        # If we can't determine base directory, assume no credentials
        return False


def get_room_encryption_warnings(rooms: Dict[str, Any], e2ee_status: Dict[str, Any]) -> List[str]:
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
        if getattr(room, 'encrypted', False):
            room_name = getattr(room, 'display_name', room_id)
            encrypted_rooms.append(room_name)
    
    if encrypted_rooms:
        if e2ee_status["overall_status"] == "unavailable":
            warnings.append(f"⚠️  {len(encrypted_rooms)} encrypted room(s) detected but E2EE is unavailable on Windows")
        elif e2ee_status["overall_status"] == "disabled":
            warnings.append(f"⚠️  {len(encrypted_rooms)} encrypted room(s) detected but E2EE is disabled")
        else:
            warnings.append(f"⚠️  {len(encrypted_rooms)} encrypted room(s) detected but E2EE setup is incomplete")
        
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
        room_name = getattr(room, 'display_name', room_id)
        encrypted = getattr(room, 'encrypted', False)
        
        if e2ee_status["overall_status"] == "ready":
            # Show detailed status when E2EE is fully ready
            if encrypted:
                room_lines.append(f"   🔒 {room_name} - Encrypted")
            else:
                room_lines.append(f"   📝 {room_name} - Plaintext")
        else:
            # Show warnings for encrypted rooms when E2EE is not ready
            if encrypted:
                if e2ee_status["overall_status"] == "unavailable":
                    room_lines.append(f"   ⚠️ {room_name} - Encrypted (E2EE unavailable on Windows)")
                elif e2ee_status["overall_status"] == "disabled":
                    room_lines.append(f"   ⚠️ {room_name} - Encrypted (E2EE disabled - messages will be blocked)")
                else:
                    room_lines.append(f"   ⚠️ {room_name} - Encrypted (E2EE incomplete - messages will be blocked)")
            else:
                room_lines.append(f"   📝 {room_name}")
    
    return room_lines


# Standard warning message templates
E2EE_WARNING_MESSAGES = {
    "unavailable": "E2EE is not supported on Windows - encrypted rooms cannot receive messages",
    "disabled": "E2EE is disabled in configuration - messages to encrypted rooms will be blocked",
    "incomplete": "E2EE setup is incomplete - messages to encrypted rooms may be blocked",
    "missing_deps": "E2EE dependencies not installed - run: pip install mmrelay[e2e]",
    "missing_auth": "Matrix authentication not configured - run: mmrelay auth login",
    "missing_config": "E2EE not enabled in configuration - add 'e2ee: enabled: true' under matrix section"
}


def get_e2ee_error_message(e2ee_status: Dict[str, Any]) -> str:
    """
    Get appropriate error message for E2EE issues.
    
    Args:
        e2ee_status: E2EE status from get_e2ee_status()
        
    Returns:
        Formatted error message explaining the issue and how to fix it
    """
    if e2ee_status["overall_status"] == "ready":
        return ""  # No error
    
    # Build error message based on specific issues
    if not e2ee_status["platform_supported"]:
        return E2EE_WARNING_MESSAGES["unavailable"]
    elif not e2ee_status["enabled"]:
        return E2EE_WARNING_MESSAGES["disabled"]
    elif not e2ee_status["dependencies_installed"]:
        return E2EE_WARNING_MESSAGES["missing_deps"]
    elif not e2ee_status["credentials_available"]:
        return E2EE_WARNING_MESSAGES["missing_auth"]
    else:
        return E2EE_WARNING_MESSAGES["incomplete"]


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
    
    if not e2ee_status["dependencies_installed"]:
        instructions.append("1. Install E2EE dependencies:")
        instructions.append("   pip install mmrelay[e2e]")
    
    if not e2ee_status["credentials_available"]:
        instructions.append("2. Set up Matrix authentication:")
        instructions.append("   mmrelay auth login")
    
    if not e2ee_status["enabled"]:
        instructions.append("3. Enable E2EE in configuration:")
        instructions.append("   Edit config.yaml and add under matrix section:")
        instructions.append("   e2ee:")
        instructions.append("     enabled: true")
    
    instructions.append("4. Verify configuration:")
    instructions.append("   mmrelay config check")
    
    return instructions
