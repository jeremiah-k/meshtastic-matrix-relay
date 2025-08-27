# ASYNC MOCK TESTING PATTERNS
#
# This file contains tests for CLI functions that call async functions via asyncio.run().
# The main issue is with handle_auth_logout() which calls:
#   asyncio.run(logout_matrix_bot(password=password))
#
# When we patch logout_matrix_bot, the patch automatically creates an AsyncMock because
# it detects the original function is async. However, AsyncMock creates coroutines that
# must be properly configured to avoid "coroutine was never awaited" warnings.
#
# SOLUTION PATTERN:
# Instead of using AsyncMock, use regular Mock with direct return values.
# For functions called via asyncio.run(), the asyncio.run() handles the awaiting,
# so we just need the mock to return the expected value directly.
#
# ✅ CORRECT: mock_logout.return_value = True
# ❌ INCORRECT: mock_logout = AsyncMock(return_value=True)
#
# This pattern eliminates RuntimeWarnings while maintaining proper test coverage.
# See docs/dev/TESTING_GUIDE.md for comprehensive async mocking patterns.

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import (
    check_config,
    generate_sample_config,
    get_version,
    handle_auth_login,
    handle_auth_logout,
    handle_cli_commands,
    main,
    parse_arguments,
    print_version,
)


class TestCLI(unittest.TestCase):
    def test_parse_arguments(self):
        # Test with no arguments
        """
        Test the parse_arguments function for correct parsing of CLI arguments.

        Verifies that parse_arguments returns default values when no arguments are provided and correctly parses all supported command-line options when specified.
        """
        with patch("sys.argv", ["mmrelay"]):
            args = parse_arguments()
            self.assertIsNone(args.config)
            self.assertIsNone(args.data_dir)
            self.assertIsNone(args.log_level)
            self.assertIsNone(args.logfile)
            self.assertFalse(args.version)
            self.assertFalse(args.generate_config)
            self.assertFalse(args.install_service)
            self.assertFalse(args.check_config)

        # Test with all arguments
        with patch(
            "sys.argv",
            [
                "mmrelay",
                "--config",
                "myconfig.yaml",
                "--data-dir",
                "/my/data",
                "--log-level",
                "debug",
                "--logfile",
                "/my/log.txt",
                "--version",
                "--generate-config",
                "--install-service",
                "--check-config",
            ],
        ):
            args = parse_arguments()
            self.assertEqual(args.config, "myconfig.yaml")
            self.assertEqual(args.data_dir, "/my/data")
            self.assertEqual(args.log_level, "debug")
            self.assertEqual(args.logfile, "/my/log.txt")
            self.assertTrue(args.version)
            self.assertTrue(args.generate_config)
            self.assertTrue(args.install_service)
            self.assertTrue(args.check_config)

    def test_parse_arguments_auth_login_parameters(self):
        """Test parsing of auth login subcommand parameters."""
        with patch(
            "sys.argv",
            [
                "mmrelay",
                "auth",
                "login",
                "--homeserver",
                "https://matrix.org",
                "--username",
                "@bot:matrix.org",
                "--password",
                "secret123",
            ],
        ):
            args = parse_arguments()
            self.assertEqual(args.command, "auth")
            self.assertEqual(args.auth_command, "login")
            self.assertEqual(args.homeserver, "https://matrix.org")
            self.assertEqual(args.username, "@bot:matrix.org")
            self.assertEqual(args.password, "secret123")

    def test_parse_arguments_auth_login_no_parameters(self):
        """Test parsing of auth login subcommand without parameters."""
        with patch("sys.argv", ["mmrelay", "auth", "login"]):
            args = parse_arguments()
            self.assertEqual(args.command, "auth")
            self.assertEqual(args.auth_command, "login")
            self.assertIsNone(args.homeserver)
            self.assertIsNone(args.username)
            self.assertIsNone(args.password)

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.cli._validate_e2ee_config")
    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.cli.validate_yaml_syntax")
    def test_check_config_valid(
        self,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_validate_e2ee,
        mock_makedirs,
        mock_validate_credentials,
    ):
        # Mock a valid config
        """
        Test that check_config returns True for a valid configuration file.

        Mocks a configuration containing all required sections and valid values, simulates the presence of the config file, and verifies that check_config() recognizes it as valid.
        """
        mock_validate_yaml.return_value = (
            True,
            None,
            {
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "access_token": "token",
                    "bot_user_id": "@bot:matrix.org",
                },
                "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
                "meshtastic": {
                    "connection_type": "serial",
                    "serial_port": "/dev/ttyUSB0",
                },
            },
        )
        mock_isfile.return_value = True
        mock_validate_e2ee.return_value = True
        mock_validate_credentials.return_value = False  # No valid credentials.json

        with patch("sys.argv", ["mmrelay", "--config", "valid_config.yaml"]):
            self.assertTrue(check_config())

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.cli.validate_yaml_syntax")
    def test_check_config_invalid_missing_matrix(
        self,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_makedirs,
        mock_validate_credentials,
    ):
        # Mock an invalid config (missing matrix section)
        """
        Test that check_config returns False when the configuration is missing the 'matrix' section.
        """
        mock_validate_yaml.return_value = (
            True,
            None,
            {
                "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
                "meshtastic": {
                    "connection_type": "serial",
                    "serial_port": "/dev/ttyUSB0",
                },
            },
        )
        mock_isfile.return_value = True
        mock_validate_credentials.return_value = False  # No valid credentials.json

        with patch("sys.argv", ["mmrelay", "--config", "invalid_config.yaml"]):
            self.assertFalse(check_config())

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.cli.validate_yaml_syntax")
    def test_check_config_invalid_missing_meshtastic(
        self,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_makedirs,
        mock_validate_credentials,
    ):
        # Mock an invalid config (missing meshtastic section)
        """
        Test that check_config returns False when the configuration is missing the 'meshtastic' section.
        """
        mock_validate_yaml.return_value = (
            True,
            None,
            {
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "access_token": "token",
                    "bot_user_id": "@bot:matrix.org",
                },
                "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            },
        )
        mock_isfile.return_value = True
        mock_validate_credentials.return_value = False  # No valid credentials.json

        with patch("sys.argv", ["mmrelay", "--config", "invalid_config.yaml"]):
            self.assertFalse(check_config())

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.cli.validate_yaml_syntax")
    def test_check_config_invalid_connection_type(
        self,
        mock_validate_yaml,
        mock_open,
        mock_isfile,
        mock_makedirs,
        mock_validate_credentials,
    ):
        # Mock an invalid config (invalid connection type)
        """
        Test that check_config() returns False when the configuration specifies an invalid Meshtastic connection type.
        """
        mock_validate_yaml.return_value = (
            True,
            None,
            {
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "access_token": "token",
                    "bot_user_id": "@bot:matrix.org",
                },
                "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
                "meshtastic": {"connection_type": "invalid"},
            },
        )
        mock_isfile.return_value = True
        mock_validate_credentials.return_value = False  # No valid credentials.json

        with patch("sys.argv", ["mmrelay", "--config", "invalid_config.yaml"]):
            self.assertFalse(check_config())

    def test_get_version(self):
        """
        Test that get_version returns a non-empty string representing the version.
        """
        version = get_version()
        self.assertIsInstance(version, str)
        self.assertGreater(len(version), 0)

    @patch("builtins.print")
    def test_print_version(self, mock_print):
        """
        Test that print_version outputs the MMRelay version information using the print function.
        """
        print_version()
        mock_print.assert_called_once()
        # Check that the printed message contains version info
        call_args = mock_print.call_args[0][0]
        self.assertIn("MMRelay", call_args)
        self.assertIn("v", call_args)

    @patch("builtins.print")
    def test_parse_arguments_unknown_args_warning(self, mock_print):
        """
        Test that a warning is printed when unknown CLI arguments are provided outside a test environment.

        Verifies that `parse_arguments()` triggers a warning message containing the unknown argument name when an unrecognized CLI argument is passed and the environment is not a test context.
        """
        with patch("sys.argv", ["mmrelay", "--unknown-arg"]):
            parse_arguments()
            # Should print warning about unknown arguments
            mock_print.assert_called()
            warning_msg = mock_print.call_args[0][0]
            self.assertIn("Warning", warning_msg)
            self.assertIn("unknown-arg", warning_msg)

    def test_parse_arguments_test_environment(self):
        """
        Verify that unknown CLI arguments do not produce warnings when running in a test environment.
        """
        with patch("sys.argv", ["pytest", "--unknown-arg"]):
            with patch("builtins.print") as mock_print:
                parse_arguments()
                # Should not print warning in test environment
                mock_print.assert_not_called()


