import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mmrelay.config
from mmrelay.config import (
    _convert_env_bool,
    _convert_env_float,
    _convert_env_int,
    apply_env_config_overrides,
    get_base_dir,
    get_config_paths,
    get_data_dir,
    get_log_dir,
    get_plugin_data_dir,
    load_config,
    load_database_config_from_env,
    load_logging_config_from_env,
    load_meshtastic_config_from_env,
)


class TestConfig(unittest.TestCase):
    def setUp(self):
        # Reset the global config before each test
        """
        Reset the global configuration state before each test to ensure test isolation.
        """
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def test_get_base_dir_linux(self):
        # Test default base dir on Linux
        """
        Test that get_base_dir() returns the default base directory on Linux systems.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            base_dir = get_base_dir()
            self.assertEqual(base_dir, os.path.expanduser("~/.mmrelay"))

    @patch("mmrelay.config.platformdirs.user_data_dir")
    def test_get_base_dir_windows(self, mock_user_data_dir):
        # Test default base dir on Windows
        """
        Test that get_base_dir returns the correct default base directory on Windows when platform detection and user data directory are mocked.
        """
        with patch("mmrelay.config.sys.platform", "win32"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            mock_user_data_dir.return_value = "C:\\Users\\test\\AppData\\Local\\mmrelay"
            base_dir = get_base_dir()
            self.assertEqual(base_dir, "C:\\Users\\test\\AppData\\Local\\mmrelay")

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_load_config_from_file(self, mock_yaml_load, mock_open, mock_isfile):
        # Mock a config file
        """
        Test that `load_config` loads and returns configuration data from a specified YAML file when the file exists.
        """
        mock_yaml_load.return_value = {"key": "value"}
        mock_isfile.return_value = True

        # Test loading from a specific path
        config = load_config(config_file="myconfig.yaml")
        self.assertEqual(config, {"key": "value"})

    @patch("mmrelay.config.os.path.isfile")
    def test_load_config_not_found(self, mock_isfile):
        # Mock no config file found
        """
        Test that `load_config` returns an empty dictionary when no configuration file is found.
        """
        mock_isfile.return_value = False

        # Test that it returns an empty dict
        with patch("sys.argv", ["mmrelay"]):
            config = load_config()
            self.assertEqual(config, {})

    def test_get_config_paths_linux(self):
        # Test with no args on Linux
        """
        Test that `get_config_paths` returns the default Linux configuration file path when no command-line arguments are provided.
        """
        with patch("sys.platform", "linux"), patch("sys.argv", ["mmrelay"]), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            paths = get_config_paths()
            self.assertIn(os.path.expanduser("~/.mmrelay/config.yaml"), paths)

    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.config.platformdirs.user_config_dir")
    def test_get_config_paths_windows(self, mock_user_config_dir, mock_makedirs):
        # Test with no args on Windows
        """
        Test that `get_config_paths` returns the correct configuration file path on Windows.

        Simulates a Windows environment and verifies that the returned config paths include the expected Windows-specific config file location.
        """
        with patch("mmrelay.config.sys.platform", "win32"), patch(
            "sys.argv", ["mmrelay"]
        ):
            mock_user_config_dir.return_value = (
                "C:\\Users\\test\\AppData\\Local\\mmrelay\\config"
            )
            paths = get_config_paths()
            expected_path = os.path.join(
                "C:\\Users\\test\\AppData\\Local\\mmrelay\\config", "config.yaml"
            )
            self.assertIn(expected_path, paths)
            # Verify makedirs was called but don't actually create directories
            mock_makedirs.assert_called_once()

    @patch("mmrelay.config.os.makedirs")
    def test_get_data_dir_linux(self, mock_makedirs):
        """
        Test that get_data_dir returns the default data directory path on Linux platforms.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            data_dir = get_data_dir()
            self.assertEqual(data_dir, os.path.expanduser("~/.mmrelay/data"))

    @patch("mmrelay.config.os.makedirs")
    def test_get_log_dir_linux(self, mock_makedirs):
        """
        Test that get_log_dir() returns the default logs directory on Linux platforms.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            log_dir = get_log_dir()
            self.assertEqual(log_dir, os.path.expanduser("~/.mmrelay/logs"))

    @patch("mmrelay.config.os.makedirs")
    def test_get_plugin_data_dir_linux(self, mock_makedirs):
        """
        Test that get_plugin_data_dir returns correct plugin data directory paths on Linux.

        Ensures the function resolves both the default plugins data directory and a plugin-specific directory for the Linux platform.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            plugin_data_dir = get_plugin_data_dir()
            self.assertEqual(
                plugin_data_dir, os.path.expanduser("~/.mmrelay/data/plugins")
            )
            plugin_specific_dir = get_plugin_data_dir("my_plugin")
            self.assertEqual(
                plugin_specific_dir,
                os.path.expanduser("~/.mmrelay/data/plugins/my_plugin"),
            )


