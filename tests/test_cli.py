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

import builtins
import json
import os
import sys
import unittest
import unittest.mock
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
            self.assertIsNone(args.base_dir)
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
                "--base-dir",
                "/my/base",
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
            self.assertEqual(args.base_dir, "/my/base")
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

    @patch("mmrelay.cli.os.makedirs")
    @patch("mmrelay.cli.os.path.expanduser")
    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.main.run_main")
    def test_main_sets_home_override(
        self, mock_run_main, mock_parse, mock_expanduser, mock_makedirs
    ):
        """
        Verify that --base-dir expands user paths and sets the HOME override.
        """
        args = MagicMock()
        args.command = None
        args.check_config = False
        args.install_service = False
        args.generate_config = False
        args.version = False
        args.auth = False
        args.home = None
        args.base_dir = "~/mmrelay"
        args.data_dir = None
        mock_parse.return_value = args
        mock_run_main.return_value = 0
        mock_expanduser.return_value = "/home/test/mmrelay"

        from mmrelay.paths import get_home_dir, reset_home_override

        try:
            result = main()

            self.assertEqual(result, 0)
            mock_expanduser.assert_called_once_with("~/mmrelay")
            mock_makedirs.assert_called_once_with("/home/test/mmrelay", exist_ok=True)
            expected_home = os.path.abspath(mock_expanduser.return_value)
            self.assertEqual(str(get_home_dir()), expected_home)
        finally:
            reset_home_override()


