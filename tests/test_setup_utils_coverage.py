#!/usr/bin/env python3
"""
Additional test suite for uncovered lines in setup utilities in MMRelay.

This file contains tests for previously uncovered code paths including:
- Rich progress display in wait_for_service_start
- loginctl not found in check_lingering_enabled
- OSError when enabling service in install_service
- OSError when starting service
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.setup_utils import (
    check_lingering_enabled,
    install_service,
    start_service,
    wait_for_service_start,
)


class TestWaitForServiceRich(unittest.TestCase):
    """Test cases for Rich progress display in wait_for_service_start."""

    def setUp(self):
        """Initialize test fixtures."""
        import mmrelay.runtime_utils

        # Mock not running as service by default
        self.original_is_running_as_service = (
            mmrelay.runtime_utils.is_running_as_service
        )

    def tearDown(self):
        """Restore original function."""
        import mmrelay.runtime_utils

        mmrelay.runtime_utils.is_running_as_service = (
            self.original_is_running_as_service
        )

    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("time.sleep")
    def test_wait_for_service_start_with_rich(self, mock_sleep, mock_active):
        """Test wait_for_service_start creates Rich progress when not running as service."""
        # Mock is_running_as_service to return False
        import mmrelay.runtime_utils

        mmrelay.runtime_utils.is_running_as_service = lambda: False

        # Ensure Rich is importable
        try:
            from rich.progress import Progress

            rich_available = True
        except ImportError:
            rich_available = False

        if rich_available:
            # Should create Rich progress display
            wait_for_service_start()

            # Should sleep 10 times (once per second)
            self.assertEqual(mock_sleep.call_count, 10)
        else:
            # Skip test if Rich not available
            self.skipTest("Rich not available")


class TestLingeringEnabledLoginctlNotFound(unittest.TestCase):
    """Test cases for check_lingering_enabled when loginctl not found."""

    @patch("shutil.which", return_value=None)
    def test_check_lingering_enabled_loginctl_not_found(self, mock_which):
        """Test check_lingering_enabled returns False when loginctl not found."""
        result = check_lingering_enabled()

        # Should return False when loginctl not found
        self.assertFalse(result)

        # Should have checked for loginctl
        mock_which.assert_called_once_with("loginctl")

    @patch("shutil.which")
    @patch("mmrelay.setup_utils.logger")
    def test_check_lingering_enabled_loginctl_which_error(
        self, mock_logger, mock_which
    ):
        """Test check_lingering_enabled handles OSError from shutil.which."""
        import subprocess

        mock_which.side_effect = OSError("command not found")

        result = check_lingering_enabled()

        # Should return False on OSError
        self.assertFalse(result)

        # Should log warning about error
        mock_logger.warning.assert_called()
        warning_call = mock_logger.warning.call_args[0][0]
        self.assertIn("check loginctl availability", warning_call)


class TestInstallServiceOSError(unittest.TestCase):
    """Test cases for install_service OSError handling when enabling service."""

    def setUp(self):
        """Create temporary directory for testing."""
        import mmrelay.setup_utils

        self.test_dir = tempfile.mkdtemp()
        self.test_service_path = Path(self.test_dir) / "mmrelay.service"

        # Store original values
        self.original_get_user_service_path = mmrelay.setup_utils.get_user_service_path
        self.original_is_service_enabled = mmrelay.setup_utils.is_service_enabled

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

        # Restore original values
        import mmrelay.setup_utils

        mmrelay.setup_utils.get_user_service_path = self.original_get_user_service_path
        mmrelay.setup_utils.is_service_enabled = self.original_is_service_enabled

    @patch(
        "mmrelay.setup_utils.get_template_service_content",
        return_value="[Unit]\nExecStart=cmd",
    )
    @patch("mmrelay.setup_utils.reload_daemon", return_value=True)
    @patch("mmrelay.setup_utils.get_user_service_path")
    @patch("mmrelay.setup_utils.check_loginctl_available", return_value=False)
    @patch("mmrelay.setup_utils.is_service_enabled", return_value=False)
    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("builtins.input", return_value="n")
    @patch("mmrelay.setup_utils.os.path.exists", return_value=False)
    @patch("subprocess.run")
    def test_install_service_enable_os_error(
        self,
        mock_subprocess,
        mock_exists,
        mock_input,
        mock_active,
        mock_enabled,
        mock_check_login,
        mock_reload,
        mock_template,
        mock_get_user_service_path,
    ):
        """Test install_service handles OSError when enabling service."""
        import mmrelay.setup_utils

        # Mock get_user_service_path
        mock_get_user_service_path.return_value = self.test_service_path

        # First subprocess.run is for enable command, second is for start check
        # Make enable fail with OSError
        def subprocess_side_effect(*args, **kwargs):
            if "enable" in args[0]:
                raise OSError("Permission denied")
            # Other calls succeed
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_subprocess.side_effect = subprocess_side_effect

        result = install_service()

        # Should still return True (best-effort, continues despite error)
        self.assertTrue(result)

        # Should have attempted to create service file and reload
        mock_template.assert_called_once()
        mock_reload.assert_called_once()

    @patch(
        "mmrelay.setup_utils.get_template_service_content",
        return_value="[Unit]\nExecStart=cmd",
    )
    @patch("mmrelay.setup_utils.reload_daemon", return_value=True)
    @patch("mmrelay.setup_utils.get_user_service_path")
    @patch("mmrelay.setup_utils.check_loginctl_available", return_value=False)
    @patch("mmrelay.setup_utils.is_service_enabled", return_value=False)
    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("builtins.input", return_value="n")
    @patch("mmrelay.setup_utils.os.path.exists", return_value=False)
    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.logger")
    def test_install_service_enable_os_error_logged(
        self,
        mock_logger,
        mock_subprocess,
        mock_exists,
        mock_input,
        mock_active,
        mock_enabled,
        mock_check_login,
        mock_reload,
        mock_template,
        mock_get_user_service_path,
    ):
        """Test install_service logs OSError when enabling service."""
        import mmrelay.setup_utils

        # Mock get_user_service_path
        mock_get_user_service_path.return_value = self.test_service_path

        # Make enable fail with OSError
        def subprocess_side_effect(*args, **kwargs):
            if "enable" in args[0]:
                raise OSError("Permission denied")
            # Other calls succeed
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_subprocess.side_effect = subprocess_side_effect

        install_service()

        # Should log OSError
        mock_logger.exception.assert_called()
        error_call = mock_logger.exception.call_args[0][0]
        self.assertIn("Error enabling service", error_call)


class TestStartServiceOSError(unittest.TestCase):
    """Test cases for start_service OSError handling."""

    @patch("mmrelay.setup_utils.SYSTEMCTL", "/usr/bin/systemctl")
    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.logger")
    def test_start_service_os_error(self, mock_logger, mock_run, mock_systemctl):
        """Test start_service handles OSError gracefully."""
        # Make subprocess.run raise OSError
        mock_run.side_effect = OSError("systemd not running")

        result = start_service()

        # Should return False on OSError
        self.assertFalse(result)

        # Should log the error
        mock_logger.exception.assert_called()
        error_call = mock_logger.exception.call_args[0][0]
        self.assertIn("Error starting mmrelay service", error_call)

    @patch("mmrelay.setup_utils.SYSTEMCTL", "/usr/bin/systemctl")
    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.logger")
    def test_start_service_os_error_logged_correctly(
        self, mock_logger, mock_run, mock_systemctl
    ):
        """Test start_service logs OSError with correct message."""
        # Make subprocess.run raise OSError
        test_error = OSError("Permission denied")
        mock_run.side_effect = test_error

        result = start_service()

        # Should return False
        self.assertFalse(result)

        # Should log the OSError
        mock_logger.exception.assert_called_once()
        error_message = mock_logger.exception.call_args[0][0]
        self.assertEqual(error_message, "Error starting mmrelay service")


if __name__ == "__main__":
    unittest.main()
