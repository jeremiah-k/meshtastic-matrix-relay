"""
Command-line interface handling for the Meshtastic Matrix Relay.
"""

import argparse
import importlib.resources
import os
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
from mmrelay.config import get_config_paths, validate_yaml_syntax
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
    Parse and validate command-line arguments for the Meshtastic Matrix Relay CLI.

    Supports options for specifying configuration file, data directory, logging preferences, version display, sample configuration generation, service installation, and configuration validation. On Windows, also accepts a deprecated positional argument for the config file path with a warning. Ignores unknown arguments outside of test environments and warns if any are present.

    Returns:
        argparse.Namespace: Parsed command-line arguments.
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
    auth_login_parser = auth_subparsers.add_parser(
        "login",
        help="Authenticate with Matrix",
        description="Set up Matrix authentication for E2EE support",
    )
    auth_login_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip status check and force new authentication",
    )
    auth_subparsers.add_parser(
        "status",
        help="Check authentication status",
        description="Display current Matrix authentication status",
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
    print(f"MMRelay v{__version__}")


def _validate_e2ee_dependencies():
    """Check if E2EE dependencies are available."""
    if sys.platform == WINDOWS_PLATFORM:
        print("❌ Error: E2EE is not supported on Windows")
        print("   Reason: python-olm library requires native C libraries")
        print("   Solution: Use Linux or macOS for E2EE support")
        return False

    # Check if python-olm is available
    try:
        import olm  # noqa: F401

        print("✅ E2EE dependencies are installed")
        return True
    except ImportError:
        print("❌ Error: E2EE enabled but dependencies not installed")
        print("   Install E2EE support: pipx install mmrelay[e2e]")
        return False


def _validate_credentials_json(config_path):
    """Validate credentials.json file exists and has required fields."""
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


def _validate_matrix_authentication(config_path, matrix_section):
    """Validate Matrix authentication configuration."""
    has_valid_credentials = _validate_credentials_json(config_path)
    has_access_token = matrix_section and "access_token" in matrix_section

    if has_valid_credentials:
        print("✅ Using credentials.json for Matrix authentication")
        if sys.platform != WINDOWS_PLATFORM:
            print("   E2EE support available (if enabled)")
        return True

    elif has_access_token:
        print("✅ Using access_token for Matrix authentication")
        print(f"   {msg_for_e2ee_support()}")
        return True

    else:
        print("❌ Error: No Matrix authentication configured")
        print(f"   {msg_setup_auth()}")
        return False


def _validate_e2ee_config(config, matrix_section, config_path):
    """Validate E2EE configuration and authentication."""
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


def _print_environment_summary():
    """Print environment and capability summary."""
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

            print("   E2EE Support: ✅ Available and installed")
        except ImportError:
            print("   E2EE Support: ⚠️  Available but not installed")
            print("   Install: pipx install mmrelay[e2e]")


def check_config(args=None):
    """
    Validate the application's YAML configuration file for required sections and fields.

    Reads candidate config files (from get_config_paths), validates YAML syntax via validate_yaml_syntax, and performs structural and semantic checks:
    - Ensures the config is not empty.
    - Verifies the 'matrix' section contains HOMESERVER, ACCESS_TOKEN, and BOT_USER_ID.
    - Verifies 'matrix_rooms' exists, is a non-empty list, and each room is a dict containing an 'id'.
    - Verifies the 'meshtastic' section contains a valid connection_type and the connection-type-specific fields:
      - serial -> serial_port
      - tcp/network -> host
      - ble -> ble_address
      - warns if connection_type == 'network' (deprecated)
    - Validates optional meshtastic fields and types: broadcast_enabled (bool), detection_sensor (bool), message_delay (int|float, >= 2.0), meshnet_name (str); reports missing optional fields as guidance.
    - Warns if a deprecated 'db' section is present.

    Side effects:
    - Prints validation errors, warnings, and status messages to stdout.

    Parameters:
        args (argparse.Namespace | None): Parsed CLI arguments; if None, CLI args are parsed internally.

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
                            "   Either add matrix section with access_token and bot_user_id,"
                        )
                        print(f"   {msg_or_run_auth_login()}")
                        return False

                    matrix_section = config[CONFIG_SECTION_MATRIX]
                    required_matrix_fields = [
                        CONFIG_KEY_HOMESERVER,
                        CONFIG_KEY_ACCESS_TOKEN,
                        CONFIG_KEY_BOT_USER_ID,
                    ]

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

                # Validate E2EE configuration and authentication
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

                # Print environment summary
                _print_environment_summary()

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
    Runs the Meshtastic Matrix Relay CLI, handling argument parsing, command execution, and error reporting.

    Returns:
        int: Exit code indicating success (0) or failure (non-zero).
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
    """Handle modern grouped subcommand interface.

    Args:
        args: Parsed arguments with subcommand

    Returns:
        int: Exit code (0 for success, non-zero for failure)
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
    """Handle config subcommands.

    Args:
        args: Parsed arguments with config subcommand

    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    if args.config_command == "generate":
        return 0 if generate_sample_config() else 1
    elif args.config_command == "check":
        return 0 if check_config(args) else 1
    else:
        print(f"Unknown config command: {args.config_command}")
        return 1


def handle_auth_command(args):
    """Handle auth subcommands.

    Args:
        args: Parsed arguments with auth subcommand

    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    if hasattr(args, "auth_command") and args.auth_command == "status":
        return handle_auth_status(args)
    else:
        # Default to login for both legacy --auth and new auth login
        return handle_auth_login(args)


def handle_auth_login(args):
    """Handle auth login command.

    Args:
        args: Parsed arguments

    Returns:
        int: Exit code (0 for success, non-zero for failure)
    """
    import asyncio

    from mmrelay.matrix_utils import login_matrix_bot

    # Show header
    print("Matrix Bot Authentication for E2EE")
    print("===================================")

    try:
        # Pass --force flag as logout_others parameter if provided
        logout_others = getattr(args, 'force', False)
        result = asyncio.run(login_matrix_bot(logout_others=logout_others))
        return 0 if result else 1
    except KeyboardInterrupt:
        print("\nAuthentication cancelled by user.")
        return 1
    except Exception as e:
        print(f"\nError during authentication: {e}")
        return 1


def handle_auth_status(args):
    """Handle auth status command.

    Args:
        args: Parsed arguments

    Returns:
        int: Exit code (0 for success, non-zero for failure)
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


def handle_service_command(args):
    """Handle service subcommands.

    Args:
        args: Parsed arguments with service subcommand

    Returns:
        int: Exit code (0 for success, non-zero for failure)
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
    """Handle legacy CLI flags like --generate-config, --install-service, and --check-config.

    Note:
        This helper may call sys.exit() for certain flags and is kept only for backward compatibility.
        Prefer using the modern grouped subcommands via main()/handle_subcommand().

    Args:
        args: The parsed command-line arguments

    Returns:
        bool: True if a command was handled (and process may already have exited),
              False if normal execution should continue.
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
    Generate a sample configuration file (`config.yaml`) in the default location if one does not already exist.

    Attempts to copy a sample config from various sources, handling directory creation and file system errors gracefully. Prints informative messages on success or failure.

    Returns:
        bool: True if the sample config was generated successfully, False otherwise.
    """

    import shutil

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
