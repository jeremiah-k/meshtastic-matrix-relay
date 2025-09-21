"""
Command-line interface handling for the Meshtastic Matrix Relay.
"""

import argparse
import importlib.resources
import os
import shutil
import sys
from collections.abc import Mapping

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
from mmrelay.tools import get_sample_config_path

# =============================================================================
# CLI Argument Parsing and Command Handling
# =============================================================================


def parse_arguments():
    """
    Parse command-line arguments for the Meshtastic Matrix Relay CLI.

    Builds a modern grouped CLI with subcommands for config (generate, check), auth (login, status),
    and service (install), while preserving hidden legacy flags (--generate-config, --install-service,
    --check-config, --auth) for backward compatibility. Supports global options: --config,
    --data-dir, --log-level, --logfile, and --version.

    Unknown arguments are ignored when running outside of test environments (parsed via
    parse_known_args); a warning is printed if unknown args are present and the process does not
    appear to be a test run.

    Returns:
        argparse.Namespace: The parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Meshtastic Matrix Relay - Bridge between Meshtastic and Matrix"
    )
    parser.add_argument("--config", help="Path to config file", default=None)
    parser.add_argument(
        "--data-dir",
        help="Base directory for all data (logs, database, plugins)",
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
        help="Path to log file (can be overridden by --data-dir)",
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
        "diagnose",
        help="Diagnose configuration system issues",
        description="Test config generation capabilities and troubleshoot platform-specific issues",
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
        description="Install or update the systemd user service for MMRelay",
    )

    # Use parse_known_args to handle unknown arguments gracefully (e.g., pytest args)
    args, unknown = parser.parse_known_args()
    # If there are unknown arguments and we're not in a test invocation, warn about them
    # Heuristic: suppress warning when pytest appears in argv (unit tests may pass extra args)
    if unknown and not any("pytest" in arg or "py.test" in arg for arg in sys.argv):
        print(f"Warning: Unknown arguments ignored: {unknown}", file=sys.stderr)

    return args


def get_version():
    """
    Returns the current version of the application.

    Returns:
        str: The version string
    """
    return __version__


def print_version():
    """
    Print the version in a simple format.
    """
    print(f"MMRelay version {__version__}")


def _validate_e2ee_dependencies():
    """
    Return True if the current platform supports E2EE and the required Python packages are importable.
    
    Checks platform compatibility (Windows is considered unsupported) and tries to import the native and nio-backed E2EE components required by MMRelay (python-olm, nio.crypto.OlmDevice, nio.store.SqliteStore). Prints concise, user-facing status and guidance messages when support or dependencies are missing.
    
    Returns:
        bool: True when the platform is supported and all imports succeed; False otherwise.
    """
    if sys.platform == WINDOWS_PLATFORM:
        print("❌ Error: E2EE is not supported on Windows")
        print("   Reason: python-olm library requires native C libraries")
        print("   Solution: Use Linux or macOS for E2EE support")
        return False

    # Check if E2EE dependencies are available
    try:
        import olm  # noqa: F401
        from nio.crypto import OlmDevice  # noqa: F401
        from nio.store import SqliteStore  # noqa: F401

        print("✅ E2EE dependencies are installed")
        return True
    except ImportError:
        print("❌ Error: E2EE dependencies not installed")
        print("   End-to-end encryption features require additional dependencies")
        print("   Install E2EE support: pipx install 'mmrelay[e2e]'")
        return False


def _validate_credentials_json(config_path):
    """
    Validate that a credentials.json file exists (adjacent to config_path or in the base directory) and contains the required Matrix session fields.

    Checks for a credentials.json via _find_credentials_json_path(config_path). If found, the file is parsed as JSON and must include non-empty string values for the keys "homeserver", "access_token", "user_id", and "device_id". On validation failure the function prints a brief error and guidance to run the auth login flow.

    Parameters:
        config_path (str): Path to the configuration file used to determine the primary search directory for credentials.json.

    Returns:
        bool: True if a credentials.json was found and contains all required non-empty fields; False otherwise.
    """
    try:
        import json

        # Look for credentials.json using helper function
        credentials_path = _find_credentials_json_path(config_path)
        if not credentials_path:
            return False

        # Load and validate credentials
        with open(credentials_path, "r", encoding="utf-8") as f:
            credentials = json.load(f)

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
    except Exception as e:
        print(f"❌ Error: Could not validate credentials.json: {e}")
        return False


def _is_valid_non_empty_string(value) -> bool:
    """
    Return True if value is a string containing non-whitespace characters.

    Checks that the input is an instance of `str` and that stripping whitespace
    does not produce an empty string.

    Returns:
        bool: True when value is a non-empty, non-whitespace-only string; otherwise False.
    """
    return isinstance(value, str) and value.strip() != ""


def _has_valid_password_auth(matrix_section):
    """
    Return True if the given Matrix config section contains valid password-based authentication settings.

    The function expects matrix_section to be a dict-like mapping from configuration keys to values.
    It validates that:
    - `homeserver` and `bot_user_id` are present and are non-empty strings (after trimming),
    - `password` is present and is a string (it may be an empty string, which is accepted).

    If matrix_section is not a dict, the function returns False.

    Parameters:
        matrix_section: dict-like Matrix configuration section (may be the parsed "matrix" config).

    Returns:
        bool: True when password-based authentication is correctly configured as described above; otherwise False.
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


