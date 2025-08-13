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

    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_valid(self, mock_yaml_load, mock_open, mock_isfile):
        # Mock a valid config
        """
        Test that check_config returns True for a valid configuration file.

        Mocks a configuration containing all required sections and valid values, simulates the presence of the config file, and verifies that check_config() recognizes it as valid.
        """
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }
        mock_isfile.return_value = True

        with patch("sys.argv", ["mmrelay", "--config", "valid_config.yaml"]):
            self.assertTrue(check_config())

    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_invalid_missing_matrix(
        self, mock_yaml_load, mock_open, mock_isfile
    ):
        # Mock an invalid config (missing matrix section)
        """
        Test that check_config returns False when the configuration is missing the 'matrix' section.
        """
        mock_yaml_load.return_value = {
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }
        mock_isfile.return_value = True

        with patch("sys.argv", ["mmrelay", "--config", "invalid_config.yaml"]):
            self.assertFalse(check_config())

    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_invalid_missing_meshtastic(
        self, mock_yaml_load, mock_open, mock_isfile
    ):
        # Mock an invalid config (missing meshtastic section)
        """
        Test that check_config returns False when the configuration is missing the 'meshtastic' section.
        """
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
        }
        mock_isfile.return_value = True

        with patch("sys.argv", ["mmrelay", "--config", "invalid_config.yaml"]):
            self.assertFalse(check_config())

    @patch("mmrelay.cli.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_invalid_connection_type(
        self, mock_yaml_load, mock_open, mock_isfile
    ):
        # Mock an invalid config (invalid connection type)
        """
        Test that check_config() returns False when the configuration specifies an invalid Meshtastic connection type.
        """
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "invalid"},
        }
        mock_isfile.return_value = True

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

    @patch("sys.platform", "win32")
    def test_parse_arguments_windows_positional(self):
        """
        Test that on Windows, a positional argument is interpreted as the config file path.
        """
        with patch("sys.argv", ["mmrelay", "config.yaml"]):
            args = parse_arguments()
            self.assertEqual(args.config, "config.yaml")

    @patch("sys.platform", "win32")
    def test_parse_arguments_windows_both_args(self):
        """
        Test that on Windows, the --config option takes precedence over a positional config file argument when both are provided.
        """
        with patch(
            "sys.argv", ["mmrelay", "--config", "explicit.yaml", "positional.yaml"]
        ):
            args = parse_arguments()
            # --config should take precedence
            self.assertEqual(args.config, "explicit.yaml")

    @patch("builtins.print")
    def test_parse_arguments_unknown_args_warning(self, mock_print):
        """
        Test that a warning is printed when unknown CLI arguments are provided outside a test environment.

        Verifies that `parse_arguments()` triggers a warning message containing the unknown argument name when an unrecognized CLI argument is passed and the environment is not a test context.
        """
        with patch("sys.argv", ["mmrelay", "--unknown-arg", "value"]):
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
        with patch("sys.argv", ["pytest", "mmrelay", "--unknown-arg"]), patch("builtins.print") as mock_print:
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

        with patch("builtins.open", mock_open()) as mock_file, patch("builtins.print"):
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
        args.check_config = False
        args.install_service = False
        args.generate_config = False
        args.version = False
        mock_parse.return_value = args
        mock_run_main.return_value = 0

        result = main()

        self.assertEqual(result, 0)
        mock_run_main.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()

# --------------------------------------------------------------------------------------
# Additional tests appended by CodeRabbit Inc to increase coverage of mmrelay.cli
# Test framework: Python unittest (standard library)
# --------------------------------------------------------------------------------------