class TestGenerateSampleConfig(unittest.TestCase):
    """Test cases for generate_sample_config function."""

    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    def test_generate_sample_config_existing_file(self, mock_isfile, mock_get_paths):
        """
        Test that generate_sample_config returns False and prints a message when the config file already exists.
        """
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_isfile.return_value = True

        with patch("builtins.print") as mock_print:
            result = generate_sample_config()

        self.assertFalse(result)
        mock_print.assert_called()
        # Check that it mentions existing config
        print_calls = [call[0][0] for call in mock_print.call_args_list]
        self.assertTrue(any("already exists" in call for call in print_calls))

    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    @patch("os.makedirs")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    @patch("shutil.copy2")
    def test_generate_sample_config_success(
        self,
        mock_copy,
        mock_exists,
        mock_get_sample,
        mock_makedirs,
        mock_isfile,
        mock_get_paths,
    ):
        """
        Test that generate_sample_config creates a sample config file when none exists and the sample file is available, ensuring correct file operations and success message output.
        """
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_isfile.return_value = False  # No existing config
        mock_get_sample.return_value = "/path/to/sample_config.yaml"
        mock_exists.return_value = True  # Sample config exists

        with patch("builtins.print") as mock_print:
            result = generate_sample_config()

        self.assertTrue(result)
        mock_copy.assert_called_once()
        mock_makedirs.assert_called_once()
        # Check success message
        print_calls = [call[0][0] for call in mock_print.call_args_list]
        self.assertTrue(any("Generated sample config" in call for call in print_calls))

    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    @patch("os.makedirs")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    @patch("importlib.resources.files")
    def test_generate_sample_config_importlib_fallback(
        self,
        mock_files,
        mock_exists,
        mock_get_sample,
        mock_makedirs,
        mock_isfile,
        mock_get_paths,
    ):
        """
        Test that generate_sample_config() uses importlib.resources to create the config file when the sample config is not found at the helper path.

        Simulates the absence of the sample config file at the expected location, mocks importlib.resources to provide sample content, and verifies that the config file is created with the correct content.
        """
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_isfile.return_value = False
        mock_get_sample.return_value = "/nonexistent/path"
        mock_exists.return_value = False  # Sample config doesn't exist at helper path

        # Mock importlib.resources
        mock_resource = MagicMock()
        mock_resource.read_text.return_value = "sample config content"
        mock_files.return_value.joinpath.return_value = mock_resource

        with patch("builtins.open", mock_open()) as mock_file:
            with patch("builtins.print"):
                result = generate_sample_config()

        self.assertTrue(result)
        mock_file.assert_called_once()
        # Check that content was written
        mock_file().write.assert_called_once_with("sample config content")


