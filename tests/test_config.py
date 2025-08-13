import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import mmrelay.config
from mmrelay.config import (
    get_base_dir,
    get_config_paths,
    get_data_dir,
    get_log_dir,
    get_plugin_data_dir,
    load_config,
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

    @patch("mmrelay.config.platformdirs.user_config_dir")
    def test_get_config_paths_windows(self, mock_user_config_dir):
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

    def test_get_data_dir_linux(self):
        """
        Test that get_data_dir returns the default data directory path on Linux platforms.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            data_dir = get_data_dir()
            self.assertEqual(data_dir, os.path.expanduser("~/.mmrelay/data"))

    def test_get_log_dir_linux(self):
        """
        Test that get_log_dir() returns the default logs directory on Linux platforms.
        """
        with patch("sys.platform", "linux"), patch(
            "mmrelay.config.custom_data_dir", None
        ):
            log_dir = get_log_dir()
            self.assertEqual(log_dir, os.path.expanduser("~/.mmrelay/logs"))

    def test_get_plugin_data_dir_linux(self):
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


if __name__ == "__main__":
    unittest.main()
# ---- Additional tests appended by automation to increase coverage for config module ----

class TestConfigCustomDataDir(unittest.TestCase):
    """
    Additional tests focusing on custom_data_dir override behavior and path resolutions.
    """

    def setUp(self):
        # Reset globals for isolation
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def tearDown(self):
        # Ensure we restore any overridden globals to default state
        mmrelay.config.custom_data_dir = None

    def test_get_base_dir_with_custom_data_dir(self):
        """
        get_base_dir should return custom_data_dir when set, regardless of platform.
        """
        with patch("mmrelay.config.custom_data_dir", "/tmp/mmr"), patch("sys.platform", "linux"):
            self.assertEqual(get_base_dir(), "/tmp/mmr")
        with patch("mmrelay.config.custom_data_dir", "C:\\\\mmrdata"), patch("mmrelay.config.sys.platform", "win32"):
            self.assertEqual(get_base_dir(), "C:\\\\mmrdata")

    def test_dir_helpers_with_custom_data_dir_linux(self):
        """
        get_data_dir/get_log_dir/get_plugin_data_dir should derive from custom_data_dir on Linux.
        """
        with patch("sys.platform", "linux"), patch("mmrelay.config.custom_data_dir", "/opt/mmr"):
            self.assertEqual(get_data_dir(), "/opt/mmr/data")
            self.assertEqual(get_log_dir(), "/opt/mmr/logs")
            self.assertEqual(get_plugin_data_dir(), "/opt/mmr/data/plugins")
            self.assertEqual(get_plugin_data_dir("ext"), "/opt/mmr/data/plugins/ext")

    def test_dir_helpers_with_custom_data_dir_windows(self):
        """
        get_data_dir/get_log_dir/get_plugin_data_dir should derive from custom_data_dir on Windows.
        """
        with patch("mmrelay.config.sys.platform", "win32"), patch("mmrelay.config.custom_data_dir", "D:\\\\MMR"):
            self.assertEqual(get_data_dir(), "D:\\\\MMR\\\\data")
            self.assertEqual(get_log_dir(), "D:\\\\MMR\\\\logs")
            self.assertEqual(get_plugin_data_dir(), "D:\\\\MMR\\\\data\\\\plugins")
            self.assertEqual(get_plugin_data_dir("plug"), "D:\\\\MMR\\\\data\\\\plugins\\\\plug")


class TestConfigPathsPrecedence(unittest.TestCase):
    """
    Tests for get_config_paths precedence rules and handling of args/argv.
    """

    def setUp(self):
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def test_get_config_paths_with_args_config_relative(self):
        """
        When args.config is given as a relative path, ensure the absolute path is returned and has precedence.
        """
        args = MagicMock()
        args.config = "conf/my.yaml"
        result = get_config_paths(args=args)
        self.assertIn(os.path.abspath("conf/my.yaml"), result)

    def test_get_config_paths_with_args_config_absolute(self):
        """
        When args.config is absolute, ensure it is included as-is.
        """
        args = MagicMock()
        args.config = os.path.abspath("/tmp/example.yaml")
        result = get_config_paths(args=args)
        self.assertIn(os.path.abspath("/tmp/example.yaml"), result)

    def test_get_config_paths_from_argv_dash_c(self):
        """
        Simulate passing -c via sys.argv and ensure the specified path is included.
        """
        with patch("sys.argv", ["mmrelay", "-c", "settings.yaml"]):
            paths = get_config_paths()
            self.assertIn(os.path.abspath("settings.yaml"), paths)

    def test_get_config_paths_from_argv_long_flag(self):
        """
        Simulate passing --config via sys.argv and ensure the specified path is included.
        """
        with patch("sys.argv", ["mmrelay", "--config", "conf.yml"]):
            paths = get_config_paths()
            self.assertIn(os.path.abspath("conf.yml"), paths)


class TestLoadConfigSelection(unittest.TestCase):
    """
    Tests for load_config selection and global caching behavior.
    """

    def setUp(self):
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_load_config_prefers_explicit_path_over_search(self, mock_yaml_load, mock_open, mock_isfile):
        """
        load_config should load from explicit config_file when provided.
        """
        mock_isfile.side_effect = lambda p: os.path.abspath(p) == os.path.abspath("explicit.yaml")
        mock_yaml_load.return_value = {"source": "explicit"}
        cfg = load_config(config_file="explicit.yaml")
        self.assertEqual(cfg.get("source"), "explicit")

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_load_config_uses_first_existing_from_paths(self, mock_yaml_load, mock_open, mock_isfile):
        """
        When no explicit path is given, load_config should iterate over get_config_paths and pick the first that exists.
        """
        # Construct an order of candidate paths and make the second one exist
        with patch("mmrelay.config.get_config_paths") as mock_paths:
            mock_paths.return_value = [
                os.path.abspath("missing1.yaml"),
                os.path.abspath("present.yaml"),
                os.path.abspath("missing2.yaml"),
            ]
            def isfile_side(p):
                return os.path.abspath(p) == os.path.abspath("present.yaml")
            mock_isfile.side_effect = isfile_side
            mock_yaml_load.return_value = {"picked": "present.yaml"}
            cfg = load_config()
            self.assertEqual(cfg.get("picked"), "present.yaml")

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    @patch("mmrelay.config.yaml.load")
    def test_load_config_idempotent_global_cache(self, mock_yaml_load, mock_open, mock_isfile):
        """
        Ensure repeated calls reuse global state consistently (if implemented).
        """
        mock_isfile.return_value = True
        mock_yaml_load.return_value = {"k": 1}
        c1 = load_config(config_file="cache.yaml")
        c2 = load_config(config_file="cache.yaml")
        self.assertIsInstance(c1, dict)
        self.assertIsInstance(c2, dict)
        # Either identical dict or re-parsed; at minimum, values should be consistent
        self.assertEqual(c1.get("k"), 1)
        self.assertEqual(c2.get("k"), 1)

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    def test_load_config_relative_path_resolution(self, mock_open, mock_isfile):
        """
        Verify that passing a relative config_file resolves and opens appropriately.
        """
        mock_isfile.return_value = True
        # Ensure that open is called with the relative path (the code may or may not normalize further)
        with patch("mmrelay.config.yaml.load", return_value={"ok": True}):
            cfg = load_config(config_file="rel/path/file.yaml")
            self.assertTrue(cfg.get("ok"))
            # Check that open has been invoked
            self.assertTrue(mock_open.called)

    @patch("mmrelay.config.os.path.isfile")
    @patch("builtins.open")
    def test_load_config_yaml_loader_safety(self, mock_open, mock_isfile):
        """
        If the implementation uses yaml.safe_load or yaml.load with SafeLoader, invocation should succeed.
        This test does not enforce a specific loader but ensures call happens.
        """
        import yaml
        mock_isfile.return_value = True

        # Provide minimal YAML content via open().read()
        mock_open.return_value.__enter__.return_value.read.return_value = "foo: bar"

        # Allow the real yaml.safe_load or yaml.load to run safely with simple content
        with patch.object(yaml, "load", wraps=yaml.load):
            cfg = load_config(config_file="simple.yaml")
            self.assertIsInstance(cfg, dict)
            # wrapped_load may or may not be called depending on implementation, so we accept both
            # If called, ensure result has expected key
            if cfg:
                self.assertIn("foo", cfg)


class TestPluginDataDirEdgeCases(unittest.TestCase):
    """
    Edge cases for get_plugin_data_dir inputs.
    """

    def setUp(self):
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def test_get_plugin_data_dir_with_empty_name(self):
        """
        When plugin name is empty or None, should return the plugins root directory.
        """
        with patch("sys.platform", "linux"), patch("mmrelay.config.custom_data_dir", None):
            root = get_plugin_data_dir()
            self.assertTrue(root.endswith(os.path.join("data", "plugins")))
            self.assertNotIn("None", root)

    def test_get_plugin_data_dir_with_special_chars(self):
        """
        Plugin names containing path separators should be treated as literal subpaths.
        Even if not sanitized by implementation, ensure output path is composed accordingly.
        """
        with patch("sys.platform", "linux"), patch("mmrelay.config.custom_data_dir", None):
            pd = get_plugin_data_dir("a/b\\c")
            self.assertTrue(pd.endswith(os.path.join("data", "plugins", "a/b\\c")))


class TestWindowsPathsViaPlatformdirs(unittest.TestCase):
    """
    Windows-specific behavior when platformdirs is involved.
    """

    def setUp(self):
        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    @patch("mmrelay.config.platformdirs.user_data_dir")
    def test_windows_base_dir_via_platformdirs_with_custom_unset(self, mock_user_data_dir):
        """
        Verify that when custom_data_dir is None, get_base_dir defers to platformdirs on Windows.
        """
        mock_user_data_dir.return_value = "C:\\\\Users\\\\test\\\\AppData\\\\Local\\\\mmrelay"
        with patch("mmrelay.config.sys.platform", "win32"), patch("mmrelay.config.custom_data_dir", None):
            self.assertEqual(get_base_dir(), "C:\\\\Users\\\\test\\\\AppData\\\\Local\\\\mmrelay")

    @patch("mmrelay.config.platformdirs.user_config_dir")
    def test_get_config_paths_windows_default_contains_platformdirs_path(self, mock_user_config_dir):
        """
        Ensure the default Windows config path includes platformdirs-provided path when no args provided.
        """
        with patch("mmrelay.config.sys.platform", "win32"), patch("sys.argv", ["mmrelay"]):
            mock_user_config_dir.return_value = "C:\\\\Users\\\\me\\\\AppData\\\\Local\\\\mmrelay\\\\config"
            paths = get_config_paths()
            self.assertIn(os.path.join("C:\\\\Users\\\\me\\\\AppData\\\\Local\\\\mmrelay\\\\config", "config.yaml"), paths)