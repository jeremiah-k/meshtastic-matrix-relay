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
        Tests that the main function calls run_main with parsed arguments and returns its exit code when no special CLI commands are specified.
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

        with patch('importlib.util.find_spec') as mock_find_spec:
            mock_find_spec.return_value = MagicMock()  # Dependency found
            result = _validate_e2ee_dependencies()
            self.assertTrue(result)

    def test_validate_e2ee_dependencies_missing(self):
        """Test _validate_e2ee_dependencies when dependencies are missing."""
        from mmrelay.cli import _validate_e2ee_dependencies

        with patch('importlib.util.find_spec') as mock_find_spec:
            mock_find_spec.return_value = None  # Dependency not found
            result = _validate_e2ee_dependencies()
            self.assertFalse(result)

    @patch('os.path.exists')
    def test_validate_credentials_json_exists(self, mock_exists):
        """Test _validate_credentials_json when credentials.json exists and is valid."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        with patch('builtins.open', mock_open(read_data='{"access_token": "test_token", "device_id": "test_device"}')):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertTrue(result)

    @patch('os.path.exists')
    def test_validate_credentials_json_missing(self, mock_exists):
        """Test _validate_credentials_json when credentials.json doesn't exist."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = False
        result = _validate_credentials_json("/path/to/config.yaml")
        self.assertFalse(result)

    @patch('os.path.exists')
    def test_validate_credentials_json_invalid(self, mock_exists):
        """Test _validate_credentials_json when credentials.json exists but is invalid."""
        from mmrelay.cli import _validate_credentials_json

        mock_exists.return_value = True

        with patch('builtins.open', mock_open(read_data='{"incomplete": "data"}')):
            result = _validate_credentials_json("/path/to/config.yaml")
            self.assertFalse(result)

    def test_validate_matrix_authentication_with_credentials(self):
        """Test _validate_matrix_authentication with valid credentials.json."""
        from mmrelay.cli import _validate_matrix_authentication

        with patch('mmrelay.cli._validate_credentials_json', return_value=True):
            result = _validate_matrix_authentication("/path/to/config.yaml", None)
            self.assertEqual(result, "credentials")

    def test_validate_matrix_authentication_with_config(self):
        """Test _validate_matrix_authentication with valid matrix config section."""
        from mmrelay.cli import _validate_matrix_authentication

        matrix_section = {
            "homeserver": "https://matrix.org",
            "access_token": "test_token",
            "bot_user_id": "@bot:matrix.org"
        }

        with patch('mmrelay.cli._validate_credentials_json', return_value=False):
            result = _validate_matrix_authentication("/path/to/config.yaml", matrix_section)
            self.assertEqual(result, "config")

    def test_validate_matrix_authentication_none(self):
        """Test _validate_matrix_authentication with no valid authentication."""
        from mmrelay.cli import _validate_matrix_authentication

        with patch('mmrelay.cli._validate_credentials_json', return_value=False):
            result = _validate_matrix_authentication("/path/to/config.yaml", None)
            self.assertIsNone(result)


class TestCLISubcommandHandlers(unittest.TestCase):
    """Test cases for CLI subcommand handler functions."""

    def test_handle_subcommand_config(self):
        """Test handle_subcommand dispatching to config commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "config"

        with patch('mmrelay.cli.handle_config_command', return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_subcommand_auth(self):
        """Test handle_subcommand dispatching to auth commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "auth"

        with patch('mmrelay.cli.handle_auth_command', return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_subcommand_service(self):
        """Test handle_subcommand dispatching to service commands."""
        from mmrelay.cli import handle_subcommand

        args = MagicMock()
        args.command = "service"

        with patch('mmrelay.cli.handle_service_command', return_value=0) as mock_handle:
            result = handle_subcommand(args)
            self.assertEqual(result, 0)
            mock_handle.assert_called_once_with(args)

    def test_handle_config_command_generate(self):
        """Test handle_config_command with generate subcommand."""
        from mmrelay.cli import handle_config_command

        args = MagicMock()
        args.config_command = "generate"

        with patch('mmrelay.cli.generate_sample_config', return_value=True) as mock_generate:
            result = handle_config_command(args)
            self.assertEqual(result, 0)
            mock_generate.assert_called_once()

    def test_handle_config_command_check(self):
        """Test handle_config_command with check subcommand."""
        from mmrelay.cli import handle_config_command

        args = MagicMock()
        args.config_command = "check"

        with patch('mmrelay.cli.check_config', return_value=True) as mock_check:
            result = handle_config_command(args)
            self.assertEqual(result, 0)
            mock_check.assert_called_once_with(args)

    def test_handle_auth_command_login(self):
        """Test handle_auth_command with login subcommand."""
        from mmrelay.cli import handle_auth_command

        args = MagicMock()
        args.auth_command = "login"

        with patch('mmrelay.cli.handle_auth_login', return_value=0) as mock_login:
            result = handle_auth_command(args)
            self.assertEqual(result, 0)
            mock_login.assert_called_once_with(args)

    def test_handle_auth_command_status(self):
        """Test handle_auth_command with status subcommand."""
        from mmrelay.cli import handle_auth_command

        args = MagicMock()
        args.auth_command = "status"

        with patch('mmrelay.cli.handle_auth_status', return_value=0) as mock_status:
            result = handle_auth_command(args)
            self.assertEqual(result, 0)
            mock_status.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