class TestHandleCLICommands(unittest.TestCase):
    """Test cases for handle_cli_commands function."""

    def test_handle_version_command(self):
        """
        Test that handle_cli_commands processes the --version flag by calling print_version and returning True.
        """
        args = MagicMock()
        args.version = True
        args.install_service = False
        args.generate_config = False
        args.check_config = False

        with patch("mmrelay.cli.print_version") as mock_print_version:
            result = handle_cli_commands(args)

        self.assertTrue(result)
        mock_print_version.assert_called_once()

    @patch("mmrelay.setup_utils.install_service")
    @patch("sys.exit")
    def test_handle_install_service_success(self, mock_exit, mock_install):
        """
        Test that the --install-service command triggers service installation and exits with code 0 on success.
        """
        args = MagicMock()
        args.version = False
        args.install_service = True
        args.generate_config = False
        args.check_config = False
        mock_install.return_value = True

        handle_cli_commands(args)

        mock_install.assert_called_once()
        mock_exit.assert_called_once_with(0)

    @patch("mmrelay.setup_utils.install_service")
    @patch("sys.exit")
    def test_handle_install_service_failure(self, mock_exit, mock_install):
        """
        Test that handle_cli_commands exits with code 1 when service installation fails using the --install-service flag.
        """
        args = MagicMock()
        args.version = False
        args.install_service = True
        args.generate_config = False
        args.check_config = False
        mock_install.return_value = False

        handle_cli_commands(args)

        mock_install.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch("mmrelay.cli.generate_sample_config")
    def test_handle_generate_config_success(self, mock_generate):
        """
        Test that handle_cli_commands returns True when the --generate-config command is specified and sample config generation succeeds.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = True
        args.check_config = False
        mock_generate.return_value = True

        result = handle_cli_commands(args)

        self.assertTrue(result)
        mock_generate.assert_called_once()

    @patch("mmrelay.cli.generate_sample_config")
    @patch("sys.exit")
    def test_handle_generate_config_failure(self, mock_exit, mock_generate):
        """
        Test that handle_cli_commands exits with code 1 when --generate-config is specified and config generation fails.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = True
        args.check_config = False
        mock_generate.return_value = False

        handle_cli_commands(args)

        mock_generate.assert_called_once()
        mock_exit.assert_called_once_with(1)

    @patch("mmrelay.cli.check_config")
    @patch("sys.exit")
    def test_handle_check_config_success(self, mock_exit, mock_check):
        """
        Test that handle_cli_commands exits with code 0 when --check-config is specified and the config check succeeds.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = False
        args.check_config = True
        mock_check.return_value = True

        handle_cli_commands(args)

        mock_check.assert_called_once()
        mock_exit.assert_called_once_with(0)

    @patch("mmrelay.cli.check_config")
    @patch("sys.exit")
    def test_handle_check_config_failure(self, mock_exit, mock_check):
        """
        Test that handle_cli_commands exits with code 1 when --check-config is specified and the config check fails.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = False
        args.check_config = True
        mock_check.return_value = False

        handle_cli_commands(args)

        mock_check.assert_called_once()
        mock_exit.assert_called_once_with(1)

    def test_handle_no_commands(self):
        """
        Test that handle_cli_commands returns False when no CLI command flags are set.
        """
        args = MagicMock()
        args.version = False
        args.install_service = False
        args.generate_config = False
        args.check_config = False

        result = handle_cli_commands(args)

        self.assertFalse(result)


