"""Tests specifically targeting patch coverage improvements."""

import unittest
from unittest.mock import MagicMock, mock_open, patch

from mmrelay.setup_utils import (
    check_lingering_enabled,
    check_loginctl_available,
    enable_lingering,
    is_service_active,
    is_service_enabled,
)


class TestPatchCoverageImprovements(unittest.TestCase):
    """Test the specific lines changed in the patch for coverage."""

    def test_warning_messages_in_setup_utils(self):
        """Test that warning messages are logged via logger."""
        # Test warning message in get_template_service_path
        from mmrelay.setup_utils import get_template_service_path

        with patch("os.path.exists", return_value=False):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = get_template_service_path()

        # Should log warning
        self.assertIsNone(result)
        mock_logger.warning.assert_called()
        # Check that warning contains service file path info
        call_args = mock_logger.warning.call_args_list
        self.assertTrue(
            any("Could not find mmrelay.service" in str(call) for call in call_args)
        )

    def test_exception_handling_improvements(self):
        """
        Verify is_service_enabled returns False and logs a warning when subprocess.run raises an OSError.
        
        This test patches subprocess.run to raise OSError and asserts that is_service_enabled handles the exception by returning False and emitting a warning log containing "Failed to check service enabled status".
        """
        # Test is_service_enabled with OSError
        with patch("subprocess.run", side_effect=OSError("Test error")):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = is_service_enabled()

        self.assertFalse(result)
        mock_logger.warning.assert_called()
        # Check that warning is logged
        call_args = mock_logger.warning.call_args_list
        self.assertTrue(
            any(
                "Failed to check service enabled status" in str(call)
                for call in call_args
            )
        )

    def test_is_service_active_exception_handling(self):
        """Test is_service_active exception handling."""
        with patch("subprocess.run", side_effect=OSError("Test error")):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = is_service_active()

        self.assertFalse(result)
        mock_logger.warning.assert_called()
        # Check that warning is logged
        call_args = mock_logger.warning.call_args_list
        self.assertTrue(
            any(
                "Failed to check service active status" in str(call)
                for call in call_args
            )
        )

    def test_check_loginctl_available_exception_handling(self):
        """Test check_loginctl_available exception handling."""
        with patch("shutil.which", return_value="/usr/bin/loginctl"):
            with patch("subprocess.run", side_effect=OSError("Test error")):
                with patch("mmrelay.setup_utils.logger") as mock_logger:
                    result = check_loginctl_available()

        self.assertFalse(result)
        mock_logger.warning.assert_called()
        # Check that warning is logged
        call_args = mock_logger.warning.call_args_list
        self.assertTrue(
            any(
                "Failed to check loginctl availability" in str(call)
                for call in call_args
            )
        )

    def test_check_lingering_enabled_exception_handling(self):
        """Test check_lingering_enabled exception handling."""
        with patch("shutil.which", return_value="/usr/bin/loginctl"):
            with patch("subprocess.run", side_effect=OSError("Test error")):
                with patch("mmrelay.setup_utils.logger") as mock_logger:
                    result = check_lingering_enabled()

        self.assertFalse(result)
        mock_logger.exception.assert_called()
        # Check that error is logged
        call_args = mock_logger.exception.call_args_list
        self.assertTrue(
            any("Error checking lingering status" in str(call) for call in call_args)
        )

    def test_enable_lingering_exception_handling(self):
        """Test enable_lingering exception handling."""
        with patch("subprocess.run", side_effect=OSError("Test error")):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = enable_lingering()

        self.assertFalse(result)
        mock_logger.exception.assert_called()
        # Check that error is logged
        call_args = mock_logger.exception.call_args_list
        self.assertTrue(
            any("Error enabling lingering" in str(call) for call in call_args)
        )

    def test_cli_exception_logging_path(self):
        """Test CLI exception logging by testing the config function directly."""
        # Test the config function that has improved exception handling
        from mmrelay.config import check_e2ee_enabled_silently

        # This function should handle exceptions gracefully
        # Test with invalid args to trigger exception paths
        result = check_e2ee_enabled_silently(None)

        # Should return False when no config is found
        self.assertFalse(result)

    def test_is_e2ee_enabled_function(self):
        """Test the new is_e2ee_enabled function in config.py."""
        from mmrelay.config import is_e2ee_enabled

        # Test with None config (line 349)
        self.assertFalse(is_e2ee_enabled(None))

        # Test with empty config (line 349)
        self.assertFalse(is_e2ee_enabled({}))

        # Test with False config (line 349)
        self.assertFalse(is_e2ee_enabled(False))

        # Test with empty string config (line 349)
        self.assertFalse(is_e2ee_enabled(""))

        # Test with encryption enabled (legacy)
        config_encryption = {"matrix": {"encryption": {"enabled": True}}}
        self.assertTrue(is_e2ee_enabled(config_encryption))

        # Test with e2ee enabled (new format)
        config_e2ee = {"matrix": {"e2ee": {"enabled": True}}}
        self.assertTrue(is_e2ee_enabled(config_e2ee))

        # Test with both disabled
        config_disabled = {
            "matrix": {"encryption": {"enabled": False}, "e2ee": {"enabled": False}}
        }
        self.assertFalse(is_e2ee_enabled(config_disabled))

    def test_check_e2ee_enabled_silently_function(self):
        """Test the new check_e2ee_enabled_silently function."""
        from mmrelay.config import check_e2ee_enabled_silently

        # Test with no args - should not crash
        result = check_e2ee_enabled_silently()
        self.assertFalse(result)

        # Test with mock args
        mock_args = MagicMock()
        mock_args.config = None

        with patch("mmrelay.config.get_config_paths", return_value=[]):
            result = check_e2ee_enabled_silently(mock_args)
            self.assertFalse(result)

    def test_config_edge_cases_for_coverage(self):
        """Test additional config.py edge cases for coverage."""
        from mmrelay.config import is_e2ee_enabled

        # Test with missing matrix section
        no_matrix_config = {"other_section": {"key": "value"}}
        self.assertFalse(is_e2ee_enabled(no_matrix_config))

        # Test with encryption enabled but e2ee disabled (both should be true for OR logic)
        mixed_config = {
            "matrix": {"encryption": {"enabled": True}, "e2ee": {"enabled": False}}
        }
        # Should be True because encryption is enabled (OR logic)
        self.assertTrue(is_e2ee_enabled(mixed_config))

        # Test with matrix section being None
        none_matrix_config = {"matrix": None}
        self.assertFalse(is_e2ee_enabled(none_matrix_config))

    def test_config_silent_check_exception_paths(self):
        """Test exception handling paths in check_e2ee_enabled_silently."""
        from mmrelay.config import check_e2ee_enabled_silently

        # Test with args that have config but file doesn't exist
        mock_args = MagicMock()
        mock_args.config = "/nonexistent/config.yaml"

        result = check_e2ee_enabled_silently(mock_args)
        self.assertFalse(result)

        # Test YAML error handling (line 383-384)
        mock_args.config = None
        with patch(
            "mmrelay.config.get_config_paths", return_value=["/test/config.yaml"]
        ):
            with patch("os.path.isfile", return_value=True):
                with patch(
                    "builtins.open", mock_open(read_data="invalid: yaml: content: [")
                ):
                    result = check_e2ee_enabled_silently(mock_args)
                    self.assertFalse(result)

    def test_config_silent_check_falsy_config(self):
        """Test the falsy config check (line 381) in check_e2ee_enabled_silently."""
        from mmrelay.config import check_e2ee_enabled_silently

        # Test with config file that loads as None/empty (line 381)
        mock_args = MagicMock()
        mock_args.config = None
        with patch(
            "mmrelay.config.get_config_paths", return_value=["/test/config.yaml"]
        ):
            with patch("os.path.isfile", return_value=True):
                with patch("builtins.open", mock_open(read_data="")):
                    with patch("yaml.load", return_value=None):  # Falsy config
                        result = check_e2ee_enabled_silently(mock_args)
                        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()