def _validate_matrix_authentication(config_path, matrix_section):
    """
    Determine whether Matrix authentication is configured and usable.

    Checks for a valid credentials.json (located relative to the provided config path) and, if not present,
    falls back to an access_token in the provided matrix_section. Returns True when authentication
    information is found and usable; returns False when no authentication is configured.

    Parameters:
        config_path (str | os.PathLike): Path to the application's YAML config file; used to locate a
            credentials.json candidate in the same directory or standard locations.
        matrix_section (Mapping | None): The parsed "matrix" configuration section (mapping-like). If
            provided, an "access_token" key will be considered as a valid fallback when credentials.json
            is absent.

    Returns:
        bool: True if a valid authentication method (credentials.json or access_token) is available,
        False otherwise.

    Notes:
        - The function prefers credentials.json over an access_token if both are present.
        - The function emits user-facing status messages describing which authentication source is used
          and whether E2EE support is available.
    """
    has_valid_credentials = _validate_credentials_json(config_path)
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


def _validate_e2ee_config(config, matrix_section, config_path):
    """
    Validate end-to-end encryption (E2EE) configuration and Matrix authentication for the given config.

    Performs these checks:
    - Confirms Matrix authentication is available (via credentials.json or matrix access token); returns False if authentication is missing or invalid.
    - If no matrix section is present, treats E2EE as not configured and returns True.
    - If E2EE/encryption is enabled in the matrix config, verifies platform/dependency support and inspects the configured store path. If the store directory does not yet exist, a note is printed indicating it will be created.

    Parameters:
        config_path (str): Path to the active configuration file (used to locate credentials.json and related auth artifacts).
        matrix_section (dict | None): The "matrix" subsection of the parsed config (may be None or empty).
        config (dict): Full parsed configuration (unused for most checks but kept for consistency with caller signature).

    Returns:
        bool: True if configuration and required authentication/dependencies are valid (or E2EE is not configured); False if validation fails.

    Side effects:
        Prints informational or error messages about authentication, dependency checks, and E2EE store path status.
    """
    # First validate authentication
    if not _validate_matrix_authentication(config_path, matrix_section):
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
                print(f"ℹ️  Note: E2EE store directory will be created: {expanded_path}")

        print("✅ E2EE configuration is valid")

    return True