class TestConfigEdgeCases(unittest.TestCase):
    """Test configuration edge cases and error handling."""

    def setUp(self):
        """
        Resets the global configuration state to ensure test isolation before each test.
        """
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_config_migration_scenarios(self, mock_yaml_load, mock_open, mock_isfile):
        """
        Test migration of configuration files from an old format to a new format.

        Simulates loading a legacy configuration file missing newer fields and verifies that loading proceeds without errors, preserving original data and handling missing fields gracefully.
        """
        # Simulate old config format (missing new fields)
        old_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "username": "@bot:matrix.org",
                "password": "secret",
            },
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }

        mock_yaml_load.return_value = old_config
        mock_isfile.return_value = True

        # Load config and verify migration
        config = load_config(config_file="old_config.yaml")

        # Should contain original data
        self.assertEqual(config["matrix"]["homeserver"], "https://matrix.org")
        self.assertEqual(config["meshtastic"]["connection_type"], "serial")

        # Should handle missing fields gracefully
        self.assertIsInstance(config, dict)

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_partial_config_handling(self, mock_yaml_load, mock_open, mock_isfile):
        """
        Test that loading a partial or incomplete configuration file does not cause errors.

        Ensures that configuration files missing sections or fields are loaded without exceptions, and missing keys are handled gracefully.
        """
        # Test with minimal config
        minimal_config = {
            "matrix": {
                "homeserver": "https://matrix.org"
                # Missing username, password, etc.
            }
            # Missing meshtastic section entirely
        }

        mock_yaml_load.return_value = minimal_config
        mock_isfile.return_value = True

        # Should load without error
        config = load_config(config_file="minimal_config.yaml")

        # Should contain what was provided
        self.assertEqual(config["matrix"]["homeserver"], "https://matrix.org")

        # Should handle missing sections gracefully
        self.assertNotIn("username", config.get("matrix", {}))

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_config_validation_error_messages(
        self, mock_yaml_load, mock_open, mock_isfile
    ):
        """
        Test loading of invalid configuration structures and ensure they are returned as dictionaries.

        This test verifies that when a configuration file contains invalid types or values, the `load_config` function still loads and returns the raw configuration dictionary. Validation and error messaging are expected to occur outside of this function.
        """
        # Test with invalid YAML structure
        invalid_config = {
            "matrix": "not_a_dict",  # Should be a dictionary
            "meshtastic": {
                "connection_type": "invalid_type"  # Invalid connection type
            },
        }

        mock_yaml_load.return_value = invalid_config
        mock_isfile.return_value = True

        # Should load but config validation elsewhere should catch issues
        config = load_config(config_file="invalid_config.yaml")

        # Config should load (validation happens elsewhere)
        self.assertIsInstance(config, dict)
        self.assertEqual(config["matrix"], "not_a_dict")

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    def test_corrupted_config_file_handling(self, mock_open, mock_isfile):
        """
        Test that loading a corrupted YAML configuration file is handled gracefully.

        Simulates a YAML parsing error and verifies that `load_config` does not raise uncaught exceptions and returns a dictionary as fallback.
        """
        import yaml

        mock_isfile.return_value = True

        # Simulate YAML parsing error
        mock_open.return_value.__enter__.return_value.read.return_value = (
            "invalid: yaml: content: ["
        )

        with patch(
            "mmrelay.config.yaml.load", side_effect=yaml.YAMLError("Invalid YAML")
        ):
            # Should handle YAML errors gracefully
            try:
                config = load_config(config_file="corrupted.yaml")
                # If no exception, should return empty dict or handle gracefully
                self.assertIsInstance(config, dict)
            except yaml.YAMLError:
                # If exception is raised, it should be a YAML error
                pass

    @patch("mmrelay.config.os.path.isfile")
    def test_missing_config_file_fallback(self, mock_isfile):
        """
        Test that loading configuration with a missing file returns an empty dictionary without raising exceptions.
        """
        mock_isfile.return_value = False

        with patch("sys.argv", ["mmrelay"]):
            config = load_config()

            # Should return empty dict when no config found
            self.assertEqual(config, {})

            # Should not crash or raise exceptions
            self.assertIsInstance(config, dict)

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_config_with_environment_variables(
        self, mock_yaml_load, mock_open, mock_isfile
    ):
        """
        Test loading a configuration file containing environment variable references.

        Ensures that configuration values with environment variable placeholders are loaded as raw strings, without expansion, as expected at this stage.
        """
        # Config with environment variable references
        env_config = {
            "matrix": {
                "homeserver": "${MATRIX_HOMESERVER}",
                "access_token": "${MATRIX_TOKEN}",
            },
            "meshtastic": {"serial_port": "${MESHTASTIC_PORT}"},
        }

        mock_yaml_load.return_value = env_config
        mock_isfile.return_value = True

        # Set environment variables
        with patch.dict(
            os.environ,
            {
                "MATRIX_HOMESERVER": "https://test.matrix.org",
                "MATRIX_TOKEN": "test_token_123",
                "MESHTASTIC_PORT": "/dev/ttyUSB1",
            },
        ):
            config = load_config(config_file="env_config.yaml")

            # Should load the raw config (environment variable expansion happens elsewhere)
            self.assertEqual(config["matrix"]["homeserver"], "${MATRIX_HOMESERVER}")
            self.assertEqual(config["matrix"]["access_token"], "${MATRIX_TOKEN}")

    def test_config_path_resolution_edge_cases(self):
        """
        Test that configuration path resolution correctly handles relative and absolute paths.

        Ensures that get_config_paths returns absolute paths for both relative and absolute config file arguments, covering edge cases in path normalization.
        """
        # Mock argparse Namespace object for relative path
        mock_args = MagicMock()
        mock_args.config = "../config/test.yaml"

        paths = get_config_paths(args=mock_args)

        # Should include the absolute version of the relative path
        expected_path = os.path.abspath("../config/test.yaml")
        self.assertIn(expected_path, paths)

        # Mock argparse Namespace object for absolute path
        mock_args.config = "/absolute/path/config.yaml"

        paths = get_config_paths(args=mock_args)

        # Should include the absolute path
        self.assertIn("/absolute/path/config.yaml", paths)

    @patch("mmrelay.config.platformdirs.user_data_dir")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.config.sys.platform", "win32")
    def test_get_data_dir_windows(self, mock_makedirs, mock_user_data_dir):
        """Test get_data_dir on Windows platform."""
        mock_user_data_dir.return_value = "C:\\Users\\test\\AppData\\Local\\mmrelay"

        result = get_data_dir()

        self.assertEqual(result, "C:\\Users\\test\\AppData\\Local\\mmrelay")
        mock_user_data_dir.assert_called_once_with("mmrelay", None)
        mock_makedirs.assert_called_once_with(
            "C:\\Users\\test\\AppData\\Local\\mmrelay", exist_ok=True
        )

    @patch("mmrelay.config.platformdirs.user_log_dir")
    @patch("mmrelay.config.os.makedirs")
    @patch("mmrelay.config.sys.platform", "win32")
    def test_get_log_dir_windows(self, mock_makedirs, mock_user_log_dir):
        """Test get_log_dir on Windows platform."""
        mock_user_log_dir.return_value = (
            "C:\\Users\\test\\AppData\\Local\\mmrelay\\Logs"
        )

        result = get_log_dir()

        self.assertEqual(result, "C:\\Users\\test\\AppData\\Local\\mmrelay\\Logs")
        mock_user_log_dir.assert_called_once_with("mmrelay", None)
        mock_makedirs.assert_called_once_with(
            "C:\\Users\\test\\AppData\\Local\\mmrelay\\Logs", exist_ok=True
        )

    @patch("mmrelay.config.os.makedirs")
    def test_get_config_paths_permission_error(self, mock_makedirs):
        """Test get_config_paths when directory creation fails."""
        # Mock OSError when creating user config directory
        mock_makedirs.side_effect = [OSError("Permission denied"), None, None]

        paths = get_config_paths()

        # Should still return paths even if user config dir creation fails
        self.assertIsInstance(paths, list)
        self.assertGreater(len(paths), 0)


