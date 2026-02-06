"""
Command-line interface handling for Meshtastic Matrix Relay.
"""

import argparse
import importlib
import importlib.resources
import ipaddress
import logging
import os
import platform
import re
import shutil
import sys
from collections.abc import Mapping
from typing import Any

import yaml

# Import version from package
from mmrelay import __version__
from mmrelay.cli_utils import (
    get_command,
    get_deprecation_warning,
    msg_for_e2ee_support,
    msg_or_run_auth_login,
    msg_run_auth_login,
    msg_setup_auth,
    msg_setup_authentication,
    msg_suggest_generate_config,
)
from mmrelay.config import (
    apply_env_config_overrides,
    get_config_paths,
    set_secure_file_permissions,
    validate_yaml_syntax,
)
from mmrelay.constants.app import WINDOWS_PLATFORM
from mmrelay.constants.config import (
    CONFIG_KEY_ACCESS_TOKEN,
    CONFIG_KEY_BOT_USER_ID,
    CONFIG_KEY_HOMESERVER,
    CONFIG_SECTION_MATRIX,
    CONFIG_SECTION_MESHTASTIC,
)
from mmrelay.constants.network import (
    CONFIG_KEY_BLE_ADDRESS,
    CONFIG_KEY_CONNECTION_TYPE,
    CONFIG_KEY_HOST,
    CONFIG_KEY_SERIAL_PORT,
    CONNECTION_TYPE_BLE,
    CONNECTION_TYPE_NETWORK,
    CONNECTION_TYPE_SERIAL,
    CONNECTION_TYPE_TCP,
)
from mmrelay.e2ee_utils import E2EEStatus
from mmrelay.log_utils import get_logger
from mmrelay.tools import get_sample_config_path

# Lazy-initialized logger to avoid circular imports and filesystem access during import
_logger: logging.Logger | None = None
logger: logging.Logger | None = None


def _get_logger() -> logging.Logger:
    """
    Return the module-level logger, creating it on first access.

    Returns:
        logging.Logger: The module logger instance.

    Raises:
        RuntimeError: If the logger could not be initialized.
    """
    global _logger
    if _logger is None:
        _logger = get_logger(__name__)
    global logger
    if logger is None:
        logger = _logger
    if _logger is None:
        raise RuntimeError("Logger must be initialized")
    return _logger


# =============================================================================
# CLI Argument Parsing and Command Handling
# =============================================================================


def _apply_dir_overrides(args: argparse.Namespace | None) -> None:
    """
    Apply CLI directory overrides to the application's unified HOME path.

    Checks CLI flags in this priority: --home, --base-dir, then --data-dir. When a valid override is found the function sets the resolved absolute HOME override via the paths subsystem (so all runtime subsystems use the same root), prints deprecation/conflict warnings for legacy flags as appropriate, and ensures the target directory exists.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments; expected to possibly contain `home`, `base_dir`, or `data_dir`. If None or no valid override is present, the function is a no-op.
    """
    if not args:
        return

    def _is_valid_path(value: object) -> bool:
        """
        Check whether the given value is a non-empty string after trimming whitespace.

        Parameters:
            value (object): The value to test.

        Returns:
            bool: `True` if `value` is a string containing at least one non-whitespace character, `False` otherwise.
        """
        return isinstance(value, str) and value.strip() != ""

    # Determine which path to use for HOME override
    home_override = None
    home_source = None

    # Priority 1: --home (recommended flag)
    home_value = getattr(args, "home", None)
    base_value = getattr(args, "base_dir", None)
    data_value = getattr(args, "data_dir", None)

    if _is_valid_path(home_value):
        if _is_valid_path(base_value) or _is_valid_path(data_value):
            print(
                "Warning: --home overrides --base-dir/--data-dir; ignoring legacy flags.",
                file=sys.stderr,
            )
        home_override = home_value
        home_source = "--home"

    # Priority 2: --base-dir (legacy flag)
    elif _is_valid_path(base_value):
        if _is_valid_path(data_value):
            print(
                "Warning: --base-dir overrides --data-dir; ignoring --data-dir.",
                file=sys.stderr,
            )
        home_override = base_value
        home_source = "--base-dir"
        print(
            "Warning: --base-dir is deprecated; use --home instead.",
            file=sys.stderr,
        )

    # Priority 3: --data-dir (most deprecated flag)
    elif _is_valid_path(data_value):
        home_override = data_value
        home_source = "--data-dir"
        print(
            "Warning: --data-dir is deprecated. Use --home instead.",
            file=sys.stderr,
        )

    # If no home override is specified, nothing to do
    if not home_override:
        return

    # Apply the HOME override to the paths module
    import mmrelay.paths

    expanded_home = os.path.expanduser(home_override)
    absolute_home = os.path.abspath(expanded_home)
    mmrelay.paths.set_home_override(absolute_home, source=home_source)
    os.makedirs(absolute_home, exist_ok=True)


def parse_arguments() -> argparse.Namespace:
    """
    Builds and parses the command-line interface for MMRelay, providing modern grouped subcommands and hidden legacy flags.

    Parses global options (e.g., --config, --base-dir/--data-dir, --log-level, --logfile, --version), grouped subcommands (config, auth, service) and several deprecated hidden flags kept for backward compatibility. Unknown arguments are ignored; a warning is printed unless the invocation appears to be a test run.

    Returns:
        argparse.Namespace: Parsed namespace containing the selected command, subcommand, and option values.
    """
    parser = argparse.ArgumentParser(
        description="Meshtastic Matrix Relay - Bridge between Meshtastic and Matrix"
    )
    parser.add_argument("--config", help="Path to config file", default=None)
    parser.add_argument(
        "--home",
        help="Home directory for all data (logs, database, plugins, credentials)",
        default=None,
    )
    parser.add_argument(
        "--base-dir",
        help="Deprecated: use --home instead",
        default=None,
    )
    parser.add_argument(
        "--data-dir",
        help="Deprecated: use --home instead",
        default=None,
    )
    parser.add_argument(
        "--log-level",
        choices=["error", "warning", "info", "debug"],
        help="Set logging level",
        default=None,
    )
    parser.add_argument(
        "--logfile",
        help="Path to log file (can be overridden by --base-dir)",
        default=None,
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    # Deprecated flags (hidden from help but still functional)
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--install-service",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--auth",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # Add grouped subcommands for modern CLI interface
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # PATHS command (top-level)
    subparsers.add_parser(
        "paths",
        help="Show path configuration and diagnostics",
        description="Display all path information for debugging and verification",
    )

    # DOCTOR command (top-level)
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnose path configuration and migration status",
        description="Display comprehensive diagnostic information about HOME, legacy paths, and migration recommendations",
    )
    doctor_parser.add_argument(
        "--migration",
        action="store_true",
        help="Run migration verification checks (read-only)",
    )

    subparsers.add_parser(
        "verify-migration",
        help="Verify migration state and detect legacy data",
        description="Check that MMRELAY_HOME is the single source of runtime data",
    )

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Migrate data from legacy directory structure",
        description="Migrate data from v1.2.x to v1.3 unified layout with safe defaults",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without making changes",
    )
    migrate_parser.add_argument(
        "--move",
        action="store_true",
        help="Use MOVE operation instead of COPY (requires --force or manual confirmation)",
    )
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing files without backup",
    )

    # CONFIG group
    config_parser = subparsers.add_parser(
        "config",
        help="Configuration management",
        description="Manage configuration files and validation",
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", help="Config commands", required=True
    )
    config_subparsers.add_parser(
        "generate",
        help="Create sample config.yaml file",
        description="Generate a sample configuration file with default settings",
    )
    config_subparsers.add_parser(
        "check",
        help="Validate configuration file",
        description="Check configuration file syntax and completeness",
    )
    config_subparsers.add_parser(
        "paths",
        help="Show path configuration and diagnostics",
        description="Display all path information for debugging and verification",
    )
    config_subparsers.add_parser(
        "diagnose",
        help="Run configuration diagnostics",
        description="Run non-destructive configuration diagnostics",
    )

    # AUTH group
    auth_parser = subparsers.add_parser(
        "auth",
        help="Authentication management",
        description="Manage Matrix authentication and credentials",
    )
    auth_subparsers = auth_parser.add_subparsers(
        dest="auth_command", help="Auth commands"
    )
    login_parser = auth_subparsers.add_parser(
        "login",
        help="Authenticate with Matrix",
        description="Set up Matrix authentication for E2EE support",
    )
    login_parser.add_argument(
        "--homeserver",
        help="Matrix homeserver URL (e.g., https://matrix.org). If provided, --username and --password are also required.",
    )
    login_parser.add_argument(
        "--username",
        help="Matrix username (with or without @ and :server). If provided, --homeserver and --password are also required.",
    )
    login_parser.add_argument(
        "--password",
        metavar="PWD",
        help="Matrix password (can be empty). If provided, --homeserver and --username are also required. For security, prefer interactive mode.",
    )

    auth_subparsers.add_parser(
        "status",
        help="Check authentication status",
        description="Display current Matrix authentication status",
    )

    logout_parser = auth_subparsers.add_parser(
        "logout",
        help="Log out and clear all sessions",
        description="Clear all Matrix authentication data and E2EE store",
    )
    logout_parser.add_argument(
        "--password",
        nargs="?",
        const="",
        help="Password for verification. If no value provided, will prompt securely.",
        type=str,
    )
    logout_parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Do not prompt for confirmation (useful for non-interactive environments)",
    )

    # SERVICE group
    service_parser = subparsers.add_parser(
        "service",
        help="Service management",
        description="Manage systemd user service for MMRelay",
    )
    service_subparsers = service_parser.add_subparsers(
        dest="service_command", help="Service commands", required=True
    )
    service_subparsers.add_parser(
        "install",
        help="Install systemd user service",
        description="Install or update systemd user service for MMRelay",
    )
    migrate_parser = service_subparsers.add_parser(
        "migrate",
        help="Migrate data from legacy directory structure",
        description="Migrate data from v1.2.x to v1.3 unified layout with safe defaults",
    )
    migrate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without making changes",
    )
    migrate_parser.add_argument(
        "--move",
        action="store_true",
        help="Use MOVE operation instead of COPY (requires --force or manual confirmation)",
    )
    migrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting existing files without backup",
    )

    # Use parse_known_args to handle unknown arguments gracefully (e.g., pytest args)
    args, unknown = parser.parse_known_args()
    # If there are unknown arguments and we're not in a test invocation, warn about them
    # Heuristic: suppress warning when pytest appears in argv (unit tests may pass extra args)
    if unknown and not any("pytest" in arg or "py.test" in arg for arg in sys.argv):
        print(f"Warning: Unknown arguments ignored: {unknown}", file=sys.stderr)

    return args


def get_version() -> str:
    """
    Get the application's current version string.

    Returns:
        str: The application's version string.
    """
    return __version__


