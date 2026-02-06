#!/usr/bin/env python3
"""
Test suite for Config module edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- YAML parsing errors
- File permission issues
- Invalid configuration structures
- Platform-specific path handling
- Module configuration setup edge cases
- Configuration file search priority
"""

import json
import ntpath
import os
import sys
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.config import (
    get_app_path,
    get_config_paths,
    get_credentials_search_paths,
    get_data_dir,
    get_e2ee_store_dir,
    get_explicit_credentials_path,
    get_log_dir,
    load_config,
    load_credentials,
    save_credentials,
    set_config,
)


class TestConfigEdgeCases(unittest.TestCase):
    """Test cases for Config module edge cases and error handling."""

    def setUp(self):
        """
        Resets global state variables in mmrelay.config before each test to ensure test isolation.
        """
        # Reset global state
        import mmrelay.config

        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def test_get_app_path_frozen_executable(self):
        """
        Test that get_app_path returns the executable's directory when running as a frozen binary.
        """
        with patch("sys.frozen", True, create=True):
            with patch("sys.executable", "/path/to/executable"):
                result = get_app_path()
                self.assertEqual(result, "/path/to")

    def test_get_app_path_normal_python(self):
        """
        Test that get_app_path returns the directory containing the config.py file when not running as a frozen executable.
        """
        with patch("sys.frozen", False, create=True):
            result = get_app_path()
            # Should return directory containing config.py
            self.assertTrue(result.endswith("mmrelay"))

    def test_get_config_paths_with_args(self):
        """
        Test that get_config_paths returns the specified config path when provided via command line arguments.
        """
        mock_args = MagicMock()
        mock_args.config = "/custom/path/config.yaml"

        paths = get_config_paths(mock_args)
        self.assertEqual(paths[0], "/custom/path/config.yaml")

    def test_get_config_paths_windows_platform(self):
        """
        Test that get_config_paths() returns Windows-style configuration paths when running on a Windows platform.

        Verifies that the returned paths include a directory under 'AppData', as expected for Windows environments.
        """
        with (
            patch("mmrelay.paths.sys.platform", "win32"),
            patch("mmrelay.paths.platformdirs.user_data_dir") as mock_user_data,
            patch("mmrelay.config.os.makedirs"),
        ):
            mock_user_data.return_value = "C:\\Users\\Test\\AppData\\Local\\mmrelay"
            paths = get_config_paths()
            # Check that a Windows-style path is in the list
            # We normalize because get_config_paths uses absolute path which might
            # prepend CWD on Linux if the mock path isn't recognized as absolute.
            # But "C:\" should be absolute enough.
            windows_path_found = any("AppData" in str(path) for path in paths)
            self.assertTrue(windows_path_found)

    def test_get_config_paths_darwin_platform(self):
        """
        Test that get_config_paths returns the correct configuration file path for macOS.

        Simulates a Darwin platform and a custom base directory to ensure get_config_paths includes the expected config.yaml path in its results.
        """
        with (
            patch("mmrelay.paths.sys.platform", "darwin"),
            patch("mmrelay.paths.Path.home", return_value=Path("/home/test")),
            patch("mmrelay.config.os.makedirs"),
        ):
            paths = get_config_paths()
            self.assertIn(
                "/home/test/.mmrelay/config.yaml", [os.path.normpath(p) for p in paths]
            )

    def test_load_config_yaml_parse_error(self):
        """
        Test that load_config returns an empty dictionary when a YAML parsing error occurs.
        """
        with patch("builtins.open", mock_open(read_data="invalid: yaml: content: [")):
            with patch("os.path.isfile", return_value=True):
                with patch("mmrelay.config.logger"):
                    config = load_config(config_file="test.yaml")
                    # Should return empty config on YAML error
                    self.assertEqual(config, {})

    def test_load_config_file_permission_error(self):
        """
        Test that load_config handles file permission errors gracefully.

        Verifies that when a PermissionError occurs while opening the config file, load_config either returns an empty config dictionary or raises the exception, without causing unexpected failures.
        """
        with patch("builtins.open", side_effect=PermissionError("Permission denied")):
            with patch("os.path.isfile", return_value=True):
                with patch("mmrelay.config.logger"):
                    # Should not raise exception, should return empty config
                    try:
                        config = load_config(config_file="test.yaml")
                        self.assertEqual(config, {})
                    except PermissionError:
                        # If exception is raised, that's also acceptable behavior
                        pass

    def test_load_config_file_not_found_error(self):
        """
        Test that load_config returns an empty config or handles exceptions when the config file is not found.

        Simulates a FileNotFoundError when attempting to open the config file and verifies that load_config either returns an empty dictionary or allows the exception to propagate without causing test failure.
        """
        with patch("builtins.open", side_effect=FileNotFoundError("File not found")):
            with patch("os.path.isfile", return_value=True):
                with patch("mmrelay.config.logger"):
                    # Should not raise exception, should return empty config
                    try:
                        config = load_config(config_file="nonexistent.yaml")
                        self.assertEqual(config, {})
                    except FileNotFoundError:
                        # If exception is raised, that's also acceptable behavior
                        pass

    def test_load_config_empty_file(self):
        """
        Verify load_config returns an empty dict when given an empty YAML configuration file.

        This ensures the function handles an empty file without raising and returns {} so environment-variable
        overrides can still be applied by callers.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("")  # Empty file
            temp_path = f.name

        try:
            config = load_config(config_file=temp_path)
            # Should handle empty file gracefully and return empty dict to allow env var overrides
            self.assertEqual(config, {})
        finally:
            os.unlink(temp_path)

    def test_load_config_null_yaml(self):
        """
        Test that load_config returns empty dict when the YAML config file contains only a null value.
        """
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("null")
            temp_path = f.name

        try:
            config = load_config(config_file=temp_path)
            # Should handle null YAML gracefully and return empty dict to allow env var overrides
            self.assertEqual(config, {})
        finally:
            os.unlink(temp_path)

    def test_load_config_search_priority(self):
        """
        Verify that load_config loads configuration from the first existing file in the prioritized search path list.
        """
        with patch("mmrelay.config.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = [
                "/first/config.yaml",
                "/second/config.yaml",
                "/third/config.yaml",
            ]

            # Mock only the second file exists
            def mock_isfile(path):
                """
                Mock implementation of os.path.isfile that returns True only for '/second/config.yaml'.

                Parameters:
                    path (str): The file path to check.

                Returns:
                    bool: True if the path is '/second/config.yaml', otherwise False.
                """
                return path == "/second/config.yaml"

            with patch("os.path.isfile", side_effect=mock_isfile):
                with patch("builtins.open", mock_open(read_data="test: value")):
                    with patch("yaml.load", return_value={"test": "value"}):
                        config = load_config()
                        self.assertEqual(config, {"test": "value"})

    def test_set_config_matrix_utils(self):
        """
        Tests that set_config correctly sets the config and matrix_homeserver attributes for a matrix_utils module.

        Verifies that the configuration dictionary is assigned to the module, the matrix_homeserver is set from the config, and the function returns the config.
        """
        mock_module = MagicMock()
        mock_module.__name__ = "mmrelay.matrix_utils"
        mock_module.matrix_homeserver = None

        config = {
            "matrix": {
                "homeserver": "https://test.matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [{"id": "!test:matrix.org"}],
        }

        result = set_config(mock_module, config)

        self.assertEqual(mock_module.config, config)
        self.assertEqual(mock_module.matrix_homeserver, "https://test.matrix.org")
        self.assertEqual(result, config)

    def test_set_config_meshtastic_utils(self):
        """
        Test that set_config correctly assigns configuration and matrix_rooms for a meshtastic_utils module.

        Verifies that set_config sets the config and matrix_rooms attributes on a module named "mmrelay.meshtastic_utils" and returns the provided config dictionary.
        """
        mock_module = MagicMock()
        mock_module.__name__ = "mmrelay.meshtastic_utils"
        mock_module.matrix_rooms = None

        config = {"matrix_rooms": [{"id": "!test:matrix.org", "meshtastic_channel": 0}]}

        result = set_config(mock_module, config)

        self.assertEqual(mock_module.config, config)
        self.assertEqual(mock_module.matrix_rooms, config["matrix_rooms"])
        self.assertEqual(result, config)

    def test_set_config_with_legacy_setup_function(self):
        """
        Test that set_config correctly handles modules with a legacy setup_config function.

        Verifies that set_config calls the module's setup_config method, sets the config attribute, and returns the provided config dictionary when the module defines a setup_config function.
        """
        mock_module = MagicMock()
        mock_module.__name__ = "test_module"
        mock_module.setup_config = MagicMock()

        config = {"test": "value"}

        result = set_config(mock_module, config)

        self.assertEqual(mock_module.config, config)
        mock_module.setup_config.assert_called_once()
        self.assertEqual(result, config)

    def test_set_config_without_required_attributes(self):
        """
        Verify that set_config does not raise an exception and returns the config when the module is missing expected attributes.
        """
        mock_module = MagicMock()
        mock_module.__name__ = "mmrelay.matrix_utils"
        # Remove the matrix_homeserver attribute
        del mock_module.matrix_homeserver

        config = {
            "matrix": {
                "homeserver": "https://test.matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            }
        }

        # Should not raise an exception
        result = set_config(mock_module, config)
        self.assertEqual(result, config)

    def test_load_config_no_files_found(self):
        """
        Test that load_config returns an empty config and logs errors when no configuration files are found.
        """
        with patch("mmrelay.config.get_config_paths") as mock_get_paths:
            mock_get_paths.return_value = ["/nonexistent1.yaml", "/nonexistent2.yaml"]

            with patch("os.path.isfile", return_value=False):
                with patch("mmrelay.config.logger") as mock_logger:
                    config = load_config()

                    # Should return empty config
                    self.assertEqual(config, {})

                    # Should log error messages
                    mock_logger.error.assert_called()

    def test_get_credentials_search_paths_with_explicit_path(self):
        """Test get_credentials_search_paths with explicit path."""
        with tempfile.TemporaryDirectory() as temp_dir:
            explicit_path = os.path.join(temp_dir, "creds.json")

            result = get_credentials_search_paths(explicit_path=explicit_path)

            # Explicit path should be first
            self.assertEqual(result[0], explicit_path)

    def test_get_credentials_search_paths_with_directory(self):
        """Test get_credentials_search_paths treats directory paths correctly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = get_credentials_search_paths(
                explicit_path=temp_dir + os.sep, include_base_data=False
            )

            # Should append credentials.json to directory
            self.assertTrue(any("credentials.json" in path for path in result))

    def test_get_credentials_search_paths_with_config_paths(self):
        """Test get_credentials_search_paths with config file paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "config.yaml")

            result = get_credentials_search_paths(
                config_paths=[config_path], include_base_data=False
            )

            # Should include credentials.json in same dir as config
            expected_creds = os.path.join(temp_dir, "credentials.json")
            self.assertIn(expected_creds, result)

    def test_get_explicit_credentials_path_no_config(self):
        """Test get_explicit_credentials_path returns None when no config provided."""
        with patch.dict(os.environ, {}, clear=True):
            result = get_explicit_credentials_path(None)
            self.assertIsNone(result)

    def test_get_data_dir_uses_home_env(self):
        """Test get_data_dir respects MMRELAY_HOME and emits deprecation warning."""
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.dict(os.environ, {"MMRELAY_HOME": temp_dir}),
                patch("mmrelay.config.os.makedirs"),
            ):
                from mmrelay import config as config_module

                config_module._warn_deprecated.cache_clear()
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", DeprecationWarning)
                    result = get_data_dir(create=False)
                self.assertTrue(
                    caught, "Expected DeprecationWarning from get_data_dir()"
                )
                self.assertIn("Use paths.get_home_dir()", str(caught[0].message))
                self.assertEqual(result, temp_dir)

    def test_get_log_dir_windows_with_override(self):
        """Test get_log_dir on Windows with directory override."""
        with (
            patch.dict(os.environ, {"MMRELAY_HOME": "C:\\mmrelay"}, clear=True),
            patch("mmrelay.config.sys.platform", "win32"),
            patch("mmrelay.paths.sys.platform", "win32"),
            patch("mmrelay.config.os.makedirs"),
        ):
            result = get_log_dir()

            # Should use base_dir/logs with override
            expected = str(Path("C:\\mmrelay").expanduser().absolute() / "logs")
            self.assertEqual(result, expected)

    def test_get_e2ee_store_dir_windows_without_override(self):
        """Test get_e2ee_store_dir on Windows without directory override."""
        with (
            patch("mmrelay.config.sys.platform", "win32"),
            patch("mmrelay.paths.sys.platform", "win32"),
            patch.dict(os.environ, {"MMRELAY_HOME": "C:\\mmrelay"}, clear=True),
            patch("mmrelay.config.os.makedirs"),
        ):
            result = get_e2ee_store_dir()

            # Should use home/matrix/store on Windows fallback
            expected = ntpath.join(
                str(Path("C:\\mmrelay").expanduser().absolute()),
                "matrix",
                "store",
            )
            self.assertEqual(result, expected)

    def test_load_credentials_windows_debug(self):
        """Test load_credentials on Windows logs directory contents."""
        with patch("mmrelay.config.sys.platform", "win32"):
            with patch("mmrelay.config.os.path.exists", return_value=False):
                with patch(
                    "mmrelay.config.get_home_dir",
                    return_value=Path("C:\\mmrelay"),
                ):
                    with patch(
                        "mmrelay.config.os.listdir",
                        return_value=["file1.txt", "file2.json"],
                    ):
                        with patch("mmrelay.config.logger") as mock_logger:
                            # Reset credentials state
                            import mmrelay.config

                            original_config = mmrelay.config.relay_config
                            original_config_path = mmrelay.config.config_path
                            mmrelay.config.relay_config = {}
                            mmrelay.config.config_path = None

                            try:
                                load_credentials()

                                # Should log directory contents on Windows
                                debug_calls = [
                                    call
                                    for call in mock_logger.debug.call_args_list
                                    if "Directory contents" in str(call)
                                ]
                                self.assertGreaterEqual(len(debug_calls), 1)
                            finally:
                                mmrelay.config.relay_config = original_config
                                mmrelay.config.config_path = original_config_path

    def test_save_credentials_writes_to_file(self):
        """Test save_credentials actually writes JSON to file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            creds_path = os.path.join(temp_dir, "credentials.json")
            credentials = {
                "user_id": "@test:matrix.org",
                "access_token": "secret_token",
            }

            from mmrelay import config as config_module

            original_relay_config = config_module.relay_config.copy()
            original_config_path = config_module.config_path

            try:
                config_module.relay_config = {}
                config_module.config_path = None

                with patch.dict(os.environ, {"MMRELAY_CREDENTIALS_PATH": creds_path}):
                    save_credentials(credentials)

                # Verify file was written
                self.assertTrue(os.path.exists(creds_path))

                with open(creds_path, "r") as f:
                    saved_creds = json.load(f)

                self.assertEqual(saved_creds["user_id"], "@test:matrix.org")
                self.assertEqual(saved_creds["access_token"], "secret_token")
            finally:
                config_module.relay_config = original_relay_config
                config_module.config_path = original_config_path

    def test_save_credentials_exception_handling(self):
        """Test save_credentials handles exceptions gracefully."""
        credentials = {"user_id": "test"}

        with patch("mmrelay.config.os.makedirs", side_effect=OSError("Disk full")):
            with patch("mmrelay.config.logger") as mock_logger:
                with self.assertRaises(OSError):
                    save_credentials(credentials)

                # Should log exception and raise
                mock_logger.exception.assert_called()


if __name__ == "__main__":
    unittest.main()