def _analyze_e2ee_setup(config, config_path):
    """
    Analyze local E2EE readiness without contacting Matrix.

    Performs an offline inspection of the environment and configuration to determine
    whether end-to-end encryption (E2EE) can be used. Checks platform support
    (Windows is considered unsupported), presence of required Python dependencies
    (olm and selected nio components), whether E2EE is enabled in the provided
    config, and whether a credentials.json is available adjacent to the supplied
    config_path or in the standard base directory.

    Parameters:
        config (dict): Parsed configuration (typically from config.yaml). Only the
            "matrix" section is consulted to detect E2EE/encryption enablement.
        config_path (str): Path to the configuration file used to locate a
            credentials.json sibling; also used to resolve an alternate standard
            credentials location.

    Returns:
        dict: Analysis summary with these keys:
          - config_enabled (bool): True if E2EE/encryption is enabled in config.
          - dependencies_available (bool): True if required E2EE packages are
            importable.
          - credentials_available (bool): True if a usable credentials.json was
            found.
          - platform_supported (bool): False on unsupported platforms (Windows).
          - overall_status (str): One of "ready", "disabled", "not_supported",
            "incomplete", or "unknown" describing the combined readiness.
          - recommendations (list): Human-actionable strings suggesting fixes or
            next steps (e.g., enable E2EE in config, install dependencies, run
            auth login).
    """
    analysis = {
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
    try:
        import olm  # noqa: F401
        from nio.crypto import OlmDevice  # noqa: F401
        from nio.store import SqliteStore  # noqa: F401

        analysis["dependencies_available"] = True
    except ImportError:
        analysis["dependencies_available"] = False
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
    credentials_path = _find_credentials_json_path(config_path)
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


def _find_credentials_json_path(config_path: str | None) -> str | None:
    """
    Return the filesystem path to a credentials.json file if one can be found, otherwise None.

    Search order:
    1. A credentials.json file located in the same directory as the provided config_path.
    2. A credentials.json file in the application's base directory (get_base_dir()).

    Parameters:
        config_path (str | None): Path to the configuration file used to derive the adjacent credentials.json location.

    Returns:
        str | None: Absolute path to the discovered credentials.json, or None if no file is found.
    """
    if not config_path:
        from mmrelay.config import get_base_dir

        standard = os.path.join(get_base_dir(), "credentials.json")
        return standard if os.path.exists(standard) else None

    config_dir = os.path.dirname(config_path)
    candidate = os.path.join(config_dir, "credentials.json")
    if os.path.exists(candidate):
        return candidate
    from mmrelay.config import get_base_dir

    standard = os.path.join(get_base_dir(), "credentials.json")
    return standard if os.path.exists(standard) else None


def _print_unified_e2ee_analysis(e2ee_status):
    """
    Print a concise, user-facing analysis of E2EE readiness.

    Given a status dictionary produced by the E2EE analysis routines, prints platform support,
    dependency availability, whether E2EE is enabled in configuration, whether credentials.json
    is available, and the overall status. If the overall status is not "ready", prints actionable
    fix instructions.

    Parameters:
        e2ee_status (dict): Status dictionary with (at least) the following keys:
            - platform_supported (bool): whether the current OS/platform supports E2EE.
            - dependencies_installed or dependencies_available (bool): whether required E2EE
              Python packages and runtime dependencies are present.
            - enabled or config_enabled (bool): whether E2EE is enabled in the configuration.
            - credentials_available (bool): whether a usable credentials.json is present.
            - overall_status (str): high-level status ("ready", "disabled", "incomplete", etc.).
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


def _print_e2ee_analysis(analysis):
    """
    Print a user-facing analysis of end-to-end encryption (E2EE) readiness to standard output.

    Parameters:
        analysis (dict): Analysis results with the following keys:
            - dependencies_available (bool): True if required E2EE dependencies (e.g., python-olm) are present.
            - credentials_available (bool): True if a usable credentials.json was found.
            - platform_supported (bool): True if the current platform supports E2EE (Windows is considered unsupported).
            - config_enabled (bool): True if E2EE is enabled in the configuration.
            - overall_status (str): One of "ready", "disabled", "not_supported", or "incomplete" indicating the aggregated readiness.
            - recommendations (list[str]): User-facing remediation steps or suggestions (may be empty).

    Returns:
        None

    Notes:
        - This function only prints a human-readable report and does not modify state.
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


def _print_environment_summary():
    """
    Print a concise environment summary including platform, Python version, and Matrix E2EE capability.

    Provides:
    - Platform and Python version.
    - Whether E2EE is supported on the current platform (Windows is reported as not supported).
    - Whether the `olm` dependency is installed when E2EE is supported, and a brief installation hint if missing.

    This function writes human-facing lines to standard output and returns None.
    """
    print("\n🖥️  Environment Summary:")
    print(f"   Platform: {sys.platform}")
    print(f"   Python: {sys.version.split()[0]}")

    # E2EE capability check
    if sys.platform == WINDOWS_PLATFORM:
        print("   E2EE Support: ❌ Not available (Windows limitation)")
        print("   Matrix Support: ✅ Available")
    else:
        try:
            import olm  # noqa: F401
            from nio.crypto import OlmDevice  # noqa: F401
            from nio.store import SqliteStore  # noqa: F401

            print("   E2EE Support: ✅ Available and installed")
        except ImportError:
            print("   E2EE Support: ⚠️  Available but not installed")
            print("   Install: pipx install 'mmrelay[e2e]'")


def check_config(args=None):
    """
    Validate the application's YAML configuration file and its required sections.

    Performs these checks: locates the first existing config file from get_config_paths(args),
    validates YAML syntax and non-empty content, verifies Matrix authentication (prefers
    credentials.json but accepts access_token or password in the matrix section), performs
    E2EE configuration and dependency checks, validates matrix_rooms and meshtastic sections
    (including connection-specific requirements), checks a set of optional meshtastic settings
    (and value constraints), and warns about deprecated sections.

    Side effects:
    - Prints human-readable errors, warnings, and status messages to stdout.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments; if None, CLI arguments are parsed internally.

    Returns:
        bool: True if a configuration file was found and passed all checks; False otherwise.
    """

    # If args is None, parse them now
    if args is None:
        args = parse_arguments()

    config_paths = get_config_paths(args)
    config_path = None

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

                # Check if we have valid credentials.json first
                has_valid_credentials = _validate_credentials_json(config_path)

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
                    required_matrix_fields = (
                        []
                    )  # No fields required from config when using credentials.json
                else:
                    # Without credentials.json, require full matrix section
                    if CONFIG_SECTION_MATRIX not in config:
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
                except Exception as e:
                    print(f"\n⚠️  Could not perform E2EE analysis: {e}")
                    print("   Falling back to basic E2EE validation...")
                    if not _validate_e2ee_config(config, matrix_section, config_path):
                        return False

                # Check matrix_rooms section
                if "matrix_rooms" not in config or not config["matrix_rooms"]:
                    print("Error: Missing or empty 'matrix_rooms' section in config")
                    return False

                if not isinstance(config["matrix_rooms"], list):
                    print("Error: 'matrix_rooms' must be a list")
                    return False

                for i, room in enumerate(config["matrix_rooms"]):
                    if not isinstance(room, dict):
                        print(
                            f"Error: Room {i+1} in 'matrix_rooms' must be a dictionary"
                        )
                        return False

                    if "id" not in room:
                        print(
                            f"Error: Room {i+1} in 'matrix_rooms' is missing the 'id' field"
                        )
                        return False

                # Check meshtastic section
                if CONFIG_SECTION_MESHTASTIC not in config:
                    print("Error: Missing 'meshtastic' section in config")
                    return False

                meshtastic_section = config[CONFIG_SECTION_MESHTASTIC]
                if "connection_type" not in meshtastic_section:
                    print("Error: Missing 'connection_type' in 'meshtastic' section")
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
                    return False

                if (
                    connection_type in [CONNECTION_TYPE_TCP, CONNECTION_TYPE_NETWORK]
                    and CONFIG_KEY_HOST not in meshtastic_section
                ):
                    print("Error: Missing 'host' for 'tcp' connection type")
                    return False

                if (
                    connection_type == CONNECTION_TYPE_BLE
                    and CONFIG_KEY_BLE_ADDRESS not in meshtastic_section
                ):
                    print("Error: Missing 'ble_address' for 'ble' connection type")
                    return False

                # Check for other important optional configurations and provide guidance
                optional_configs = {
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

                warnings = []
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


def main():
    """
    Entry point for the MMRelay command-line interface; parses arguments, dispatches commands, and returns an appropriate process exit code.

    This function:
    - Parses CLI arguments (modern grouped subcommands and hidden legacy flags).
    - If a modern subcommand is provided, dispatches to the grouped subcommand handlers.
    - If legacy flags are present, emits deprecation warnings and executes the corresponding legacy behavior (config check/generate, service install, auth, version).
    - If no command flags are present, attempts to run the main runtime.
    - Catches and reports import or unexpected errors and maps success/failure to exit codes.

    Returns:
        int: Exit code (0 on success, non-zero on failure).
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

        # Handle the --data-dir option
        if args and args.data_dir:
            import mmrelay.config

            # Set the global custom_data_dir variable
            mmrelay.config.custom_data_dir = os.path.abspath(args.data_dir)
            # Create the directory if it doesn't exist
            os.makedirs(mmrelay.config.custom_data_dir, exist_ok=True)

        # Handle subcommands first (modern interface)
        if hasattr(args, "command") and args.command:
            return handle_subcommand(args)

        # Handle legacy flags (with deprecation warnings)
        if args.check_config:
            print(get_deprecation_warning("--check-config"))
            return 0 if check_config(args) else 1

        if args.install_service:
            print(get_deprecation_warning("--install-service"))
            try:
                from mmrelay.setup_utils import install_service

                return 0 if install_service() else 1
            except ImportError as e:
                print(f"Error importing setup utilities: {e}")
                return 1

        if args.generate_config:
            print(get_deprecation_warning("--generate-config"))
            return 0 if generate_sample_config() else 1

        if args.version:
            print_version()
            return 0

        if args.auth:
            print(get_deprecation_warning("--auth"))
            return handle_auth_command(args)

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


def handle_subcommand(args):
    """
    Dispatch the modern grouped CLI subcommand to the appropriate handler and return an exit code.

    The function expects an argparse.Namespace from parse_arguments() with a `command`
    attribute set to one of: "config", "auth", or "service". Delegates to the
    corresponding handler and returns its exit code. If `command` is unknown,
    prints an error and returns 1.
    """
    if args.command == "config":
        return handle_config_command(args)
    elif args.command == "auth":
        return handle_auth_command(args)
    elif args.command == "service":
        return handle_service_command(args)

    else:
        print(f"Unknown command: {args.command}")
        return 1


def handle_config_command(args):
    """
    Dispatch the "config" command group to the selected subcommand handler.
    
    Supported subcommands:
    - "generate": create or update the sample configuration file at the preferred location.
    - "check": validate the resolved configuration file (delegates to check_config).
    - "diagnose": run a sequence of non-destructive diagnostics and print a report (delegates to handle_config_diagnose).
    
    Parameters:
        args (argparse.Namespace): CLI namespace containing `config_command` (one of "generate", "check", "diagnose") and any subcommand-specific options.
    
    Returns:
        int: Exit code (0 on success, 1 on failure or for unknown subcommands).
    """
    if args.config_command == "generate":
        return 0 if generate_sample_config() else 1
    elif args.config_command == "check":
        return 0 if check_config(args) else 1
    elif args.config_command == "diagnose":
        return handle_config_diagnose(args)
    else:
        print(f"Unknown config command: {args.config_command}")
        return 1


def handle_auth_command(args):
    """
    Dispatch the "auth" CLI subcommand to the appropriate handler.

    If args.auth_command is "status" calls handle_auth_status; if "logout" calls handle_auth_logout;
    any other value (or missing attribute) defaults to handle_auth_login.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments. Expected to optionally provide `auth_command`
            with one of "login", "status", or "logout".

    Returns:
        int: Exit code from the invoked handler (0 = success, non-zero = failure).
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


def handle_auth_login(args):
    """
    Run the Matrix bot login flow and return a CLI-style exit code.

    Performs Matrix bot authentication either interactively (prompts the user) or non-interactively
    when all three parameters (--homeserver, --username, --password) are provided on the command line.
    For non-interactive mode, --homeserver and --username must be non-empty strings; --password may be
    an empty string (some flows will prompt). Supplying some but not all of the three parameters
    is treated as an error and the function exits with a non-zero status.

    Returns:
        int: 0 on successful authentication, 1 on failure, cancellation (KeyboardInterrupt), or unexpected errors.

    Parameters:
        args: Parsed CLI namespace; may contain attributes `homeserver`, `username`, and `password`.
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
   Missing: {', '.join(missing_params)}

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
            from mmrelay.log_utils import get_logger

            logger = get_logger("CLI")
            logger.debug(f"Failed to silently check E2EE status: {e}")
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


def handle_auth_status(args):
    """
    Print the Matrix authentication status by locating and reading a credentials.json file.

    Searches for credentials.json next to each discovered config file (in preference order),
    then falls back to the application's base directory. If a readable credentials.json is
    found, prints its path and the homeserver, user_id, and device_id values.

    Parameters:
        args: argparse.Namespace
            Parsed CLI arguments (used to locate config file paths).

    Returns:
        int: Exit code — 0 if a valid credentials.json was found and read, 1 otherwise.

    Side effects:
        Writes human-readable status messages to stdout.
    """
    import json

    from mmrelay.config import get_base_dir, get_config_paths

    print("Matrix Authentication Status")
    print("============================")

    config_paths = get_config_paths(args)

    # Developer note: Build a de-duplicated sequence of candidate locations,
    # preserving preference order: each config-adjacent credentials.json first,
    # then the standard base-dir fallback.
    seen = set()
    candidate_paths = []
    for p in (
        os.path.join(os.path.dirname(cp), "credentials.json") for cp in config_paths
    ):
        if p not in seen:
            candidate_paths.append(p)
            seen.add(p)
    base_candidate = os.path.join(get_base_dir(), "credentials.json")
    if base_candidate not in seen:
        candidate_paths.append(base_candidate)

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


def handle_auth_logout(args):
    """
    Log out the Matrix bot and remove local session artifacts.

    Prompts for a verification password (unless a non-empty password is provided via args.password),
    optionally asks for interactive confirmation (skipped if args.yes is True), and attempts to clear
    local session data (credentials, E2EE store) and invalidate the bot's access token.

    Parameters:
        args (argparse.Namespace): CLI arguments with the following relevant attributes:
            password (str | None): If a non-empty string is provided, it will be used as the
                verification password. If None or an empty string, the function prompts securely.
            yes (bool): If True, skip the confirmation prompt.

    Returns:
        int: 0 on successful logout, 1 on failure or if the operation is cancelled (including
             KeyboardInterrupt).
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


def handle_service_command(args):
    """
    Dispatch service-related subcommands.
    
    Currently recognizes args.service_command == "install": calls the package's install_service routine and returns 0 on success or 1 on failure.
    For any other service_command the function prints an error and returns 1.
    
    Parameters:
        args: argparse.Namespace containing at least the `service_command` attribute.
    
    Returns:
        int: Exit code (0 on success, 1 on error). The function may print error messages for unknown commands or import failures.
    """
    if args.service_command == "install":
        try:
            from mmrelay.setup_utils import install_service

            return 0 if install_service() else 1
        except ImportError as e:
            print(f"Error importing setup utilities: {e}")
            return 1
    else:
        print(f"Unknown service command: {args.service_command}")
        return 1


def _diagnose_config_paths(args):
    """
    Print a diagnostic summary of resolved configuration file search paths and their directory accessibility.
    
    Given the parsed CLI arguments (forwarded to get_config_paths), this prints a numbered list of candidate config file paths and a simple status for each directory:
    - ✅ directory exists and is writable
    - ⚠️ directory exists but is not writable
    - ❌ directory does not exist
    
    Parameters:
        args (argparse.Namespace): Parsed command-line arguments used to determine the config search order via get_config_paths.
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


def _diagnose_sample_config_accessibility():
    """
    Check accessibility of the bundled sample configuration file.
    
    Performs two checks and prints human-facing diagnostic lines:
    1. Resolves the sample config path via mmrelay.tools.get_sample_config_path() and reports whether that filesystem path exists.
    2. Attempts to read the embedded resource "sample_config.yaml" using importlib.resources as a fallback and reports success and content length.
    
    Returns:
        bool: True if the filesystem sample config exists at the resolved path; False otherwise.
    
    Notes:
        - Resource read failures are handled and reported; this function does not raise for importlib.resources-related errors.
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


def _diagnose_platform_specific(args):
    """
    Run platform-specific diagnostic checks.
    
    On Windows, run Windows-specific requirement checks and a configuration-generation
    test (via mmrelay.windows_utils) and print per-component results and warnings.
    On non-Windows systems, report that platform-specific tests are not required.
    
    Parameters:
        args: Parsed CLI arguments namespace; only used when invoking the Windows
            config-generation test.
    
    Returns:
        bool: True when running on Windows (Windows checks were executed), False
        for non-Windows platforms.
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


def _get_minimal_config_template():
    """
    Return a minimal MMRelay YAML configuration template used as a fallback when the packaged sample_config.yaml cannot be located.

    The template contains a small, functional configuration (matrix connection hints, a serial meshtastic connection, one room entry, and basic logging) that users can edit to create a working config file.

    Returns:
        str: A YAML-formatted minimal configuration template.
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


def _diagnose_minimal_config_template():
    """
    Validate the built-in minimal YAML configuration template and report a concise result.
    
    Parses the template returned by _get_minimal_config_template() with yaml.safe_load to verify it is valid YAML, then prints a single-line status indicating validity and the template length (on success) or the YAML error (on failure). This function is a non-destructive diagnostic helper and does not return a value.
    """
    print("4. Testing minimal config template fallback...")
    try:
        template = _get_minimal_config_template()
        yaml.safe_load(template)
        print(f"   Minimal template: ✅ ({len(template)} chars, valid YAML)")
    except yaml.YAMLError as e:
        print(f"   Minimal template: ❌ ({e})")

    print()


def handle_config_diagnose(args):
    """
    Run a series of non-destructive diagnostics for the MMRelay configuration subsystem and print a human-readable report.
    
    Performs four checks in order:
    1. Resolve configured config file search paths and report directory accessibility.
    2. Verify accessibility of the packaged sample configuration (filesystem and importlib.resources fallback).
    3. Run platform-specific diagnostics (Windows-focused checks when applicable).
    4. Validate the built-in minimal fallback YAML configuration template.
    
    Parameters:
        args (argparse.Namespace): Parsed CLI arguments used to resolve config locations and influence platform checks.
    
    Returns:
        int: Exit code (0 on success, 1 on failure). The function prints detailed diagnostic output to stdout/stderr and handles exceptions internally; on failure a non-zero exit code is returned.
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


if __name__ == "__main__":
    import sys

    sys.exit(main())


def handle_cli_commands(args):
    """
    Handle legacy CLI flags (--version, --install-service, --generate-config, --check-config).

    This helper processes backward-compatible flags and may call sys.exit() for flags that perform an immediate action
    (e.g., install service, check config). Prefer the modern grouped subcommands (e.g., `mmrelay config`, `mmrelay auth`)
    when available.

    Parameters:
        args (argparse.Namespace): Parsed command-line arguments produced by parse_arguments().

    Returns:
        bool: True if a legacy command was handled (the process may have already exited), False to continue normal flow.
    """
    # Handle --version
    if args.version:
        print_version()
        return True

    # Handle --install-service
    if args.install_service:
        from mmrelay.setup_utils import install_service

        success = install_service()
        import sys

        sys.exit(0 if success else 1)

    # Handle --generate-config
    if args.generate_config:
        if generate_sample_config():
            # Exit with success if config was generated
            return True
        else:
            # Exit with error if config generation failed
            import sys

            sys.exit(1)

    # Handle --check-config
    if args.check_config:
        import sys

        sys.exit(0 if check_config() else 1)

    # No commands were handled
    return False


def generate_sample_config():
    """
    Generate a sample configuration file at the highest-priority config path if no config already exists.

    If an existing config file is found in any candidate path (from get_config_paths()), this function aborts and prints its location. Otherwise it creates a sample config at the first candidate path. Sources tried, in order, are:
    - the path returned by get_sample_config_path(),
    - the packaged resource mmrelay.tools:sample_config.yaml via importlib.resources,
    - a set of fallback filesystem locations relative to the package and current working directory,
    - a minimal configuration template as a last resort.

    On success the sample is written to disk and (on Unix-like systems) secure file permissions are applied (owner read/write, 0o600). Returns True when a sample config is successfully generated and False on any error or if a config already exists.
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