def print_version() -> None:
    """
    Print the current MMRelay package version to standard output.
    """
    print(f"MMRelay version {__version__}")


def _e2ee_dependencies_available() -> bool:
    """
    Check whether required E2EE runtime dependencies are importable.

    Checks for the presence of the `olm` package and the `OlmDevice` and `SqliteStore`
    symbols in the `nio.crypto` and `nio.store` modules respectively.

    Returns:
        True if all required dependencies and symbols are importable, False otherwise.
    """
    try:
        # import_module raises ImportError on failure; no None checks needed.
        importlib.import_module("olm")

        nio_crypto = importlib.import_module("nio.crypto")
        if not hasattr(nio_crypto, "OlmDevice"):
            raise ImportError("nio.crypto.OlmDevice is unavailable")

        nio_store = importlib.import_module("nio.store")
        if not hasattr(nio_store, "SqliteStore"):
            raise ImportError("nio.store.SqliteStore is unavailable")

        return True
    except ImportError:
        return False


def _validate_e2ee_dependencies() -> bool:
    """
    Determine whether E2EE is supported on this platform and the required libraries are available.

    Performs only local checks (platform and importability) and prints user-facing messages when
    E2EE is not supported or dependencies are missing.

    Returns:
        `True` if the current platform supports E2EE and the required E2EE libraries can be imported, `False` otherwise.
    """
    if sys.platform == WINDOWS_PLATFORM:
        print("❌ Error: E2EE is not supported on Windows")
        print("   Reason: python-olm library requires native C libraries")
        print("   Solution: Use Linux or macOS for E2EE support")
        return False

    # Check if E2EE dependencies are available
    if _e2ee_dependencies_available():
        print("✅ E2EE dependencies are installed")
        return True

    print("❌ Error: E2EE dependencies not installed")
    print("   End-to-end encryption features require additional dependencies")
    print("   Install E2EE support: pipx install 'mmrelay[e2e]'")
    return False


def _validate_credentials_json(
    config_path: str, config: Mapping[str, Any] | None = None
) -> bool:
    """
    Validate a Matrix credentials.json located relative to the given configuration.

    Searches for a credentials.json file (honoring an explicit credentials_path in `config` when present) and verifies it contains non-empty string values for "homeserver", "access_token", "user_id", and "device_id". On validation failure this function prints concise, user-facing error messages and guidance to run the authentication login flow.

    Parameters:
        config_path (str): Path to the configuration file used to locate credentials.json.
        config (Mapping[str, Any] | None): Parsed configuration to honor an explicit credentials_path, if provided.

    Returns:
        bool: `True` if a valid credentials.json was found with the required non-empty fields; `False` otherwise.
    """
    import json

    try:
        # Look for credentials.json using helper function
        credentials_path = _find_credentials_json_path(config_path, config)
        if not credentials_path:
            return False

        # Load and validate credentials
        with open(credentials_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)
        if not isinstance(credentials, dict):
            print(
                "❌ Error: credentials.json must be a JSON object",
                file=sys.stderr,
            )
            print(f"   {msg_run_auth_login()}", file=sys.stderr)
            return False

        # Check for required fields
        required_fields = ["homeserver", "access_token", "user_id", "device_id"]
        missing_fields = [
            field
            for field in required_fields
            if not _is_valid_non_empty_string((credentials or {}).get(field))
        ]

        if missing_fields:
            print(
                f"❌ Error: credentials.json missing required fields: {', '.join(missing_fields)}"
            )
            print(f"   {msg_run_auth_login()}")
            return False

        return True
    except (OSError, json.JSONDecodeError) as e:
        _get_logger().exception("Could not validate credentials.json")
        print(f"❌ Error: Could not validate credentials.json: {e}", file=sys.stderr)
        print(f"   {msg_run_auth_login()}", file=sys.stderr)
        return False


def _is_valid_non_empty_string(value: Any) -> bool:
    """
    Check whether a value contains at least one non-whitespace character.

    Returns:
        `true` if `value` is a `str` and stripping whitespace yields a non-empty string, `false` otherwise.
    """
    return isinstance(value, str) and value.strip() != ""


def _has_valid_password_auth(matrix_section: Mapping[str, Any] | None) -> bool:
    """
    Determine whether a Matrix configuration section provides valid password-based authentication.

    Validates that `homeserver` and `bot_user_id` are strings containing non-whitespace characters and that `password` is a string (an empty string is allowed).

    Parameters:
        matrix_section (Mapping[str, Any] | None): The parsed "matrix" configuration section to validate.

    Returns:
        bool: `True` if `homeserver` and `bot_user_id` are non-empty strings and `password` is a string, `False` otherwise.
    """
    if not isinstance(matrix_section, Mapping):
        return False

    pwd = matrix_section.get("password")
    homeserver = matrix_section.get(CONFIG_KEY_HOMESERVER)
    bot_user_id = matrix_section.get(CONFIG_KEY_BOT_USER_ID)

    # Allow empty password strings (some environments legitimately use empty passwords).
    # Homeserver and bot_user_id must still be valid non-empty strings.
    return (
        isinstance(pwd, str)
        and _is_valid_non_empty_string(homeserver)
        and _is_valid_non_empty_string(bot_user_id)
    )


