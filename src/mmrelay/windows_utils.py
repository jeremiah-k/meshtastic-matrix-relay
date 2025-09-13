"""
Windows-specific utilities for MMRelay.

This module provides Windows-specific functionality and workarounds
for better compatibility and user experience on Windows systems.
"""

import os
import sys
from typing import Optional

from mmrelay.constants.app import WINDOWS_PLATFORM


def is_windows() -> bool:
    """Check if running on Windows."""
    return sys.platform == WINDOWS_PLATFORM


def setup_windows_console() -> None:
    """
    Set up Windows console for better Unicode and color support.

    This function enables UTF-8 output and ANSI color codes on Windows
    when possible, improving the display of log messages and CLI output.
    """
    if not is_windows():
        return

    try:
        # Enable UTF-8 output on Windows
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")

        # Enable ANSI color codes on Windows 10+
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        # If console setup fails, continue without it
        # This is expected on non-Windows systems or older Windows versions
        return


def get_windows_error_message(error: Exception) -> str:
    """
    Get a Windows-specific error message with helpful context.

    Args:
        error: The exception that occurred

    Returns:
        str: A user-friendly error message with Windows-specific guidance
    """
    if not is_windows():
        return str(error)

    error_str = str(error).lower()

    if "access is denied" in error_str or "permission denied" in error_str:
        return (
            f"Permission denied: {error}\n"
            "This may be caused by:\n"
            "• Antivirus software blocking the operation\n"
            "• Windows User Account Control (UAC) restrictions\n"
            "• File being used by another process\n"
            "Try running as administrator or check antivirus settings."
        )
    elif "file not found" in error_str or "no such file" in error_str:
        return (
            f"File not found: {error}\n"
            "This may be caused by:\n"
            "• Incorrect file path (check for spaces or special characters)\n"
            "• File moved or deleted by antivirus software\n"
            "• Network drive disconnection\n"
            "Verify the file path and check antivirus quarantine."
        )
    elif "network" in error_str or "connection" in error_str:
        return (
            f"Network error: {error}\n"
            "This may be caused by:\n"
            "• Windows Firewall blocking the connection\n"
            "• Antivirus software blocking network access\n"
            "• VPN or proxy configuration issues\n"
            "Check firewall settings and antivirus network protection."
        )
    else:
        return str(error)


def check_windows_requirements() -> Optional[str]:
    """
    Check Windows-specific requirements and return any issues found.

    Returns:
        str: Warning message if issues are found, None if everything is OK
    """
    if not is_windows():
        return None

    warnings = []

    # Check Python version for Windows compatibility
    if sys.version_info < (3, 9):
        warnings.append(
            "Python 3.9+ is recommended on Windows for better compatibility"
        )

    # Check if running in a virtual environment
    if not hasattr(sys, "real_prefix") and not (
        hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix
    ):
        warnings.append(
            "Consider using a virtual environment (venv) or pipx for better isolation"
        )

    # Check for common Windows path issues
    if len(os.getcwd()) > 200:
        warnings.append(
            "Current directory path is very long - this may cause issues on Windows"
        )

    if warnings:
        return "Windows compatibility warnings:\n• " + "\n• ".join(warnings)

    return None


def get_windows_install_guidance() -> str:
    """
    Get Windows-specific installation and troubleshooting guidance.

    Returns:
        str: Formatted guidance text for Windows users
    """
    return """
Windows Installation & Troubleshooting Guide:

📦 Recommended Installation:
   pipx install mmrelay
   (pipx provides better isolation and fewer conflicts)

🔧 If pipx is not available:
   pip install --user mmrelay
   (installs to user directory, avoiding system conflicts)

⚠️  Common Windows Issues:

1. "ModuleNotFoundError: No module named 'pkg_resources'"
   Solution: pip install --upgrade setuptools

2. "Access denied" or permission errors
   Solution: Run command prompt as administrator
   Or: Use --user flag with pip

3. "SSL certificate verify failed"
   Solution: Update certificates or use --trusted-host flag

4. Antivirus blocking installation/execution
   Solution: Add Python and pip to antivirus exclusions

5. Long path issues
   Solution: Enable long path support in Windows 10+
   Or: Use shorter installation directory

🆘 Need Help?
   • Check Windows Event Viewer for detailed error logs
   • Temporarily disable antivirus for testing
   • Use Windows PowerShell instead of Command Prompt
   • Consider using Windows Subsystem for Linux (WSL)
"""