class TestEnvironmentVariableHelpers(unittest.TestCase):
    """Test environment variable conversion helper functions."""

    def test_convert_env_bool_valid_true(self):
        """Test conversion of valid true boolean values."""
        true_values = ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"]
        for value in true_values:
            with self.subTest(value=value):
                self.assertTrue(_convert_env_bool(value, "TEST_VAR"))

    def test_convert_env_bool_valid_false(self):
        """Test conversion of valid false boolean values."""
        false_values = ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF"]
        for value in false_values:
            with self.subTest(value=value):
                self.assertFalse(_convert_env_bool(value, "TEST_VAR"))

    def test_convert_env_bool_invalid(self):
        """Test conversion of invalid boolean values."""
        invalid_values = ["maybe", "invalid", "2", "truee", "falsee"]
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as cm:
                    _convert_env_bool(value, "TEST_VAR")
                self.assertIn("Invalid boolean value for TEST_VAR", str(cm.exception))

    def test_convert_env_int_valid(self):
        """Test conversion of valid integer values."""
        self.assertEqual(_convert_env_int("42", "TEST_VAR"), 42)
        self.assertEqual(_convert_env_int("-10", "TEST_VAR"), -10)
        self.assertEqual(_convert_env_int("0", "TEST_VAR"), 0)

    def test_convert_env_int_with_range(self):
        """Test integer conversion with range validation."""
        self.assertEqual(
            _convert_env_int("50", "TEST_VAR", min_value=1, max_value=100), 50
        )

        with self.assertRaises(ValueError) as cm:
            _convert_env_int("0", "TEST_VAR", min_value=1)
        self.assertIn("must be >= 1", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            _convert_env_int("101", "TEST_VAR", max_value=100)
        self.assertIn("must be <= 100", str(cm.exception))

    def test_convert_env_int_invalid(self):
        """Test conversion of invalid integer values."""
        with self.assertRaises(ValueError) as cm:
            _convert_env_int("not_a_number", "TEST_VAR")
        self.assertIn("Invalid integer value for TEST_VAR", str(cm.exception))

    def test_convert_env_float_valid(self):
        """Test conversion of valid float values."""
        self.assertEqual(_convert_env_float("3.14", "TEST_VAR"), 3.14)
        self.assertEqual(_convert_env_float("-2.5", "TEST_VAR"), -2.5)
        self.assertEqual(_convert_env_float("42", "TEST_VAR"), 42.0)

    def test_convert_env_float_with_range(self):
        """Test float conversion with range validation."""
        self.assertEqual(
            _convert_env_float("2.5", "TEST_VAR", min_value=2.0, max_value=3.0), 2.5
        )

        with self.assertRaises(ValueError) as cm:
            _convert_env_float("1.5", "TEST_VAR", min_value=2.0)
        self.assertIn("must be >= 2.0", str(cm.exception))

    def test_convert_env_float_invalid(self):
        """Test conversion of invalid float values."""
        with self.assertRaises(ValueError) as cm:
            _convert_env_float("not_a_float", "TEST_VAR")
        self.assertIn("Invalid float value for TEST_VAR", str(cm.exception))


class TestMeshtasticEnvironmentVariables(unittest.TestCase):
    """Test Meshtastic configuration loading from environment variables."""

    def setUp(self):
        """Clear environment variables before each test."""
        self.env_vars = [
            "MMRELAY_MESHTASTIC_CONNECTION_TYPE",
            "MMRELAY_MESHTASTIC_HOST",
            "MMRELAY_MESHTASTIC_PORT",
            "MMRELAY_MESHTASTIC_SERIAL_PORT",
            "MMRELAY_MESHTASTIC_BLE_ADDRESS",
            "MMRELAY_MESHTASTIC_BROADCAST_ENABLED",
            "MMRELAY_MESHTASTIC_MESHNET_NAME",
            "MMRELAY_MESHTASTIC_MESSAGE_DELAY",
        ]
        for var in self.env_vars:
            if var in os.environ:
                del os.environ[var]

    def tearDown(self):
        """
        Clear environment variables named in self.env_vars from the process environment.
        
        This teardown helper removes each variable listed in self.env_vars from os.environ if present, ensuring test isolation by reverting any environment changes made during a test. It mutates the process environment and returns None.
        """
        for var in self.env_vars:
            if var in os.environ:
                del os.environ[var]

    def test_load_meshtastic_tcp_config(self):
        """Test loading TCP Meshtastic configuration."""
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "tcp"
        os.environ["MMRELAY_MESHTASTIC_HOST"] = "192.168.1.100"
        os.environ["MMRELAY_MESHTASTIC_PORT"] = "4403"

        config = load_meshtastic_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["connection_type"], "tcp")
        self.assertEqual(config["host"], "192.168.1.100")
        self.assertEqual(config["port"], 4403)

    def test_load_meshtastic_serial_config(self):
        """Test loading serial Meshtastic configuration."""
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "serial"
        os.environ["MMRELAY_MESHTASTIC_SERIAL_PORT"] = "/dev/ttyUSB0"

        config = load_meshtastic_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["connection_type"], "serial")
        self.assertEqual(config["serial_port"], "/dev/ttyUSB0")

    def test_load_meshtastic_ble_config(self):
        """Test loading BLE Meshtastic configuration."""
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "ble"
        os.environ["MMRELAY_MESHTASTIC_BLE_ADDRESS"] = "AA:BB:CC:DD:EE:FF"

        config = load_meshtastic_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["connection_type"], "ble")
        self.assertEqual(config["ble_address"], "AA:BB:CC:DD:EE:FF")

    def test_load_meshtastic_operational_settings(self):
        """Test loading operational Meshtastic settings."""
        os.environ["MMRELAY_MESHTASTIC_BROADCAST_ENABLED"] = "true"
        os.environ["MMRELAY_MESHTASTIC_MESHNET_NAME"] = "Test Mesh"
        os.environ["MMRELAY_MESHTASTIC_MESSAGE_DELAY"] = "2.5"

        config = load_meshtastic_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["broadcast_enabled"], True)
        self.assertEqual(config["meshnet_name"], "Test Mesh")
        self.assertEqual(config["message_delay"], 2.5)

    def test_invalid_connection_type(self):
        """Test invalid connection type handling."""
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "invalid"

        config = load_meshtastic_config_from_env()
        self.assertIsNone(config)

    def test_invalid_port(self):
        """Test invalid port handling."""
        os.environ["MMRELAY_MESHTASTIC_PORT"] = "invalid_port"

        config = load_meshtastic_config_from_env()
        self.assertIsNone(config)

    def test_port_out_of_range(self):
        """Test port out of range handling."""
        os.environ["MMRELAY_MESHTASTIC_PORT"] = "70000"

        config = load_meshtastic_config_from_env()
        self.assertIsNone(config)

    def test_invalid_message_delay(self):
        """Test invalid message delay handling."""
        os.environ["MMRELAY_MESHTASTIC_MESSAGE_DELAY"] = "1.0"  # Below minimum of 2.0

        config = load_meshtastic_config_from_env()
        self.assertIsNone(config)

    def test_no_env_vars_returns_none(self):
        """Test that no environment variables returns None."""
        config = load_meshtastic_config_from_env()
        self.assertIsNone(config)


