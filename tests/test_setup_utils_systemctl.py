"""
Test cases for setup_utils systemctl path resolution improvements.

This module tests the dynamic systemctl path resolution and improved
error handling added to setup_utils.py.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestSystemctlPathResolution(unittest.TestCase):
    """Test cases for dynamic systemctl path resolution."""

    def test_systemctl_path_resolution_with_which(self):
        """Test that SYSTEMCTL uses shutil.which result when available."""
        with patch("shutil.which", return_value="/usr/local/bin/systemctl"):
            # Re-import to trigger the path resolution
            import importlib

            import mmrelay.setup_utils

            importlib.reload(mmrelay.setup_utils)

            self.assertEqual(mmrelay.setup_utils.SYSTEMCTL, "/usr/local/bin/systemctl")

    def test_systemctl_path_resolution_fallback(self):
        """Test that SYSTEMCTL falls back to /usr/bin/systemctl when which returns None."""
        with patch("shutil.which", return_value=None):
            # Re-import to trigger the path resolution
            import importlib

            import mmrelay.setup_utils

            importlib.reload(mmrelay.setup_utils)

            self.assertEqual(mmrelay.setup_utils.SYSTEMCTL, "/usr/bin/systemctl")

    def test_systemctl_path_used_in_is_service_enabled(self):
        """Test that is_service_enabled uses the resolved systemctl path."""
        from mmrelay.setup_utils import is_service_enabled

        with patch("mmrelay.setup_utils.SYSTEMCTL", "/custom/path/systemctl"), patch(
            "subprocess.run"
        ) as mock_run:

            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "enabled"

            is_service_enabled()

            # Should use the custom systemctl path
            mock_run.assert_called_once_with(
                ["/custom/path/systemctl", "--user", "is-enabled", "mmrelay.service"],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_systemctl_path_used_in_is_service_active(self):
        """Test that is_service_active uses the resolved systemctl path."""
        from mmrelay.setup_utils import is_service_active

        with patch("mmrelay.setup_utils.SYSTEMCTL", "/custom/path/systemctl"), patch(
            "subprocess.run"
        ) as mock_run:

            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "active"

            is_service_active()

            # Should use the custom systemctl path
            mock_run.assert_called_once_with(
                ["/custom/path/systemctl", "--user", "is-active", "mmrelay.service"],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_systemctl_path_used_in_reload_daemon(self):
        """Test that reload_daemon uses the resolved systemctl path."""
        from mmrelay.setup_utils import reload_daemon

        with patch("mmrelay.setup_utils.SYSTEMCTL", "/custom/path/systemctl"), patch(
            "subprocess.run"
        ) as mock_run, patch("builtins.print"):

            mock_run.return_value = None  # Successful run

            result = reload_daemon()

            # Should use the custom systemctl path
            mock_run.assert_called_once_with(
                ["/custom/path/systemctl", "--user", "daemon-reload"], check=True
            )
            self.assertTrue(result)

    def test_systemctl_path_used_in_start_service(self):
        """Test that start_service uses the resolved systemctl path."""
        from mmrelay.setup_utils import start_service

        with patch("mmrelay.setup_utils.SYSTEMCTL", "/custom/path/systemctl"), patch(
            "subprocess.run"
        ) as mock_run:

            mock_run.return_value = None  # Successful run

            result = start_service()

            # Should use the custom systemctl path
            mock_run.assert_called_once_with(
                ["/custom/path/systemctl", "--user", "start", "mmrelay.service"],
                check=True,
            )
            self.assertTrue(result)

    def test_systemctl_path_used_in_show_service_status(self):
        """Test that show_service_status uses the resolved systemctl path."""
        from mmrelay.setup_utils import show_service_status

        with patch("mmrelay.setup_utils.SYSTEMCTL", "/custom/path/systemctl"), patch(
            "subprocess.run"
        ) as mock_run:

            mock_run.return_value.stdout = (
                "● mmrelay.service - MMRelay\n   Active: active (running)"
            )

            show_service_status()

            # Should use the custom systemctl path
            mock_run.assert_called_once_with(
                ["/custom/path/systemctl", "--user", "status", "mmrelay.service"],
                check=True,
                capture_output=True,
                text=True,
            )


class TestServiceTemplateImprovements(unittest.TestCase):
    """Test cases for service template improvements."""

    @patch("builtins.print")
    def test_get_template_service_content_stderr_output(self, mock_print):
        """Test that error messages are printed to stderr."""
        from mmrelay.setup_utils import get_template_service_content

        # Mock all methods to fail
        with patch(
            "mmrelay.setup_utils.get_service_template_path", return_value=None
        ), patch(
            "importlib.resources.files", side_effect=ImportError("No module")
        ), patch(
            "mmrelay.setup_utils.get_template_service_path", return_value="/nonexistent"
        ):

            result = get_template_service_content()

            # Should return default template
            self.assertIn("MMRelay - Meshtastic <=> Matrix Relay", result)

            # Should print error messages to stderr
            mock_print.assert_any_call(
                "Using default service template", file=sys.stderr
            )

    def test_default_service_template_updated_description(self):
        """Test that default service template has updated description."""
        from mmrelay.setup_utils import get_template_service_content

        # Mock all methods to fail to force default template
        with patch(
            "mmrelay.setup_utils.get_service_template_path", return_value=None
        ), patch(
            "importlib.resources.files", side_effect=ImportError("No module")
        ), patch(
            "mmrelay.setup_utils.get_template_service_path", return_value="/nonexistent"
        ):

            result = get_template_service_content()

            # Should have updated description
            self.assertIn("Description=MMRelay - Meshtastic <=> Matrix Relay", result)
            self.assertNotIn("Description=A Meshtastic <=> Matrix Relay", result)

    def test_service_template_exec_start_path(self):
        """Test that service template uses env to find mmrelay."""
        from mmrelay.setup_utils import get_template_service_content

        # Mock all methods to fail to force default template
        with patch(
            "mmrelay.setup_utils.get_service_template_path", return_value=None
        ), patch(
            "importlib.resources.files", side_effect=ImportError("No module")
        ), patch(
            "mmrelay.setup_utils.get_template_service_path", return_value="/nonexistent"
        ):

            result = get_template_service_content()

            # Should use resolved executable path (better than hardcoded env)
            self.assertIn("ExecStart=", result)
            # Should contain mmrelay in some form
            self.assertIn("mmrelay", result)
            self.assertNotIn("ExecStart=%h/.local/bin/mmrelay", result)


class TestInstallServiceSystemctlUsage(unittest.TestCase):
    """Test cases for systemctl usage in install_service function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("mmrelay.setup_utils.SYSTEMCTL", "/test/systemctl")
    @patch("subprocess.run")
    @patch("builtins.input", side_effect=["y", "y"])  # Enable service, start service
    @patch("builtins.print")
    def test_install_service_uses_custom_systemctl_path(
        self, mock_print, mock_input, mock_run
    ):
        """Test that install_service uses the resolved systemctl path."""
        from mmrelay.setup_utils import install_service

        # Mock successful operations - create a proper mock result object
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "active"
        mock_run.return_value = mock_result

        # Mock other dependencies
        with patch("mmrelay.setup_utils.create_service_file", return_value=True), patch(
            "mmrelay.setup_utils.reload_daemon", return_value=True
        ), patch("mmrelay.setup_utils.read_service_file", return_value=None), patch(
            "mmrelay.setup_utils.is_service_enabled", return_value=False
        ), patch(
            "mmrelay.setup_utils.check_loginctl_available", return_value=False
        ), patch(
            "mmrelay.setup_utils.is_service_active", return_value=False
        ):

            install_service()

            # Should use custom systemctl path for enable and start
            enable_call = ["/test/systemctl", "--user", "enable", "mmrelay.service"]
            start_call = ["/test/systemctl", "--user", "start", "mmrelay.service"]

            # Check that the custom systemctl path was used
            mock_run.assert_any_call(enable_call, check=True)
            mock_run.assert_any_call(start_call, check=True)

    @patch("mmrelay.setup_utils.SYSTEMCTL", "/test/systemctl")
    @patch("builtins.input", side_effect=["y", "y"])  # Enable service, start service
    @patch("builtins.print")
    def test_install_service_handles_systemctl_error(self, mock_print, mock_input):
        """Test that install_service handles systemctl errors gracefully."""
        from mmrelay.setup_utils import install_service

        # Mock other dependencies
        with patch("mmrelay.setup_utils.create_service_file", return_value=True), patch(
            "mmrelay.setup_utils.reload_daemon", return_value=True
        ), patch("mmrelay.setup_utils.read_service_file", return_value=None), patch(
            "mmrelay.setup_utils.is_service_enabled", return_value=False
        ), patch(
            "mmrelay.setup_utils.check_loginctl_available", return_value=False
        ), patch(
            "mmrelay.setup_utils.is_service_active", return_value=False
        ), patch(
            "subprocess.run", return_value=MagicMock(returncode=1)
        ):

            result = install_service()

            # Should still complete (returns True even if systemctl operations fail)
            self.assertTrue(result)

            # Should print error messages
            mock_print.assert_called()


if __name__ == "__main__":
    unittest.main()