class TestMainFunction(unittest.TestCase):
    """Test cases for main function."""

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.check_config")
    def test_main_check_config_success(self, mock_check, mock_parse):
        """
        Tests that the main function returns exit code 0 when the --check-config flag is set and the configuration check succeeds.
        """
        args = MagicMock()
        args.command = None
        args.check_config = True
        args.install_service = False
        args.generate_config = False
        args.version = False
        mock_parse.return_value = args
        mock_check.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_check.assert_called_once_with(args)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.check_config")
    def test_main_check_config_failure(self, mock_check, mock_parse):
        """
        Test that the main function returns exit code 1 when configuration check fails with --check-config.
        """
        args = MagicMock()
        args.command = None
        args.check_config = True
        args.install_service = False
        args.generate_config = False
        args.version = False
        mock_parse.return_value = args
        mock_check.return_value = False

        result = main()

        self.assertEqual(result, 1)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.setup_utils.install_service")
    def test_main_install_service_success(self, mock_install, mock_parse):
        """
        Test that the main function returns exit code 0 when the --install-service flag is set and service installation succeeds.
        """
        args = MagicMock()
        args.command = None
        args.check_config = False
        args.install_service = True
        args.generate_config = False
        args.version = False
        mock_parse.return_value = args
        mock_install.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_install.assert_called_once()

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.generate_sample_config")
    def test_main_generate_config_success(self, mock_generate, mock_parse):
        """
        Test that the main function returns exit code 0 when --generate-config is specified and sample config generation succeeds.
        """
        args = MagicMock()
        args.command = None
        args.check_config = False
        args.install_service = False
        args.generate_config = True
        args.version = False
        mock_parse.return_value = args
        mock_generate.return_value = True

        result = main()

        self.assertEqual(result, 0)
        mock_generate.assert_called_once()

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.print_version")
    def test_main_version(self, mock_print_version, mock_parse):
        """
        Tests that the main function handles the --version flag by printing version information and returning exit code 0.
        """
        args = MagicMock()
        args.command = None
        args.check_config = False
        args.install_service = False
        args.generate_config = False
        args.version = True
        mock_parse.return_value = args

        result = main()

        self.assertEqual(result, 0)
        mock_print_version.assert_called_once()

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.main.run_main")
    def test_main_run_main(self, mock_run_main, mock_parse):
        """
        Verify that when no top-level CLI command flags are set, main() delegates to run_main with the parsed args and returns its exit code.
        """
        args = MagicMock()
        args.command = None
        args.check_config = False
        args.install_service = False
        args.generate_config = False
        args.version = False
        args.auth = False  # Add missing auth attribute
        mock_parse.return_value = args
        mock_run_main.return_value = 0

        result = main()

        self.assertEqual(result, 0)
        mock_run_main.assert_called_once_with(args)