class TestLoggingEnvironmentVariables(unittest.TestCase):
    """Test logging configuration loading from environment variables."""

    def setUp(self):
        """
        Clear logging-related environment variables before each test.
        
        Executed before each test case; removes MMRELAY_LOGGING_LEVEL and MMRELAY_LOG_FILE from os.environ to ensure tests run without influence from external logging configuration.
        """
        self.env_vars = ["MMRELAY_LOGGING_LEVEL", "MMRELAY_LOG_FILE"]
        for var in self.env_vars:
            if var in os.environ:
                del os.environ[var]

    def tearDown(self):
        """
        Clear environment variables named in self.env_vars from the process environment.
        
        This teardown helper removes each variable listed in self.env_vars from os.environ if present, ensuring test isolation by reverting any environment changes made during a test. It mutates the process environment and returns None.
        """
        for var in self.env_vars:
            if var in os.environ:
                del os.environ[var]

    def test_load_logging_level(self):
        """Test loading logging level."""
        os.environ["MMRELAY_LOGGING_LEVEL"] = "DEBUG"

        config = load_logging_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["level"], "debug")

    def test_load_log_file(self):
        """Test loading log file path."""
        os.environ["MMRELAY_LOG_FILE"] = "/app/logs/mmrelay.log"

        config = load_logging_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["filename"], "/app/logs/mmrelay.log")
        self.assertTrue(config["log_to_file"])

    def test_invalid_logging_level(self):
        """Test invalid logging level handling."""
        os.environ["MMRELAY_LOGGING_LEVEL"] = "INVALID"

        config = load_logging_config_from_env()
        self.assertIsNone(config)

    def test_no_env_vars_returns_none(self):
        """Test that no environment variables returns None."""
        config = load_logging_config_from_env()
        self.assertIsNone(config)


