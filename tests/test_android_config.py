"""
Tests for Android-specific configuration utilities
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from mmrelay.android.android_config import (
    get_android_config_dir,
    get_android_data_dir,
    get_android_log_dir,
    get_config_file_path,
    get_data_file_path,
    get_log_file_path,
    is_android_environment,
    set_android_paths,
    setup_android_logging,
)


class TestAndroidConfig(unittest.TestCase):
    """Test Android configuration utilities"""

    def setUp(self):
        """Reset global state before each test"""
        # Reset the global variables to None
        import mmrelay.android.android_config as config_module

        config_module._android_config_dir = None
        config_module._android_log_dir = None
        config_module._android_data_dir = None

    def tearDown(self):
        """Clean up after each test"""
        # Reset the global variables after each test
        import mmrelay.android.android_config as config_module

        config_module._android_config_dir = None
        config_module._android_log_dir = None
        config_module._android_data_dir = None

    def test_set_android_paths_creates_directories(self):
        """Test that set_android_paths creates the specified directories"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = os.path.join(temp_dir, "config")
            log_dir = os.path.join(temp_dir, "logs")
            data_dir = os.path.join(temp_dir, "data")

            # Verify directories don't exist initially
            self.assertFalse(os.path.exists(config_dir))
            self.assertFalse(os.path.exists(log_dir))
            self.assertFalse(os.path.exists(data_dir))

            # Set Android paths
            set_android_paths(config_dir, log_dir, data_dir)

            # Verify directories were created
            self.assertTrue(os.path.exists(config_dir))
            self.assertTrue(os.path.exists(log_dir))
            self.assertTrue(os.path.exists(data_dir))

    def test_set_android_paths_stores_values(self):
        """Test that set_android_paths stores the path values correctly"""
        config_dir = "/android/config"
        log_dir = "/android/logs"
        data_dir = "/android/data"

        set_android_paths(config_dir, log_dir, data_dir)

        self.assertEqual(get_android_config_dir(), config_dir)
        self.assertEqual(get_android_log_dir(), log_dir)
        self.assertEqual(get_android_data_dir(), data_dir)

    def test_is_android_environment_true_when_paths_set(self):
        """Test that is_android_environment returns True when paths are set"""
        self.assertFalse(is_android_environment())

        set_android_paths("/config", "/logs", "/data")
        self.assertTrue(is_android_environment())

    def test_is_android_environment_false_when_no_paths(self):
        """Test that is_android_environment returns False when no paths are set"""
        self.assertFalse(is_android_environment())

    def test_get_config_file_path_android_environment(self):
        """Test get_config_file_path in Android environment"""
        config_dir = "/android/config"
        set_android_paths(config_dir, "/logs", "/data")

        expected_path = os.path.join(config_dir, "config.yaml")
        self.assertEqual(get_config_file_path(), expected_path)

    def test_get_config_file_path_custom_filename(self):
        """Test get_config_file_path with custom filename"""
        config_dir = "/android/config"
        set_android_paths(config_dir, "/logs", "/data")

        expected_path = os.path.join(config_dir, "custom.yaml")
        self.assertEqual(get_config_file_path("custom.yaml"), expected_path)

    def test_get_config_file_path_non_android_environment(self):
        """Test get_config_file_path in non-Android environment"""
        # Don't set Android paths
        expected_path = "config.yaml"
        self.assertEqual(get_config_file_path(), expected_path)

    def test_get_log_file_path_android_environment(self):
        """Test get_log_file_path in Android environment"""
        log_dir = "/android/logs"
        set_android_paths("/config", log_dir, "/data")

        expected_path = os.path.join(log_dir, "mmrelay.log")
        self.assertEqual(get_log_file_path(), expected_path)

    def test_get_log_file_path_custom_filename(self):
        """Test get_log_file_path with custom filename"""
        log_dir = "/android/logs"
        set_android_paths("/config", log_dir, "/data")

        expected_path = os.path.join(log_dir, "custom.log")
        self.assertEqual(get_log_file_path("custom.log"), expected_path)

    def test_get_log_file_path_non_android_environment(self):
        """Test get_log_file_path in non-Android environment"""
        # Don't set Android paths
        expected_path = "mmrelay.log"
        self.assertEqual(get_log_file_path(), expected_path)

    def test_get_data_file_path_android_environment(self):
        """Test get_data_file_path in Android environment"""
        data_dir = "/android/data"
        set_android_paths("/config", "/logs", data_dir)

        filename = "test.db"
        expected_path = os.path.join(data_dir, filename)
        self.assertEqual(get_data_file_path(filename), expected_path)

    def test_get_data_file_path_non_android_environment(self):
        """Test get_data_file_path in non-Android environment"""
        # Don't set Android paths
        filename = "test.db"
        self.assertEqual(get_data_file_path(filename), filename)

    @patch("mmrelay.android.android_config.logging.basicConfig")
    @patch("mmrelay.android.android_config.logging.FileHandler")
    @patch("mmrelay.android.android_config.logging.StreamHandler")
    def test_setup_android_logging_android_environment(
        self, mock_stream_handler, mock_file_handler, mock_basic_config
    ):
        """Test setup_android_logging in Android environment"""
        log_dir = "/android/logs"
        set_android_paths("/config", log_dir, "/data")

        setup_android_logging()

        # Verify logging.basicConfig was called
        mock_basic_config.assert_called_once()

        # Check that FileHandler was created with correct path
        expected_log_path = os.path.join(log_dir, "mmrelay.log")
        mock_file_handler.assert_called_once_with(expected_log_path)

        # Check that StreamHandler was created
        mock_stream_handler.assert_called_once()

    @patch("mmrelay.android.android_config.logging.basicConfig")
    def test_setup_android_logging_non_android_environment(self, mock_basic_config):
        """Test setup_android_logging in non-Android environment"""
        # Don't set Android paths
        setup_android_logging()

        # Verify logging.basicConfig was NOT called
        mock_basic_config.assert_not_called()

    def test_set_android_paths_handles_existing_directories(self):
        """Test that set_android_paths handles existing directories gracefully"""
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = os.path.join(temp_dir, "config")
            log_dir = os.path.join(temp_dir, "logs")
            data_dir = os.path.join(temp_dir, "data")

            # Create directories beforehand
            os.makedirs(config_dir, exist_ok=True)
            os.makedirs(log_dir, exist_ok=True)
            os.makedirs(data_dir, exist_ok=True)

            # Set Android paths - should not raise exception
            set_android_paths(config_dir, log_dir, data_dir)

            # Verify directories still exist
            self.assertTrue(os.path.exists(config_dir))
            self.assertTrue(os.path.exists(log_dir))
            self.assertTrue(os.path.exists(data_dir))

    @patch("os.makedirs")
    def test_set_android_paths_handles_makedirs_error(self, mock_makedirs):
        """Test that set_android_paths handles makedirs errors gracefully"""
        mock_makedirs.side_effect = OSError("Permission denied")

        config_dir = "/restricted/config"
        log_dir = "/restricted/logs"
        data_dir = "/restricted/data"

        # Should not raise exception even if makedirs fails
        set_android_paths(config_dir, log_dir, data_dir)

        # Verify paths were still set
        self.assertEqual(get_android_config_dir(), config_dir)
        self.assertEqual(get_android_log_dir(), log_dir)
        self.assertEqual(get_android_data_dir(), data_dir)

    def test_getters_return_none_when_not_set(self):
        """Test that getters return None when paths are not set"""
        self.assertIsNone(get_android_config_dir())
        self.assertIsNone(get_android_log_dir())
        self.assertIsNone(get_android_data_dir())

    def test_path_functions_handle_none_values(self):
        """Test that path functions handle None directory values gracefully"""
        # This shouldn't happen in practice, but test robustness
        import mmrelay.android.android_config as config_module

        # Manually set one to None (simulating partial initialization)
        config_module._android_config_dir = None
        config_module._android_log_dir = "/logs"
        config_module._android_data_dir = "/data"

        # Should return fallback paths
        self.assertEqual(get_config_file_path(), "config.yaml")
        self.assertEqual(get_log_file_path(), "/logs/mmrelay.log")
        self.assertEqual(get_data_file_path("test.db"), "/data/test.db")


if __name__ == "__main__":
    unittest.main()
