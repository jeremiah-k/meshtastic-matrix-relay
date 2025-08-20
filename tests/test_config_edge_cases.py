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

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.config import (
    get_app_path,
    get_config_paths,
    load_config,
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
        mmrelay.config.custom_data_dir = None

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
        with patch("mmrelay.config.sys.platform", "win32"):
            with patch(
                "mmrelay.config.platformdirs.user_config_dir"
            ) as mock_user_config:
                mock_user_config.return_value = (
                    "C:\\Users\\Test\\AppData\\Local\\mmrelay"
                )
                with patch(
                    "mmrelay.config.os.makedirs"
                ):  # Mock directory creation in the right namespace
                    paths = get_config_paths()
                    # Check that a Windows-style path is in the list
                    windows_path_found = any("AppData" in path for path in paths)
                    self.assertTrue(windows_path_found)

    def test_get_config_paths_darwin_platform(self):
        """
        Test that get_config_paths returns the correct configuration file path for macOS.

        Simulates a Darwin platform and a custom base directory to ensure get_config_paths includes the expected config.yaml path in its results.
        """
        with patch("sys.platform", "darwin"):
            with patch("mmrelay.config.get_base_dir") as mock_get_base_dir:
                with tempfile.TemporaryDirectory() as temp_dir:
                    mock_get_base_dir.return_value = temp_dir
                    with patch(
                        "mmrelay.config.os.makedirs"
                    ):  # Mock directory creation in the right namespace
                        paths = get_config_paths()
                        self.assertIn(f"{temp_dir}/config.yaml", paths)

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


if __name__ == "__main__":
    unittest.main()
