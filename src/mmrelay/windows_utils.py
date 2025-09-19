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
    return os.name == "nt" or sys.platform == WINDOWS_PLATFORM


def setup_windows_console() -> None:
    """
    Configure the Windows console for UTF-8 output and ANSI (VT100) color support.
    
    On Windows, attempts to reconfigure sys.stdout/sys.stderr to UTF-8 and enable
    virtual terminal processing so ANSI color and Unicode output render correctly.
    This is a best-effort operation: it is a no-op on non-Windows platforms and
    any failures (older Windows, restricted environments) are silently ignored.
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
        ENABLE_VTP = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        for handle in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            h = kernel32.GetStdHandle(handle)
            if h is not None and h != -1:
                mode = ctypes.c_uint()
                if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                    kernel32.SetConsoleMode(h, mode.value | ENABLE_VTP)
    except (OSError, AttributeError):
        # If console setup fails, continue without it
        # This is expected on non-Windows systems or older Windows versions
        return


def get_windows_error_message(error: Exception) -> str:
    """
    Return a Windows-tailored, user-friendly message for common filesystem and network exceptions.
    
    If not running on Windows this returns str(error) unchanged. For Windows, the function recognizes
    permission errors (PermissionError or OSError with EACCES/EPERM), missing-file errors
    (FileNotFoundError or OSError with ENOENT), and common network-related OSErrors
    (EHOSTUNREACH, ENETDOWN, ENETUNREACH, ECONNREFUSED, ETIMEDOUT) and returns guidance specific
    to each case (possible causes and suggested next steps). For other exceptions it returns
    str(error).
    
    Parameters:
        error (Exception): The exception to translate into a user-facing message.
    
    Returns:
        str: A user-oriented error message; either a Windows-specific guidance string or the
        original exception string for unhandled cases or non-Windows platforms.
    """
    if not is_windows():
        return str(error)

    import errno as _errno

    # Use exception types and errno codes for more robust error detection
    if isinstance(error, PermissionError) or (
        isinstance(error, OSError) and error.errno in {_errno.EACCES, _errno.EPERM}
    ):
        return (
            f"Permission denied: {error}\n"
            "This may be caused by:\n"
            "• Antivirus software blocking the operation\n"
            "• Windows User Account Control (UAC) restrictions\n"
            "• File being used by another process\n"
            "Try running as administrator or check antivirus settings."
        )
    elif isinstance(error, FileNotFoundError) or (
        isinstance(error, OSError) and error.errno in {_errno.ENOENT}
    ):
        return (
            f"File not found: {error}\n"
            "This may be caused by:\n"
            "• Incorrect file path (check for spaces or special characters)\n"
            "• File moved or deleted by antivirus software\n"
            "• Network drive disconnection\n"
            "Verify the file path and check antivirus quarantine."
        )
    elif isinstance(error, ConnectionError) or (
        isinstance(error, OSError)
        and error.errno
        in {
            _errno.EHOSTUNREACH,
            _errno.ENETDOWN,
            _errno.ENETUNREACH,
            _errno.ECONNREFUSED,
            _errno.ETIMEDOUT,
        }
    ):
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
    Return a formatted warning string if common Windows environment issues are detected, otherwise None.
    
    Checks:
    - Python version (recommends Python 3.9+ on Windows).
    - Whether the process appears to be running inside a virtual environment (venv/pipx).
    - Current working directory path length (warns if > 200 characters).
    
    Returns:
        Optional[str]: A multi-line, human-readable warning message when one or more checks fail;
        None if no issues are found or the platform is not Windows.
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


def test_config_generation_windows(args=None) -> dict:
    """
    Run Windows-only diagnostics for MMRelay configuration generation.
    
    Performs four checks and returns a dictionary with per-test results and an overall status:
    - sample_config_path: verifies mmrelay.tools.get_sample_config_path() exists.
    - importlib_resources: attempts to read mmrelay.tools/sample_config.yaml via importlib.resources.
    - config_paths: calls mmrelay.config.get_config_paths(args) and reports the returned paths.
    - directory_creation: ensures parent directories for the config paths exist (creates them if missing).
    
    Parameters:
        args (optional): Forwarded to mmrelay.config.get_config_paths; typically CLI-style arguments or None.
    
    Returns:
        dict: Diagnostic results with these keys:
            - sample_config_path, importlib_resources, config_paths, directory_creation:
                dict objects with "status" ("ok" or "error") and "details" (string).
            - overall_status: one of "ok" (no errors), "partial" (1–2 errors), or "error" (3+ errors).
        If called on a non-Windows platform, returns {"error": "This function is only for Windows systems"}.
    
    Notes:
        - The function does not raise on expected failures; errors are reported in the returned dict.
        - Directory creation test may create directories on disk when missing.
    """
    if not is_windows():
        return {"error": "This function is only for Windows systems"}

    results = {
        "sample_config_path": {"status": "unknown", "details": ""},
        "importlib_resources": {"status": "unknown", "details": ""},
        "config_paths": {"status": "unknown", "details": ""},
        "directory_creation": {"status": "unknown", "details": ""},
        "overall_status": "unknown",
    }

    try:
        # Test 1: Sample config path
        try:
            from mmrelay.tools import get_sample_config_path

            sample_path = get_sample_config_path()
            if os.path.exists(sample_path):
                results["sample_config_path"] = {
                    "status": "ok",
                    "details": f"Found at: {sample_path}",
                }
            else:
                results["sample_config_path"] = {
                    "status": "error",
                    "details": f"Not found at: {sample_path}",
                }
        except (
            ImportError,
            OSError,
            FileNotFoundError,
            AttributeError,
            TypeError,
        ) as e:
            results["sample_config_path"] = {"status": "error", "details": str(e)}

        # Test 2: importlib.resources fallback
        try:
            import importlib.resources

            content = (
                importlib.resources.files("mmrelay.tools")
                .joinpath("sample_config.yaml")
                .read_text()
            )
            results["importlib_resources"] = {
                "status": "ok",
                "details": f"Content length: {len(content)} chars",
            }
        except (ImportError, OSError, FileNotFoundError) as e:
            results["importlib_resources"] = {"status": "error", "details": str(e)}

        # Test 3: Config paths
        try:
            from mmrelay.config import get_config_paths

            paths = get_config_paths(args)
            results["config_paths"] = {"status": "ok", "details": f"Paths: {paths}"}
        except (ImportError, OSError) as e:
            results["config_paths"] = {"status": "error", "details": str(e)}

        # Test 4: Directory creation
        try:
            from mmrelay.config import get_config_paths

            paths = get_config_paths(args)
            created_dirs = []
            for path in paths:
                dir_path = os.path.dirname(path)
                if not os.path.exists(dir_path):
                    os.makedirs(dir_path, exist_ok=True)
                    created_dirs.append(dir_path)
            results["directory_creation"] = {
                "status": "ok",
                "details": f"Created: {created_dirs}",
            }
        except OSError as e:
            results["directory_creation"] = {"status": "error", "details": str(e)}

        # Determine overall status
        error_count = sum(
            1
            for r in results.values()
            if isinstance(r, dict) and r.get("status") == "error"
        )
        if error_count == 0:
            results["overall_status"] = "ok"
        elif error_count < 3:  # If at least one fallback works
            results["overall_status"] = "partial"
        else:
            results["overall_status"] = "error"

    except OSError as e:
        results["overall_status"] = "error"
        results["error"] = str(e)

    return results


def get_windows_install_guidance() -> str:
    """
    Return a Windows-specific installation and troubleshooting guide as a formatted string.
    
    The returned text contains recommended installation methods, common Windows-specific problems with actionable remedies, and troubleshooting tips for configuration and runtime issues. It is safe to display directly to end users or include in logs/help output.
    
    Returns:
        str: Multiline guidance text suited for Windows users.
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
   Alternative: Use 'python -m mmrelay' instead of 'mmrelay'

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

6. Config generation fails
   Solution: Check if sample_config.yaml is accessible
   Alternative: Manually create config file from documentation

🆘 Need Help?
   • Check Windows Event Viewer for detailed error logs
   • Temporarily disable antivirus for testing
   • Use Windows PowerShell instead of Command Prompt
   • Consider using Windows Subsystem for Linux (WSL)
   • Test config generation: 'python -m mmrelay config diagnose'
"""
