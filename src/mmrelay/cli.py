"""
Command-line interface handling for the Meshtastic Matrix Relay.
"""

import argparse
import importlib.resources
import os
import sys

import yaml
from yaml.loader import SafeLoader

# Import version from package
from mmrelay import __version__
from mmrelay.config import get_config_paths
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
    parser.add_argument(
        "--generate-config",
        action="store_true",
        help="Generate a sample config.yaml file",
    )
    parser.add_argument(
        "--install-service",
        action="store_true",
        help="Install or update the systemd user service",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Check if the configuration file is valid",
    )

    # Windows-specific handling for backward compatibility
    # On Windows, add a positional argument for the config file path
    if sys.platform == WINDOWS_PLATFORM:
        parser.add_argument(
            "config_path", nargs="?", help=argparse.SUPPRESS, default=None
        )

    # Use parse_known_args to handle unknown arguments gracefully (e.g., pytest args)
    args, unknown = parser.parse_known_args()
    # If there are unknown arguments and we're not in a test environment, warn about them
    if unknown and not any("pytest" in arg or "test" in arg for arg in sys.argv):
        print(f"Warning: Unknown arguments ignored: {unknown}")

    # If on Windows and a positional config path is provided but --config is not, use the positional one
    if (
        sys.platform == WINDOWS_PLATFORM
        and hasattr(args, "config_path")
        and args.config_path
        and not args.config
    ):
        args.config = args.config_path
        # Print a deprecation warning
        print("Warning: Using positional argument for config file is deprecated.")
        print(f"Please use --config {args.config_path} instead.")
        # Remove the positional argument from sys.argv to avoid issues with other argument parsers
        if args.config_path in sys.argv:
            sys.argv.remove(args.config_path)

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


def check_config(args=None):
    """
    Validates the application's configuration file for required structure and fields.

    If a configuration file is found, checks for the presence and correctness of required sections and keys, including Matrix and Meshtastic settings, and validates the format of matrix rooms. Prints errors or warnings for missing or deprecated fields. Returns True if the configuration is valid, otherwise False.

    Parameters:
        args: Parsed command-line arguments. If None, arguments are parsed internally.

    Returns:
        bool: True if the configuration file is valid, False otherwise.
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
                    config = yaml.load(f, Loader=SafeLoader)

                # Check if config is empty
                if not config:
                    print("Error: Configuration file is empty or invalid")
                    return False

                # Check matrix section
                if CONFIG_SECTION_MATRIX not in config:
                    print("Error: Missing 'matrix' section in config")
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
                    print(
                        f"Error: Missing required fields in 'matrix' section: {', '.join(missing_matrix_fields)}"
                    )
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
                        f"Error: Invalid 'connection_type': {connection_type}. Must be '{CONNECTION_TYPE_TCP}', '{CONNECTION_TYPE_SERIAL}', or '{CONNECTION_TYPE_BLE}'"
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

                # Check for broadcast_enabled - required for Matrix to Meshtastic message flow
                if "broadcast_enabled" not in meshtastic_section:
                    print("Warning: Missing 'broadcast_enabled' in 'meshtastic' section")
                    print("  This option must be set to 'true' to enable Matrix to Meshtastic messages")
                    print("  Add 'broadcast_enabled: true' to your meshtastic section")
                    return False

                broadcast_enabled = meshtastic_section["broadcast_enabled"]
                if not isinstance(broadcast_enabled, bool):
                    print(f"Error: 'broadcast_enabled' must be a boolean (true/false), got: {broadcast_enabled}")
                    return False

                # Check for other important optional configurations and provide guidance
                optional_configs = {
                    "detection_sensor": {
                        "type": bool,
                        "description": "Enable forwarding of Meshtastic detection sensor messages"
                    },
                    "message_delay": {
                        "type": (int, float),
                        "description": "Delay in seconds between messages sent to mesh (minimum: 2.0)"
                    },
                    "meshnet_name": {
                        "type": str,
                        "description": "Name displayed for your meshnet in Matrix messages"
                    }
                }

                warnings = []
                for option, config_info in optional_configs.items():
                    if option in meshtastic_section:
                        value = meshtastic_section[option]
                        expected_type = config_info["type"]
                        if not isinstance(value, expected_type):
                            type_name = expected_type.__name__ if hasattr(expected_type, '__name__') else str(expected_type)
                            print(f"Error: '{option}' must be of type {type_name}, got: {value}")
                            return False

                        # Special validation for message_delay
                        if option == "message_delay" and value < 2.0:
                            print(f"Error: 'message_delay' must be at least 2.0 seconds (firmware limitation), got: {value}")
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

                print("Configuration file is valid!")
                return True
            except yaml.YAMLError as e:
                print(f"Error parsing YAML in {config_path}: {e}")
                return False
            except Exception as e:
                print(f"Error checking configuration: {e}")
                return False

    print("Error: No configuration file found in any of the following locations:")
    for path in config_paths:
        print(f"  - {path}")
    print("\nRun 'mmrelay --generate-config' to generate a sample configuration file.")
    return False


def main():
    """
    Runs the Meshtastic Matrix Relay CLI, handling argument parsing, command execution, and error reporting.

    Returns:
        int: Exit code indicating success (0) or failure (non-zero).
    """
    try:
        args = parse_arguments()

        # Handle --check-config
        if args.check_config:
            return 0 if check_config(args) else 1

        # Handle --install-service
        if args.install_service:
            try:
                from mmrelay.setup_utils import install_service

                return 0 if install_service() else 1
            except ImportError as e:
                print(f"Error importing setup utilities: {e}")
                return 1

        # Handle --generate-config
        if args.generate_config:
            return 0 if generate_sample_config() else 1

        # Handle --version
        if args.version:
            print_version()
            return 0

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


if __name__ == "__main__":
    import sys

    sys.exit(main())


def handle_cli_commands(args):
    """Handle CLI commands like --generate-config, --install-service, and --check-config.

    Args:
        args: The parsed command-line arguments

    Returns:
        bool: True if a command was handled and the program should exit,
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
        import shutil

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