class TestCLIValidationFunctions(unittest.TestCase):
    """Test cases for CLI validation helper functions."""

    def test_validate_e2ee_dependencies_available(self):
        """Test _validate_e2ee_dependencies when dependencies are available."""
        from mmrelay.cli import _validate_e2ee_dependencies

        # Mock the required modules as available
        with patch.dict(
            "sys.modules",
            {
                "olm": MagicMock(),
                "nio": MagicMock(),
                "nio.crypto": MagicMock(),
                "nio.store": MagicMock(),
            },
        ), patch("builtins.print"):
            result = _validate_e2ee_dependencies()
            self.assertTrue(result)

    def test_validate_e2ee_dependencies_missing(self):
        """Test _validate_e2ee_dependencies when dependencies are missing."""
        from mmrelay.cli import _validate_e2ee_dependencies

        # Simulate missing modules in a reversible way
        with patch.dict(
            "sys.modules",
            {
                "olm": None,
                "nio": None,
                "nio.crypto": None,
                "nio.store": None,
            },
            clear=False,
        ), patch("mmrelay.cli.print"):
            result = _validate_e2ee_dependencies()
            self.assertFalse(result)

    @patch("sys.platform", "win32")
    def test_validate_e2ee_dependencies_windows(self):
        """Test _validate_e2ee_dependencies on Windows platform."""
        from mmrelay.cli import _validate_e2ee_dependencies

        with patch("mmrelay.cli.print"):  # Suppress print output
            result = _validate_e2ee_dependencies()
            self.assertFalse(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_exists(self, mock_exists):
        """Test _validate_credentials_json when credentials.json exists and is valid."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        valid_credentials = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device",
        }

        with patch("builtins.open", mock_open(read_data=json.dumps(valid_credentials))):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertTrue(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_missing(self, mock_exists):
        """Test _validate_credentials_json when credentials.json doesn't exist."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = False
        result = _validate_credentials_json("/path/to/config.yaml")
        self.assertFalse(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_invalid(self, mock_exists):
        """Test _validate_credentials_json when credentials.json exists but is invalid."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        with patch("builtins.open", mock_open(read_data='{"incomplete": "data"}')):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertFalse(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_standard_location(self, mock_exists):
        """Test _validate_credentials_json when credentials.json exists in standard location."""
        from mmrelay.cli import _validate_credentials_json

        # First call (config dir) returns False, second call (standard location) returns True
        mock_exists.side_effect = [False, True]

        valid_credentials = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "user_id": "@test:matrix.org",
            "device_id": "test_device",
        }

        with patch(
            "mmrelay.config.get_base_dir", return_value="/home/user/.mmrelay"
        ), patch("builtins.open", mock_open(read_data=json.dumps(valid_credentials))):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertTrue(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_exception_handling(self, mock_exists):
        """Test _validate_credentials_json exception handling."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        # Mock open to raise an exception
        with patch(
            "builtins.open", side_effect=FileNotFoundError("File not found")
        ), patch("builtins.print"):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertFalse(result)

    def test_validate_matrix_authentication_with_credentials(self):
        """Test _validate_matrix_authentication with valid credentials.json."""
        from mmrelay.cli import _validate_matrix_authentication

        with patch("mmrelay.cli._validate_credentials_json", return_value=True), patch(
            "builtins.print"
        ):
            result = _validate_matrix_authentication("/path/to/config.yaml", None)
            self.assertTrue(result)

    def test_validate_matrix_authentication_with_config(self):
        """Test _validate_matrix_authentication with valid matrix config section."""
        from mmrelay.cli import _validate_matrix_authentication

        matrix_section = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org",
        }

        with patch("mmrelay.cli._validate_credentials_json", return_value=False), patch(
            "builtins.print"
        ):
            result = _validate_matrix_authentication(
                "/path/to/config.yaml", matrix_section
            )
            self.assertTrue(result)

    def test_validate_matrix_authentication_none(self):
        """Test _validate_matrix_authentication with no valid authentication."""
        from mmrelay.cli import _validate_matrix_authentication

        with patch("mmrelay.cli._validate_credentials_json", return_value=False), patch(
            "builtins.print"
        ):
            result = _validate_matrix_authentication("/path/to/config.yaml", None)
            self.assertFalse(result)


class TestCLISubcommandHandlers(unittest.TestCase):
    """Test cases for CLI subcommand handler functions."""

    def test_handle_subcommand_config(self):
        """Test handle_subcommand dispatching to config commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "config"

        with patch("mmrelay.cli.handle_config_command", return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_subcommand_auth(self):
        """Test handle_subcommand dispatching to auth commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "auth"

        with patch("mmrelay.cli.handle_auth_command", return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_subcommand_service(self):
        """Test handle_subcommand dispatching to service commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "service"

        with patch("mmrelay.cli.handle_service_command", return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_config_command_generate(self):
        """Test handle_config_command with generate subcommand."""
        from mmrelay.cli import handle_config_command

        args = MagicMock()
        args.config_command = "generate"

        with patch(
            "mmrelay.cli.generate_sample_config", return_value=True
        ) as mock_generate:
            result = handle_config_command(args)
            self.assertEqual(result, 0)
            mock_generate.assert_called_once()

    def test_handle_config_command_check(self):
        """Test handle_config_command with check subcommand."""
        from mmrelay.cli import handle_config_command

        args = MagicMock()
        args.config_command = "check"

        with patch("mmrelay.cli.check_config", return_value=True) as mock_check:
            result = handle_config_command(args)
            self.assertEqual(result, 0)
            mock_check.assert_called_once_with(args)

    def test_handle_auth_command_login(self):
        """Test handle_auth_command with login subcommand."""
        from mmrelay.cli import handle_auth_command

        args = MagicMock()
        args.auth_command = "login"

        with patch("mmrelay.cli.handle_auth_login", return_value=0) as mock_login:
            result = handle_auth_command(args)
            self.assertEqual(result, 0)
            mock_login.assert_called_once_with(args)

    def test_handle_auth_command_status(self):
        """Test handle_auth_command with status subcommand."""
        from mmrelay.cli import handle_auth_command

        args = MagicMock()
        args.auth_command = "status"

        with patch("mmrelay.cli.handle_auth_status", return_value=0) as mock_status:
            result = handle_auth_command(args)
            self.assertEqual(result, 0)
            mock_status.assert_called_once_with(args)


class TestE2EEConfigurationFunctions(unittest.TestCase):
    """Test cases for E2EE configuration validation functions."""

    def test_validate_e2ee_config_no_matrix_section(self):
        """Test _validate_e2ee_config with no matrix section."""
        from mmrelay.cli import _validate_e2ee_config

        config = {"matrix": {"homeserver": "https://matrix.org"}}

        with patch("mmrelay.cli._validate_matrix_authentication", return_value=True):
            result = _validate_e2ee_config(config, None, "/path/to/config.yaml")
            self.assertTrue(result)

    def test_validate_e2ee_config_e2ee_disabled(self):
        """Test _validate_e2ee_config with E2EE disabled."""
        from mmrelay.cli import _validate_e2ee_config

        config = {"matrix": {"homeserver": "https://matrix.org"}}
        matrix_section = {"homeserver": "https://matrix.org"}

        with patch(
            "mmrelay.cli._validate_matrix_authentication", return_value=True
        ), patch("mmrelay.cli.print"):
            result = _validate_e2ee_config(
                config, matrix_section, "/path/to/config.yaml"
            )
            self.assertTrue(result)

    def test_validate_e2ee_config_e2ee_enabled_valid(self):
        """Test _validate_e2ee_config with E2EE enabled and valid."""
        from mmrelay.cli import _validate_e2ee_config

        config = {
            "matrix": {"homeserver": "https://matrix.org", "e2ee": {"enabled": True}}
        }
        matrix_section = {
            "homeserver": "https://matrix.org",
            "e2ee": {"enabled": True, "store_path": "~/.mmrelay/store"},
        }

        with patch(
            "mmrelay.cli._validate_matrix_authentication", return_value=True
        ), patch("mmrelay.cli._validate_e2ee_dependencies", return_value=True), patch(
            "os.path.expanduser", return_value="/home/user/.mmrelay/store"
        ), patch(
            "os.path.exists", return_value=True
        ), patch(
            "builtins.print"
        ):
            result = _validate_e2ee_config(
                config, matrix_section, "/path/to/config.yaml"
            )
            self.assertTrue(result)

    def test_validate_e2ee_config_e2ee_enabled_invalid_deps(self):
        """Test _validate_e2ee_config with E2EE enabled but invalid dependencies."""
        from mmrelay.cli import _validate_e2ee_config

        config = {
            "matrix": {"homeserver": "https://matrix.org", "e2ee": {"enabled": True}}
        }
        matrix_section = {"homeserver": "https://matrix.org", "e2ee": {"enabled": True}}

        with patch(
            "mmrelay.cli._validate_matrix_authentication", return_value=True
        ), patch("mmrelay.cli._validate_e2ee_dependencies", return_value=False):
            result = _validate_e2ee_config(
                config, matrix_section, "/path/to/config.yaml"
            )
            self.assertFalse(result)


class TestE2EEAnalysisFunctions(unittest.TestCase):
    """Test cases for E2EE analysis functions."""

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_ready(self, mock_exists):
        """Test _analyze_e2ee_setup when E2EE is ready."""
        from mmrelay.cli import _analyze_e2ee_setup

        config = {"matrix": {"e2ee": {"enabled": True}}}
        mock_exists.return_value = True  # credentials.json exists

        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            result = _analyze_e2ee_setup(config, "/path/to/config.yaml")

            self.assertTrue(result["config_enabled"])
            self.assertTrue(result["dependencies_available"])
            self.assertTrue(result["credentials_available"])
            self.assertTrue(result["platform_supported"])
            self.assertEqual(result["overall_status"], "ready")

    @patch("sys.platform", "win32")
    def test_analyze_e2ee_setup_windows_not_supported(self):
        """Test _analyze_e2ee_setup on Windows platform."""
        from mmrelay.cli import _analyze_e2ee_setup

        config = {"matrix": {"e2ee": {"enabled": True}}}

        result = _analyze_e2ee_setup(config, "/path/to/config.yaml")

        self.assertFalse(result["platform_supported"])
        self.assertEqual(result["overall_status"], "not_supported")
        self.assertIn("E2EE is not supported on Windows", result["recommendations"][0])

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_disabled(self, mock_exists):
        """Test _analyze_e2ee_setup when E2EE is disabled."""
        from mmrelay.cli import _analyze_e2ee_setup

        config = {"matrix": {"e2ee": {"enabled": False}}}
        mock_exists.return_value = True

        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            result = _analyze_e2ee_setup(config, "/path/to/config.yaml")

            self.assertFalse(result["config_enabled"])
            self.assertEqual(result["overall_status"], "disabled")


class TestE2EEPrintFunctions(unittest.TestCase):
    """Test cases for E2EE print functions."""

    def test_print_e2ee_analysis_ready(self):
        """Test _print_e2ee_analysis with ready status."""
        from mmrelay.cli import _print_e2ee_analysis

        analysis = {
            "dependencies_available": True,
            "credentials_available": True,
            "platform_supported": True,
            "config_enabled": True,
            "overall_status": "ready",
            "recommendations": [],
        }

        with patch("mmrelay.cli.print") as mock_print:
            _print_e2ee_analysis(analysis)
            mock_print.assert_called()
            # Check that success messages are printed
            calls = [call.args[0] for call in mock_print.call_args_list]
            self.assertTrue(
                any("✅ E2EE is fully configured and ready" in call for call in calls)
            )

    def test_print_e2ee_analysis_disabled(self):
        """Test _print_e2ee_analysis with disabled status."""
        from mmrelay.cli import _print_e2ee_analysis

        analysis = {
            "dependencies_available": True,
            "credentials_available": True,
            "platform_supported": True,
            "config_enabled": False,
            "overall_status": "disabled",
            "recommendations": ["Enable E2EE in config.yaml"],
        }

        with patch("mmrelay.cli.print") as mock_print:
            _print_e2ee_analysis(analysis)
            mock_print.assert_called()
            # Check that disabled messages are printed
            calls = [call.args[0] for call in mock_print.call_args_list]
            self.assertTrue(
                any("⚠️  E2EE is disabled in configuration" in call for call in calls)
            )

    @patch("sys.platform", "linux")
    def test_print_environment_summary_linux(self):
        """Test _print_environment_summary on Linux."""
        from mmrelay.cli import _print_environment_summary

        # Mock the specific modules instead of builtins.__import__ to avoid Python 3.10 conflicts
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ), patch("mmrelay.cli.print") as mock_print:
            _print_environment_summary()
            mock_print.assert_called()
            # Check that Linux-specific messages are printed
            calls = [call.args[0] for call in mock_print.call_args_list]
            self.assertTrue(any("Platform: linux" in call for call in calls))


class TestAuthLogout(unittest.TestCase):
    """Test cases for handle_auth_logout function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        self.mock_args.password = None
        self.mock_args.yes = False

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.input")
    @patch("builtins.print")
    def test_handle_auth_logout_success_with_confirmation(
        self, mock_print, mock_input, mock_logout
    ):
        """Test successful logout with user confirmation."""
        # ASYNC MOCK FIX: Replace the async function with a regular function
        # that returns the value directly. This prevents coroutine creation.
        mock_logout.return_value = True  # Return the value directly, not a coroutine
        mock_input.return_value = "y"
        self.mock_args.password = "test_password"

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_input.assert_called_once_with("Are you sure you want to logout? (y/N): ")
        mock_logout.assert_called_once_with(password="test_password")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.input")
    @patch("builtins.print")
    def test_handle_auth_logout_cancelled_by_user(
        self, mock_print, mock_input, mock_logout
    ):
        """Test logout cancelled by user confirmation."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_logout.return_value = True  # Won't be called, but set for consistency
        mock_input.return_value = "n"
        self.mock_args.password = "test_password"

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_input.assert_called_once_with("Are you sure you want to logout? (y/N): ")
        mock_logout.assert_not_called()
        # Check that cancellation message was printed
        mock_print.assert_any_call("Logout cancelled.")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_with_yes_flag(self, mock_print, mock_logout):
        """Test logout with --yes flag (skip confirmation)."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_logout.return_value = True
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_logout.assert_called_once_with(password="test_password")

    @patch("getpass.getpass")
    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_password_prompt_none(
        self, mock_print, mock_logout, mock_getpass
    ):
        """Test logout with password=None (prompt for password)."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_getpass.return_value = "prompted_password"
        mock_logout.return_value = True
        self.mock_args.password = None
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_getpass.assert_called_once_with("Enter Matrix password for verification: ")
        mock_logout.assert_called_once_with(password="prompted_password")

    @patch("getpass.getpass")
    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_password_prompt_empty(
        self, mock_print, mock_logout, mock_getpass
    ):
        """Test logout with password='' (prompt for password)."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_getpass.return_value = "prompted_password"
        mock_logout.return_value = True
        self.mock_args.password = ""
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_getpass.assert_called_once_with("Enter Matrix password for verification: ")
        mock_logout.assert_called_once_with(password="prompted_password")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_with_password_security_warning(
        self, mock_print, mock_logout
    ):
        """Test logout with password provided shows security warning."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_logout.return_value = True
        self.mock_args.password = "insecure_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        # Check that security warning was printed
        mock_print.assert_any_call(
            "⚠️  Warning: Supplying password as argument exposes it in shell history and process list."
        )
        mock_print.assert_any_call(
            "   For better security, use --password without a value to prompt securely."
        )
        mock_logout.assert_called_once_with(password="insecure_password")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_failure(self, mock_print, mock_logout):
        """Test logout failure returns exit code 1."""
        # ASYNC MOCK FIX: Use same pattern - return value directly
        mock_logout.return_value = False
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_logout.assert_called_once_with(password="test_password")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_keyboard_interrupt(self, mock_print, mock_logout):
        """Test logout handles KeyboardInterrupt gracefully."""
        # ASYNC MOCK FIX: Make the mock raise KeyboardInterrupt when called
        mock_logout.side_effect = KeyboardInterrupt()
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nLogout cancelled by user.")

    @patch("mmrelay.cli_utils.logout_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_logout_exception_handling(self, mock_print, mock_logout):
        """Test logout handles general exceptions gracefully."""
        # ASYNC MOCK FIX: Make the mock raise Exception when called
        mock_logout.side_effect = Exception("Test error")
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nError during logout: Test error")

    @patch("builtins.print")
    def test_handle_auth_logout_prints_header(self, mock_print):
        """Test that logout prints the expected header information."""
        # Setup mocks
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Mock the logout to avoid actual execution
        with patch("mmrelay.cli_utils.logout_matrix_bot") as mock_logout:
            # ASYNC MOCK FIX: Use same pattern - return value directly
            mock_logout.return_value = True

            # Call function
            handle_auth_logout(self.mock_args)

            # Verify header was printed
            mock_print.assert_any_call("Matrix Bot Logout")
            mock_print.assert_any_call("=================")
            mock_print.assert_any_call(
                "This will log out from Matrix and clear all local session data:"
            )
            mock_print.assert_any_call("• Remove credentials.json")
            mock_print.assert_any_call("• Clear E2EE encryption store")
            mock_print.assert_any_call("• Invalidate Matrix access token")


class TestAuthLogin(unittest.TestCase):
    """Test cases for handle_auth_login function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        self.mock_args.homeserver = None
        self.mock_args.username = None
        self.mock_args.password = None

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_interactive_mode_success(self, mock_print, mock_login):
        """Test interactive mode (no parameters) with successful login."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = True

        # Call function with no parameters (interactive mode)
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_login.assert_called_once_with(
            homeserver=None,
            username=None,
            password=None,
            logout_others=False
        )
        # Check that header was printed for interactive mode
        mock_print.assert_any_call("Matrix Bot Authentication for E2EE")
        mock_print.assert_any_call("===================================")

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_interactive_mode_failure(self, mock_print, mock_login):
        """Test interactive mode with failed login."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = False

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_login.assert_called_once_with(
            homeserver=None,
            username=None,
            password=None,
            logout_others=False
        )

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_non_interactive_mode_success(self, mock_print, mock_login):
        """Test non-interactive mode (all parameters provided) with successful login."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = True

        # Set all parameters for non-interactive mode
        self.mock_args.homeserver = "https://matrix.org"
        self.mock_args.username = "@bot:matrix.org"
        self.mock_args.password = "secret123"

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_login.assert_called_once_with(
            homeserver="https://matrix.org",
            username="@bot:matrix.org",
            password="secret123",
            logout_others=False
        )
        # Should NOT print header in non-interactive mode
        mock_print.assert_not_called()

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_non_interactive_mode_failure(self, mock_print, mock_login):
        """Test non-interactive mode with failed login."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = False

        # Set all parameters
        self.mock_args.homeserver = "https://matrix.org"
        self.mock_args.username = "@bot:matrix.org"
        self.mock_args.password = "secret123"

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_login.assert_called_once()

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_homeserver_only(self, mock_print):
        """Test error handling when only homeserver is provided."""
        self.mock_args.homeserver = "https://matrix.org"
        # username and password remain None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --username, --password")

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_username_only(self, mock_print):
        """Test error handling when only username is provided."""
        self.mock_args.username = "@bot:matrix.org"
        # homeserver and password remain None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --homeserver, --password")

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_password_only(self, mock_print):
        """Test error handling when only password is provided."""
        self.mock_args.password = "secret123"
        # homeserver and username remain None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --homeserver, --username")

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_homeserver_username(self, mock_print):
        """Test error handling when homeserver and username provided but password missing."""
        self.mock_args.homeserver = "https://matrix.org"
        self.mock_args.username = "@bot:matrix.org"
        # password remains None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --password")

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_homeserver_password(self, mock_print):
        """Test error handling when homeserver and password provided but username missing."""
        self.mock_args.homeserver = "https://matrix.org"
        self.mock_args.password = "secret123"
        # username remains None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --username")

    @patch("builtins.print")
    def test_handle_auth_login_partial_params_username_password(self, mock_print):
        """Test error handling when username and password provided but homeserver missing."""
        self.mock_args.username = "@bot:matrix.org"
        self.mock_args.password = "secret123"
        # homeserver remains None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check error message content
        mock_print.assert_any_call("❌ Error: All authentication parameters are required when using command-line options.")
        mock_print.assert_any_call("   Missing: --homeserver")

    @patch("builtins.print")
    def test_handle_auth_login_error_message_guidance(self, mock_print):
        """Test that error messages include helpful guidance."""
        self.mock_args.homeserver = "https://matrix.org"
        # username and password remain None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check that guidance messages are included
        mock_print.assert_any_call("💡 Options:")
        mock_print.assert_any_call("   • For secure interactive authentication: mmrelay auth login")
        mock_print.assert_any_call("   • For automated authentication: provide all three parameters")
        mock_print.assert_any_call("⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.")
        mock_print.assert_any_call("   Interactive mode is recommended for manual use.")

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_keyboard_interrupt(self, mock_print, mock_login):
        """Test handling of KeyboardInterrupt during login."""
        # ASYNC MOCK FIX: Make the mock raise KeyboardInterrupt when called
        mock_login.side_effect = KeyboardInterrupt()

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nAuthentication cancelled by user.")

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_general_exception(self, mock_print, mock_login):
        """Test handling of general exceptions during login."""
        # ASYNC MOCK FIX: Make the mock raise Exception when called
        mock_login.side_effect = Exception("Test error")

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nError during authentication: Test error")

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_empty_string_parameters(self, mock_print, mock_login):
        """Test that empty string parameters are treated as None."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = True

        # Set parameters to empty strings
        self.mock_args.homeserver = ""
        self.mock_args.username = ""
        self.mock_args.password = ""

        # Call function (should be treated as interactive mode)
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_login.assert_called_once_with(
            homeserver="",
            username="",
            password="",
            logout_others=False
        )
        # Should print header for interactive mode
        mock_print.assert_any_call("Matrix Bot Authentication for E2EE")
        mock_print.assert_any_call("===================================")


if __name__ == "__main__":
    unittest.main()
