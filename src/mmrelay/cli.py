"""
Command-line interface handling for the Meshtastic Matrix Relay.
"""

import argparse
import importlib.resources
import os
import shutil
import sys

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
        help="Matrix password. If provided, --homeserver and --username are also required. For security, consider using interactive mode instead.",
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
    # If there are unknown arguments and we're not in a test environment, warn about them
    if unknown and not any("pytest" in arg or "test" in arg for arg in sys.argv):
        print(f"Warning: Unknown arguments ignored: {unknown}")

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
    Check whether end-to-end encryption (E2EE) runtime dependencies are available.

    Performs a platform check and attempts to import required packages (python-olm, nio.crypto.OlmDevice,
    and nio.store.SqliteStore). Prints a short user-facing status message and guidance.

    Returns:
        bool: True if the platform supports E2EE and all required dependencies can be imported;
              False if running on an unsupported platform (Windows) or if any dependency is missing.
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
        print("❌ Error: E2EE enabled but dependencies not installed")
        print("   Install E2EE support: pipx install 'mmrelay[e2e]'")
        return False


def _validate_credentials_json(config_path):
    """
    Validate that a credentials.json file exists next to the given config and contains the required Matrix authentication fields.

    Searches for credentials.json in the same directory as config_path, then falls back to the application's base directory. If found, the file is parsed as JSON and must include non-empty values for: "homeserver", "access_token", "user_id", and "device_id".

    Parameters:
        config_path (str): Path to the configuration file used to determine the primary search directory for credentials.json.

    Returns:
        bool: True if a valid credentials.json was found and contains all required fields; False otherwise. When invalid or missing fields are detected the function prints a short error and guidance to run the auth login flow.
    """
    try:
        import json

        # Look for credentials.json in the same directory as the config file
        config_dir = os.path.dirname(config_path)
        credentials_path = os.path.join(config_dir, "credentials.json")

        if not os.path.exists(credentials_path):
            # Also try the standard location
            from mmrelay.config import get_base_dir

            standard_credentials_path = os.path.join(get_base_dir(), "credentials.json")
            if os.path.exists(standard_credentials_path):
                credentials_path = standard_credentials_path
            else:
                return False

        # Load and validate credentials
        with open(credentials_path, "r") as f:
            credentials = json.load(f)

        # Check for required fields
        required_fields = ["homeserver", "access_token", "user_id", "device_id"]
        missing_fields = [
            field
            for field in required_fields
            if field not in credentials or not credentials[field]
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
    """Check if a value is a non-empty string."""
    return isinstance(value, str) and value.strip() != ""


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

    pwd = (matrix_section or {}).get("password")
    has_password = (
        isinstance(matrix_section, dict)
        and _is_valid_non_empty_string(pwd)
        and bool(matrix_section.get(CONFIG_KEY_HOMESERVER))
        and bool(matrix_section.get(CONFIG_KEY_BOT_USER_ID))
    )

    if has_valid_credentials:
        print("✅ Using credentials.json for Matrix authentication")
        if sys.platform != WINDOWS_PLATFORM:
            print("   E2EE support available (if enabled)")
        return True

    elif has_access_token:
        print("✅ Using access_token for Matrix authentication")
        print(f"   {msg_for_e2ee_support()}")
        return True
    elif has_password:
        print("✅ Using password in config for initial authentication (credentials.json will be created on first run)")
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
            if not os.path.exists(os.path.dirname(expanded_path)):
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

    # Check credentials (same logic as _validate_credentials_json)
    config_dir = os.path.dirname(config_path)
    credentials_path = os.path.join(config_dir, "credentials.json")

    if not os.path.exists(credentials_path):
        # Also try the standard location
        from mmrelay.config import get_base_dir

        standard_credentials_path = os.path.join(get_base_dir(), "credentials.json")
        if os.path.exists(standard_credentials_path):
            credentials_path = standard_credentials_path

    analysis["credentials_available"] = os.path.exists(credentials_path)

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


def _print_unified_e2ee_analysis(e2ee_status):
    """
    Print a concise, user-facing analysis of E2EE readiness from a centralized status object.

    This formats and prints the platform support, dependency availability, configuration enabled state,
    authentication (credentials.json) presence, and the overall status. If the overall status is not
    "ready", prints actionable fix instructions obtained from mmrelay.e2ee_utils.get_e2ee_fix_instructions.

    Parameters:
        e2ee_status (dict): Status dictionary as returned by get_e2ee_status(config, config_path).
            Expected keys:
                - platform_supported (bool)
                - dependencies_installed (bool)
                - enabled (bool)
                - credentials_available (bool)
                - overall_status (str)
    """
    print("\n🔐 E2EE Configuration Analysis:")

    # Platform support
    if e2ee_status["platform_supported"]:
        print("✅ Platform: E2EE supported")
    else:
        print("❌ Platform: E2EE not supported on Windows")

    # Dependencies
    if e2ee_status["dependencies_installed"]:
        print("✅ Dependencies: E2EE dependencies installed")
    else:
        print("❌ Dependencies: E2EE dependencies not fully installed")

    # Configuration
    if e2ee_status["enabled"]:
        print("✅ Configuration: E2EE enabled")
    else:
        print("❌ Configuration: E2EE disabled")

    # Authentication
    if e2ee_status["credentials_available"]:
        print("✅ Authentication: credentials.json found")
    else:
        print("❌ Authentication: credentials.json not found")

    # Overall status
    print(f"\n📊 Overall Status: {e2ee_status['overall_status'].upper()}")

    # Show fix instructions if needed
    if e2ee_status["overall_status"] != "ready":
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

    Performs these checks:
    - Locates the first existing config file from get_config_paths(args) (parses CLI args if args is None).
    - Verifies YAML syntax and reports syntax errors or style warnings.
    - Ensures the config is non-empty.
    - Validates Matrix authentication: accepts credentials supplied via credentials.json or requires a matrix section with homeserver and bot_user_id plus either access_token or password when credentials.json is absent.
    - Validates end-to-end-encryption (E2EE) configuration and dependencies.
    - Ensures matrix_rooms exists, is a non-empty list, and each room is a dict containing an id.
    - Validates the meshtastic section: requires connection_type and the connection-specific fields (serial_port for serial, host for tcp/network, ble_address for ble). Warns about deprecated connection types.
    - Validates optional meshtastic fields and types (broadcast_enabled, detection_sensor, message_delay >= 2.0, meshnet_name) and reports missing optional settings as guidance.
    - Warns if a deprecated db section is present.
    - Prints a short environment summary on success.

    Side effects:
    - Prints errors, warnings, and status messages to stdout.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments. If None, CLI arguments will be parsed internally.

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
                with open(config_path, "r") as f:
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
                    required_matrix_fields = [CONFIG_KEY_HOMESERVER, CONFIG_KEY_BOT_USER_ID]
                    token = matrix_section.get(CONFIG_KEY_ACCESS_TOKEN)
                    pwd = matrix_section.get("password")
                    has_token = _is_valid_non_empty_string(token)
                    has_password = _is_valid_non_empty_string(pwd)
                    if not (has_token or has_password):
                        print("Error: Missing authentication in 'matrix' section: provide non-empty 'access_token' or 'password'")
                        print(f"   {msg_or_run_auth_login()}")
                        return False

                missing_matrix_fields = [
                    field
                    for field in required_matrix_fields
                    if field not in matrix_section
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
                                f"Error: 'message_delay' must be at least 2.0 seconds (firmware limitation), got: {value}"
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
                        "\nWarning: 'db' section is deprecated. Please use 'database' instead."
                    )
                    print(
                        "This option still works but may be removed in future versions.\n"
                    )

                print("\n✅ Configuration file is valid!")
                return True
            except Exception as e:
                print(f"Error checking configuration: {e}")
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
        args = parse_arguments()

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

    except Exception as e:
        print(f"Unexpected error: {e}")
        return 1


def handle_subcommand(args):
    """
    Dispatch the modern grouped CLI subcommand to its handler and return an exit code.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments (as produced by parse_arguments()). Must have a `command` attribute with one of: "config", "auth", or "service".

    Returns:
        int: Process exit code — 0 on success, non-zero on error or unknown command.
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
    Dispatch the 'config' subgroup commands: "generate" and "check".

    If `args.config_command` is "generate", writes a sample config to the default location.
    If "check", validates the configuration referenced by `args` (see check_config).

    Parameters:
        args (argparse.Namespace): Parsed CLI namespace with a `config_command` attribute.

    Returns:
        int: Process exit code (0 on success, 1 on failure or unknown subcommand).
    """
    if args.config_command == "generate":
        return 0 if generate_sample_config() else 1
    elif args.config_command == "check":
        return 0 if check_config(args) else 1
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

    Runs the login_matrix_bot coroutine to perform authentication for the Matrix/E2EE bot.
    Can run interactively (prompting for inputs) or non-interactively using provided arguments.
    Returns 0 on successful authentication; returns 1 if the login fails, is cancelled by the user (KeyboardInterrupt), or an unexpected error occurs.

    Parameters:
        args: Parsed command-line arguments containing optional homeserver, username, and password.
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
        # All parameters provided - run in non-interactive mode
        pass  # Continue to login
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
        print("Matrix Bot Authentication for E2EE")
        print("===================================")

    try:
        result = asyncio.run(login_matrix_bot(
            homeserver=homeserver,
            username=username,
            password=password,
            logout_others=False
        ))
        return 0 if result else 1
    except KeyboardInterrupt:
        print("\nAuthentication cancelled by user.")
        return 1
    except Exception as e:
        print(f"\nError during authentication: {e}")
        return 1


def handle_auth_status(args):
    """
    Show Matrix authentication status by locating and reading a credentials.json file.

    Searches candidate config directories derived from the provided parsed-arguments namespace for a credentials.json file. If found and readable, prints the file path and the homeserver, user_id, and device_id values. If the file is unreadable or not found, prints guidance to run the authentication flow.

    Parameters:
        args (argparse.Namespace): Parsed CLI arguments used to determine the list of config paths to search.

    Returns:
        int: Exit code — 0 if a readable credentials.json was found, 1 otherwise.
    """
    import json
    import os

    from mmrelay.config import get_config_paths

    print("Matrix Authentication Status")
    print("============================")

    # Check for credentials.json
    config_paths = get_config_paths(args)
    for config_path in config_paths:
        config_dir = os.path.dirname(config_path)
        credentials_path = os.path.join(config_dir, "credentials.json")
        if os.path.exists(credentials_path):
            try:
                with open(credentials_path, "r") as f:
                    credentials = json.load(f)

                print(f"✅ Found credentials.json at: {credentials_path}")
                print(f"   Homeserver: {credentials.get('homeserver', 'Unknown')}")
                print(f"   User ID: {credentials.get('user_id', 'Unknown')}")
                print(f"   Device ID: {credentials.get('device_id', 'Unknown')}")
                return 0
            except Exception as e:
                print(f"❌ Error reading credentials.json: {e}")
                return 1

    print("❌ No credentials.json found")
    print(f"Run '{get_command('auth_login')}' to authenticate")
    return 1


def handle_auth_logout(args):
    """
    Log out the bot from Matrix and remove local session artifacts.

    Prompts for a verification password (unless provided via args.password), warns if the password was supplied on the command line, asks for confirmation unless args.yes is True, and then performs the logout by calling the logout_matrix_bot routine. On success the function returns 0; on failure or cancellation it returns 1. KeyboardInterrupt is treated as a cancellation and returns 1.

    Parameters:
        args (argparse.Namespace): CLI arguments. Relevant attributes:
            password (str | None): If provided and non-empty, used as the verification password.
                If provided as an empty string or omitted, the function will prompt securely.
            yes (bool): If True, skip the interactive confirmation prompt.
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

        if password is None or password == "":
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
    Handle service-related CLI subcommands.

    Currently supports the "install" subcommand which attempts to import and run mmrelay.setup_utils.install_service.
    Returns 0 on success, 1 on failure or for unknown subcommands. Prints an error message if setup utilities cannot be imported.
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
    - a set of fallback filesystem locations relative to the package and current working directory.

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
        with open(target_path, "w") as f:
            f.write(sample_config_content)

        # Set secure permissions on Unix systems (600 - owner read/write)
        set_secure_file_permissions(target_path)

        print(f"Generated sample config file at: {target_path}")
        print(
            "\nEdit this file with your Matrix and Meshtastic settings before running mmrelay."
        )
        return True
    except (FileNotFoundError, ImportError, OSError) as e:
        print(f"Error accessing sample_config.yaml: {e}")

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
                    print(f"Error copying sample config file from {path}: {e}")
                    return False

        print("Error: Could not find sample_config.yaml")
        return False
