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

from mmrelay.constants.app import CONFIG_FILENAME, CREDENTIALS_FILENAME

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

        with patch("mmrelay.config.os.makedirs"):
            paths = get_config_paths(mock_args)

        self.assertEqual(paths[0], "/custom/path/config.yaml")

    def test_get_config_paths_windows_platform(self):
        """
        Test that get_config_paths() returns Windows-style configuration paths when running on a Windows platform.

        Verifies that the returned paths include a directory under 'AppData', as expected for Windows environments.
        """
        with patch.dict(os.environ, {}, clear=True):
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
        with patch.dict(os.environ, {}, clear=True):
            with (
                patch("mmrelay.paths.sys.platform", "darwin"),
                patch("mmrelay.paths.Path.home", return_value=Path("/home/test")),
                patch("mmrelay.config.os.makedirs"),
            ):
                paths = get_config_paths()
                self.assertIn(
                    f"/home/test/.mmrelay/{CONFIG_FILENAME}",
                    [os.path.normpath(p) for p in paths],
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

    def test_load_config_explicit_path_not_found(self):
        """
        Test that load_config errors when explicit --config path doesn't exist.

        When a user provides --config with a non-existent file, the function should
        error immediately rather than silently falling back to other locations.
        This prevents confusion when the wrong configuration is loaded.
        """
        with patch("os.path.isfile", return_value=False):
            with patch("mmrelay.config.logger") as mock_logger:
                # Create mock args with explicit config path that doesn't exist
                mock_args = MagicMock()
                mock_args.config = "/nonexistent/explicit/config.yaml"

                config = load_config(args=mock_args)

                # Should return empty config (indicating failure)
                self.assertEqual(config, {})

                # Should log explicit error about missing config file
                mock_logger.error.assert_any_call(
                    "Explicit config file not found: /nonexistent/explicit/config.yaml"
                )
                mock_logger.error.assert_any_call(
                    "Please check the path or omit --config to use default search locations."
                )

    def test_load_config_explicit_path_found(self):
        """
        Test that load_config works normally when explicit --config path exists.

        When a user provides --config with a valid file path, it should be loaded
        successfully without falling back to other locations.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as temp_file:
            temp_file.write("matrix:\n  homeserver: https://test.example.com\n")
            temp_path = temp_file.name

        try:
            # Create mock args with explicit config path that exists
            mock_args = MagicMock()
            mock_args.config = temp_path

            config = load_config(args=mock_args)

            # Should load the config successfully
            self.assertIn("matrix", config)
            self.assertEqual(config["matrix"]["homeserver"], "https://test.example.com")
        finally:
            os.unlink(temp_path)

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
            self.assertTrue(any(CREDENTIALS_FILENAME in path for path in result))

    def test_get_credentials_search_paths_with_config_paths(self):
        """Test get_credentials_search_paths with config file paths."""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, CONFIG_FILENAME)

            result = get_credentials_search_paths(
                config_paths=[config_path], include_base_data=False
            )

            # Should include credentials.json in same dir as config
            expected_creds = os.path.join(temp_dir, CREDENTIALS_FILENAME)
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
            creds_path = os.path.join(temp_dir, CREDENTIALS_FILENAME)
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


class TestConfigAdditionalCoverage(unittest.TestCase):
    """Additional tests for config.py uncovered branches."""

    def setUp(self):
        import mmrelay.config

        mmrelay.config.relay_config = {}
        mmrelay.config.config_path = None

    def test_credentials_path_error_message(self):
        from mmrelay.config import CredentialsPathError

        err = CredentialsPathError()
        assert str(err) == "No candidate credentials paths available"

    @patch("mmrelay.config.logger")
    def test_emit_legacy_credentials_warning(self, mock_logger):
        from mmrelay.config import _emit_legacy_credentials_warning

        _emit_legacy_credentials_warning("/some/legacy/path")
        mock_logger.warning.assert_called_once()
        call_args = mock_logger.warning.call_args
        assert "legacy location" in call_args[0][0]
        assert "/some/legacy/path" in call_args[0][1]

    def test_get_credentials_search_paths_empty_config_path(self):
        from mmrelay.config import get_credentials_search_paths

        paths = get_credentials_search_paths(config_paths=[])
        assert isinstance(paths, list)

    @patch("mmrelay.config.is_deprecation_window_active", return_value=True)
    @patch("mmrelay.config.get_legacy_dirs", return_value=["/legacy/dir"])
    def test_get_credentials_search_paths_deprecation_window(
        self, mock_legacy, mock_dep
    ):
        from mmrelay.config import get_credentials_search_paths

        paths = get_credentials_search_paths(config_paths=None, include_base_data=False)
        assert any("legacy" in p for p in paths)

    def test_get_explicit_credentials_path_non_string(self):
        from mmrelay.config import (
            InvalidCredentialsPathTypeError,
            get_explicit_credentials_path,
        )

        with self.assertRaises(InvalidCredentialsPathTypeError):
            get_explicit_credentials_path({"credentials_path": 123})

    def test_get_explicit_credentials_path_matrix_section_non_string(self):
        from mmrelay.config import (
            InvalidCredentialsPathTypeError,
            get_explicit_credentials_path,
        )

        with self.assertRaises(InvalidCredentialsPathTypeError):
            get_explicit_credentials_path({"matrix": {"credentials_path": 456}})

    def test_get_explicit_credentials_path_matrix_section_returns_path(self):
        from mmrelay.config import get_explicit_credentials_path

        result = get_explicit_credentials_path(
            {"matrix": {"credentials_path": "/path/to/creds.json"}}
        )
        assert result == "/path/to/creds.json"

    def test_get_explicit_credentials_path_matrix_section_empty_returns_none(self):
        from mmrelay.config import get_explicit_credentials_path

        result = get_explicit_credentials_path({"matrix": {"credentials_path": ""}})
        assert result is None

    def test_get_data_dir_creates_directory(self):
        from mmrelay.config import get_data_dir

        with tempfile.TemporaryDirectory() as tmpdir:
            test_dir = os.path.join(tmpdir, "new_subdir")
            with patch("mmrelay.config.get_home_dir", return_value=Path(test_dir)):
                result = get_data_dir(create=True)
                assert os.path.isdir(result)

    @patch(
        "mmrelay.config.relay_config", {"community_plugins": {"myplug": {"url": "x"}}}
    )
    @patch(
        "mmrelay.config.get_unified_plugin_data_dir",
        return_value=Path("/plugins/data/myplug"),
    )
    @patch("mmrelay.config.get_plugins_dir", return_value=Path("/plugins/data"))
    @patch("os.makedirs")
    def test_get_plugin_data_dir_infers_community_type(
        self, mock_makedirs, mock_plugins, mock_unified
    ):
        from mmrelay.config import get_plugin_data_dir

        result = get_plugin_data_dir("myplug")
        assert "myplug" in result

    @patch("mmrelay.config.relay_config", {"custom_plugins": {"myplug": {"url": "x"}}})
    @patch(
        "mmrelay.config.get_unified_plugin_data_dir",
        return_value=Path("/plugins/data/myplug"),
    )
    @patch("mmrelay.config.get_plugins_dir", return_value=Path("/plugins/data"))
    @patch("os.makedirs")
    def test_get_plugin_data_dir_infers_custom_type(
        self, mock_makedirs, mock_plugins, mock_unified
    ):
        from mmrelay.config import get_plugin_data_dir

        result = get_plugin_data_dir("myplug")
        assert "myplug" in result

    @patch("mmrelay.config.relay_config", {})
    @patch(
        "mmrelay.config.get_unified_plugin_data_dir",
        return_value=Path("/plugins/data/myplug"),
    )
    @patch("mmrelay.config.get_plugins_dir", return_value=Path("/plugins/data"))
    @patch("os.makedirs")
    def test_get_plugin_data_dir_infers_core_type(
        self, mock_makedirs, mock_plugins, mock_unified
    ):
        from mmrelay.config import get_plugin_data_dir

        result = get_plugin_data_dir("myplug")
        assert "myplug" in result

    @patch("sys.platform", "win32")
    def test_get_fallback_store_dir_win32(self):
        from mmrelay.config import _get_fallback_store_dir

        with patch("mmrelay.config.get_home_dir", return_value=Path("C:/Users/test")):
            result = _get_fallback_store_dir()
            assert "matrix" in result
            assert "store" in result

    @patch(
        "mmrelay.config.get_unified_store_dir", side_effect=OSError("permission denied")
    )
    @patch(
        "mmrelay.config._get_fallback_store_dir", return_value="/home/fallback/store"
    )
    @patch("mmrelay.config.logger")
    def test_get_e2ee_store_dir_oserror_fallback(
        self, mock_logger, mock_fallback, mock_store
    ):
        from mmrelay.config import get_e2ee_store_dir

        result = get_e2ee_store_dir()
        assert result == "/home/fallback/store"
        mock_logger.warning.assert_called()

    def test_convert_env_float_exceeds_max(self):
        from mmrelay.config import _convert_env_float

        with self.assertRaises(ValueError) as ctx:
            _convert_env_float("100", "TEST_VAR", max_value=10)
        assert "must be <=" in str(ctx.exception)

    @patch("sys.platform", "win32")
    def test_is_e2ee_enabled_win32(self):
        from mmrelay.config import is_e2ee_enabled

        assert is_e2ee_enabled({"matrix": {"encryption": {"enabled": True}}}) is False

    @patch("sys.platform", "linux")
    @patch("os.path.isfile", return_value=True)
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data="matrix:\n  encryption:\n    enabled: true\n",
    )
    @patch("mmrelay.config.get_config_paths", return_value=["/test/config.yaml"])
    @patch("mmrelay.config.is_e2ee_enabled", return_value=True)
    def test_check_e2ee_enabled_silently_found(
        self, mock_e2ee, mock_paths, mock_file, mock_isfile
    ):
        from mmrelay.config import check_e2ee_enabled_silently

        result = check_e2ee_enabled_silently()
        assert result is True

    @patch("sys.platform", "linux")
    @patch("os.path.isfile", return_value=False)
    @patch("mmrelay.config.get_config_paths", return_value=["/nonexistent/config.yaml"])
    def test_check_e2ee_enabled_silently_no_config(self, mock_paths, mock_isfile):
        from mmrelay.config import check_e2ee_enabled_silently

        result = check_e2ee_enabled_silently()
        assert result is False

    @patch("sys.platform", "win32")
    def test_check_e2ee_enabled_silently_win32(self):
        from mmrelay.config import check_e2ee_enabled_silently

        assert check_e2ee_enabled_silently() is False

    def test_normalize_optional_dict_sections_with_none(self):
        from mmrelay.config import _normalize_optional_dict_sections

        config = {"meshtastic": None, "matrix": None}
        _normalize_optional_dict_sections(config, ("meshtastic", "matrix"))
        assert config["meshtastic"] == {}
        assert config["matrix"] == {}

    @patch(
        "mmrelay.config.load_meshtastic_config_from_env",
        return_value={"serial_port": "/dev/ttyUSB0"},
    )
    @patch("mmrelay.config.load_logging_config_from_env", return_value=None)
    @patch("mmrelay.config.load_database_config_from_env", return_value=None)
    @patch("mmrelay.config.load_matrix_config_from_env", return_value=None)
    @patch("mmrelay.config.logger")
    def test_apply_env_overrides_section_is_none(
        self, mock_logger, mock_matrix, mock_db, mock_log, mock_mesh
    ):
        from mmrelay.config import apply_env_config_overrides

        config = {"meshtastic": "not_a_dict"}
        result = apply_env_config_overrides(config)
        assert "meshtastic" in result

    @patch("mmrelay.config.load_meshtastic_config_from_env", return_value=None)
    @patch(
        "mmrelay.config.load_logging_config_from_env", return_value={"level": "DEBUG"}
    )
    @patch("mmrelay.config.load_database_config_from_env", return_value=None)
    @patch("mmrelay.config.load_matrix_config_from_env", return_value=None)
    @patch("mmrelay.config.logger")
    def test_apply_env_overrides_logging_section_none(
        self, mock_logger, mock_matrix, mock_db, mock_log, mock_mesh
    ):
        from mmrelay.config import apply_env_config_overrides

        config = {"logging": "not_a_dict"}
        result = apply_env_config_overrides(config)
        assert "logging" in result

    @patch("mmrelay.config.load_meshtastic_config_from_env", return_value=None)
    @patch("mmrelay.config.load_logging_config_from_env", return_value=None)
    @patch("mmrelay.config.load_database_config_from_env", return_value={"path": "/db"})
    @patch("mmrelay.config.load_matrix_config_from_env", return_value=None)
    @patch("mmrelay.config.logger")
    def test_apply_env_overrides_db_section_none(
        self, mock_logger, mock_matrix, mock_db, mock_log, mock_mesh
    ):
        from mmrelay.config import apply_env_config_overrides

        config = {"database": "not_a_dict"}
        result = apply_env_config_overrides(config)
        assert "database" in result

    @patch("mmrelay.config.load_meshtastic_config_from_env", return_value=None)
    @patch("mmrelay.config.load_logging_config_from_env", return_value=None)
    @patch("mmrelay.config.load_database_config_from_env", return_value=None)
    @patch(
        "mmrelay.config.load_matrix_config_from_env",
        return_value={"homeserver": "https://m.org"},
    )
    @patch("mmrelay.config.logger")
    def test_apply_env_overrides_matrix_section_none(
        self, mock_logger, mock_matrix, mock_db, mock_log, mock_mesh
    ):
        from mmrelay.config import apply_env_config_overrides

        config = {"matrix": "not_a_dict"}
        result = apply_env_config_overrides(config)
        assert "matrix" in result

    @patch("mmrelay.config.get_explicit_credentials_path", side_effect=OSError("fail"))
    @patch("mmrelay.config.logger")
    def test_load_credentials_path_error_returns_none(self, mock_logger, mock_explicit):
        from mmrelay.config import load_credentials

        result = load_credentials()
        assert result is None
        mock_logger.exception.assert_called()

    @patch("mmrelay.config.get_credentials_search_paths", return_value=[])
    @patch("mmrelay.config.get_explicit_credentials_path", return_value=None)
    @patch("mmrelay.config.os.path.isfile", return_value=True)
    def test_load_credentials_missing_required_keys(
        self, mock_isfile, mock_explicit, mock_search
    ):
        from mmrelay.config import load_credentials

        json.dumps({"homeserver": "https://m.org"}).encode()
        with patch(
            "builtins.open",
            mock_open(
                read_data=json.dumps(
                    {"homeserver": "https://m.org", "access_token": ""}
                )
            ),
        ):
            result = load_credentials(config_override={})
            assert result is None

    @patch(
        "mmrelay.config.get_credentials_search_paths", return_value=["/test/creds.json"]
    )
    @patch("mmrelay.config.get_explicit_credentials_path", return_value=None)
    @patch("mmrelay.config.os.path.exists", return_value=True)
    @patch("mmrelay.config.get_credentials_path", return_value="/other/creds.json")
    @patch("mmrelay.config.get_home_dir", return_value=Path("/home"))
    @patch("mmrelay.config.is_deprecation_window_active", return_value=True)
    @patch("mmrelay.config.get_legacy_dirs", return_value=[])
    @patch("mmrelay.config.logger")
    def test_load_credentials_invalid_device_id(
        self,
        mock_logger,
        mock_legacy,
        mock_dep,
        mock_home,
        mock_creds_path,
        mock_exists,
        mock_explicit,
        mock_search,
    ):
        from mmrelay.config import load_credentials

        creds_data = {
            "homeserver": "https://matrix.org",
            "access_token": "tok123",
            "device_id": "",
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(creds_data))):
            result = load_credentials(config_override={})
            assert result is not None
            assert result["device_id"] is None

    @patch(
        "mmrelay.config.get_credentials_search_paths",
        return_value=["/legacy/creds.json"],
    )
    @patch("mmrelay.config.get_explicit_credentials_path", return_value=None)
    @patch("mmrelay.config.os.path.exists", return_value=True)
    @patch("mmrelay.config.get_credentials_path", return_value="/primary/creds.json")
    @patch("mmrelay.config.get_home_dir", return_value=Path("/home"))
    @patch("mmrelay.config.is_deprecation_window_active", return_value=True)
    @patch("mmrelay.config.get_legacy_dirs", return_value=["/legacy"])
    @patch("mmrelay.config.logger")
    def test_load_credentials_legacy_dir_warning(
        self,
        mock_logger,
        mock_legacy,
        mock_dep,
        mock_home,
        mock_creds_path,
        mock_exists,
        mock_explicit,
        mock_search,
    ):
        from mmrelay.config import load_credentials

        creds_data = {
            "homeserver": "https://matrix.org",
            "access_token": "tok123",
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(creds_data))):
            result = load_credentials(config_override={})
            assert result is not None
            warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
            assert any("legacy location" in w for w in warning_calls)

    @patch(
        "mmrelay.config.get_credentials_search_paths",
        return_value=["/home/.mmrelay/credentials.json"],
    )
    @patch("mmrelay.config.get_explicit_credentials_path", return_value=None)
    @patch("mmrelay.config.os.path.exists", return_value=True)
    @patch("mmrelay.config.get_credentials_path", return_value="/primary/creds.json")
    @patch("mmrelay.config.get_home_dir", return_value=Path("/home"))
    @patch("mmrelay.config.is_deprecation_window_active", return_value=False)
    @patch("mmrelay.config.logger")
    def test_load_credentials_home_dir_legacy_warning(
        self,
        mock_logger,
        mock_dep,
        mock_home,
        mock_creds_path,
        mock_exists,
        mock_explicit,
        mock_search,
    ):
        from mmrelay.config import load_credentials

        creds_data = {
            "homeserver": "https://matrix.org",
            "access_token": "tok123",
        }
        with patch("builtins.open", mock_open(read_data=json.dumps(creds_data))):
            result = load_credentials(config_override={})
            assert result is not None

    def test_load_config_search_finds_yaml(self):
        from mmrelay.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.yaml")
            with open(config_file, "w") as f:
                f.write("matrix:\n  homeserver: https://test.org\n")

            result = load_config(config_paths=[config_file])
            assert result.get("matrix", {}).get("homeserver") == "https://test.org"

    def test_load_config_null_yaml_in_search(self):
        from mmrelay.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = os.path.join(tmpdir, "config.yaml")
            with open(config_file, "w") as f:
                f.write("")

            result = load_config(config_paths=[config_file])
            assert result == {}

    def test_load_config_yaml_error_in_search_continues(self):
        from mmrelay.config import load_config

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = os.path.join(tmpdir, "bad.yaml")
            with open(bad_file, "w") as f:
                f.write("matrix: [bad: syntax")

            good_file = os.path.join(tmpdir, "good.yaml")
            with open(good_file, "w") as f:
                f.write("matrix:\n  homeserver: https://test.org\n")

            result = load_config(config_paths=[bad_file, good_file])
            assert result.get("matrix", {}).get("homeserver") == "https://test.org"

    @patch("os.path.isfile", return_value=False)
    @patch("os.path.isdir", return_value=True)
    @patch("mmrelay.config.get_config_paths", return_value=["/some/dir"])
    @patch("mmrelay.config.logger")
    def test_load_config_candidate_is_directory(
        self, mock_logger, mock_paths, mock_isdir, mock_isfile
    ):
        from mmrelay.config import load_config

        result = load_config()
        assert result == {}
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        assert any("directory" in w for w in warning_calls)

    def test_set_config_meshtastic_utils_with_rooms(self):
        from mmrelay.config import set_config

        module = MagicMock()
        module.__name__ = "mmrelay.meshtastic_utils"

        config = {"matrix_rooms": [{"room_id": "!test:matrix.org"}]}
        set_config(module, config)
        assert module.matrix_rooms == config["matrix_rooms"]

    def test_set_config_calls_setup_config(self):
        from mmrelay.config import set_config

        module = MagicMock()
        module.__name__ = "some_module"
        module.setup_config = MagicMock()

        set_config(module, {})
        module.setup_config.assert_called_once()

    def test_validate_yaml_syntax_style_warnings(self):
        from mmrelay.config import validate_yaml_syntax

        content = "key: yes\nother: no\n"
        is_valid, message, parsed = validate_yaml_syntax(content, "test.yaml")
        assert is_valid is True
        assert message is not None
        assert "Style warning" in message

    def test_validate_yaml_syntax_equals_sign(self):
        from mmrelay.config import validate_yaml_syntax

        content = "key = value\n"
        is_valid, message, parsed = validate_yaml_syntax(content, "test.yaml")
        assert is_valid is False
        assert message is not None and "=" in message

    def test_validate_yaml_syntax_parse_error_with_mark(self):
        from mmrelay.config import validate_yaml_syntax

        content = "matrix:\n  rooms:\n    - invalid: [unmatched\n"
        is_valid, message, parsed = validate_yaml_syntax(content, "test.yaml")
        assert is_valid is False
        assert parsed is None

    def test_validate_yaml_syntax_error_with_syntax_issues(self):
        from mmrelay.config import validate_yaml_syntax

        content = "key = value\nother: yes\nbad: [\n"
        is_valid, message, parsed = validate_yaml_syntax(content, "test.yaml")
        assert is_valid is False
        assert parsed is None

    def test_backward_compat_alias(self):
        from mmrelay.config import (
            get_candidate_credentials_paths,
            get_credentials_search_paths,
        )

        result1 = get_candidate_credentials_paths(include_base_data=False)
        result2 = get_credentials_search_paths(include_base_data=False)
        assert result1 == result2


class TestLegacyPathOverrideWarnings(unittest.TestCase):
    def setUp(self):
        import mmrelay.config

        mmrelay.config._legacy_path_override_warning_shown = False

    def test_warn_on_mmrelay_credentials_path_env_var(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        with patch.dict(os.environ, {"MMRELAY_CREDENTIALS_PATH": "/some/path"}):
            with patch("mmrelay.config.logger") as mock_logger:
                _warn_on_legacy_path_overrides(None)
                warning_calls = [
                    str(call) for call in mock_logger.warning.call_args_list
                ]
                assert any(
                    "MMRELAY_CREDENTIALS_PATH" in msg for msg in warning_calls
                ), f"No warning mentioning MMRELAY_CREDENTIALS_PATH in {warning_calls}"
                assert any(
                    "MMRELAY_HOME" in msg for msg in warning_calls
                ), f"No warning mentioning MMRELAY_HOME in {warning_calls}"

    def test_warn_on_top_level_credentials_path_config(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"credentials_path": "/some/path"}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any(
                "credentials_path" in msg for msg in warning_calls
            ), f"No warning mentioning credentials_path in {warning_calls}"
            assert any(
                "MMRELAY_HOME" in msg for msg in warning_calls
            ), f"No warning mentioning MMRELAY_HOME in {warning_calls}"

    def test_warn_on_matrix_credentials_path_config(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"matrix": {"credentials_path": "/some/path"}}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any(
                "matrix.credentials_path" in msg for msg in warning_calls
            ), f"No warning mentioning matrix.credentials_path in {warning_calls}"

    def test_warn_on_matrix_e2ee_store_path_config(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"matrix": {"e2ee": {"store_path": "/some/path"}}}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any(
                "e2ee" in msg and "store_path" in msg for msg in warning_calls
            ), f"No warning mentioning e2ee store_path in {warning_calls}"

    def test_warn_on_matrix_encryption_store_path_config(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"matrix": {"encryption": {"store_path": "/some/path"}}}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any(
                "encryption" in msg and "store_path" in msg for msg in warning_calls
            ), f"No warning mentioning encryption store_path in {warning_calls}"

    def test_warning_emitted_once_per_process(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"credentials_path": "/some/path"}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            _warn_on_legacy_path_overrides(config)
            assert mock_logger.warning.call_count == 1

    def test_no_warning_when_no_legacy_overrides(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        with patch.dict(os.environ, {}, clear=False):
            with patch("mmrelay.config.logger") as mock_logger:
                _warn_on_legacy_path_overrides({})
                mock_logger.warning.assert_not_called()

    def test_no_warning_with_mmrelay_home_only(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        with patch.dict(os.environ, {"MMRELAY_HOME": "/home/mmrelay"}):
            with patch("mmrelay.config.logger") as mock_logger:
                _warn_on_legacy_path_overrides({})
                mock_logger.warning.assert_not_called()

    def test_warning_includes_removal_version(self):
        from mmrelay.config import _warn_on_legacy_path_overrides

        config = {"credentials_path": "/some/path"}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            warning_calls = [str(call) for call in mock_logger.warning.call_args_list]
            assert any(
                "1.4" in msg for msg in warning_calls
            ), f"No warning mentioning 1.4 in {warning_calls}"

    def test_get_explicit_credentials_path_warns_on_env_var(self):
        from mmrelay.config import get_explicit_credentials_path

        with patch.dict(os.environ, {"MMRELAY_CREDENTIALS_PATH": "/env/creds.json"}):
            with patch("mmrelay.config.logger") as mock_logger:
                result = get_explicit_credentials_path(None)
                assert result == "/env/creds.json"
                warning_calls = [
                    str(call) for call in mock_logger.warning.call_args_list
                ]
                assert any(
                    "MMRELAY_CREDENTIALS_PATH" in msg for msg in warning_calls
                ), f"No warning mentioning MMRELAY_CREDENTIALS_PATH in {warning_calls}"
                assert any(
                    "MMRELAY_HOME" in msg for msg in warning_calls
                ), f"No warning mentioning MMRELAY_HOME in {warning_calls}"

    def test_get_explicit_credentials_path_no_double_warn(self):
        from mmrelay.config import (
            _warn_on_legacy_path_overrides,
            get_explicit_credentials_path,
        )

        config = {"credentials_path": "/some/path"}
        with patch("mmrelay.config.logger") as mock_logger:
            _warn_on_legacy_path_overrides(config)
            with patch.dict(
                os.environ, {"MMRELAY_CREDENTIALS_PATH": "/env/creds.json"}
            ):
                get_explicit_credentials_path(None)
            assert mock_logger.warning.call_count == 1

    def test_precedence_env_var_over_config(self):
        from mmrelay.config import get_explicit_credentials_path

        with patch.dict(os.environ, {"MMRELAY_CREDENTIALS_PATH": "/env/path"}):
            with patch("mmrelay.config.logger"):
                result = get_explicit_credentials_path(
                    {"credentials_path": "/config/path"}
                )
                assert result == "/env/path"

    def test_precedence_top_level_over_matrix_section(self):
        from mmrelay.config import get_explicit_credentials_path

        with patch.dict(os.environ, {}, clear=True):
            with patch("mmrelay.config.logger"):
                result = get_explicit_credentials_path(
                    {
                        "credentials_path": "/top/path",
                        "matrix": {"credentials_path": "/matrix/path"},
                    }
                )
                assert result == "/top/path"


if __name__ == "__main__":
    unittest.main()