class TestCLIValidationFunctions(unittest.TestCase):
    """Test cases for CLI validation helper functions."""

    def test_validate_e2ee_dependencies_available(self):
        """Test _validate_e2ee_dependencies when dependencies are available."""
        from mmrelay.cli import _validate_e2ee_dependencies

        # Mock the required modules as available
        with (
            patch.dict(
                "sys.modules",
                {
                    "olm": MagicMock(),
                    "nio": MagicMock(),
                    "nio.crypto": MagicMock(),
                    "nio.store": MagicMock(),
                },
            ),
            patch("builtins.print"),
        ):
            result = _validate_e2ee_dependencies()
            self.assertTrue(result)

    def test_validate_e2ee_dependencies_missing(self):
        """Test _validate_e2ee_dependencies when dependencies are missing."""
        from mmrelay.cli import _validate_e2ee_dependencies

        # Simulate missing modules in a reversible way
        with (
            patch.dict(
                "sys.modules",
                {
                    "olm": None,
                    "nio": None,
                    "nio.crypto": None,
                    "nio.store": None,
                },
                clear=False,
            ),
            patch("mmrelay.cli.print"),
        ):
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
    def test_validate_credentials_json_non_object(self, mock_exists):
        """Test _validate_credentials_json rejects non-object JSON content."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            with patch("builtins.open", mock_open(read_data='["bad"]')):
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

        with (
            patch("mmrelay.config.get_base_dir", return_value="/home/user/.mmrelay"),
            patch("builtins.open", mock_open(read_data=json.dumps(valid_credentials))),
        ):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertTrue(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_exception_handling(self, mock_exists):
        """Test _validate_credentials_json exception handling."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        # Mock open to raise an exception
        with (
            patch("builtins.open", side_effect=FileNotFoundError("File not found")),
            patch("builtins.print"),
        ):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertFalse(result)

    def test_validate_matrix_authentication_with_credentials(self):
        """Test _validate_matrix_authentication with valid credentials.json."""
        from mmrelay.cli import _validate_matrix_authentication

        with (
            patch("mmrelay.cli._validate_credentials_json", return_value=True),
            patch("builtins.print"),
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

        with (
            patch("mmrelay.cli._validate_credentials_json", return_value=False),
            patch("builtins.print"),
        ):
            result = _validate_matrix_authentication(
                "/path/to/config.yaml", matrix_section
            )
            self.assertTrue(result)

    def test_validate_matrix_authentication_none(self):
        """Test _validate_matrix_authentication with no valid authentication."""
        from mmrelay.cli import _validate_matrix_authentication

        with (
            patch("mmrelay.cli._validate_credentials_json", return_value=False),
            patch("builtins.print"),
        ):
            result = _validate_matrix_authentication("/path/to/config.yaml", None)
            self.assertFalse(result)

    def test_is_valid_serial_port_linux_valid(self):
        """Test _is_valid_serial_port with valid Linux serial ports."""
        from mmrelay.cli import _is_valid_serial_port

        with patch("platform.system", return_value="Linux"):
            self.assertTrue(_is_valid_serial_port("/dev/ttyUSB0"))
            self.assertTrue(_is_valid_serial_port("/dev/ttyACM0"))
            self.assertTrue(_is_valid_serial_port("/dev/cu.usbserial-1234"))
            self.assertTrue(_is_valid_serial_port("/dev/ttyS0"))

    def test_is_valid_serial_port_linux_invalid(self):
        """Test _is_valid_serial_port with invalid Linux serial ports."""
        from mmrelay.cli import _is_valid_serial_port

        with patch("platform.system", return_value="Linux"):
            self.assertFalse(_is_valid_serial_port("/dev/"))
            self.assertFalse(_is_valid_serial_port("ttyUSB0"))
            self.assertFalse(_is_valid_serial_port("/dev/tty"))
            self.assertFalse(_is_valid_serial_port(""))

    def test_is_valid_serial_port_windows_valid(self):
        """Test _is_valid_serial_port with valid Windows COM ports."""
        from mmrelay.cli import _is_valid_serial_port

        with patch("platform.system", return_value="Windows"):
            self.assertTrue(_is_valid_serial_port("COM1"))
            self.assertTrue(_is_valid_serial_port("COM10"))
            self.assertTrue(_is_valid_serial_port("COM100"))
            self.assertTrue(_is_valid_serial_port("COM999"))

    def test_is_valid_serial_port_windows_invalid(self):
        """Test _is_valid_serial_port with invalid Windows COM ports."""
        from mmrelay.cli import _is_valid_serial_port

        with patch("platform.system", return_value="Windows"):
            self.assertFalse(_is_valid_serial_port("COM"))
            self.assertFalse(_is_valid_serial_port("COMA"))
            self.assertFalse(_is_valid_serial_port("COM1A"))
            self.assertFalse(_is_valid_serial_port("COM 1"))
            self.assertFalse(_is_valid_serial_port("/dev/ttyUSB0"))

    def test_is_valid_serial_port_edge_cases(self):
        """Test _is_valid_serial_port with edge cases."""
        from mmrelay.cli import _is_valid_serial_port

        self.assertFalse(_is_valid_serial_port(None))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_serial_port(""))
        self.assertFalse(_is_valid_serial_port(123))  # type: ignore[arg-type]

    def test_is_valid_host_ipv4_address(self):
        """Test _is_valid_host with valid IPv4 addresses."""
        from mmrelay.cli import _is_valid_host

        self.assertTrue(_is_valid_host("192.168.1.1"))
        self.assertTrue(_is_valid_host("10.0.0.1"))
        self.assertTrue(_is_valid_host("127.0.0.1"))
        self.assertTrue(_is_valid_host("255.255.255.255"))

    def test_is_valid_host_ipv6_address(self):
        """Test _is_valid_host with valid IPv6 addresses."""
        from mmrelay.cli import _is_valid_host

        self.assertTrue(_is_valid_host("::1"))
        self.assertTrue(_is_valid_host("2001:db8::1"))
        self.assertTrue(_is_valid_host("fe80::1"))
        self.assertTrue(_is_valid_host("2001:0db8:85a3:0000:0000:8a2e:0370:7334"))

    def test_is_valid_host_valid_hostname(self):
        """Test _is_valid_host with valid hostnames."""
        from mmrelay.cli import _is_valid_host

        self.assertTrue(_is_valid_host("localhost"))
        self.assertTrue(_is_valid_host("meshtastic.local"))
        self.assertTrue(_is_valid_host("my-mesh-network.example.com"))
        self.assertTrue(_is_valid_host("server"))
        self.assertTrue(_is_valid_host("sub.domain.example"))

    def test_is_valid_host_invalid_hostname(self):
        """Test _is_valid_host with invalid hostnames."""
        from mmrelay.cli import _is_valid_host

        self.assertFalse(_is_valid_host("-invalid.com"))
        self.assertFalse(_is_valid_host("invalid-.com"))
        self.assertFalse(_is_valid_host("invalid..com"))
        self.assertFalse(_is_valid_host("a" * 254))
        self.assertFalse(_is_valid_host("a." * 100))

    def test_is_valid_host_edge_cases(self):
        """Test _is_valid_host with edge cases."""
        from mmrelay.cli import _is_valid_host

        self.assertFalse(_is_valid_host(None))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_host(""))
        self.assertFalse(_is_valid_host(123))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_host("   "))

    def test_is_valid_ble_address_mac_address(self):
        """Test _is_valid_ble_address with valid MAC addresses."""
        from mmrelay.cli import _is_valid_ble_address

        self.assertTrue(_is_valid_ble_address("AA:BB:CC:DD:EE:FF"))
        self.assertTrue(_is_valid_ble_address("aa:bb:cc:dd:ee:ff"))
        self.assertTrue(_is_valid_ble_address("00:11:22:33:44:55"))
        self.assertTrue(_is_valid_ble_address("FF:EE:DD:CC:BB:AA"))

    def test_is_valid_ble_address_device_name(self):
        """Test _is_valid_ble_address with valid device names."""
        from mmrelay.cli import _is_valid_ble_address

        self.assertTrue(_is_valid_ble_address("MyMeshtasticDevice"))
        self.assertTrue(_is_valid_ble_address("T-Beam"))
        self.assertTrue(_is_valid_ble_address("MeshDevice123"))
        self.assertTrue(_is_valid_ble_address("LilyGO-T-Beam"))

    def test_is_valid_ble_address_invalid(self):
        """Test _is_valid_ble_address with invalid addresses."""
        from mmrelay.cli import _is_valid_ble_address

        self.assertFalse(_is_valid_ble_address(None))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_ble_address(""))
        self.assertFalse(_is_valid_ble_address("AA:BB:CC:DD:EE"))
        self.assertFalse(_is_valid_ble_address("AA:BB:CC:DD:EE:FF:00"))
        self.assertFalse(_is_valid_ble_address("GG:HH:II:JJ:KK:LL"))
        self.assertFalse(_is_valid_ble_address("AA:BB:CC:DD:EE:GG"))

    def test_is_valid_ble_address_edge_cases(self):
        """Test _is_valid_ble_address with edge cases."""
        from mmrelay.cli import _is_valid_ble_address

        self.assertFalse(_is_valid_ble_address(123))  # type: ignore[arg-type]
        self.assertFalse(_is_valid_ble_address("   "))


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

        with (
            patch("mmrelay.cli._validate_matrix_authentication", return_value=True),
            patch("mmrelay.cli.print"),
        ):
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

        with (
            patch("mmrelay.cli._validate_matrix_authentication", return_value=True),
            patch("mmrelay.cli._validate_e2ee_dependencies", return_value=True),
            patch("os.path.expanduser", return_value="/home/user/.mmrelay/store"),
            patch("os.path.exists", return_value=True),
            patch("builtins.print"),
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

        with (
            patch("mmrelay.cli._validate_matrix_authentication", return_value=True),
            patch("mmrelay.cli._validate_e2ee_dependencies", return_value=False),
        ):
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
        with (
            patch.dict(
                "sys.modules",
                {
                    "olm": MagicMock(),
                    "nio.crypto": MagicMock(),
                    "nio.store": MagicMock(),
                },
            ),
            patch("mmrelay.cli.print") as mock_print,
        ):
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

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.input")
    @patch("builtins.print")
    def test_handle_auth_logout_success_with_confirmation(
        self, mock_print, mock_input, mock_asyncio_run
    ):
        """Test successful logout with user confirmation."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_asyncio_run.return_value = True
        mock_input.return_value = "y"
        self.mock_args.password = "test_password"

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_input.assert_called_once_with("Are you sure you want to logout? (y/N): ")
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.input")
    @patch("builtins.print")
    def test_handle_auth_logout_cancelled_by_user(
        self, mock_print, mock_input, mock_asyncio_run
    ):
        """Test logout cancelled by user confirmation."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_asyncio_run.return_value = True  # Won't be called due to cancellation
        mock_input.return_value = "n"
        self.mock_args.password = "test_password"

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_input.assert_called_once_with("Are you sure you want to logout? (y/N): ")
        mock_asyncio_run.assert_not_called()  # Should not attempt logout due to cancellation
        # Check that cancellation message was printed
        mock_print.assert_any_call("Logout cancelled.")

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.print")
    def test_handle_auth_logout_with_yes_flag(self, mock_print, mock_asyncio_run):
        """Test logout with --yes flag (skip confirmation)."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_asyncio_run.return_value = True
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("getpass.getpass")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.print")
    def test_handle_auth_logout_password_prompt_none(
        self, mock_print, mock_getpass, mock_asyncio_run
    ):
        """Test logout with password=None (prompt for password)."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_getpass.return_value = "prompted_password"
        mock_asyncio_run.return_value = True
        self.mock_args.password = None
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_getpass.assert_called_once_with("Enter Matrix password for verification: ")
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("getpass.getpass")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.print")
    def test_handle_auth_logout_password_prompt_empty(
        self, mock_print, mock_getpass, mock_asyncio_run
    ):
        """Test logout with password='' (prompt for password)."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_getpass.return_value = "prompted_password"
        mock_asyncio_run.return_value = True
        self.mock_args.password = ""
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_getpass.assert_called_once_with("Enter Matrix password for verification: ")
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.print")
    def test_handle_auth_logout_with_password_security_warning(
        self, mock_print, mock_asyncio_run
    ):
        """Test logout with password provided shows security warning."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_asyncio_run.return_value = True
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
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=False))
    @patch("builtins.print")
    def test_handle_auth_logout_failure(self, _mock_print, mock_asyncio_run):
        """Test logout failure returns exit code 1."""
        # ASYNC MOCK FIX: Mock asyncio.run instead of the async function directly
        mock_asyncio_run.return_value = False
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_asyncio_run.assert_called_once()

    @patch("asyncio.run")
    @patch("mmrelay.cli_utils.logout_matrix_bot", new=MagicMock(return_value=True))
    @patch("builtins.print")
    def test_handle_auth_logout_keyboard_interrupt(self, mock_print, mock_asyncio_run):
        """Test logout handles KeyboardInterrupt gracefully."""
        # ASYNC MOCK FIX: Mock asyncio.run to raise KeyboardInterrupt
        mock_asyncio_run.side_effect = KeyboardInterrupt()
        self.mock_args.password = "test_password"
        self.mock_args.yes = True

        # Call function
        result = handle_auth_logout(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nLogout cancelled by user.")

    @patch(
        "mmrelay.cli_utils.logout_matrix_bot",
        new=MagicMock(side_effect=RuntimeError("Test error")),
    )
    @patch("builtins.print")
    def test_handle_auth_logout_exception_handling(self, mock_print):
        """Test logout handles runtime exceptions gracefully."""
        # ASYNC MOCK FIX: Make the mock raise RuntimeError when called
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
            homeserver=None, username=None, password=None, logout_others=False
        )
        # Check that header was printed for interactive mode
        mock_print.assert_any_call("\nMatrix Bot Authentication")
        mock_print.assert_any_call("=========================")

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
            homeserver=None, username=None, password=None, logout_others=False
        )

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_non_interactive_mode_success(
        self, mock_print, mock_login
    ):
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
            logout_others=False,
        )
        # Should NOT print header in non-interactive mode
        mock_print.assert_not_called()

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_non_interactive_mode_failure(
        self, mock_print, mock_login
    ):
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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --username, --password

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --homeserver, --password

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --homeserver, --username

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --password

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --username

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --homeserver

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

    @patch("builtins.print")
    def test_handle_auth_login_error_message_guidance(self, mock_print):
        """Test that error messages include helpful guidance."""
        self.mock_args.homeserver = "https://matrix.org"
        # username and password remain None

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        # Check that guidance messages are included in the combined message
        expected_message = """❌ Error: All authentication parameters are required when using command-line options.
   Missing: --username, --password

💡 Options:
   • For secure interactive authentication: mmrelay auth login
   • For automated authentication: provide all three parameters

⚠️  Security Note: Command-line passwords may be visible in process lists and shell history.
   Interactive mode is recommended for manual use."""
        mock_print.assert_called_once_with(expected_message)

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
    def test_handle_auth_login_runtime_exception(self, mock_print, mock_login):
        """Test handling of runtime exceptions during login."""
        # ASYNC MOCK FIX: Make the mock raise RuntimeError when called
        mock_login.side_effect = RuntimeError("Test error")

        # Call function
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_any_call("\nError during authentication: Test error")

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_empty_string_parameters(self, mock_print, mock_login):
        """Test that empty string parameters are rejected with validation error."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = True

        # Set parameters to empty strings
        self.mock_args.homeserver = ""
        self.mock_args.username = ""
        self.mock_args.password = ""

        # Call function (should reject empty strings with validation error)
        result = handle_auth_login(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)  # Should return error code
        mock_print.assert_any_call(
            "❌ Error: --homeserver and --username must be non-empty for non-interactive login."
        )
        mock_login.assert_not_called()  # Should not attempt login

    @patch("mmrelay.matrix_utils.login_matrix_bot")
    @patch("builtins.print")
    def test_handle_auth_login_none_vs_empty_string_distinction(
        self, mock_print, mock_login
    ):
        """Test that None parameters trigger interactive mode while empty strings trigger non-interactive mode."""
        # ASYNC MOCK FIX: Return value directly, not a coroutine
        mock_login.return_value = True

        # Test 1: None parameters should trigger interactive mode
        self.mock_args.homeserver = None
        self.mock_args.username = None
        self.mock_args.password = None

        result = handle_auth_login(self.mock_args)

        # Verify interactive mode
        self.assertEqual(result, 0)
        mock_login.assert_called_with(
            homeserver=None, username=None, password=None, logout_others=False
        )
        mock_print.assert_any_call("\nMatrix Bot Authentication")
        mock_print.assert_any_call("=========================")

        # Reset mocks for second test
        mock_login.reset_mock()
        mock_print.reset_mock()

        # Test 2: Empty string parameters should be rejected with validation error
        self.mock_args.homeserver = ""
        self.mock_args.username = ""
        self.mock_args.password = ""

        result = handle_auth_login(self.mock_args)

        # Verify validation error
        self.assertEqual(result, 1)  # Should return error code
        mock_print.assert_any_call(
            "❌ Error: --homeserver and --username must be non-empty for non-interactive login."
        )
        # Should not attempt login after validation error


class TestAuthStatus(unittest.TestCase):
    """Test cases for handle_auth_status function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.cli_utils.get_command")
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("builtins.print")
    def test_handle_auth_status_credentials_found_success(
        self, mock_print, mock_file, mock_exists, mock_get_paths, mock_get_command
    ):
        """Test successful status check when credentials.json exists and is valid."""
        # Setup mocks
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_exists.return_value = True
        mock_get_command.return_value = "mmrelay auth login"

        # Mock valid credentials.json content
        credentials_data = {
            "homeserver": "https://matrix.org",
            "access_token": "syt_dGVzdA_test_token_here",
            "user_id": "@bot:matrix.org",
            "device_id": "DEVICEABC123",
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import handle_auth_status

        result = handle_auth_status(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_get_paths.assert_called_once_with(self.mock_args)
        # Ensure at least one credentials candidate path was checked and opened.
        self.assertGreaterEqual(mock_exists.call_count, 1)
        self.assertGreaterEqual(mock_file.call_count, 1)

        # Check printed output
        mock_print.assert_any_call("Matrix Authentication Status")
        mock_print.assert_any_call("============================")
        found_line = None
        for call in mock_print.call_args_list:
            if call.args and isinstance(call.args[0], str):
                if call.args[0].startswith("✅ Found credentials.json at: "):
                    found_line = call.args[0]
                    break
        self.assertIsNotNone(found_line)
        mock_print.assert_any_call("   Homeserver: https://matrix.org")
        mock_print.assert_any_call("   User ID: @bot:matrix.org")
        mock_print.assert_any_call("   Device ID: DEVICEABC123")

    @patch("mmrelay.cli_utils.get_command")
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_handle_auth_status_credentials_not_found(
        self, mock_print, mock_exists, mock_get_paths, mock_get_command
    ):
        """Test status check when credentials.json does not exist."""
        # Setup mocks
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_exists.return_value = False
        mock_get_command.return_value = "mmrelay auth login"

        # Import and call function
        from mmrelay.cli import handle_auth_status

        result = handle_auth_status(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_get_paths.assert_called_once_with(self.mock_args)
        mock_exists.assert_any_call("/home/user/.mmrelay/credentials.json")

        # Check printed output
        mock_print.assert_any_call("Matrix Authentication Status")
        mock_print.assert_any_call("============================")
        mock_print.assert_any_call("❌ No credentials.json found")
        mock_print.assert_any_call("Run 'mmrelay auth login' to authenticate")

    @patch("mmrelay.cli_utils.get_command")
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("builtins.print")
    def test_handle_auth_status_credentials_invalid_json(
        self, mock_print, mock_file, mock_exists, mock_get_paths, mock_get_command
    ):
        """Test status check when credentials.json exists but contains invalid JSON."""
        # Setup mocks
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_exists.return_value = True
        mock_get_command.return_value = "mmrelay auth login"

        # Mock invalid JSON content
        mock_file.return_value.read.return_value = "invalid json content"
        mock_file.side_effect = json.JSONDecodeError("Invalid JSON", "doc", 0)

        # Import and call function
        from mmrelay.cli import handle_auth_status

        result = handle_auth_status(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_get_paths.assert_called_once_with(self.mock_args)
        mock_exists.assert_any_call("/home/user/.mmrelay/credentials.json")

        # Check error output
        mock_print.assert_any_call("Matrix Authentication Status")
        mock_print.assert_any_call("============================")
        # Should print error message about reading credentials.json

    @patch("mmrelay.cli_utils.get_command")
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    @patch("builtins.print")
    def test_handle_auth_status_credentials_missing_fields(
        self, mock_print, mock_file, mock_exists, mock_get_paths, mock_get_command
    ):
        """Test status check when credentials.json exists but is missing some fields."""
        # Setup mocks
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_exists.return_value = True
        mock_get_command.return_value = "mmrelay auth login"

        # Mock credentials with missing fields
        credentials_data = {
            "homeserver": "https://matrix.org",
            # Missing user_id and device_id
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import handle_auth_status

        result = handle_auth_status(self.mock_args)

        # Verify results - invalid credentials should be skipped and auth is reported missing
        self.assertEqual(result, 1)

        # Check printed output shows invalid credentials were skipped
        mock_print.assert_any_call(
            "⚠️  Skipping invalid credentials.json at /home/user/.mmrelay/credentials.json (missing required fields)"
        )
        mock_print.assert_any_call("Run 'mmrelay auth login' to authenticate")

    @patch("mmrelay.cli_utils.get_command")
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_handle_auth_status_multiple_config_paths(
        self, mock_print, mock_exists, mock_get_paths, mock_get_command
    ):
        """Test status check with multiple config paths, credentials found in second path."""
        # Setup mocks - multiple config paths
        mock_get_paths.return_value = [
            "/home/user/.mmrelay/config.yaml",
            "/etc/mmrelay/config.yaml",
        ]
        # First path doesn't have credentials, second path does
        mock_exists.side_effect = lambda path: path == "/etc/mmrelay/credentials.json"
        mock_get_command.return_value = "mmrelay auth login"

        # Mock valid credentials.json content
        credentials_data = {
            "homeserver": "https://matrix.example.com",
            "access_token": "syt_dGVzdA_test_token_here",
            "user_id": "@relay:example.com",
            "device_id": "DEVICE456",
        }

        with patch("builtins.open", mock_open(read_data=json.dumps(credentials_data))):
            # Import and call function
            from mmrelay.cli import handle_auth_status

            result = handle_auth_status(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_get_paths.assert_called_once_with(self.mock_args)

        # Should check configured candidates, including second config path.
        mock_exists.assert_any_call("/home/user/.mmrelay/credentials.json")
        mock_exists.assert_any_call("/home/user/.mmrelay/matrix/credentials.json")
        mock_exists.assert_any_call("/etc/mmrelay/credentials.json")

        # Check printed output shows second path
        mock_print.assert_any_call(
            "✅ Found credentials.json at: /etc/mmrelay/credentials.json"
        )
        mock_print.assert_any_call("   Homeserver: https://matrix.example.com")
        mock_print.assert_any_call("   User ID: @relay:example.com")
        mock_print.assert_any_call("   Device ID: DEVICE456")


class TestServiceCommand(unittest.TestCase):
    """Test cases for handle_service_command function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.setup_utils.install_service")
    @patch("builtins.print")
    def test_handle_service_command_install_success(self, mock_print, mock_install):
        """Test successful service installation."""
        # Setup mocks
        self.mock_args.service_command = "install"
        mock_install.return_value = True

        # Import and call function
        from mmrelay.cli import handle_service_command

        result = handle_service_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_install.assert_called_once()
        mock_print.assert_not_called()  # No error messages on success

    @patch("mmrelay.setup_utils.install_service")
    @patch("builtins.print")
    def test_handle_service_command_install_failure(self, mock_print, mock_install):
        """Test failed service installation."""
        # Setup mocks
        self.mock_args.service_command = "install"
        mock_install.return_value = False

        # Import and call function
        from mmrelay.cli import handle_service_command

        result = handle_service_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_install.assert_called_once()
        mock_print.assert_not_called()  # No error messages, just return code

    @patch(
        "mmrelay.setup_utils.install_service",
        side_effect=ImportError("Module not found"),
    )
    @patch("builtins.print")
    def test_handle_service_command_install_import_error(
        self, mock_print, mock_install
    ):
        """Test service installation when setup_utils cannot be imported."""
        # Setup mocks
        self.mock_args.service_command = "install"

        # Import and call function
        from mmrelay.cli import handle_service_command

        result = handle_service_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_called_once_with(
            "Error importing setup utilities: Module not found"
        )

    @patch("builtins.print")
    def test_handle_service_command_unknown_command(self, mock_print):
        """Test handling of unknown service commands."""
        # Setup mocks
        self.mock_args.service_command = "unknown_command"

        # Import and call function
        from mmrelay.cli import handle_service_command

        result = handle_service_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_called_once_with("Unknown service command: unknown_command")

    @patch("builtins.print")
    def test_handle_service_command_none_command(self, mock_print):
        """Test handling when service_command is None."""
        # Setup mocks
        self.mock_args.service_command = None

        # Import and call function
        from mmrelay.cli import handle_service_command

        result = handle_service_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)
        mock_print.assert_called_once_with("Unknown service command: None")


class TestValidateE2EEDependencies(unittest.TestCase):
    """Test cases for _validate_e2ee_dependencies function."""

    @patch("sys.platform", "win32")
    @patch("builtins.print")
    def test_validate_e2ee_dependencies_windows_platform(self, mock_print):
        """Test E2EE validation on Windows platform (should fail)."""
        from mmrelay.cli import _validate_e2ee_dependencies

        result = _validate_e2ee_dependencies()

        # Verify results
        self.assertFalse(result)
        # Function uses print statements, not logger
        mock_print.assert_any_call("❌ Error: E2EE is not supported on Windows")
        mock_print.assert_any_call(
            "   Reason: python-olm library requires native C libraries"
        )
        mock_print.assert_any_call("   Solution: Use Linux or macOS for E2EE support")

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_missing_homeserver(self, mock_file, mock_exists):
        """Test validation when credentials.json is missing homeserver field."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with missing homeserver
        credentials_data = {
            "access_token": "syt_test_token_123",
            "user_id": "@bot:matrix.org",
            "device_id": "DEVICEABC123",
            # Missing homeserver
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_missing_access_token(
        self, mock_file, mock_exists
    ):
        """Test validation when credentials.json is missing access_token field."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with missing access_token
        credentials_data = {
            "homeserver": "https://matrix.org",
            "user_id": "@bot:matrix.org",
            "device_id": "DEVICEABC123",
            # Missing access_token
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_missing_user_id(self, mock_file, mock_exists):
        """Test validation when credentials.json is missing user_id field."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with missing user_id
        credentials_data = {
            "homeserver": "https://matrix.org",
            "access_token": "syt_test_token_123",
            "device_id": "DEVICEABC123",
            # Missing user_id
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_missing_device_id(self, mock_file, mock_exists):
        """Test validation when credentials.json is missing device_id field."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with missing device_id
        credentials_data = {
            "homeserver": "https://matrix.org",
            "access_token": "syt_test_token_123",
            "user_id": "@bot:matrix.org",
            # Missing device_id
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertTrue(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_empty_field_values(self, mock_file, mock_exists):
        """Test validation when credentials.json has empty field values."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with empty homeserver field
        credentials_data = {
            "homeserver": "",  # Empty value
            "access_token": "syt_test_token_123",
            "user_id": "@bot:matrix.org",
            "device_id": "DEVICEABC123",
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    def test_validate_credentials_json_file_read_error(self, mock_exists):
        """Test validation when credentials.json cannot be read due to permissions or other IO error."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock file read error
        with patch("builtins.open", side_effect=IOError("Permission denied")):
            # Import and call function
            from mmrelay.cli import _validate_credentials_json

            with self.assertLogs("mmrelay.cli", level="WARNING"):
                result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_invalid_json(self, mock_file, mock_exists):
        """Test validation when credentials.json contains invalid JSON."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True
        # Mock file with invalid JSON
        mock_file.return_value.read.return_value = "{invalid json}"

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_validate_credentials_json_multiple_missing_fields(
        self, mock_file, mock_exists
    ):
        """Test validation when credentials.json is missing multiple fields (should report first missing)."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        mock_exists.return_value = True

        # Mock credentials with multiple missing fields
        credentials_data = {
            "homeserver": "https://matrix.org"
            # Missing access_token, user_id, device_id
        }
        mock_file.return_value.read.return_value = json.dumps(credentials_data)

        # Import and call function
        from mmrelay.cli import _validate_credentials_json

        with self.assertLogs("mmrelay.cli", level="WARNING"):
            result = _validate_credentials_json(config_path)

        # Verify results
        self.assertFalse(result)


class TestIsValidNonEmptyString(unittest.TestCase):
    """Test the _is_valid_non_empty_string helper function."""

    def test_valid_non_empty_string(self):
        """Test that valid non-empty strings return True."""
        from mmrelay.cli import _is_valid_non_empty_string

        self.assertTrue(_is_valid_non_empty_string("valid_string"))
        self.assertTrue(_is_valid_non_empty_string("token123"))
        self.assertTrue(_is_valid_non_empty_string("  spaced  "))  # strips whitespace

    def test_empty_string(self):
        """Test that empty strings return False."""
        from mmrelay.cli import _is_valid_non_empty_string

        self.assertFalse(_is_valid_non_empty_string(""))
        self.assertFalse(_is_valid_non_empty_string("   "))  # whitespace only
        self.assertFalse(_is_valid_non_empty_string("\t\n"))  # tabs and newlines

    def test_non_string_types(self):
        """Test that non-string types return False."""
        from mmrelay.cli import _is_valid_non_empty_string

        self.assertFalse(_is_valid_non_empty_string(None))
        self.assertFalse(_is_valid_non_empty_string(123))
        self.assertFalse(_is_valid_non_empty_string(True))
        self.assertFalse(_is_valid_non_empty_string(["list"]))
        self.assertFalse(_is_valid_non_empty_string({"dict": "value"}))


class TestValidateMatrixAuthentication(unittest.TestCase):
    """Test cases for _validate_matrix_authentication function."""

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("builtins.print")
    def test_validate_matrix_authentication_with_valid_credentials(
        self, mock_print, mock_validate_creds
    ):
        """Test authentication validation with valid credentials.json."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        matrix_section = {"access_token": "token123"}
        mock_validate_creds.return_value = True

        # Import and call function
        from mmrelay.cli import _validate_matrix_authentication

        result = _validate_matrix_authentication(config_path, matrix_section)

        # Verify results
        self.assertTrue(result)
        mock_validate_creds.assert_called_once_with(config_path, None)
        mock_print.assert_any_call(
            "✅ Using credentials.json for Matrix authentication"
        )
        # Should show E2EE support on non-Windows platforms
        if sys.platform != "win32":
            mock_print.assert_any_call("   E2EE support available (if enabled)")

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.cli.msg_for_e2ee_support")
    @patch("builtins.print")
    def test_validate_matrix_authentication_with_access_token_fallback(
        self, mock_print, mock_msg_e2ee, mock_validate_creds
    ):
        """Test authentication validation falling back to access_token."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        matrix_section = {"access_token": "token123"}
        mock_validate_creds.return_value = False  # No valid credentials.json
        mock_msg_e2ee.return_value = "E2EE not available with access_token"

        # Import and call function
        from mmrelay.cli import _validate_matrix_authentication

        result = _validate_matrix_authentication(config_path, matrix_section)

        # Verify results
        self.assertTrue(result)
        mock_validate_creds.assert_called_once_with(config_path, None)
        mock_print.assert_any_call(
            "✅ Using access_token for Matrix authentication (deprecated — consider 'mmrelay auth login' to create credentials.json)"
        )
        mock_print.assert_any_call("   E2EE not available with access_token")

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.cli.msg_setup_auth")
    @patch("builtins.print")
    def test_validate_matrix_authentication_no_auth_configured(
        self, mock_print, mock_msg_setup, mock_validate_creds
    ):
        """Test authentication validation with no authentication configured."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        matrix_section = {}  # No access_token
        mock_validate_creds.return_value = False  # No valid credentials.json
        mock_msg_setup.return_value = (
            "Please run 'mmrelay auth login' to set up authentication"
        )

        # Import and call function
        from mmrelay.cli import _validate_matrix_authentication

        result = _validate_matrix_authentication(config_path, matrix_section)

        # Verify results
        self.assertFalse(result)
        mock_validate_creds.assert_called_once_with(config_path, None)
        mock_print.assert_any_call("❌ Error: No Matrix authentication configured")
        mock_print.assert_any_call(
            "   Please run 'mmrelay auth login' to set up authentication"
        )

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("builtins.print")
    def test_validate_matrix_authentication_none_matrix_section(
        self, mock_print, mock_validate_creds
    ):
        """Test authentication validation with None matrix_section."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        matrix_section = None  # No matrix section
        mock_validate_creds.return_value = False  # No valid credentials.json

        # Import and call function
        from mmrelay.cli import _validate_matrix_authentication

        result = _validate_matrix_authentication(config_path, matrix_section)

        # Verify results
        self.assertFalse(result)
        mock_validate_creds.assert_called_once_with(config_path, None)
        mock_print.assert_any_call("❌ Error: No Matrix authentication configured")

    @patch("mmrelay.cli._validate_credentials_json")
    @patch("mmrelay.cli.msg_for_e2ee_support")
    @patch("builtins.print")
    def test_validate_matrix_authentication_empty_access_token(
        self, mock_print, mock_msg_e2ee, mock_validate_creds
    ):
        """Test authentication validation with empty access_token (now correctly rejected)."""
        # Setup mocks
        config_path = "/home/user/.mmrelay/config.yaml"
        matrix_section = {"access_token": ""}  # Empty access_token (should be rejected)
        mock_validate_creds.return_value = False  # No valid credentials.json
        mock_msg_e2ee.return_value = "E2EE not available with access_token"

        # Import and call function
        from mmrelay.cli import _validate_matrix_authentication

        result = _validate_matrix_authentication(config_path, matrix_section)

        # Verify results
        self.assertFalse(result)  # Function now correctly rejects empty strings
        mock_validate_creds.assert_called_once_with(config_path, None)
        mock_print.assert_any_call("❌ Error: No Matrix authentication configured")
        mock_print.assert_any_call("   Setup: mmrelay auth login")


class TestHandleCliCommands(unittest.TestCase):
    """Test cases for handle_cli_commands function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        # Set all flags to False by default
        self.mock_args.version = False
        self.mock_args.install_service = False
        self.mock_args.generate_config = False
        self.mock_args.check_config = False

    @patch("mmrelay.cli.print_version")
    def test_handle_cli_commands_version_flag(self, mock_print_version):
        """Test handling of --version flag."""
        # Setup mocks
        self.mock_args.version = True

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)  # Should return 0 for success
        mock_print_version.assert_called_once()

    @patch("mmrelay.setup_utils.install_service")
    def test_handle_cli_commands_install_service_success(self, mock_install_service):
        """Test handling of --install-service flag with success."""
        # Setup mocks
        self.mock_args.install_service = True
        mock_install_service.return_value = True

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        mock_install_service.assert_called_once()
        self.assertEqual(result, 0)  # Should return 0 for success

    @patch("mmrelay.setup_utils.install_service")
    def test_handle_cli_commands_install_service_failure(self, mock_install_service):
        """Test handling of --install-service flag with failure."""
        # Setup mocks
        self.mock_args.install_service = True
        mock_install_service.return_value = False

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        mock_install_service.assert_called_once()
        self.assertEqual(result, 1)  # Should return 1 for error

    @patch("mmrelay.cli.generate_sample_config")
    def test_handle_cli_commands_generate_config_success(self, mock_generate_config):
        """Test handling of --generate-config flag with success."""
        # Setup mocks
        self.mock_args.generate_config = True
        mock_generate_config.return_value = True

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)  # Should return 0 for success
        mock_generate_config.assert_called_once()

    @patch("mmrelay.cli.generate_sample_config")
    def test_handle_cli_commands_generate_config_failure(self, mock_generate_config):
        """Test handling of --generate-config flag with failure."""
        # Setup mocks
        self.mock_args.generate_config = True
        mock_generate_config.return_value = False

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        mock_generate_config.assert_called_once()
        self.assertEqual(result, 1)  # Should return 1 for error

    @patch("mmrelay.cli.check_config")
    def test_handle_cli_commands_check_config_success(self, mock_check_config):
        """Test handling of --check-config flag with success."""
        # Setup mocks
        self.mock_args.check_config = True
        mock_check_config.return_value = True

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        mock_check_config.assert_called_once()
        self.assertEqual(result, 0)  # Should return 0 for success

    @patch("mmrelay.cli.check_config")
    def test_handle_cli_commands_check_config_failure(self, mock_check_config):
        """Test handling of --check-config flag with failure."""
        # Setup mocks
        self.mock_args.check_config = True
        mock_check_config.return_value = False

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        mock_check_config.assert_called_once()
        self.assertEqual(result, 1)  # Should return 1 for error

    def test_handle_cli_commands_no_flags(self):
        """Test handling when no CLI flags are set."""
        # All flags are False by default in setUp

        # Import and call function

        result = handle_cli_commands(self.mock_args)

        # Verify results
        self.assertIsNone(
            result
        )  # Should return None indicating no command was handled


class TestHandleSubcommand(unittest.TestCase):
    """Test cases for handle_subcommand function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.cli.handle_config_command")
    @patch("builtins.print")
    def test_handle_subcommand_config(self, mock_print, mock_handle_config):
        """Test dispatching to config command handler."""
        # Setup mocks
        self.mock_args.command = "config"
        mock_handle_config.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_subcommand

        result = handle_subcommand(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_config.assert_called_once_with(self.mock_args)
        mock_print.assert_not_called()

    @patch("mmrelay.cli.handle_auth_command")
    @patch("builtins.print")
    def test_handle_subcommand_auth(self, mock_print, mock_handle_auth):
        """Test dispatching to auth command handler."""
        # Setup mocks
        self.mock_args.command = "auth"
        mock_handle_auth.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_subcommand

        result = handle_subcommand(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_auth.assert_called_once_with(self.mock_args)
        mock_print.assert_not_called()

    @patch("mmrelay.cli.handle_service_command")
    @patch("builtins.print")
    def test_handle_subcommand_service(self, mock_print, mock_handle_service):
        """Test dispatching to service command handler."""
        # Setup mocks
        self.mock_args.command = "service"
        mock_handle_service.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_subcommand

        result = handle_subcommand(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_service.assert_called_once_with(self.mock_args)
        mock_print.assert_not_called()

    @patch("builtins.print")
    def test_handle_subcommand_unknown_command(self, mock_print):
        """Test handling of unknown command."""
        # Setup mocks
        self.mock_args.command = "unknown"

        # Import and call function
        from mmrelay.cli import handle_subcommand

        result = handle_subcommand(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)  # Should return error code
        mock_print.assert_called_once_with("Unknown command: unknown")


class TestHandleConfigCommand(unittest.TestCase):
    """Test cases for handle_config_command function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.cli.generate_sample_config")
    @patch("builtins.print")
    def test_handle_config_command_generate_success(self, mock_print, mock_generate):
        """Test config generate command with success."""
        # Setup mocks
        self.mock_args.config_command = "generate"
        mock_generate.return_value = True

        # Import and call function
        from mmrelay.cli import handle_config_command

        result = handle_config_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_generate.assert_called_once()
        mock_print.assert_not_called()

    @patch("mmrelay.cli.generate_sample_config")
    @patch("builtins.print")
    def test_handle_config_command_generate_failure(self, mock_print, mock_generate):
        """Test config generate command with failure."""
        # Setup mocks
        self.mock_args.config_command = "generate"
        mock_generate.return_value = False

        # Import and call function
        from mmrelay.cli import handle_config_command

        result = handle_config_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)  # Should return error code
        mock_generate.assert_called_once()
        mock_print.assert_not_called()

    @patch("mmrelay.cli.check_config")
    @patch("builtins.print")
    def test_handle_config_command_check_success(self, mock_print, mock_check):
        """Test config check command with success."""
        # Setup mocks
        self.mock_args.config_command = "check"
        mock_check.return_value = True

        # Import and call function
        from mmrelay.cli import handle_config_command

        result = handle_config_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_check.assert_called_once_with(self.mock_args)
        mock_print.assert_not_called()

    @patch("mmrelay.cli.check_config")
    @patch("builtins.print")
    def test_handle_config_command_check_failure(self, mock_print, mock_check):
        """Test config check command with failure."""
        # Setup mocks
        self.mock_args.config_command = "check"
        mock_check.return_value = False

        # Import and call function
        from mmrelay.cli import handle_config_command

        result = handle_config_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)  # Should return error code
        mock_check.assert_called_once_with(self.mock_args)
        mock_print.assert_not_called()

    @patch("builtins.print")
    def test_handle_config_command_unknown_subcommand(self, mock_print):
        """Test handling of unknown config subcommand."""
        # Setup mocks
        self.mock_args.config_command = "unknown"

        # Import and call function
        from mmrelay.cli import handle_config_command

        result = handle_config_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 1)  # Should return error code
        mock_print.assert_called_once_with("Unknown config command: unknown")


class TestHandleAuthCommand(unittest.TestCase):
    """Test cases for handle_auth_command function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.cli.handle_auth_status")
    def test_handle_auth_command_status(self, mock_handle_status):
        """Test dispatching to auth status handler."""
        # Setup mocks
        self.mock_args.auth_command = "status"
        mock_handle_status.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_auth_command

        result = handle_auth_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_status.assert_called_once_with(self.mock_args)

    @patch("mmrelay.cli.handle_auth_logout")
    def test_handle_auth_command_logout(self, mock_handle_logout):
        """Test dispatching to auth logout handler."""
        # Setup mocks
        self.mock_args.auth_command = "logout"
        mock_handle_logout.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_auth_command

        result = handle_auth_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_logout.assert_called_once_with(self.mock_args)

    @patch("mmrelay.cli.handle_auth_login")
    def test_handle_auth_command_login_explicit(self, mock_handle_login):
        """Test dispatching to auth login handler with explicit login command."""
        # Setup mocks
        self.mock_args.auth_command = "login"
        mock_handle_login.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_auth_command

        result = handle_auth_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_login.assert_called_once_with(self.mock_args)

    @patch("mmrelay.cli.handle_auth_login")
    def test_handle_auth_command_unknown_defaults_to_login(self, mock_handle_login):
        """Test that unknown auth commands default to login."""
        # Setup mocks
        self.mock_args.auth_command = "unknown"
        mock_handle_login.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_auth_command

        result = handle_auth_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_login.assert_called_once_with(self.mock_args)

    @patch("mmrelay.cli.handle_auth_login")
    def test_handle_auth_command_no_auth_command_attribute(self, mock_handle_login):
        """Test that missing auth_command attribute defaults to login."""
        # Setup mocks - remove auth_command attribute
        if hasattr(self.mock_args, "auth_command"):
            delattr(self.mock_args, "auth_command")
        mock_handle_login.return_value = 0

        # Import and call function
        from mmrelay.cli import handle_auth_command

        result = handle_auth_command(self.mock_args)

        # Verify results
        self.assertEqual(result, 0)
        mock_handle_login.assert_called_once_with(self.mock_args)


class TestPrintVersion(unittest.TestCase):
    """Test cases for print_version function."""

    @patch("builtins.print")
    def test_print_version(self, mock_print):
        """Test that print_version outputs the correct format."""
        # Import and call function
        from mmrelay.cli import print_version

        print_version()

        # Verify results - should print version in expected format
        mock_print.assert_called_once()
        call_args = mock_print.call_args[0][0]  # Get the first argument
        self.assertTrue(call_args.startswith("MMRelay version "))
        self.assertIn(".", call_args)  # Should contain version number with dots


class TestGetVersion(unittest.TestCase):
    """Test cases for get_version function."""

    def test_get_version_returns_string(self):
        """Test that get_version returns a version string."""
        # Import and call function
        from mmrelay.cli import get_version

        version = get_version()

        # Verify results
        self.assertIsInstance(version, str)
        self.assertTrue(len(version) > 0)
        # Should be in semantic version format (at least one dot)
        self.assertIn(".", version)


class TestPrintEnvironmentSummary(unittest.TestCase):
    """Test cases for _print_environment_summary function."""

    @patch("sys.platform", "linux")
    @patch("sys.version", "3.12.3 (main, Apr 10 2024, 05:33:47) [GCC 13.2.0] on linux")
    @patch("builtins.print")
    def test_print_environment_summary_linux_with_e2ee(self, mock_print):
        """Test environment summary on Linux with E2EE dependencies available."""
        # Mock successful E2EE imports
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            # Import and call function
            from mmrelay.cli import _print_environment_summary

            _print_environment_summary()

        # Verify results
        mock_print.assert_any_call("\n🖥️  Environment Summary:")
        mock_print.assert_any_call("   Platform: linux")
        mock_print.assert_any_call("   Python: 3.12.3")
        mock_print.assert_any_call("   E2EE Support: ✅ Available and installed")

    @patch("sys.platform", "linux")
    @patch("sys.version", "3.12.3 (main, Apr 10 2024, 05:33:47) [GCC 13.2.0] on linux")
    @patch("builtins.print")
    def test_print_environment_summary_linux_without_e2ee(self, mock_print):
        """Test environment summary on Linux without E2EE dependencies."""
        # Import function first
        from mmrelay.cli import _print_environment_summary

        # Mock failed E2EE imports by removing modules from sys.modules and making import fail
        original_modules = sys.modules.copy()
        original_import = None
        try:
            # Remove E2EE modules if they exist
            for module in ["olm", "nio.crypto", "nio.store"]:
                if module in sys.modules:
                    del sys.modules[module]

            # Mock import to raise ImportError for E2EE modules
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                """
                Simulate missing optional modules by raising ImportError for select module names during imports.

                Parameters:
                    name (str): Fully-qualified module name being imported; if it equals "olm", "nio.crypto", or "nio.store" an ImportError is raised.
                    *args: Additional positional arguments passed to the underlying import machinery (passed through unchanged).
                    **kwargs: Additional keyword arguments passed through to the underlying import machinery.

                Returns:
                    module: The result of the normal import for module names other than the ones listed.

                Raises:
                    ImportError: If `name` is "olm", "nio.crypto", or "nio.store".
                """
                if name in ["olm", "nio.crypto", "nio.store"]:
                    raise ImportError(f"No module named '{name}'")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = mock_import

            # Call function
            _print_environment_summary()

        finally:
            # Restore original state
            if original_import is not None:
                builtins.__import__ = original_import
            sys.modules.update(original_modules)

        # Verify results
        mock_print.assert_any_call("\n🖥️  Environment Summary:")
        mock_print.assert_any_call("   Platform: linux")
        mock_print.assert_any_call("   Python: 3.12.3")
        mock_print.assert_any_call("   E2EE Support: ⚠️  Available but not installed")
        mock_print.assert_any_call("   Install: pipx install 'mmrelay[e2e]'")

    @patch("sys.platform", "win32")
    @patch(
        "sys.version",
        "3.12.3 (tags/v3.12.3:f6650f9, Apr  9 2024, 14:05:25) [MSC v.1938 64 bit (AMD64)] on win32",
    )
    @patch("builtins.print")
    def test_print_environment_summary_windows(self, mock_print):
        """Test environment summary on Windows (E2EE not supported)."""
        # Import and call function
        from mmrelay.cli import _print_environment_summary

        _print_environment_summary()

        # Verify results
        mock_print.assert_any_call("\n🖥️  Environment Summary:")
        mock_print.assert_any_call("   Platform: win32")
        mock_print.assert_any_call("   Python: 3.12.3")
        mock_print.assert_any_call(
            "   E2EE Support: ❌ Not available (Windows limitation)"
        )
        mock_print.assert_any_call("   Matrix Support: ✅ Available")

    @patch("sys.platform", "darwin")
    @patch(
        "sys.version",
        "3.12.3 (main, Apr 10 2024, 05:33:47) [Clang 15.0.0 (clang-1500.3.9.4)] on darwin",
    )
    @patch("builtins.print")
    def test_print_environment_summary_macos_with_e2ee(self, mock_print):
        """Test environment summary on macOS with E2EE dependencies available."""
        # Mock successful E2EE imports
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            # Import and call function
            from mmrelay.cli import _print_environment_summary

            _print_environment_summary()

        # Verify results
        mock_print.assert_any_call("\n🖥️  Environment Summary:")
        mock_print.assert_any_call("   Platform: darwin")
        mock_print.assert_any_call("   Python: 3.12.3")
        mock_print.assert_any_call("   E2EE Support: ✅ Available and installed")


class TestValidateE2eeConfig(unittest.TestCase):
    """Test cases for _validate_e2ee_config function."""

    def setUp(self):
        """Set up test fixtures."""
        self.config_path = "/home/user/.mmrelay/config.yaml"
        self.base_config = {"matrix": {"homeserver": "https://matrix.org"}}

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("builtins.print")
    def test_validate_e2ee_config_no_matrix_auth(self, mock_print, mock_validate_auth):
        """Test E2EE config validation when Matrix authentication fails."""
        # Setup mocks
        mock_validate_auth.return_value = False
        matrix_section = {"homeserver": "https://matrix.org"}

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertFalse(result)  # Should return False when auth fails
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_print.assert_not_called()  # Should not print anything

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("builtins.print")
    def test_validate_e2ee_config_no_matrix_section(
        self, mock_print, mock_validate_auth
    ):
        """Test E2EE config validation with no matrix section."""
        # Setup mocks
        mock_validate_auth.return_value = True
        matrix_section = None

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertTrue(result)  # Should return True when no matrix section
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_print.assert_not_called()  # Should not print anything

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("builtins.print")
    def test_validate_e2ee_config_e2ee_disabled(self, mock_print, mock_validate_auth):
        """Test E2EE config validation when E2EE is disabled."""
        # Setup mocks
        mock_validate_auth.return_value = True
        matrix_section = {
            "homeserver": "https://matrix.org",
            "e2ee": {"enabled": False},
        }

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertTrue(result)  # Should return True when E2EE disabled
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_print.assert_not_called()  # Should not print anything

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("mmrelay.cli._validate_e2ee_dependencies")
    @patch("builtins.print")
    def test_validate_e2ee_config_e2ee_enabled_deps_missing(
        self, mock_print, mock_validate_deps, mock_validate_auth
    ):
        """Test E2EE config validation when E2EE is enabled but dependencies missing."""
        # Setup mocks
        mock_validate_auth.return_value = True
        mock_validate_deps.return_value = False  # Dependencies missing
        matrix_section = {"homeserver": "https://matrix.org", "e2ee": {"enabled": True}}

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertFalse(result)  # Should return False when deps missing
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_validate_deps.assert_called_once()
        mock_print.assert_not_called()  # Dependencies function handles printing

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("mmrelay.cli._validate_e2ee_dependencies")
    @patch("os.path.exists")
    @patch("os.path.expanduser")
    @patch("builtins.print")
    def test_validate_e2ee_config_e2ee_enabled_with_store_path(
        self,
        mock_print,
        mock_expanduser,
        mock_exists,
        mock_validate_deps,
        mock_validate_auth,
    ):
        """Test E2EE config validation when E2EE is enabled with custom store path."""
        # Setup mocks
        mock_validate_auth.return_value = True
        mock_validate_deps.return_value = True
        mock_expanduser.return_value = "/home/user/.mmrelay/store"
        mock_exists.return_value = False  # Directory doesn't exist yet

        matrix_section = {
            "homeserver": "https://matrix.org",
            "e2ee": {"enabled": True, "store_path": "~/.mmrelay/store"},
        }

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertTrue(result)  # Should return True on success
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_validate_deps.assert_called_once()
        mock_expanduser.assert_called_once_with("~/.mmrelay/store")
        mock_print.assert_any_call(
            "Info: E2EE store directory will be created: /home/user/.mmrelay/store"
        )
        mock_print.assert_any_call("✅ E2EE configuration is valid")

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("mmrelay.cli._validate_e2ee_dependencies")
    @patch("builtins.print")
    def test_validate_e2ee_config_legacy_encryption_config(
        self, mock_print, mock_validate_deps, mock_validate_auth
    ):
        """Test E2EE config validation with legacy 'encryption' section."""
        # Setup mocks
        mock_validate_auth.return_value = True
        mock_validate_deps.return_value = True

        matrix_section = {
            "homeserver": "https://matrix.org",
            "encryption": {"enabled": True},  # Legacy config format
        }

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertTrue(result)  # Should return True on success
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_validate_deps.assert_called_once()
        mock_print.assert_any_call("✅ E2EE configuration is valid")

    @patch("mmrelay.cli._validate_matrix_authentication")
    @patch("mmrelay.cli._validate_e2ee_dependencies")
    @patch("os.path.exists")
    @patch("os.path.expanduser")
    @patch("builtins.print")
    def test_validate_e2ee_config_legacy_store_path(
        self,
        mock_print,
        mock_expanduser,
        mock_exists,
        mock_validate_deps,
        mock_validate_auth,
    ):
        """Test E2EE config validation with legacy 'encryption.store_path'."""
        # Setup mocks
        mock_validate_auth.return_value = True
        mock_validate_deps.return_value = True
        mock_expanduser.return_value = "/home/user/.mmrelay/legacy_store"
        mock_exists.return_value = True  # Directory exists

        matrix_section = {
            "homeserver": "https://matrix.org",
            "encryption": {  # Legacy config format
                "enabled": True,
                "store_path": "~/.mmrelay/legacy_store",
            },
        }

        # Import and call function
        from mmrelay.cli import _validate_e2ee_config

        result = _validate_e2ee_config(
            self.base_config, matrix_section, self.config_path
        )

        # Verify results
        self.assertTrue(result)  # Should return True on success
        mock_validate_auth.assert_called_once_with(
            self.config_path, matrix_section, self.base_config
        )
        mock_validate_deps.assert_called_once()
        mock_expanduser.assert_called_once_with("~/.mmrelay/legacy_store")
        # Should not print directory creation message since it exists
        mock_print.assert_any_call("✅ E2EE configuration is valid")
        # Should not print directory creation message
        self.assertNotIn(
            "Note: E2EE store directory will be created", str(mock_print.call_args_list)
        )


class TestAnalyzeE2eeSetup(unittest.TestCase):
    """Test cases for _analyze_e2ee_setup function."""

    def setUp(self):
        """Set up test fixtures."""
        self.config_path = "/home/user/.mmrelay/config.yaml"
        self.base_config = {
            "matrix": {"homeserver": "https://matrix.org", "e2ee": {"enabled": True}}
        }

    @patch("sys.platform", "win32")
    def test_analyze_e2ee_setup_windows_not_supported(self):
        """Test E2EE analysis on Windows (not supported)."""
        # Import and call function
        from mmrelay.cli import _analyze_e2ee_setup

        result = _analyze_e2ee_setup(self.base_config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertFalse(result["platform_supported"])
        self.assertEqual(result["overall_status"], "not_supported")
        self.assertIn(
            "E2EE is not supported on Windows. Use Linux/macOS for E2EE support.",
            result["recommendations"],
        )

    @patch("sys.platform", "linux")
    def test_analyze_e2ee_setup_linux_no_e2ee_config(self):
        """Test E2EE analysis on Linux with no E2EE configuration."""
        # Setup config without E2EE
        config = {
            "matrix": {
                "homeserver": "https://matrix.org"
                # No e2ee section
            }
        }

        # Import and call function
        from mmrelay.cli import _analyze_e2ee_setup

        result = _analyze_e2ee_setup(config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertFalse(result["config_enabled"])
        self.assertEqual(result["overall_status"], "disabled")

    @patch("sys.platform", "linux")
    def test_analyze_e2ee_setup_linux_e2ee_disabled(self):
        """Test E2EE analysis on Linux with E2EE explicitly disabled."""
        # Setup config with E2EE disabled
        config = {
            "matrix": {"homeserver": "https://matrix.org", "e2ee": {"enabled": False}}
        }

        # Import and call function
        from mmrelay.cli import _analyze_e2ee_setup

        result = _analyze_e2ee_setup(config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertFalse(result["config_enabled"])
        self.assertEqual(result["overall_status"], "disabled")

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_linux_dependencies_missing(self, mock_exists):
        """Test E2EE analysis on Linux with missing dependencies."""
        # Setup mocks
        mock_exists.return_value = False  # No credentials file

        # Mock missing E2EE dependencies
        original_modules = sys.modules.copy()
        original_import = None
        try:
            # Remove E2EE modules if they exist
            for module in ["olm", "nio.crypto", "nio.store"]:
                if module in sys.modules:
                    del sys.modules[module]

            # Mock import to raise ImportError for E2EE modules
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                """
                Simulate missing optional modules by raising ImportError for select module names during imports.

                Parameters:
                    name (str): Fully-qualified module name being imported; if it equals "olm", "nio.crypto", or "nio.store" an ImportError is raised.
                    *args: Additional positional arguments passed to the underlying import machinery (passed through unchanged).
                    **kwargs: Additional keyword arguments passed through to the underlying import machinery.

                Returns:
                    module: The result of the normal import for module names other than the ones listed.

                Raises:
                    ImportError: If `name` is "olm", "nio.crypto", or "nio.store".
                """
                if name in ["olm", "nio.crypto", "nio.store"]:
                    raise ImportError(f"No module named '{name}'")
                return original_import(name, *args, **kwargs)

            builtins.__import__ = mock_import

            # Import and call function
            from mmrelay.cli import _analyze_e2ee_setup

            result = _analyze_e2ee_setup(self.base_config, self.config_path)

        finally:
            # Restore original state
            if original_import is not None:
                builtins.__import__ = original_import
            sys.modules.update(original_modules)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertTrue(result["config_enabled"])
        self.assertFalse(result["dependencies_available"])
        self.assertFalse(result["credentials_available"])
        self.assertEqual(result["overall_status"], "incomplete")
        self.assertIn(
            "Install E2EE dependencies: pipx install 'mmrelay[e2e]'",
            result["recommendations"],
        )
        self.assertIn(
            "Set up Matrix authentication: mmrelay auth login",
            result["recommendations"],
        )

    @patch("sys.platform", "linux")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_linux_ready_state(self, mock_exists):
        """Test E2EE analysis on Linux with everything ready."""
        # Setup mocks
        mock_exists.return_value = True  # Credentials file exists

        # Mock successful E2EE dependencies
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            # Import and call function
            from mmrelay.cli import _analyze_e2ee_setup

            result = _analyze_e2ee_setup(self.base_config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertTrue(result["config_enabled"])
        self.assertTrue(result["dependencies_available"])
        self.assertTrue(result["credentials_available"])
        self.assertEqual(result["overall_status"], "ready")
        self.assertEqual(
            len(result["recommendations"]), 0
        )  # No recommendations when ready

    @patch("sys.platform", "darwin")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_macos_legacy_encryption_config(self, mock_exists):
        """Test E2EE analysis on macOS with legacy encryption configuration."""
        # Setup config with legacy encryption section
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "encryption": {"enabled": True},  # Legacy format
            }
        }
        mock_exists.return_value = True  # Credentials file exists

        # Mock successful E2EE dependencies
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            # Import and call function
            from mmrelay.cli import _analyze_e2ee_setup

            result = _analyze_e2ee_setup(config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertTrue(result["config_enabled"])  # Should detect legacy config
        self.assertTrue(result["dependencies_available"])
        self.assertTrue(result["credentials_available"])
        self.assertEqual(result["overall_status"], "ready")

    @patch("sys.platform", "linux")
    @patch("mmrelay.config.get_base_dir")
    @patch("os.path.exists")
    def test_analyze_e2ee_setup_standard_credentials_path(
        self, mock_exists, mock_get_base_dir
    ):
        """Test E2EE analysis with standard credentials path."""
        # Setup mocks
        mock_get_base_dir.return_value = "/home/user/.mmrelay"

        # Mock exists to return True for standard path but False for config dir path
        def mock_exists_side_effect(path):
            if path == "/home/user/.mmrelay/credentials.json":
                return True  # Standard path exists
            return False  # Config dir path doesn't exist

        mock_exists.side_effect = mock_exists_side_effect

        # Mock successful E2EE dependencies
        with patch.dict(
            "sys.modules",
            {"olm": MagicMock(), "nio.crypto": MagicMock(), "nio.store": MagicMock()},
        ):
            # Import and call function
            from mmrelay.cli import _analyze_e2ee_setup

            result = _analyze_e2ee_setup(self.base_config, self.config_path)

        # Verify results
        self.assertIsInstance(result, dict)
        self.assertTrue(result["platform_supported"])
        self.assertTrue(result["config_enabled"])
        self.assertTrue(result["dependencies_available"])
        self.assertTrue(result["credentials_available"])
        self.assertEqual(result["overall_status"], "ready")


if __name__ == "__main__":
    unittest.main()
