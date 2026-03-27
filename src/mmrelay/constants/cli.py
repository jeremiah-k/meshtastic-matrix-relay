"""
CLI command and deprecation constants.

Contains the registry of CLI commands and deprecation mappings for error messages,
help text, and documentation. This provides a single source of truth for CLI command syntax.
"""

from typing import Final

# Forbidden system directories for home path validation (Unix)
FORBIDDEN_HOME_DIRECTORIES_UNIX: Final[frozenset[str]] = frozenset(
    {
        "/",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/boot",
        "/dev",
        "/proc",
        "/sys",
    }
)

# Windows forbidden environment keys for home detection
WINDOWS_FORBIDDEN_HOME_ENV_KEYS: Final[tuple[str, ...]] = (
    "SystemRoot",
    "ProgramFiles",
    "ProgramFiles(x86)",
)

# Exit codes
EXIT_CODE_SUCCESS: Final[int] = 0
EXIT_CODE_ERROR: Final[int] = 1
EXIT_CODE_SIGINT: Final[int] = 130

# Windows path display sentinel
WINDOWS_PATH_NOT_APPLICABLE_LABEL: Final[str] = "N/A (Windows)"

# Command registry - single source of truth for CLI command syntax
CLI_COMMANDS = {
    # Config commands
    "generate_config": "mmrelay config generate",
    "check_config": "mmrelay config check",
    # Auth commands
    "auth_login": "mmrelay auth login",
    "auth_status": "mmrelay auth status",
    # Service commands
    "service_install": "mmrelay service install",
    # Main commands
    "start_relay": "mmrelay",
    "show_version": "mmrelay --version",
    "show_help": "mmrelay --help",
}

# Deprecation mappings - maps old flags to new command keys
DEPRECATED_COMMANDS = {
    "--generate-config": "generate_config",
    "--check-config": "check_config",
    "--install-service": "service_install",
    "--auth": "auth_login",
}
