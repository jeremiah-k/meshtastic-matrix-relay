"""Tests specifically targeting patch coverage improvements."""

import unittest
from unittest.mock import patch

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

        with patch("mmrelay.setup_utils.os.path.exists", return_value=False):
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
        with patch(
            "mmrelay.setup_utils.subprocess.run", side_effect=OSError("Test error")
        ):
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
        with patch(
            "mmrelay.setup_utils.subprocess.run", side_effect=OSError("Test error")
        ):
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
            with patch(
                "mmrelay.setup_utils.subprocess.run",
                side_effect=OSError("Test error"),
            ):
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
            with patch(
                "mmrelay.setup_utils.subprocess.run",
                side_effect=OSError("Test error"),
            ):
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
        with patch(
            "mmrelay.setup_utils.subprocess.run", side_effect=OSError("Test error")
        ):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = enable_lingering()

        self.assertFalse(result)
        mock_logger.exception.assert_called()
        # Check that error is logged
        call_args = mock_logger.exception.call_args_list
        self.assertTrue(
            any("Error enabling lingering" in str(call) for call in call_args)
        )


if __name__ == "__main__":
    unittest.main()