class TestCheckConfigAdditional(unittest.TestCase):
    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.get_config_paths")
    def test_check_config_no_config_found(self, mock_get_paths, mock_isfile):
        """
        When no explicit --config is provided and none of the default config paths exist,
        check_config() should return False and print a helpful error.
        """
        mock_get_paths.return_value = ["/a/b/c.yaml", "/x/y/z.yaml"]
        mock_isfile.return_value = False
        with patch("builtins.print") as mock_print, patch("sys.argv", ["mmrelay"]):
            ok = check_config()
        self.assertFalse(ok)
        mock_print.assert_called()
        printed = " ".join(call[0][0] for call in mock_print.call_args_list)

        self.assertIn("No valid config file found", printed)

    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_yaml_parse_error(self, mock_yaml_load, mock_isfile):
        """
        If YAML parsing raises an exception, check_config() should return False and warn.
        """
        mock_isfile.return_value = True
        mock_yaml_load.side_effect = Exception("YAML error")
        with patch("builtins.print") as mock_print, patch("sys.argv", ["mmrelay", "--config", "bad.yaml"]):
            ok = check_config()
        self.assertFalse(ok)
        printed = " ".join(call[0][0] for call in mock_print.call_args_list)
        self.assertIn("Failed to load config", printed)

    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_missing_matrix_keys(self, mock_yaml_load, mock_isfile):
        """
        Missing required keys in matrix section should invalidate config.
        """
        mock_isfile.return_value = True
        # Missing access_token and bot_user_id
        mock_yaml_load.return_value = {
            "matrix": {"homeserver": "https://example.org"},
            "matrix_rooms": [{"id": "!room:server", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }
        with patch("builtins.print"), patch("sys.argv", ["mmrelay", "--config", "conf.yaml"]):
            ok = check_config()
        self.assertFalse(ok)

    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_matrix_rooms_invalid_entries(self, mock_yaml_load, mock_isfile):
        """
        Invalid matrix_rooms entries (missing id, invalid channel type) should return False.
        """
        mock_isfile.return_value = True
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "abc",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [
                {"meshtastic_channel": 0},  # missing id
                {"id": "!good:server", "meshtastic_channel": "not-an-int"},  # wrong type
            ],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }
        with patch("builtins.print"), patch("sys.argv", ["mmrelay", "--config", "conf.yaml"]):
            ok = check_config()
        self.assertFalse(ok)

    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_serial_missing_port(self, mock_yaml_load, mock_isfile):
        """
        For connection_type = 'serial', missing serial_port should be invalid.
        """
        mock_isfile.return_value = True
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial"},
        }
        with patch("builtins.print"), patch("sys.argv", ["mmrelay", "--config", "conf.yaml"]):
            ok = check_config()
        self.assertFalse(ok)

    @patch("mmrelay.cli.os.path.isfile")
    @patch("mmrelay.config.yaml.load")
    def test_check_config_mqtt_missing_required_fields(self, mock_yaml_load, mock_isfile):
        """
        For connection_type = 'mqtt', missing broker/username/password/topic should be invalid.
        """
        mock_isfile.return_value = True
        mock_yaml_load.return_value = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "token",
                "bot_user_id": "@bot:matrix.org",
            },
            "matrix_rooms": [{"id": "!room:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "mqtt"}  # missing mqtt config keys
        }
        with patch("builtins.print"), patch("sys.argv", ["mmrelay", "--config", "conf.yaml"]):
            ok = check_config()
        self.assertFalse(ok)


class TestGenerateSampleConfigAdditional(unittest.TestCase):
    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    @patch("os.makedirs")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    @patch("shutil.copy2")
    def test_generate_sample_config_copy_failure(
        self,
        mock_copy,
        mock_exists,
        mock_get_sample,
        mock_makedirs,
        mock_isfile,
        mock_get_paths,
    ):
        """
        If the sample config exists but copying fails, function should return False and print error.
        """
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_isfile.return_value = False
        mock_get_sample.return_value = "/path/to/sample_config.yaml"
        mock_exists.return_value = True
        mock_copy.side_effect = Exception("copy failure")

        with patch("builtins.print") as mock_print:
            ok = generate_sample_config()
        self.assertFalse(ok)
        printed = " ".join(call[0][0] for call in mock_print.call_args_list)
        self.assertIn("Failed to generate sample config", printed)

    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    @patch("os.makedirs")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    @patch("importlib.resources.files")
    def test_generate_sample_config_importlib_write_failure(
        self,
        mock_files,
        mock_exists,
        mock_get_sample,
        mock_makedirs,
        mock_isfile,
        mock_get_paths,
    ):
        """
        If importlib.resources fallback is used but writing the file fails, return False and print error.
        """
        mock_get_paths.return_value = ["/home/user/.mmrelay/config.yaml"]
        mock_isfile.return_value = False
        mock_get_sample.return_value = "/nonexistent/path"
        mock_exists.return_value = False

        mock_resource = MagicMock()
        mock_resource.read_text.return_value = "sample config content"
        # joinpath -> resource
        files_obj = MagicMock()
        files_obj.joinpath.return_value = mock_resource
        mock_files.return_value = files_obj

        with patch("builtins.open", mock_open()) as mopen, patch("builtins.print") as mock_print:
            mopen.side_effect = Exception("write error")
            ok = generate_sample_config()
        self.assertFalse(ok)
        printed = " ".join(call[0][0] for call in mock_print.call_args_list)
        self.assertIn("Failed to generate sample config", printed)

    @patch("mmrelay.config.get_config_paths")
    @patch("os.path.isfile")
    @patch("os.makedirs")
    @patch("mmrelay.tools.get_sample_config_path")
    @patch("os.path.exists")
    def test_generate_sample_config_creates_parent_dir_once(
        self, mock_exists, mock_get_sample, mock_makedirs, mock_isfile, mock_get_paths
    ):
        """
        Ensure the parent directory is created (exist_ok semantics via try/except) when no file exists.
        """
        target_path = "/home/user/.mmrelay/config.yaml"
        mock_get_paths.return_value = [target_path]
        mock_isfile.return_value = False
        mock_get_sample.return_value = "/path/to/sample_config.yaml"
        mock_exists.return_value = True
        with patch("shutil.copy2") as mock_copy, patch("builtins.print"):
            ok = generate_sample_config()
        self.assertTrue(ok)
        mock_makedirs.assert_called_once()
        mock_copy.assert_called_once()


class TestHandleCLICommandsAdditional(unittest.TestCase):
    @patch("mmrelay.cli.generate_sample_config")
    @patch("mmrelay.setup_utils.install_service")
    @patch("sys.exit")
    def test_multiple_flags_priority(self, mock_exit, mock_install, mock_gen):
        """
        If multiple flags are set, ensure behavior is deterministic.
        Prefer version > install_service > generate_config > check_config per CLI typical behavior,
        or the ordering implemented in handle_cli_commands. This test asserts only the first action triggers.
        """
        args = MagicMock()
        args.version = False
        args.install_service = True
        args.generate_config = True
        args.check_config = True
        mock_install.return_value = True
        mock_gen.return_value = True

        # According to handle_cli_commands existing tests, install_service should trigger before others.
        handle_cli_commands(args)
        mock_install.assert_called_once()
        mock_exit.assert_called_once_with(0)
        mock_gen.assert_not_called()  # ensure later flags didn't run after exit

    @patch("mmrelay.cli.print_version")
    def test_handle_version_returns_true(self, mock_print_version):
        """
        Ensure True is returned (not exiting) when only version flag is set.
        """
        args = MagicMock(version=True, install_service=False, generate_config=False, check_config=False)
        res = handle_cli_commands(args)
        self.assertTrue(res)
        mock_print_version.assert_called_once()


class TestMainAdditional(unittest.TestCase):
    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.main.run_main")
    def test_main_run_main_nonzero_propagates(self, mock_run_main, mock_parse):
        """
        main() should return whatever run_main returns when no special flags are set.
        """
        args = MagicMock()
        args.check_config = False
        args.install_service = False
        args.generate_config = False
        args.version = False
        mock_parse.return_value = args
        mock_run_main.return_value = 7

        rc = main()
        self.assertEqual(rc, 7)
        mock_run_main.assert_called_once_with(args)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.cli.generate_sample_config")
    def test_main_generate_config_failure_returns_1(self, mock_generate, mock_parse):
        """
        If generate_sample_config fails, main() should return 1.
        """
        args = MagicMock(check_config=False, install_service=False, generate_config=True, version=False)
        mock_parse.return_value = args
        mock_generate.return_value = False
        rc = main()
        self.assertEqual(rc, 1)

    @patch("mmrelay.cli.parse_arguments")
    @patch("mmrelay.setup_utils.install_service")
    def test_main_install_service_failure_returns_1(self, mock_install, mock_parse):
        """
        If install_service fails, main() should return 1.
        """
        args = MagicMock(check_config=False, install_service=True, generate_config=False, version=False)
        mock_parse.return_value = args
        mock_install.return_value = False
        rc = main()
        self.assertEqual(rc, 1)


class TestParseArgumentsAdditional(unittest.TestCase):
    def test_parse_arguments_positional_non_windows(self):
        """
        On non-Windows platforms, a single positional argument should also be treated as config path.
        """
        with patch("sys.platform", "linux"), patch("sys.argv", ["mmrelay", "config.yaml"]):
            args = parse_arguments()
            self.assertEqual(args.config, "config.yaml")

    def test_parse_arguments_ignores_unknown_in_test_env(self):
        """
        When 'pytest' is included in argv anywhere (simulating test runner), unknown args shouldn't warn.
        """
        with patch("sys.argv", ["python", "-m", "pytest", "mmrelay", "--some-unknown"]), patch("builtins.print") as mock_print:
            parse_arguments()
            mock_print.assert_not_called()