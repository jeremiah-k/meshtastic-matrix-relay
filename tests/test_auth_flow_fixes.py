"""Tests for authentication flow fixes."""

import os
import unittest
from unittest.mock import mock_open, patch

from mmrelay.config import check_e2ee_enabled_silently, is_e2ee_enabled


class TestAuthFlowFixes(unittest.TestCase):
    """Test authentication flow and E2EE detection fixes."""

    def test_e2ee_detection_with_no_config(self):
        """Test that E2EE is not detected as enabled when no config exists."""
        # Test with no config file
        with patch(
            "mmrelay.config.get_config_paths", return_value=["/nonexistent/config.yaml"]
        ):
            result = check_e2ee_enabled_silently()
            self.assertFalse(
                result, "E2EE should not be detected as enabled when no config exists"
            )

    def test_e2ee_detection_with_empty_config(self):
        """Test that E2EE is not detected as enabled with empty config."""
        # Test with empty config file
        with patch(
            "mmrelay.config.get_config_paths", return_value=["/test/config.yaml"]
        ):
            with patch("os.path.isfile", return_value=True):
                with patch("builtins.open", mock_open(read_data="")):
                    with patch("yaml.load", return_value={}):
                        result = check_e2ee_enabled_silently()
                        self.assertFalse(
                            result,
                            "E2EE should not be detected as enabled with empty config",
                        )

    def test_e2ee_detection_with_no_matrix_section(self):
        """Test that E2EE is not detected as enabled when matrix section is missing."""
        config = {"other_section": {"key": "value"}}
        result = is_e2ee_enabled(config)
        self.assertFalse(
            result, "E2EE should not be detected as enabled without matrix section"
        )

    def test_windows_e2ee_always_disabled(self):
        """Test that E2EE is always disabled on Windows."""
        # Test with config that would enable E2EE on other platforms
        config_with_e2ee = {"matrix": {"e2ee": {"enabled": True}}}

        with patch("sys.platform", "win32"):
            result = is_e2ee_enabled(config_with_e2ee)
            self.assertFalse(result, "E2EE should always be disabled on Windows")

            # Test silent check too
            result = check_e2ee_enabled_silently()
            self.assertFalse(
                result, "E2EE silent check should always return False on Windows"
            )

    def test_windows_credentials_path_handling(self):
        """Test that credentials.json path handling works on Windows."""
        from mmrelay.config import save_credentials

        # Mock Windows paths
        with patch("sys.platform", "win32"):
            with patch(
                "mmrelay.config.get_base_dir",
                return_value="C:\\Users\\Test\\AppData\\Local\\mmrelay",
            ):
                with patch("os.makedirs") as mock_makedirs:
                    with patch("builtins.open", mock_open()) as mock_file:
                        with patch("os.path.exists", return_value=True):
                            # Test saving credentials
                            test_credentials = {
                                "homeserver": "https://matrix.example.com",
                                "access_token": "test_token",
                                "user_id": "@test:example.com",
                                "device_id": "TEST_DEVICE",
                            }

                            save_credentials(test_credentials)

                            # Should create directory
                            mock_makedirs.assert_called_with(
                                "C:\\Users\\Test\\AppData\\Local\\mmrelay",
                                exist_ok=True,
                            )

                            # Should open the correct path - use os.path.join to get the right separator
                            import os

                            expected_path = os.path.join(
                                "C:\\Users\\Test\\AppData\\Local\\mmrelay",
                                "credentials.json",
                            )
                            mock_file.assert_called_with(
                                expected_path, "w", encoding="utf-8"
                            )

    def test_credentials_loading_with_debug_info(self):
        """Test that credentials loading provides debug info on Windows."""
        from mmrelay.config import load_credentials

        with patch("sys.platform", "win32"):
            config_dir = "C:\\Users\\Test\\AppData\\Local\\mmrelay"
            credentials_path = os.path.join(config_dir, "credentials.json")

            with patch("mmrelay.config.get_base_dir", return_value=config_dir):
                # Mock os.path.exists to return False for credentials.json but True for the directory
                def mock_exists(path):
                    """
                    Mock replacement for os.path.exists used in tests.
                    
                    Returns False for the credentials file path, True for the configuration directory path, and False for any other path. Relies on outer-scope variables `credentials_path` and `config_dir` (closure) to determine which paths to treat as existing.
                    
                    Parameters:
                        path (str): Filesystem path to check.
                    
                    Returns:
                        bool: True if the path should be considered present according to the mock, otherwise False.
                    """
                    if path == credentials_path:
                        return False  # credentials.json doesn't exist
                    elif path == config_dir:
                        return True  # but the directory exists
                    return False

                with patch("os.path.exists", side_effect=mock_exists):
                    with patch(
                        "os.listdir", return_value=["config.yaml", "other_file.txt"]
                    ) as mock_listdir:
                        with patch("mmrelay.config.logger") as mock_logger:
                            result = load_credentials()

                            # Should return None when file doesn't exist
                            self.assertIsNone(result)

                            # Should have called listdir on the config directory
                            mock_listdir.assert_called_once_with(config_dir)

                            # Should log debug info about directory contents on Windows
                            mock_logger.debug.assert_called()
                            debug_calls = [
                                call.args[0]
                                for call in mock_logger.debug.call_args_list
                            ]
                            self.assertTrue(
                                any(
                                    "Directory contents" in call for call in debug_calls
                                ),
                                f"Expected 'Directory contents' in debug calls: {debug_calls}",
                            )


if __name__ == "__main__":
    unittest.main()