def _validate_matrix_authentication(
    config_path: str,
    matrix_section: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> bool:
    """
    Determine whether Matrix authentication is configured and usable.

    Checks for a credentials.json located relative to config_path and, if absent, falls back to password-based fields or an access_token in matrix_section. Prints which authentication source will be used and whether E2EE support is available.

    Parameters:
        config_path (str): Path to the application's YAML config file; used to locate a credentials.json candidate.
        matrix_section (Mapping[str, Any] | None): The parsed "matrix" configuration section; an `access_token` or password-based fields may provide authentication when credentials.json is not present.
        config (Mapping[str, Any] | None): Parsed configuration to honor explicit credentials_path values.

    Returns:
        bool: `True` if a usable authentication method (credentials.json, password-based config, or access_token) is available, `False` otherwise.
    """
    has_valid_credentials = _validate_credentials_json(config_path, config)
    token = (matrix_section or {}).get(CONFIG_KEY_ACCESS_TOKEN)
    has_access_token = _is_valid_non_empty_string(token)

    has_password = _has_valid_password_auth(matrix_section)

    if has_valid_credentials:
        print("✅ Using credentials.json for Matrix authentication")
        if sys.platform != WINDOWS_PLATFORM:
            print("   E2EE support available (if enabled)")
        return True

    elif has_password:
        print(
            "✅ Using password in config for initial authentication (credentials.json will be created on first run)"
        )
        print(f"   {msg_for_e2ee_support()}")
        return True
    elif has_access_token:
        print(
            "✅ Using access_token for Matrix authentication (deprecated — consider 'mmrelay auth login' to create credentials.json)"
        )
        print(f"   {msg_for_e2ee_support()}")
        return True

    else:
        print("❌ Error: No Matrix authentication configured")
        print(f"   {msg_setup_auth()}")
        return False


def _validate_e2ee_config(
    _config: dict[str, Any], matrix_section: Mapping[str, Any] | None, config_path: str
) -> bool:
    """
    Validate E2EE settings and Matrix authentication readiness for a configuration file.

    Performs authentication checks (credentials.json, password, or access_token) and, if E2EE is enabled,
    verifies platform support and required native dependencies. If a configured E2EE store path does not
    exist, prints an informational note about its creation.

    Parameters:
        _config (dict[str, Any]): Full parsed configuration (kept for caller compatibility; not used by most checks).
        matrix_section (Mapping[str, Any] | None): The "matrix" subsection of the parsed config, or None if absent.
        config_path (str): Path to the active configuration file; used to locate adjacent authentication artifacts
            such as credentials.json.

    Returns:
        bool: `True` if authentication is usable and any enabled E2EE settings are valid (or if E2EE is not configured),
        `False` otherwise.

    Notes:
        This function prints user-facing status and guidance messages to stdout.
    """
    # First validate authentication
    if not _validate_matrix_authentication(config_path, matrix_section, _config):
        return False

    # Check for E2EE configuration
    if not matrix_section:
        return True  # No matrix section means no E2EE config to validate

    e2ee_config = matrix_section.get("e2ee", {})
    encryption_config = matrix_section.get("encryption", {})  # Legacy support

    e2ee_enabled = e2ee_config.get("enabled", False) or encryption_config.get(
        "enabled", False
    )

    if e2ee_enabled:
        # Platform and dependency check
        if not _validate_e2ee_dependencies():
            return False

        # Store path validation
        store_path = e2ee_config.get("store_path") or encryption_config.get(
            "store_path"
        )
        if store_path:
            expanded_path = os.path.expanduser(store_path)
            if not os.path.exists(expanded_path):
                print(f"Info: E2EE store directory will be created: {expanded_path}")

        print("✅ E2EE configuration is valid")

    return True


def _analyze_e2ee_setup(config: dict[str, Any], config_path: str) -> dict[str, Any]:
    """
    Analyze local end-to-end encryption (E2EE) readiness without contacting Matrix.

    Performs an offline inspection of the environment and provided configuration to
    determine whether E2EE can be used. The check includes platform support,
    presence of required E2EE dependencies, whether E2EE is enabled in the
    configuration, and whether a usable credentials.json can be located.

    Parameters:
        config (dict): Parsed configuration (usually from config.yaml); the
            "matrix" section is consulted for E2EE/encryption enablement.
        config_path (str): Path to the configuration file; used to locate a
            credentials.json sibling or other standard credential locations.

    Returns:
        dict: Analysis summary with the following keys:
          - config_enabled (bool): True if E2EE/encryption is enabled in config.
          - dependencies_available (bool): True if required E2EE packages are importable.
          - credentials_available (bool): True if a usable credentials.json was found.
          - platform_supported (bool): False when the current platform does not support E2EE (e.g., Windows).
          - overall_status (str): One of "ready", "disabled", "not_supported", "incomplete", or "unknown".
          - recommendations (list[str]): Human-actionable suggestions to resolve gaps (e.g., enable E2EE, install dependencies, run auth login).
    """
    analysis: dict[str, Any] = {
        "config_enabled": False,
        "dependencies_available": False,
        "credentials_available": False,
        "platform_supported": True,
        "overall_status": "unknown",
        "recommendations": [],
    }

    # Check platform support
    if sys.platform == WINDOWS_PLATFORM:
        analysis["platform_supported"] = False
        analysis["recommendations"].append(
            "E2EE is not supported on Windows. Use Linux/macOS for E2EE support."
        )

    # Check dependencies
    analysis["dependencies_available"] = _e2ee_dependencies_available()
    if not analysis["dependencies_available"]:
        analysis["recommendations"].append(
            "Install E2EE dependencies: pipx install 'mmrelay[e2e]'"
        )

    # Check config setting
    matrix_section = config.get("matrix", {})
    e2ee_config = matrix_section.get("e2ee", {})
    encryption_config = matrix_section.get("encryption", {})  # Legacy support
    analysis["config_enabled"] = e2ee_config.get(
        "enabled", False
    ) or encryption_config.get("enabled", False)

    if not analysis["config_enabled"]:
        analysis["recommendations"].append(
            "Enable E2EE in config.yaml under matrix section: e2ee: enabled: true"
        )

    # Check credentials file existence
    credentials_path = _find_credentials_json_path(config_path, config)
    analysis["credentials_available"] = bool(credentials_path)

    if not analysis["credentials_available"]:
        analysis["recommendations"].append(
            "Set up Matrix authentication: mmrelay auth login"
        )

    # Determine overall status based on setup only
    if not analysis["platform_supported"]:
        analysis["overall_status"] = "not_supported"
    elif (
        analysis["config_enabled"]
        and analysis["dependencies_available"]
        and analysis["credentials_available"]
    ):
        analysis["overall_status"] = "ready"
    elif not analysis["config_enabled"]:
        analysis["overall_status"] = "disabled"
    else:
        analysis["overall_status"] = "incomplete"

    return analysis


def _find_credentials_json_path(
    config_path: str | None, config: Mapping[str, Any] | None = None
) -> str | None:
    """
    Locate a credentials.json file following the unified HOME-first then legacy search order.

    Search order:
    1) Explicit credentials path from env/config (MMRELAY_CREDENTIALS_PATH or
       credentials_path in config),
    2) credentials.json adjacent to `config_path` (if `config_path` is provided),
    3) credentials.json at the resolved MMRELAY_HOME credentials path,
    4) credentials.json in each legacy source root.

    If a credentials.json is found in a legacy location, a migration suggestion is printed to stderr.

    Parameters:
        config_path (str | None): Optional path to a configuration file; its directory is checked first for an adjacent credentials.json.
        config (Mapping[str, Any] | None): Parsed configuration to honor explicit credentials_path values.

    Returns:
        str | None: Absolute path to the discovered credentials.json, or `None` if no credentials file is found.
    """
    from mmrelay.config import get_explicit_credentials_path, relay_config
    from mmrelay.paths import resolve_all_paths

    def _normalize_path(path: str) -> str:
        """
        Normalize a filesystem path by expanding a leading `~` and resolving it to an absolute path.

        Parameters:
                path (str): A filesystem path, which may be relative or contain a user-home shorthand.

        Returns:
                A string containing the absolute, user-expanded path.
        """
        return os.path.abspath(os.path.expanduser(path))

    explicit_path = get_explicit_credentials_path(config or relay_config)
    if explicit_path:
        raw_explicit = explicit_path
        explicit_path = _normalize_path(explicit_path)
        if (
            os.path.isdir(explicit_path)
            or raw_explicit.endswith(os.path.sep)
            or (os.path.altsep and raw_explicit.endswith(os.path.altsep))
        ):
            explicit_path = _normalize_path(
                os.path.join(explicit_path, "credentials.json")
            )
        if os.path.exists(explicit_path):
            return explicit_path

    if config_path:
        config_dir = os.path.dirname(_normalize_path(config_path))
        candidate = _normalize_path(os.path.join(config_dir, "credentials.json"))
        if os.path.exists(candidate):
            return candidate

    p = resolve_all_paths()
    home_cred = p["credentials_path"]
    if isinstance(home_cred, str):
        home_cred = _normalize_path(home_cred)
        if os.path.exists(home_cred):
            return home_cred

    for legacy_root in p["legacy_sources"]:
        if not isinstance(legacy_root, str):
            continue
        legacy_cred = _normalize_path(os.path.join(legacy_root, "credentials.json"))
        if os.path.exists(legacy_cred):
            print(
                f"INFO: Found legacy credentials at: {legacy_cred}\n"
                f"   Run 'mmrelay migrate --dry-run' (or 'mmrelay service migrate --dry-run') to preview migration to HOME.",
                file=sys.stderr,
            )
            return legacy_cred

    return None


def _print_unified_e2ee_analysis(e2ee_status: E2EEStatus) -> None:
    """
    Print a concise, user-facing analysis of end-to-end encryption (E2EE) readiness.

    Given an E2EE status mapping, prints platform support, dependency availability,
    configuration state, credentials presence, an overall readiness line, and
    actionable fix instructions when the status is not ready.

    Parameters:
        e2ee_status (E2EEStatus): Mapping with keys used to determine readiness, commonly:
            - platform_supported (bool): True when the OS/platform supports E2EE.
            - dependencies_installed or dependencies_available (bool): True when required
              E2EE packages/runtime are present.
            - enabled or config_enabled (bool): True when E2EE is enabled in config.
            - credentials_available (bool): True when a usable credentials.json is present.
            - overall_status (str): High-level status such as "ready", "disabled", or "incomplete".
    """
    print("\n🔐 E2EE Configuration Analysis:")

    # Platform support
    if e2ee_status.get("platform_supported", True):
        print("✅ Platform: E2EE supported")
    else:
        print("❌ Platform: E2EE not supported on Windows")

    # Dependencies
    if e2ee_status.get(
        "dependencies_installed", e2ee_status.get("dependencies_available", False)
    ):
        print("✅ Dependencies: E2EE dependencies installed")
    else:
        print("❌ Dependencies: E2EE dependencies not fully installed")

    # Configuration
    if e2ee_status.get("enabled", e2ee_status.get("config_enabled", False)):
        print("✅ Configuration: E2EE enabled")
    else:
        print("❌ Configuration: E2EE disabled")

    # Authentication
    if e2ee_status.get("credentials_available", False):
        print("✅ Authentication: credentials.json found")
    else:
        print("❌ Authentication: credentials.json not found")

    # Overall status
    print(
        f"\n📊 Overall Status: {e2ee_status.get('overall_status', 'unknown').upper()}"
    )

    # Show fix instructions if needed
    if e2ee_status.get("overall_status") != "ready":
        from mmrelay.e2ee_utils import get_e2ee_fix_instructions

        instructions = get_e2ee_fix_instructions(e2ee_status)
        print("\n🔧 To fix E2EE issues:")
        for instruction in instructions:
            print(f"   {instruction}")


def _print_e2ee_analysis(analysis: dict[str, Any]) -> None:
    """
    Print a human-readable analysis of end-to-end encryption (E2EE) readiness to standard output.

    Parameters:
        analysis (dict[str, Any]): Mapping describing E2EE status with these keys:
            - dependencies_available (bool): True if required E2EE dependencies (e.g., python-olm) are present.
            - credentials_available (bool): True if a usable credentials.json was found.
            - platform_supported (bool): True if the current platform supports E2EE (Windows is considered unsupported).
            - config_enabled (bool): True if E2EE is enabled in the application's configuration.
            - overall_status (str): Aggregated readiness state; expected values include "ready", "disabled", "not_supported", or "incomplete".
            - recommendations (list[str]): Zero-or-more user-facing remediation steps or suggestions.

    """
    print("\n🔐 E2EE Configuration Analysis:")

    # Current settings
    print("\n📋 Current Settings:")

    # Dependencies
    if analysis["dependencies_available"]:
        print("   ✅ Dependencies: Installed (python-olm available)")
    else:
        print("   ❌ Dependencies: Missing (python-olm not installed)")

    # Credentials
    if analysis["credentials_available"]:
        print("   ✅ Authentication: Ready (credentials.json found)")
    else:
        print("   ❌ Authentication: Missing (no credentials.json)")

    # Platform
    if not analysis["platform_supported"]:
        print("   ❌ Platform: Windows (E2EE not supported)")
    else:
        print("   ✅ Platform: Supported")

    # Config setting
    if analysis["config_enabled"]:
        print("   ✅ Configuration: ENABLED (e2ee.enabled: true)")
    else:
        print("   ❌ Configuration: DISABLED (e2ee.enabled: false)")

    # Predicted behavior
    print("\n🚨 PREDICTED BEHAVIOR:")
    if analysis["overall_status"] == "ready":
        print("   ✅ E2EE is fully configured and ready")
        print("   ✅ Encrypted rooms will receive encrypted messages")
        print("   ✅ Unencrypted rooms will receive normal messages")
    elif analysis["overall_status"] == "disabled":
        print("   ⚠️  E2EE is disabled in configuration")
        print("   ❌ Messages to encrypted rooms will be BLOCKED")
        print("   ✅ Messages to unencrypted rooms will work normally")
    elif analysis["overall_status"] == "not_supported":
        print("   ❌ E2EE not supported on Windows")
        print("   ❌ Messages to encrypted rooms will be BLOCKED")
    else:
        print("   ⚠️  E2EE setup incomplete - some issues need to be resolved")
        print("   ❌ Messages to encrypted rooms may be BLOCKED")

    print(
        "\n💡 Note: Room encryption status will be checked when mmrelay connects to Matrix"
    )

    # Recommendations
    if analysis["recommendations"]:
        print("\n🔧 TO FIX:")
        for i, rec in enumerate(analysis["recommendations"], 1):
            print(f"   {i}. {rec}")

        if analysis["overall_status"] == "ready":
            print(
                "\n✅ E2EE setup is complete! Run 'mmrelay' to start with E2EE support."
            )
        else:
            print(
                "\n⚠️  After fixing issues above, run 'mmrelay config check' again to verify."
            )


def _print_environment_summary() -> None:
    """
    Print a concise summary of the runtime environment and Matrix E2EE readiness.

    Reports the current platform and Python version, whether the platform supports E2EE (Windows is reported as not supported), and whether required E2EE dependencies are installed. If E2EE is supported but dependencies are missing, prints a brief installation hint.
    """
    print("\n🖥️  Environment Summary:")
    print(f"   Platform: {sys.platform}")
    print(f"   Python: {sys.version.split()[0]}")

    # E2EE capability check
    if sys.platform == WINDOWS_PLATFORM:
        print("   E2EE Support: ❌ Not available (Windows limitation)")
        print("   Matrix Support: ✅ Available")
    else:
        if _e2ee_dependencies_available():
            print("   E2EE Support: ✅ Available and installed")
        else:
            print("   E2EE Support: ⚠️  Available but not installed")
            print("   Install: pipx install 'mmrelay[e2e]'")


def _is_valid_serial_port(port: str) -> bool:
    """
    Validate that serial port is in a valid format for the platform.

    Args:
        port (str): Serial port path to validate

    Returns:
        bool: True if port format is valid, False otherwise
    """
    if not isinstance(port, str) or not port:
        return False

    # Use platform.system() at runtime instead of WINDOWS_PLATFORM constant
    # to handle edge cases like WSL or testing environments
    is_windows = platform.system() == "Windows"
    if is_windows:
        # Windows: COM1, COM3, COM10, etc.
        # COM followed by one or more digits (COM1, COM10, COM100, COM1000, etc.)
        return re.match(r"^COM\d+$", port) is not None
    else:
        # Linux/macOS: /dev/ttyUSB0, /dev/ttyACM0, /dev/cu.usbserial*, etc.
        # Must start with /dev/tty or /dev/cu followed by at least one character
        linux_pattern = r"^/dev/(tty|cu).+$"
        return re.match(linux_pattern, port) is not None


def _is_valid_host(host: str) -> bool:
    """
    Validate that host is a valid IP address or hostname.

    Args:
        host (str): Host address to validate

    Returns:
        bool: True if host format is valid, False otherwise
    """
    if not isinstance(host, str) or not host:
        return False

    # Try to parse as IP address (handles both IPv4 and IPv6)
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass

    # Validate as hostname (alphanumeric with hyphens and dots)
    # RFC 952 and RFC 1123 hostname rules
    hostname_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$"
    if not re.match(hostname_pattern, host):
        return False

    # Check length limits (hostname max 253 chars, each label max 63)
    if len(host) > 253:
        return False

    labels = host.split(".")
    for label in labels:
        if len(label) > 63 or len(label) == 0:
            return False

    return True


def _is_valid_ble_address(address: str) -> bool:
    """
    Validate that BLE address is a valid MAC address or non-empty device name.

    Args:
        address (str): BLE address to validate

    Returns:
        bool: True if the address format is valid, False otherwise
    """
    if not isinstance(address, str):
        return False
    trimmed_address = address.strip()
    if not trimmed_address:
        return False

    # Check for standard MAC address: AA:BB:CC:DD:EE:FF (6 groups of 2 hex chars)
    mac_pattern = r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
    if re.match(mac_pattern, trimmed_address):
        return True

    # Device name: non-empty string without colons (to avoid confusion with MAC)
    # Accepts typical device names like "MyMeshtasticDevice", "T-Beam", etc.
    if ":" not in trimmed_address and len(trimmed_address) > 0:
        return True

    return False


def check_config(args: argparse.Namespace | None = None) -> bool:
    """
    Validate the application's YAML configuration along with required Matrix and Meshtastic settings.

    Performs syntax and semantic checks, verifies authentication sources (credentials.json, access_token, or password), assesses E2EE readiness, and emits human-readable errors, warnings, and status messages to guide remediation.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments; if None, CLI arguments will be parsed internally to locate configuration paths.

    Returns:
        bool: `True` if a configuration file was found and passed all checks, `False` otherwise.
    """

    # If args is None, parse them now
    if args is None:
        args = parse_arguments()

    config_paths = get_config_paths(args)
    config_path = None
    allow_missing_matrix_auth = (
        getattr(args, "allow_missing_matrix_auth", False) is True
    )

    # Try each config path in order until we find one that exists
    for path in config_paths:
        if os.path.isfile(path):
            config_path = path
            print(f"Found configuration file at: {config_path}")
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config_content = f.read()

                # Validate YAML syntax first
                is_valid, message, config = validate_yaml_syntax(
                    config_content, config_path
                )
                if not is_valid:
                    print(f"YAML Syntax Error:\n{message}")
                    return False
                elif message:  # Warnings
                    print(f"YAML Style Warnings:\n{message}\n")

                # Check if config is empty
                if not config:
                    print(
                        "Error: Configuration file is empty or contains only comments"
                    )
                    return False

                # Merge environment variable overrides (if any)
                config = apply_env_config_overrides(config)

                # Check if we have valid credentials.json first
                has_valid_credentials = _validate_credentials_json(config_path, config)

                # Check matrix section requirements based on credentials.json availability
                if has_valid_credentials:
                    # With credentials.json, no matrix section fields are required
                    # (homeserver, access_token, user_id, device_id all come from credentials.json)
                    if CONFIG_SECTION_MATRIX not in config:
                        # Create empty matrix section if missing - no fields required
                        config[CONFIG_SECTION_MATRIX] = {}
                    matrix_section = config[CONFIG_SECTION_MATRIX]
                    if not isinstance(matrix_section, dict):
                        print("Error: 'matrix' section must be a mapping (YAML object)")
                        return False
                    required_matrix_fields: list[str] = (
                        []
                    )  # No fields required from config when using credentials.json
                else:
                    # Without credentials.json, require full matrix section
                    if CONFIG_SECTION_MATRIX not in config:
                        if allow_missing_matrix_auth:
                            print(
                                "⚠️  Warning: Matrix authentication not found in config.yaml. "
                                "Assuming environment variables or a Kubernetes Secret will provide it in-cluster."
                            )
                            config[CONFIG_SECTION_MATRIX] = {}
                        else:
                            print("Error: Missing 'matrix' section in config")
                            print(
                                "   Either add matrix section with access_token or password and bot_user_id,"
                            )
                            print(f"   {msg_or_run_auth_login()}")
                            return False

                    matrix_section = config[CONFIG_SECTION_MATRIX]
                    if not isinstance(matrix_section, dict):
                        print("Error: 'matrix' section must be a mapping (YAML object)")
                        return False

                    if allow_missing_matrix_auth:
                        required_matrix_fields = []
                    else:
                        required_matrix_fields = [
                            CONFIG_KEY_HOMESERVER,
                            CONFIG_KEY_BOT_USER_ID,
                        ]
                        token = matrix_section.get(CONFIG_KEY_ACCESS_TOKEN)
                        pwd = matrix_section.get("password")
                        has_token = _is_valid_non_empty_string(token)
                        # Allow explicitly empty password strings; require the value to be a string
                        # (reject unquoted numeric types)
                        has_password = isinstance(pwd, str)
                        if not (has_token or has_password):
                            print(
                                "Error: Missing authentication in 'matrix' section: provide 'access_token' or 'password'"
                            )
                            print(f"   {msg_or_run_auth_login()}")
                            return False

                missing_matrix_fields = [
                    field
                    for field in required_matrix_fields
                    if not _is_valid_non_empty_string(matrix_section.get(field))
                ]

                if missing_matrix_fields:
                    if has_valid_credentials:
                        print(
                            f"Error: Missing required fields in 'matrix' section: {', '.join(missing_matrix_fields)}"
                        )
                        print(
                            "   Note: credentials.json provides authentication; no matrix.* fields are required in config"
                        )
                    else:
                        print(
                            f"Error: Missing required fields in 'matrix' section: {', '.join(missing_matrix_fields)}"
                        )
                        print(f"   {msg_setup_authentication()}")
                    return False

                # Perform comprehensive E2EE analysis using centralized utilities
                try:
                    from mmrelay.e2ee_utils import (
                        get_e2ee_status,
                    )

                    e2ee_status = get_e2ee_status(config, config_path)
                    _print_unified_e2ee_analysis(e2ee_status)

                    # Check if there are critical E2EE issues
                    if not e2ee_status.get("platform_supported", True):
                        print("\n⚠️  Warning: E2EE is not supported on Windows")
                        print("   Messages to encrypted rooms will be blocked")
                except (ImportError, OSError) as e:
                    print(f"\n⚠️  Could not perform E2EE analysis: {e}")
                    print("   Falling back to basic E2EE validation...")
                    if not _validate_e2ee_config(config, matrix_section, config_path):
                        return False

                # Check matrix_rooms section
                if "matrix_rooms" not in config or not config["matrix_rooms"]:
                    print("Error: Missing or empty 'matrix_rooms' section in config")
                    print(
                        "   You need to map at least one Matrix room to a Meshtastic channel."
                    )
                    print("   Example:")
                    print("     matrix_rooms:")
                    print('       - id: "!room:matrix.org"')
                    print("         meshtastic_channel: 0")
                    return False

                if not isinstance(config["matrix_rooms"], list):
                    print("Error: 'matrix_rooms' must be a list")
                    print("   Example:")
                    print("     matrix_rooms:")
                    print('       - id: "!room:matrix.org"')
                    print("         meshtastic_channel: 0")
                    return False

                for i, room in enumerate(config["matrix_rooms"]):
                    if not isinstance(room, dict):
                        print(
                            f"Error: Room {i + 1} in 'matrix_rooms' must be a dictionary"
                        )
                        print("   Example:")
                        print("     matrix_rooms:")
                        print('       - id: "!room:matrix.org"')
                        print("         meshtastic_channel: 0")
                        return False

                    if "id" not in room:
                        print(
                            f"Error: Room {i + 1} in 'matrix_rooms' is missing the 'id' field"
                        )
                        print(
                            "   Add the 'id' field with your Matrix room ID or alias:"
                        )
                        print('     - id: "!room:matrix.org"')
                        return False

                    if "meshtastic_channel" not in room:
                        print(
                            f"Error: Room {room['id']} is missing the 'meshtastic_channel' field"
                        )
                        print(
                            "   Add the 'meshtastic_channel' field (0-7 for primary channels):"
                        )
                        print(f'     - id: "{room["id"]}"')
                        print("       meshtastic_channel: 0")
                        return False

                    meshtastic_channel = room["meshtastic_channel"]
                    if (
                        not isinstance(meshtastic_channel, int)
                        or not 0 <= meshtastic_channel <= 7
                    ):
                        print(
                            f"Error: Room {room['id']} has invalid 'meshtastic_channel' value: {meshtastic_channel}"
                        )
                        print(
                            "   meshtastic_channel must be a non-negative integer (0-7 for primary channels)"
                        )
                        return False

                # Check meshtastic section
                if CONFIG_SECTION_MESHTASTIC not in config:
                    print("Error: Missing 'meshtastic' section in config")
                    print("   You need to configure Meshtastic connection settings.")
                    print("   Example:")
                    print("     meshtastic:")
                    print("       connection_type: tcp  # or 'serial' or 'ble'")
                    print("       host: meshtastic.local")
                    print("       broadcast_enabled: true")
                    return False

                meshtastic_section = config[CONFIG_SECTION_MESHTASTIC]
                if "connection_type" not in meshtastic_section:
                    print("Error: Missing 'connection_type' in 'meshtastic' section")
                    print("   Add connection_type: 'tcp', 'serial', or 'ble'")
                    return False

                connection_type = meshtastic_section[CONFIG_KEY_CONNECTION_TYPE]
                if connection_type not in [
                    CONNECTION_TYPE_TCP,
                    CONNECTION_TYPE_SERIAL,
                    CONNECTION_TYPE_BLE,
                    CONNECTION_TYPE_NETWORK,
                ]:
                    print(
                        f"Error: Invalid 'connection_type': {connection_type}. Must be "
                        f"'{CONNECTION_TYPE_TCP}', '{CONNECTION_TYPE_SERIAL}', '{CONNECTION_TYPE_BLE}'"
                        f" or '{CONNECTION_TYPE_NETWORK}' (deprecated)"
                    )
                    return False

                # Check for deprecated connection_type
                if connection_type == CONNECTION_TYPE_NETWORK:
                    print(
                        "\nWarning: 'network' connection_type is deprecated. Please use 'tcp' instead."
                    )
                    print(
                        "This option still works but may be removed in future versions.\n"
                    )

                # Check connection-specific fields
                if (
                    connection_type == CONNECTION_TYPE_SERIAL
                    and CONFIG_KEY_SERIAL_PORT not in meshtastic_section
                ):
                    print("Error: Missing 'serial_port' for 'serial' connection type")
                    print("   Add serial_port with your device path:")
                    print("     meshtastic:")
                    print("       connection_type: serial")
                    print("       serial_port: /dev/ttyUSB0  # Linux/macOS")
                    print("       # serial_port: COM3  # Windows")
                    return False

                if (
                    connection_type == CONNECTION_TYPE_SERIAL
                    and CONFIG_KEY_SERIAL_PORT in meshtastic_section
                ):
                    serial_port = meshtastic_section[CONFIG_KEY_SERIAL_PORT]
                    if not _is_valid_serial_port(serial_port):
                        print(f"Error: Invalid 'serial_port' value: {serial_port}")
                        print("   serial_port must be a valid device path:")
                        if sys.platform == WINDOWS_PLATFORM:
                            print("     serial_port: COM3  # Windows")
                            print("     serial_port: COM10  # For COM ports above 9")
                        else:
                            print("     serial_port: /dev/ttyUSB0  # Linux/macOS (USB)")
                            print("     serial_port: /dev/ttyACM0  # Linux/macOS (CDC)")
                            print("     serial_port: /dev/cu.usbserial-*  # macOS")
                        return False

                if (
                    connection_type in [CONNECTION_TYPE_TCP, CONNECTION_TYPE_NETWORK]
                    and CONFIG_KEY_HOST not in meshtastic_section
                ):
                    print("Error: Missing 'host' for 'tcp' connection type")
                    print("   Add host with your Meshtastic device address:")
                    print("     meshtastic:")
                    print("       connection_type: tcp")
                    print(
                        "       host: meshtastic.local  # or IP address like 192.168.1.100"
                    )
                    return False

                if (
                    connection_type in [CONNECTION_TYPE_TCP, CONNECTION_TYPE_NETWORK]
                    and CONFIG_KEY_HOST in meshtastic_section
                ):
                    host = meshtastic_section[CONFIG_KEY_HOST]
                    if not _is_valid_host(host):
                        print(f"Error: Invalid 'host' value: {host}")
                        print("   host must be a valid IP address or hostname:")
                        print("     host: 192.168.1.100  # IPv4 address")
                        print("     host: meshtastic.local  # Hostname")
                        print("     host: 2001:db8::1  # IPv6 address")
                        return False

                if (
                    connection_type == CONNECTION_TYPE_BLE
                    and CONFIG_KEY_BLE_ADDRESS not in meshtastic_section
                ):
                    print("Error: Missing 'ble_address' for 'ble' connection type")
                    print("   Add ble_address with your device MAC address or name:")
                    print("     meshtastic:")
                    print("       connection_type: ble")
                    print(
                        "       ble_address: AA:BB:CC:DD:EE:FF  # or device name from 'meshtastic --ble-scan'"
                    )
                    return False

                if (
                    connection_type == CONNECTION_TYPE_BLE
                    and CONFIG_KEY_BLE_ADDRESS in meshtastic_section
                ):
                    ble_address = meshtastic_section[CONFIG_KEY_BLE_ADDRESS]
                    if not _is_valid_ble_address(ble_address):
                        print(f"Error: Invalid 'ble_address' value: {ble_address}")
                        print(
                            "   ble_address must be a valid MAC address or device name:"
                        )
                        print("     ble_address: AA:BB:CC:DD:EE:FF  # MAC address")
                        print("     ble_address: MyMeshtasticDevice  # Device name")
                        print(
                            "   Find MAC/name with: meshtastic --ble-scan (requires pipx install 'mmrelay[ble]')"
                        )
                        return False

                # Check for other important optional configurations and provide guidance
                optional_configs: dict[str, dict[str, Any]] = {
                    "broadcast_enabled": {
                        "type": bool,
                        "description": "Enable Matrix to Meshtastic message forwarding (required for two-way communication)",
                    },
                    "detection_sensor": {
                        "type": bool,
                        "description": "Enable forwarding of Meshtastic detection sensor messages",
                    },
                    "message_delay": {
                        "type": (int, float),
                        "description": "Delay in seconds between messages sent to mesh (minimum: 2.0)",
                    },
                    "meshnet_name": {
                        "type": str,
                        "description": "Name displayed for your meshnet in Matrix messages",
                    },
                }

                warnings: list[str] = []
                for option, config_info in optional_configs.items():
                    if option in meshtastic_section:
                        value = meshtastic_section[option]
                        expected_type = config_info["type"]
                        if not isinstance(value, expected_type):
                            if isinstance(expected_type, tuple):
                                type_name = " or ".join(
                                    t.__name__ for t in expected_type
                                )
                            else:
                                type_name = (
                                    expected_type.__name__
                                    if hasattr(expected_type, "__name__")
                                    else str(expected_type)
                                )
                            print(
                                f"Error: '{option}' must be of type {type_name}, got: {value}"
                            )
                            return False

                        # Special validation for message_delay
                        if option == "message_delay" and value < 2.0:
                            print(
                                f"Error: 'message_delay' must be at least 2.0 seconds (firmware limitation), got: {value}",
                                file=sys.stderr,
                            )
                            return False
                    else:
                        warnings.append(f"  - {option}: {config_info['description']}")

                if warnings:
                    print("\nOptional configurations not found (using defaults):")
                    for warning in warnings:
                        print(warning)

                # Check for deprecated db section
                if "db" in config:
                    print(
                        "\nWarning: 'db' section is deprecated. Please use 'database' instead.",
                        file=sys.stderr,
                    )
                    print(
                        "This option still works but may be removed in future versions.\n",
                        file=sys.stderr,
                    )

                print("\n✅ Configuration file is valid!")
                return True
            except (OSError, ValueError, UnicodeDecodeError) as e:
                print(
                    f"Error checking configuration: {e.__class__.__name__}: {e}",
                    file=sys.stderr,
                )
            except Exception as e:
                print(f"Error checking configuration: {e}", file=sys.stderr)
                return False

    print("Error: No configuration file found in any of the following locations:")
    for path in config_paths:
        print(f"  - {path}")
    print(f"\n{msg_suggest_generate_config()}")
    return False


def main() -> int:
    """
    Run the MMRelay command-line interface, dispatching modern subcommands, deprecated legacy flags, or the main runtime.

    Parses command-line arguments (including --base-dir / deprecated --data-dir), configures runtime directories, and invokes the appropriate handler for config, auth, service subcommands, legacy CLI actions, or the primary application entrypoint. Prints user-facing error messages and maps failures to non-zero exit codes.

    Returns:
        int: Exit code — `0` on success, non-zero on failure.
    """
    try:
        # Set up Windows console for better compatibility
        try:
            from mmrelay.windows_utils import setup_windows_console

            setup_windows_console()
        except (ImportError, OSError, AttributeError):
            # windows_utils not available or Windows console setup failed
            # This is intentional - we want to continue if Windows utils fail
            pass

        args = parse_arguments()

        # Handle the --base-dir/--data-dir options
        _apply_dir_overrides(args)

        args_dict = vars(args)
        has_modern_command = bool(getattr(args, "command", None))
        has_legacy_flag = any(
            args_dict.get(flag)
            for flag in (
                "version",
                "install_service",
                "generate_config",
                "check_config",
                "auth",
            )
        )

        if has_modern_command or has_legacy_flag:
            from mmrelay import log_utils

            # CLI commands print user-facing output; suppress console logging noise.
            with log_utils.cli_logging_mode(args=args):
                if has_modern_command:
                    return handle_subcommand(args)

                legacy_exit = handle_cli_commands(args)
                if legacy_exit is not None:
                    return legacy_exit

        # If no command was specified, run the main functionality
        try:
            from mmrelay.main import run_main

            return run_main(args)
        except ImportError as e:
            print(f"Error importing main module: {e}")
            return 1

    except (OSError, PermissionError, KeyboardInterrupt) as e:
        # Handle common system-level errors
        print(f"System error: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # Default error message
        error_msg = f"Unexpected error: {e.__class__.__name__}: {e}"
        # Provide Windows-specific error guidance if available
        try:
            from mmrelay.windows_utils import get_windows_error_message, is_windows

            if is_windows():
                error_msg = f"Error: {get_windows_error_message(e)}"
        except ImportError:
            pass  # Use default message
        print(error_msg, file=sys.stderr)
        return 1


def handle_subcommand(args: argparse.Namespace) -> int:
    """
    Dispatch a top-level CLI subcommand to its handler.

    Supported commands: config, auth, service, paths, doctor, verify-migration, migrate.

    Returns:
        Exit code returned by the invoked handler; `1` if the command is unknown.
    """
    if args.command == "config":
        return handle_config_command(args)
    elif args.command == "auth":
        return handle_auth_command(args)
    elif args.command == "service":
        return handle_service_command(args)
    elif args.command == "paths":
        return handle_paths_command(args)
    elif args.command == "doctor":
        return handle_doctor_command(args)
    elif args.command == "verify-migration":
        return handle_verify_migration_command(args)
    elif args.command == "migrate":
        return handle_migrate_command(args)
    else:
        print(f"Unknown command: {args.command}")
        return 1


def handle_config_command(args: argparse.Namespace) -> int:
    """
    Dispatches "config" command group to the selected subcommand handler.

    Supported subcommands:
        - "generate": create or update of sample configuration file at preferred location.
        - "check": validate of resolved configuration file (delegates to check_config).
        - "paths": show path configuration (delegates to handle_paths_command).
        - "diagnose": run a sequence of non-destructive diagnostics and print a report (delegates to handle_config_diagnose).

    Parameters:
        args (argparse.Namespace): CLI namespace containing `config_command` (one of "generate", "check", "paths", "diagnose") and any subcommand-specific options.

    Returns:
        int: Exit code (0 on success, 1 on failure or for unknown subcommands).
    """
    if args.config_command == "generate":
        return 0 if generate_sample_config() else 1
    elif args.config_command == "check":
        return 0 if check_config(args) else 1
    elif args.config_command == "paths":
        return handle_paths_command(args)
    elif args.config_command == "diagnose":
        return handle_config_diagnose(args)
    else:
        print(f"Unknown config command: {args.config_command}")
        return 1


def handle_auth_command(args: argparse.Namespace) -> int:
    """
    Dispatches the "auth" subcommand to the appropriate handler (login, status, or logout).

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments. May include `auth_command` with values
            "login", "status", or "logout"; if missing or any other value, defaults to login.

    Returns:
        int: Exit code from the invoked handler — 0 on success, non-zero on failure.
    """
    if hasattr(args, "auth_command"):
        if args.auth_command == "status":
            return handle_auth_status(args)
        elif args.auth_command == "logout":
            return handle_auth_logout(args)
        else:
            # Default to login for auth login command
            return handle_auth_login(args)
    else:
        # Default to login for legacy --auth
        return handle_auth_login(args)


def handle_paths_command(_args: argparse.Namespace) -> int:
    """
    Display all path configuration and diagnostics.

    Parameters:
        args (argparse.Namespace): CLI namespace.

    Returns:
        int: Exit code (0 on success, non-zero on failure).
    """
    from mmrelay.paths import resolve_all_paths

    # Get path information
    paths_info = resolve_all_paths()

    # Print header
    print("\n" + "=" * 60)
    print("MMRelay Path Configuration (mmrelay paths)")
    print("=" * 60)

    # Print HOME information
    print("\n📍 HOME Directory:")
    print(f"   Location: {paths_info['home']}")
    print(f"   Source: {paths_info['home_source']}")

    # Print runtime artifact paths
    print("\n📁 Runtime Artifacts (all in HOME):")
    print(f"   Credentials: {paths_info['credentials_path']}")
    print(f"   Database: {paths_info['database_dir']}")
    print(f"   Store (E2EE): {paths_info['store_dir']}")
    print(f"   Logs: {paths_info['logs_dir']}")
    print(f"   Log File: {paths_info['log_file']}")

    # Print plugin paths
    print("\n📦 Plugins:")
    print(f"   Plugins: {paths_info['plugins_dir']}")
    print(f"   Custom: {paths_info['custom_plugins_dir']}")
    print(f"   Community: {paths_info['community_plugins_dir']}")

    # Print legacy sources
    print("\n📋 Legacy Sources (read-only):")
    if paths_info.get("legacy_sources"):
        for legacy_dir in paths_info["legacy_sources"]:
            print(f"   - {legacy_dir}")
    else:
        print("   (none detected)")

    # Print environment variables
    print("\n🔧 Environment Variables:")
    if paths_info.get("env_vars_detected"):
        for var_name, var_value in paths_info["env_vars_detected"].items():
            print(f"   {var_name}={var_value}")
    else:
        print("   (none detected)")

    # Print CLI override
    print(f"\n⚙️  CLI Override: {paths_info.get('cli_override', 'None')}")

    # Print plugin roots searched
    print("\n   Plugin Roots Searched:")
    print(f"   Primary: {paths_info['plugins_dir']}")

    # Check for legacy data
    if paths_info.get("legacy_sources"):
        print("\n   ⚠️  Legacy data detected!")
        print(
            f"   Legacy directories found: {', '.join(str(d) for d in paths_info['legacy_sources'])}"
        )
        print("   Migration will scan all legacy roots for data to migrate")
        print(
            "   💡 Run 'mmrelay service migrate --dry-run' to see what would be moved"
        )
        print("   💡 Run 'mmrelay migrate' to migrate data to new structure")

    return 0


# Alias for backward compatibility with existing tests
handle_config_paths = handle_paths_command


def handle_verify_migration_command(_args: argparse.Namespace) -> int:
    """
    Verify migration readiness and report legacy data findings in read-only mode.

    Runs the migration verification routine and prints a human-readable report.

    Returns:
        0 if the verification report indicates success, 1 otherwise.
    """
    from mmrelay.migrate import print_migration_verification, verify_migration

    report = verify_migration()
    print_migration_verification(report)
    return 0 if report["ok"] else 1


def handle_doctor_command(args: argparse.Namespace) -> int:
    """
    Print a diagnostic summary of resolved runtime paths, legacy sources, environment variables, CLI overrides, and migration status.

    If the provided args has a boolean attribute `migration` set to True, run migration verification and include its warnings/errors in the output.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments; may include `migration` (bool) to enable migration verification.

    Returns:
        int: 0 on success, 1 if migration verification reported errors.
    """
    from mmrelay.migrate import is_migration_needed, verify_migration
    from mmrelay.paths import resolve_all_paths

    # Get path information
    paths_info = resolve_all_paths()

    # Print header
    print("\n" + "=" * 60)
    print("MMRelay Path Diagnostics (mmrelay doctor)")
    print("=" * 60)

    # Print HOME information
    print("\n📍 HOME Directory:")
    print(f"   Location: {paths_info['home']}")
    print(f"   Source: {paths_info['home_source']}")

    # Print runtime artifact paths
    print("\n📁 Runtime Artifacts (all in HOME):")
    print(f"   Credentials: {paths_info['credentials_path']}")
    print(f"   Database: {paths_info['database_dir']}")
    print(f"   Store (E2EE): {paths_info['store_dir']}")
    print(f"   Logs: {paths_info['logs_dir']}")
    print(f"   Plugins: {paths_info['plugins_dir']}")

    # Print legacy sources
    print("\n📋 Legacy Sources (read-only):")
    if paths_info.get("legacy_sources"):
        for legacy_dir in paths_info["legacy_sources"]:
            print(f"   - {legacy_dir}")
    else:
        print("   (none detected)")

    # Print environment variables
    print("\n🔧 Environment Variables:")
    if paths_info.get("env_vars_detected"):
        for var_name, var_value in paths_info["env_vars_detected"].items():
            print(f"   {var_name}={var_value}")
    else:
        print("   (none detected)")

    # Print CLI override
    print(f"\n⚙️  CLI Override: {paths_info.get('cli_override', 'None')}")

    # Check migration status and print recommendations
    print("\n🔄 Migration Status:")
    if is_migration_needed():
        print("   ⚠️  Migration RECOMMENDED:")
        print("       Legacy data detected in one or more locations.")
        print("       Run 'mmrelay migrate --dry-run' to preview migration.")
        print("       Run 'mmrelay migrate' to perform migration (default: copy).")
        print("       Use '--move' flag to move instead of copy.")
        print("       Use '--force' flag to skip backup prompts.")
    else:
        print("   ✅ No migration needed (clean install or already migrated)")

    if getattr(args, "migration", False):
        report = verify_migration()
        print("\n🧭 Migration Verification:")
        if report["warnings"]:
            for warning in report["warnings"]:
                print(f"   ⚠️  {warning}")
        else:
            print("   ✅ No legacy data found")
        if report["errors"]:
            for error in report["errors"]:
                print(f"   ❌ {error}")
            return 1

    # Return success
    return 0


def handle_auth_login(args: argparse.Namespace) -> int:
    """
    Authenticate a Matrix bot either non-interactively with provided credentials or interactively via prompts.

    If `args` supplies all three of `homeserver`, `username`, and `password`, performs a non-interactive login (validates that `homeserver` and `username` are non-empty). If none are supplied, runs an interactive login flow and attempts a silent check for E2EE-enabled configuration to tailor user-facing messages. If some but not all credentials are provided, reports the missing parameters and fails.

    Parameters:
        args (argparse.Namespace): Parsed CLI namespace; may include `homeserver`, `username`, and `password`.

    Returns:
        int: 0 on successful authentication, 1 on failure, cancellation, or unexpected errors.
    """
    import asyncio

    from mmrelay.matrix_utils import login_matrix_bot

    # Extract arguments
    homeserver = getattr(args, "homeserver", None)
    username = getattr(args, "username", None)
    password = getattr(args, "password", None)

    # Count provided parameters (empty strings count as provided)
    provided_params = [p for p in [homeserver, username, password] if p is not None]

    # Determine mode based on parameters provided
    if len(provided_params) == 3:
        # All parameters provided - validate required non-empty fields
        if not _is_valid_non_empty_string(homeserver) or not _is_valid_non_empty_string(
            username
        ):
            print(
                "❌ Error: --homeserver and --username must be non-empty for non-interactive login."
            )
            return 1
        # Password may be empty (flows may prompt)
    elif len(provided_params) > 0:
        # Some but not all parameters provided - show error
        missing_params = []
        if homeserver is None:
            missing_params.append("--homeserver")
        if username is None:
            missing_params.append("--username")
        if password is None:
            missing_params.append("--password")

        error_message = f"""❌ Error: All authentication parameters are required when using command-line options.
   Missing: {", ".join(missing_params)}

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        print(error_message)
        return 1
    else:
        # No parameters provided - run in interactive mode
        # Check if E2EE is actually configured before mentioning it
        # Use silent checking to avoid warnings during initial setup
        try:
            from mmrelay.config import check_e2ee_enabled_silently

            e2ee_enabled = check_e2ee_enabled_silently(args)

            if e2ee_enabled:
                print("Matrix Bot Authentication for E2EE")
                print("===================================")
            else:
                print("\nMatrix Bot Authentication")
                print("=========================")
        except (OSError, PermissionError, ImportError, ValueError) as e:
            # Fallback if silent checking fails due to config file or import issues
            _get_logger().debug(f"Failed to silently check E2EE status: {e}")
            print("\nMatrix Bot Authentication")
            print("=========================")

    try:
        result = asyncio.run(
            login_matrix_bot(
                homeserver=homeserver,
                username=username,
                password=password,
                logout_others=False,
            )
        )
        return 0 if result else 1
    except KeyboardInterrupt:
        print("\nAuthentication cancelled by user.")
        return 1
    except Exception as e:
        print(f"\nError during authentication: {e}")
        return 1


def handle_auth_status(args: argparse.Namespace) -> int:
    """
    Display Matrix authentication status by locating and validating a credentials.json file.

    Searches for credentials.json in the same locations as the main runtime (explicit path,
    config-adjacent files, then base/data directory fallbacks). If a readable credentials.json is found,
    prints its path and the `homeserver`, `user_id`, and `device_id` values and reports validity.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments used to resolve config file search paths.

    Returns:
        int: `0` if a valid credentials.json was found and read, `1` otherwise.

    Notes:
        Prints human-readable status and guidance messages to stdout.
    """
    import json

    from mmrelay.config import (
        get_config_paths,
        get_credentials_search_paths,
        get_explicit_credentials_path,
        load_config,
    )

    print("Matrix Authentication Status")
    print("============================")

    config_paths = get_config_paths(args)
    config_data = load_config(args=args, config_paths=config_paths)
    explicit_path = get_explicit_credentials_path(config_data)
    candidate_paths = get_credentials_search_paths(
        explicit_path=explicit_path,
        config_paths=config_paths,
    )

    for credentials_path in candidate_paths:
        if os.path.exists(credentials_path):
            try:
                with open(credentials_path, "r", encoding="utf-8") as f:
                    credentials = json.load(f)

                required = ("homeserver", "access_token", "user_id", "device_id")
                if not all(
                    isinstance(credentials.get(k), str) and credentials.get(k).strip()
                    for k in required
                ):
                    print(
                        f"❌ Error: credentials.json at {credentials_path} is missing required fields"
                    )
                    print(f"Run '{get_command('auth_login')}' to authenticate")
                    return 1
                print(f"✅ Found credentials.json at: {credentials_path}")
                print(f"   Homeserver: {credentials.get('homeserver')}")
                print(f"   User ID: {credentials.get('user_id')}")
                print(f"   Device ID: {credentials.get('device_id')}")
                return 0
            except Exception as e:
                print(f"❌ Error reading credentials.json: {e}")
                return 1

    print("❌ No credentials.json found")
    print(f"Run '{get_command('auth_login')}' to authenticate")
    return 1


def handle_auth_logout(args: argparse.Namespace) -> int:
    """
    Log out the Matrix bot, clear local session data, and invalidate the bot's access token.

    Prompts for a verification password if `args.password` is None or empty, and asks for confirmation
    unless `args.yes` is True. On success this removes local credentials and clears any E2EE store.

    Parameters:
        args (argparse.Namespace): CLI arguments containing:
            password (str | None): Verification password to use; if None or empty the function prompts securely.
            yes (bool): If True, skip the interactive confirmation prompt.

    Returns:
        int: 0 on successful logout, 1 if the operation fails or is cancelled.
    """
    import asyncio

    from mmrelay.cli_utils import logout_matrix_bot

    # Show header
    print("Matrix Bot Logout")
    print("=================")
    print()
    print("This will log out from Matrix and clear all local session data:")
    print("• Remove credentials.json")
    print("• Clear E2EE encryption store")
    print("• Invalidate Matrix access token")
    print()

    try:
        # Handle password input
        password = getattr(args, "password", None)

        if (
            password is None
            or password
            == ""  # nosec B105 (user-entered secret; prompting securely via getpass)
        ):
            # No --password flag or --password with no value, prompt securely
            import getpass

            password = getpass.getpass("Enter Matrix password for verification: ")
        else:
            # --password VALUE provided, warn about security
            print(
                "⚠️  Warning: Supplying password as argument exposes it in shell history and process list."
            )
            print(
                "   For better security, use --password without a value to prompt securely."
            )

        # Confirm the action unless forced
        if not getattr(args, "yes", False):
            confirm = input("Are you sure you want to logout? (y/N): ").lower().strip()
            if not confirm.startswith("y"):
                print("Logout cancelled.")
                return 0

        # Run the logout process
        result = asyncio.run(logout_matrix_bot(password=password))
        return 0 if result else 1
    except KeyboardInterrupt:
        print("\nLogout cancelled by user.")
        return 1
    except Exception as e:
        print(f"\nError during logout: {e}")
        return 1


def handle_migrate_command(args: argparse.Namespace) -> int:
    """
    Run data migration from legacy directory layouts to the unified HOME-based layout.

    Honors CLI flags on `args`: `dry_run` (report actions without changing files), `move` (move files instead of copying), and `force` (override safety checks).

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments containing optional `dry_run`, `move`, and `force` attributes.

    Returns:
        int: `0` on success, `1` on failure.
    """
    try:
        from mmrelay.migrate import perform_migration
        from mmrelay.paths import resolve_all_paths

        dry_run = getattr(args, "dry_run", False)
        force = getattr(args, "force", False)
        move = getattr(args, "move", False)
        paths_info = resolve_all_paths()
        legacy_sources = paths_info.get("legacy_sources", [])

        print("MMRelay Migration")
        print("=================")
        print(f"Mode: {'DRY RUN' if dry_run else 'APPLY'}")
        print(f"Action: {'MOVE' if move else 'COPY'}")
        print(f"Force overwrite: {'yes' if force else 'no'}")
        print(f"MMRELAY_HOME: {paths_info.get('home')}")
        if legacy_sources:
            print("Legacy sources detected:")
            for source in legacy_sources:
                print(f"  - {source}")
        else:
            print("Legacy sources detected: none")
        print()

        result = perform_migration(dry_run=dry_run, force=force, move=move)

        if result.get("ok", result.get("success")):
            print(
                "✅ Dry-run completed successfully"
                if dry_run
                else "✅ Migration completed successfully"
            )
            migrated_steps = 0
            for migration in result.get("migrations", []):
                mtype = migration.get("type", "unknown")
                mresult = migration.get("result", {})
                ok = mresult.get("ok", mresult.get("success"))
                if ok:
                    migrated_steps += 1
                status_icon = "✅" if ok else "❌"
                message = (
                    mresult.get("message")
                    or mresult.get("error")
                    or "No additional details"
                )
                print(f"  {status_icon} {mtype}: {message}")

                old_path = mresult.get("old_path")
                if old_path:
                    print(f"     from: {old_path}")
                new_path = mresult.get("new_path")
                if new_path:
                    print(f"       to: {new_path}")
                action = mresult.get("action")
                if action:
                    print(f"   action: {str(action).upper()}")
                migrated_count = mresult.get("migrated_count")
                if isinstance(migrated_count, int):
                    print(f"    files: {migrated_count}")
                migrated_types = mresult.get("migrated_types")
                if isinstance(migrated_types, list) and migrated_types:
                    print(f"    types: {', '.join(str(t) for t in migrated_types)}")
                if mresult.get("dry_run"):
                    print("     note: no changes were made")

            print(
                f"\nSummary: {migrated_steps}/{len(result.get('migrations', []))} steps succeeded"
            )
            if dry_run:
                print("Next step: run `mmrelay migrate` to apply changes.")
            print("Verification: run `mmrelay verify-migration`.")
            return 0
        else:
            print(f"❌ Migration failed: {result.get('error', 'Unknown error')}")
            return 1
    except ImportError as e:
        print(f"Error importing migration module: {e}")
        return 1


def handle_service_command(args: argparse.Namespace) -> int:
    """
    Dispatch a service-related CLI subcommand.

    Currently supports the "install" action, which attempts to install the application service.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments with a `service_command` attribute indicating the requested action.

    Returns:
        int: `0` on success, `1` on failure or for unknown subcommands.
    """
    if args.service_command == "install":
        try:
            from mmrelay.setup_utils import install_service

            return 0 if install_service() else 1
        except ImportError as e:
            print(f"Error importing setup utilities: {e}")
            return 1
    elif args.service_command == "migrate":
        return handle_migrate_command(args)
    else:
        print(f"Unknown service command: {args.service_command}")
        return 1


def _diagnose_config_paths(args: argparse.Namespace) -> None:
    """
    Prints a diagnostic summary of resolved configuration file search paths and their directory accessibility.

    For each candidate config path prints its index, the path, and a status icon:
    - ✅ directory exists and is writable
    - ⚠️ directory exists but is not writable
    - ❌ directory does not exist

    Parameters:
        args (argparse.Namespace): CLI arguments used to determine the ordered list of candidate config paths (passed to get_config_paths).
    """
    print("1. Testing configuration paths...")
    from mmrelay.config import get_config_paths

    paths = get_config_paths(args)
    print(f"   Config search paths: {len(paths)} locations")
    for i, path in enumerate(paths, 1):
        dir_path = os.path.dirname(path)
        dir_exists = os.path.exists(dir_path)
        dir_writable = os.access(dir_path, os.W_OK) if dir_exists else False
        status = "✅" if dir_exists and dir_writable else "⚠️" if dir_exists else "❌"
        print(f"   {i}. {path} {status}")
    print()


def _diagnose_sample_config_accessibility() -> bool:
    """
    Print a short diagnostic about accessibility of the bundled sample configuration.

    Performs two non-destructive checks: reports whether a filesystem copy of the sample
    configuration exists at the path returned by mmrelay.tools.get_sample_config_path(),
    and attempts to read the embedded resource "sample_config.yaml" from the mmrelay.tools
    package via importlib.resources, reporting the read result and content length.

    Returns:
        bool: `True` if a filesystem sample config exists at the resolved path, `False` otherwise.
    """
    print("2. Testing sample config accessibility...")
    from mmrelay.tools import get_sample_config_path

    sample_path = get_sample_config_path()
    sample_exists = os.path.exists(sample_path)
    print(f"   Sample config path: {sample_path}")
    print(f"   Sample config exists: {'✅' if sample_exists else '❌'}")

    # Test importlib.resources fallback
    try:
        import importlib.resources

        content = (
            importlib.resources.files("mmrelay.tools")
            .joinpath("sample_config.yaml")
            .read_text()
        )
        print(f"   importlib.resources fallback: ✅ ({len(content)} chars)")
    except (FileNotFoundError, ImportError, OSError) as e:
        print(f"   importlib.resources fallback: ❌ ({e})")
    print()

    return sample_exists


def _diagnose_platform_specific(args: argparse.Namespace) -> bool:
    """
    Run platform-specific diagnostic checks and print a concise report.

    On Windows this runs Windows-specific requirement checks and a configuration-generation test using the provided CLI arguments; on non-Windows platforms it reports that platform-specific tests are not required.

    Parameters:
        args (argparse.Namespace): CLI arguments forwarded to the Windows configuration-generation test (used only when running on Windows).

    Returns:
        bool: `True` if Windows checks were executed (running on Windows), `False` otherwise.
    """
    print("3. Platform-specific diagnostics...")
    import sys

    from mmrelay.constants.app import WINDOWS_PLATFORM

    on_windows = sys.platform == WINDOWS_PLATFORM
    print(f"   Platform: {sys.platform}")
    print(f"   Windows: {'Yes' if on_windows else 'No'}")

    if on_windows:
        try:
            from mmrelay.windows_utils import (
                check_windows_requirements,
                test_config_generation_windows,
            )

            # Check Windows requirements
            warnings = check_windows_requirements()
            if warnings:
                print("   Windows warnings: ⚠️")
                for line in warnings.split("\n"):
                    if line.strip():
                        print(f"     {line}")
            else:
                print("   Windows compatibility: ✅")

            # Run Windows-specific tests
            print("\n   Windows config generation test:")
            results = test_config_generation_windows(args)

            for component, result in results.items():
                if component == "overall_status":
                    continue
                if isinstance(result, dict):
                    status_icon = (
                        "✅"
                        if result["status"] == "ok"
                        else "❌" if result["status"] == "error" else "⚠️"
                    )
                    print(f"     {component}: {status_icon}")

            overall = results.get("overall_status", "unknown")
            print(
                f"   Overall Windows status: {'✅' if overall == 'ok' else '⚠️' if overall == 'partial' else '❌'}"
            )

        except ImportError:
            print("   Windows utilities: ❌ (not available)")
    else:
        print("   Platform-specific tests: ✅ (Unix-like system)")

    print()
    return on_windows


def _get_minimal_config_template() -> str:
    """
    Return a minimal YAML configuration template used as a fallback when the packaged sample_config.yaml is unavailable.

    This template provides the smallest sensible configuration for MMRelay (basic matrix section, a serial meshtastic connection example, one room entry, and minimal logging) intended for editing into a working config file.

    Returns:
        template (str): A YAML-formatted minimal configuration template.
    """
    return """# MMRelay Configuration File
# This is a minimal template created when the full sample config was unavailable
# For complete configuration options, visit:
# https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki

matrix:
  homeserver: https://matrix.example.org
  # Use 'mmrelay auth login' to set up authentication
  # access_token: your_access_token_here
  # bot_user_id: '@your_bot:matrix.example.org'

meshtastic:
  connection_type: serial
  serial_port: /dev/ttyUSB0  # Windows: COM3, macOS: /dev/cu.usbserial-*
  # host: meshtastic.local  # For network connection
  # ble_address: "your_device_address"  # For BLE connection

matrix_rooms:
  - id: '#your-room:matrix.example.org'
    meshtastic_channel: 0

logging:
  level: info

# Uncomment and configure as needed:
# database:
#   msg_map:
#     msgs_to_keep: 100

# plugins:
#   ping:
#     active: true
#   weather:
#     active: true
#     units: metric
"""


def _diagnose_minimal_config_template() -> None:
    """
    Validate the bundled minimal YAML configuration template and print a concise pass/fail result.

    Parses the built-in minimal template and prints a single-line outcome:
    on success it prints "Minimal template: ✅ (<n> chars, valid YAML)"; on failure
    it prints "Minimal template: ❌ (<error>)". This function is a non-destructive
    diagnostic helper that writes to stdout and does not return a value.
    """
    print("4. Testing minimal config template fallback...")
    try:
        template = _get_minimal_config_template()
        yaml.safe_load(template)
        print(f"   Minimal template: ✅ ({len(template)} chars, valid YAML)")
    except yaml.YAMLError as e:
        print(f"   Minimal template: ❌ ({e})")

    print()


def handle_config_diagnose(args: argparse.Namespace) -> int:
    """
    Run non-destructive diagnostics for the MMRelay configuration subsystem and print a human-readable report.

    Performs four checks without modifying user files: (1) resolves and reports candidate configuration paths and directory accessibility, (2) verifies the packaged sample configuration is accessible, (3) runs platform-specific diagnostics (Windows checks when applicable), and (4) validates the bundled minimal YAML template.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments used to determine configuration search paths and to control platform-specific diagnostic behavior.

    Returns:
        int: `0` if diagnostics completed successfully, `1` if a failure occurred and an error summary was printed to stderr.
    """
    print("MMRelay Configuration System Diagnostics")
    print("=" * 40)
    print()

    try:
        # Test 1: Basic config path resolution
        _diagnose_config_paths(args)

        # Test 2: Sample config accessibility
        sample_exists = _diagnose_sample_config_accessibility()

        # Test 3: Platform-specific diagnostics
        on_windows = _diagnose_platform_specific(args)

        # Test 4: Minimal config template
        _diagnose_minimal_config_template()

        print("=" * 40)
        print("Diagnostics complete!")

        # Provide guidance based on results
        if on_windows and not sample_exists:
            print("\n💡 Windows Troubleshooting Tips:")
            print("   • Try: pip install --upgrade --force-reinstall mmrelay")
            print("   • Use: python -m mmrelay config generate")
            print("   • Check antivirus software for quarantined files")

        return 0

    except Exception as e:
        print(f"❌ Diagnostics failed: {e}", file=sys.stderr)

        # Provide platform-specific guidance
        try:
            from mmrelay.windows_utils import get_windows_error_message, is_windows

            if is_windows():
                error_msg = get_windows_error_message(e)
                print(f"\nWindows-specific guidance: {error_msg}", file=sys.stderr)
        except ImportError:
            pass

        return 1


def handle_cli_commands(args: argparse.Namespace) -> int | None:
    """
    Dispatch legacy CLI flags to their immediate handlers.

    Parameters:
        args (argparse.Namespace): Parsed command-line arguments.

    Returns:
        int | None: Exit code (`0` on success, `1` on failure) if a legacy command was handled; `None` if no legacy flag was present.
    """
    _apply_dir_overrides(args)
    args_dict = vars(args)

    # Handle --version
    if args_dict.get("version"):
        print_version()
        return 0

    # Handle --install-service
    if args_dict.get("install_service"):
        warning = get_deprecation_warning("--install-service")
        print(warning, file=sys.stderr)
        _get_logger().warning(warning)
        try:
            from mmrelay.setup_utils import install_service

            return 0 if install_service() else 1
        except ImportError as e:
            _get_logger().exception("Error importing setup utilities")
            print(f"Error importing setup utilities: {e}", file=sys.stderr)
            return 1

    # Handle --generate-config
    if args_dict.get("generate_config"):
        warning = get_deprecation_warning("--generate-config")
        print(warning, file=sys.stderr)
        _get_logger().warning(warning)
        return 0 if generate_sample_config() else 1

    # Handle --check-config
    if args_dict.get("check_config"):
        warning = get_deprecation_warning("--check-config")
        print(warning, file=sys.stderr)
        _get_logger().warning(warning)
        return 0 if check_config(args) else 1

    # Handle --auth
    if args_dict.get("auth"):
        warning = get_deprecation_warning("--auth")
        print(warning, file=sys.stderr)
        _get_logger().warning(warning)
        return handle_auth_command(args)

    # No commands were handled
    return None


def generate_sample_config() -> bool:
    """
    Generate a sample configuration file at the highest-priority config path when no configuration exists.

    Attempts to copy the bundled sample_config.yaml into the first candidate config path. If the packaged resource is unavailable, falls back to reading the sample from importlib.resources, several standard filesystem locations, and finally writes a minimal built-in template as a last resort. When a file is created, the function will attempt to apply secure owner-only permissions on Unix-like systems. If a config file already exists at any candidate path, no file is created.

    Returns:
        True if a sample configuration file was created, False if no file was created (because a config already existed or an error occurred).
    """

    # Get the first config path (highest priority)
    config_paths = get_config_paths()

    # Check if any config file exists
    existing_config = None
    for path in config_paths:
        if os.path.isfile(path):
            existing_config = path
            break

    if existing_config:
        print(f"A config file already exists at: {existing_config}")
        print(
            "Use --config to specify a different location if you want to generate a new one."
        )
        return False

    # No config file exists, generate one in the first location
    target_path = config_paths[0]

    # Directory should already exist from get_config_paths() call

    # Use the helper function to get the sample config path
    sample_config_path = get_sample_config_path()

    if os.path.exists(sample_config_path):
        # Copy the sample config file to the target path

        try:
            shutil.copy2(sample_config_path, target_path)

            # Set secure permissions on Unix systems (600 - owner read/write)
            set_secure_file_permissions(target_path)

            print(f"Generated sample config file at: {target_path}")
            print(
                "\nEdit this file with your Matrix and Meshtastic settings before running mmrelay."
            )
            return True
        except (IOError, OSError) as e:
            # Provide Windows-specific error guidance if available
            try:
                from mmrelay.windows_utils import get_windows_error_message, is_windows

                if is_windows():
                    error_msg = get_windows_error_message(e)
                    print(f"Error copying sample config file: {error_msg}")
                else:
                    print(f"Error copying sample config file: {e}")
            except ImportError:
                print(f"Error copying sample config file: {e}")
            return False

    # If the helper function failed, try using importlib.resources directly
    try:
        # Try to get the sample config from the package resources
        sample_config_content = (
            importlib.resources.files("mmrelay.tools")
            .joinpath("sample_config.yaml")
            .read_text()
        )

        # Write the sample config to the target path
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(sample_config_content)

        # Set secure permissions on Unix systems (600 - owner read/write)
        set_secure_file_permissions(target_path)

        print(f"Generated sample config file at: {target_path}")
        print(
            "\nEdit this file with your Matrix and Meshtastic settings before running mmrelay."
        )
        return True
    except (FileNotFoundError, ImportError, OSError) as e:
        print(f"Error accessing sample_config.yaml via importlib.resources: {e}")

        # Provide Windows-specific guidance if needed
        try:
            from mmrelay.windows_utils import is_windows

            if is_windows():
                print("This may be due to Windows installer packaging differences.")
                print("Trying alternative methods...")
        except ImportError:
            pass

        # Fallback to traditional file paths if importlib.resources fails
        # First, check in the package directory
        package_dir = os.path.dirname(__file__)
        sample_config_paths = [
            # Check in the tools subdirectory of the package
            os.path.join(package_dir, "tools", "sample_config.yaml"),
            # Check in the package directory
            os.path.join(package_dir, "sample_config.yaml"),
            # Check in the repository root
            os.path.join(
                os.path.dirname(os.path.dirname(package_dir)), "sample_config.yaml"
            ),
            # Check in the current directory
            os.path.join(os.getcwd(), "sample_config.yaml"),
        ]

        for path in sample_config_paths:
            if os.path.exists(path):
                try:
                    shutil.copy(path, target_path)
                    print(f"Generated sample config file at: {target_path}")
                    print(
                        "\nEdit this file with your Matrix and Meshtastic settings before running mmrelay."
                    )
                    return True
                except (IOError, OSError) as e:
                    # Provide Windows-specific error guidance if available
                    try:
                        from mmrelay.windows_utils import (
                            get_windows_error_message,
                            is_windows,
                        )

                        if is_windows():
                            error_msg = get_windows_error_message(e)
                            print(
                                f"Error copying sample config file from {path}: {error_msg}"
                            )
                        else:
                            print(f"Error copying sample config file from {path}: {e}")
                    except ImportError:
                        print(f"Error copying sample config file from {path}: {e}")
                    return False

        print("Error: Could not find sample_config.yaml in any location")

        # Last resort: create a minimal config template
        print("\nAttempting to create minimal config template...")
        try:
            minimal_config = _get_minimal_config_template()
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(minimal_config)

            # Set secure permissions on Unix systems
            set_secure_file_permissions(target_path)

            print(f"Created minimal config template at: {target_path}")
            print(
                "\n⚠️  This is a minimal template. Please refer to documentation for full configuration options."
            )
            print("Visit: https://github.com/jeremiah-k/meshtastic-matrix-relay/wiki")
            return True

        except (IOError, OSError) as e:
            print(f"Failed to create minimal config template: {e}")

        # Provide Windows-specific troubleshooting guidance
        try:
            from mmrelay.windows_utils import is_windows

            if is_windows():
                print("\nWindows Troubleshooting:")
                print("1. Check if MMRelay was installed correctly")
                print("2. Try reinstalling with: pipx install --force mmrelay")
                print(
                    "3. Use alternative entry point: python -m mmrelay config generate"
                )
                print("4. Check antivirus software - it may have quarantined files")
                print("5. Run diagnostics: python -m mmrelay config diagnose")
                print("6. Manually create config file using documentation")
        except ImportError:
            pass

        return False


if __name__ == "__main__":
    sys.exit(main())