class TestDatabaseEnvironmentVariables(unittest.TestCase):
    """Test database configuration loading from environment variables."""

    def setUp(self):
        """
        Ensure the MMRELAY_DATABASE_PATH environment variable is removed before each test to avoid cross-test contamination.
        """
        if "MMRELAY_DATABASE_PATH" in os.environ:
            del os.environ["MMRELAY_DATABASE_PATH"]

    def tearDown(self):
        """Clear environment variables after each test."""
        if "MMRELAY_DATABASE_PATH" in os.environ:
            del os.environ["MMRELAY_DATABASE_PATH"]

    def test_load_database_path(self):
        """Test loading database path."""
        os.environ["MMRELAY_DATABASE_PATH"] = "/app/data/custom.sqlite"

        config = load_database_config_from_env()

        self.assertIsNotNone(config)
        self.assertEqual(config["path"], "/app/data/custom.sqlite")

    def test_no_env_vars_returns_none(self):
        """Test that no environment variables returns None."""
        config = load_database_config_from_env()
        self.assertIsNone(config)


class TestEnvironmentVariableIntegration(unittest.TestCase):
    """Test integration of environment variables with configuration loading."""

    def setUp(self):
        """Clear environment variables before each test."""
        self.all_env_vars = [
            "MMRELAY_MESHTASTIC_CONNECTION_TYPE",
            "MMRELAY_MESHTASTIC_HOST",
            "MMRELAY_MESHTASTIC_PORT",
            "MMRELAY_LOGGING_LEVEL",
            "MMRELAY_DATABASE_PATH",
        ]
        for var in self.all_env_vars:
            if var in os.environ:
                del os.environ[var]

    def tearDown(self):
        """
        Remove any environment variables listed in self.all_env_vars.

        Iterates over self.all_env_vars and deletes each key from os.environ if present.
        Used in test teardown to ensure environment state is cleared between tests.
        """
        for var in self.all_env_vars:
            if var in os.environ:
                del os.environ[var]

    def test_apply_env_config_overrides_empty_config(self):
        """Test applying environment variable overrides to empty configuration."""
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "tcp"
        os.environ["MMRELAY_MESHTASTIC_HOST"] = "192.168.1.100"
        os.environ["MMRELAY_LOGGING_LEVEL"] = "INFO"
        os.environ["MMRELAY_DATABASE_PATH"] = "/app/data/test.sqlite"

        config = apply_env_config_overrides({})

        self.assertIn("meshtastic", config)
        self.assertEqual(config["meshtastic"]["connection_type"], "tcp")
        self.assertEqual(config["meshtastic"]["host"], "192.168.1.100")

        self.assertIn("logging", config)
        self.assertEqual(config["logging"]["level"], "info")

        self.assertIn("database", config)
        self.assertEqual(config["database"]["path"], "/app/data/test.sqlite")

    def test_apply_env_config_overrides_existing_config(self):
        """Test applying environment variable overrides to existing configuration."""
        base_config = {
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "meshnet_name": "Original Name",
            },
            "logging": {"level": "warning"},
        }

        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "tcp"
        os.environ["MMRELAY_MESHTASTIC_HOST"] = "192.168.1.100"
        os.environ["MMRELAY_LOGGING_LEVEL"] = "DEBUG"

        config = apply_env_config_overrides(base_config)

        # Environment variables should override existing values
        self.assertEqual(config["meshtastic"]["connection_type"], "tcp")
        self.assertEqual(config["meshtastic"]["host"], "192.168.1.100")
        # Existing values not overridden should remain
        self.assertEqual(config["meshtastic"]["serial_port"], "/dev/ttyUSB0")
        self.assertEqual(config["meshtastic"]["meshnet_name"], "Original Name")
        # Logging level should be overridden
        self.assertEqual(config["logging"]["level"], "debug")

    @patch("mmrelay.config.yaml.load")
    @patch("builtins.open")
    @patch("mmrelay.config.os.path.isfile")
    def test_load_config_with_env_overrides(
        self, mock_isfile, mock_open, mock_yaml_load
    ):
        """Test that load_config applies environment variable overrides."""
        # Mock file existence and YAML loading
        mock_isfile.return_value = True
        mock_yaml_load.return_value = {
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"}
        }

        # Set environment variables
        os.environ["MMRELAY_MESHTASTIC_CONNECTION_TYPE"] = "tcp"
        os.environ["MMRELAY_MESHTASTIC_HOST"] = "192.168.1.100"

        config = load_config("/fake/config.yaml")

        # Should have both file config and env var overrides
        self.assertEqual(config["meshtastic"]["connection_type"], "tcp")  # From env var
        self.assertEqual(config["meshtastic"]["host"], "192.168.1.100")  # From env var
        self.assertEqual(
            config["meshtastic"]["serial_port"], "/dev/ttyUSB0"
        )  # From file

    def test_no_env_vars_returns_empty_dict(self):
        """Test that no environment variables returns empty dict."""
        config = apply_env_config_overrides({})
        self.assertEqual(config, {})


if __name__ == "__main__":
    unittest.main()
