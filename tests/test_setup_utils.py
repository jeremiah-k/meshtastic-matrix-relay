#!/usr/bin/env python3
"""
Test suite for setup utilities in MMRelay.

Tests the service installation and management functionality including:
- Service file creation and template handling
- Systemd user service management
- Executable path detection
- Service status checking and control
- User lingering configuration
- Service file update detection
"""

import os
import subprocess  # nosec B404 - Used for controlled test environment operations
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, mock_open, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.setup_utils import (
    SYSTEMCTL,
    check_lingering_enabled,
    create_service_file,
    enable_lingering,
    get_executable_path,
    get_resolved_exec_cmd,
    get_resolved_exec_start,
    get_template_service_content,
    get_template_service_path,
    get_user_service_path,
    install_service,
    is_service_active,
    is_service_enabled,
    print_service_commands,
    read_service_file,
    reload_daemon,
    service_exists,
    service_needs_update,
    show_service_status,
    start_service,
    wait_for_service_start,
)


class TestSetupUtils(unittest.TestCase):
    """Test cases for setup utilities."""

    def setUp(self):
        """
        Creates a temporary directory and service file path for test isolation.
        """
        # Create temporary directory for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_service_path = Path(self.test_dir) / "mmrelay.service"

    def tearDown(self):
        """
        Remove the temporary directory and its contents after each test to clean up the test environment.
        """
        # Clean up temporary files
        import shutil

        shutil.rmtree(self.test_dir, ignore_errors=True)

    @patch("shutil.which")
    def test_get_executable_path_found(self, mock_which):
        """Test getting executable path when mmrelay is found in PATH."""
        mock_which.return_value = "/usr/local/bin/mmrelay"

        path = get_executable_path()

        self.assertEqual(path, "/usr/local/bin/mmrelay")
        mock_which.assert_called_once_with("mmrelay")

    @patch("shutil.which")
    def test_get_executable_path_not_found(self, mock_which):
        """Test getting executable path when mmrelay is not found in PATH."""
        mock_which.return_value = None

        path = get_executable_path()

        self.assertEqual(path, f"{sys.executable} -m mmrelay")

    @patch("shutil.which")
    def test_get_resolved_exec_cmd_found(self, mock_which):
        """Test get_resolved_exec_cmd when mmrelay binary is found."""
        mock_which.return_value = "/usr/local/bin/mmrelay"

        result = get_resolved_exec_cmd()

        self.assertEqual(result, "/usr/local/bin/mmrelay")

    @patch("shutil.which")
    def test_get_resolved_exec_cmd_not_found(self, mock_which):
        """Test get_resolved_exec_cmd when mmrelay binary is not found."""
        mock_which.return_value = None

        result = get_resolved_exec_cmd()

        self.assertEqual(result, f"{sys.executable} -m mmrelay")

    @patch("shutil.which")
    def test_get_resolved_exec_cmd_with_spaces(self, mock_which):
        """Test get_resolved_exec_cmd quotes paths with spaces."""
        mock_which.return_value = None

        with patch("sys.executable", "/path with spaces/python"):
            result = get_resolved_exec_cmd()
            self.assertEqual(result, '"/path with spaces/python" -m mmrelay')

    @patch("shutil.which")
    def test_get_resolved_exec_start_found(self, mock_which):
        """Test get_resolved_exec_start when mmrelay binary is found."""
        mock_which.return_value = "/usr/local/bin/mmrelay"

        result = get_resolved_exec_start()

        expected = "ExecStart=/usr/local/bin/mmrelay --config %h/.mmrelay/config.yaml --logfile %h/.mmrelay/logs/mmrelay.log"
        self.assertEqual(result, expected)

    @patch("shutil.which")
    def test_get_resolved_exec_start_not_found(self, mock_which):
        """Test get_resolved_exec_start when mmrelay binary is not found."""
        mock_which.return_value = None

        result = get_resolved_exec_start()

        expected = f"ExecStart={sys.executable} -m mmrelay --config %h/.mmrelay/config.yaml --logfile %h/.mmrelay/logs/mmrelay.log"
        self.assertEqual(result, expected)

    @patch("builtins.print")
    def test_print_service_commands(self, mock_print):
        """Test that print_service_commands prints the correct commands."""
        print_service_commands()

        # Verify all expected commands were printed
        expected_calls = [
            call("  systemctl --user start mmrelay.service    # Start the service"),
            call("  systemctl --user stop mmrelay.service     # Stop the service"),
            call("  systemctl --user restart mmrelay.service  # Restart the service"),
            call("  systemctl --user status mmrelay.service   # Check service status"),
        ]
        mock_print.assert_has_calls(expected_calls)

    @patch("mmrelay.setup_utils.is_service_active")
    @patch("time.sleep")
    def test_wait_for_service_start_early_completion(self, mock_sleep, mock_is_active):
        """Test wait_for_service_start completes early when service becomes active."""
        # Mock service becoming active on first check (i=5)
        mock_is_active.return_value = True

        wait_for_service_start()

        # Should have called sleep 6 times (iterations 0-5)
        self.assertEqual(mock_sleep.call_count, 6)
        # Should have checked service status once (when i=5)
        self.assertEqual(mock_is_active.call_count, 1)

    @patch("mmrelay.setup_utils.is_service_active")
    @patch("time.sleep")
    def test_wait_for_service_start_full_duration(self, mock_sleep, mock_is_active):
        """Test wait_for_service_start runs full 10 seconds when service doesn't start."""
        # Mock service never becoming active
        mock_is_active.return_value = False

        wait_for_service_start()

        # Should have called sleep 10 times (full duration)
        self.assertEqual(mock_sleep.call_count, 10)
        # Should have checked service status 5 times (iterations 5-9)
        self.assertEqual(mock_is_active.call_count, 5)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.print_service_commands")
    @patch("builtins.input")
    def test_install_service_update_cancelled_by_user(
        self, mock_input, mock_print_commands, mock_needs_update, mock_read_service
    ):
        """Test install_service when user cancels update."""
        mock_read_service.return_value = "existing service content"
        mock_needs_update.return_value = (True, "Executable path changed")
        mock_input.return_value = "n"

        result = install_service()

        self.assertTrue(result)
        mock_print_commands.assert_called_once()
        mock_input.assert_called_once_with(
            "Do you want to update the service file? (y/n): "
        )

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.print_service_commands")
    @patch("builtins.input")
    def test_install_service_update_cancelled_by_eof(
        self, mock_input, mock_print_commands, mock_needs_update, mock_read_service
    ):
        """Test install_service when user input is cancelled with EOF."""
        mock_read_service.return_value = "existing service content"
        mock_needs_update.return_value = (True, "Executable path changed")
        mock_input.side_effect = EOFError()

        result = install_service()

        self.assertTrue(result)
        mock_print_commands.assert_called_once()

    @patch("mmrelay.setup_utils.get_template_service_path")
    @patch("os.path.exists")
    @patch("builtins.open", side_effect=IOError("File read error"))
    @patch("sys.stderr")
    def test_get_template_service_content_file_read_error(
        self, mock_stderr, mock_open, mock_exists, mock_get_path
    ):
        """Test get_template_service_content handles file read errors gracefully."""
        mock_get_path.return_value = "/path/to/template"
        mock_exists.return_value = True

        # This should fall back to importlib.resources or default template
        _ = get_template_service_content()

        # Should have attempted to read the file and caught the error
        mock_open.assert_called()
        # Should have printed error message
        mock_stderr.write.assert_called()

    @patch("mmrelay.setup_utils.Path.home")
    def test_get_user_service_path(self, mock_home):
        """
        Test that get_user_service_path returns the correct path to the user's mmrelay systemd service file.
        """
        mock_home.return_value = Path("/home/user")

        service_path = get_user_service_path()

        expected_path = Path("/home/user/.config/systemd/user/mmrelay.service")
        self.assertEqual(service_path, expected_path)

    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_service_exists_true(self, mock_get_path):
        """Test service_exists when service file exists."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_get_path.return_value = mock_path

        result = service_exists()

        self.assertTrue(result)

    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_service_exists_false(self, mock_get_path):
        """Test service_exists when service file doesn't exist."""
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_get_path.return_value = mock_path

        result = service_exists()

        self.assertFalse(result)

    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_read_service_file_exists(self, mock_get_path):
        """
        Test that `read_service_file` returns the service file content when the file exists.
        """
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "service content"
        mock_get_path.return_value = mock_path

        content = read_service_file()

        self.assertEqual(content, "service content")

    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_read_service_file_not_exists(self, mock_get_path):
        """Test reading service file when it doesn't exist."""
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_get_path.return_value = mock_path

        content = read_service_file()

        self.assertIsNone(content)

    @patch("os.path.exists")
    def test_get_template_service_path_found(self, mock_exists):
        """
        Test that get_template_service_path returns the template path when the service file exists.
        """
        # Mock the first path to exist
        mock_exists.side_effect = lambda path: "mmrelay.service" in path

        path = get_template_service_path()

        self.assertIsNotNone(path)
        self.assertIn("mmrelay.service", path)

    @patch("os.path.exists")
    def test_get_template_service_path_not_found(self, mock_exists):
        """Test getting template service path when file is not found."""
        mock_exists.return_value = False

        path = get_template_service_path()

        self.assertIsNone(path)

    @patch("mmrelay.setup_utils.get_service_template_path")
    @patch("os.path.exists")
    def test_get_template_service_content_from_file(
        self, mock_exists, mock_get_template_path
    ):
        """
        Test that the service template content is correctly read from a file when the template file exists.
        """
        mock_get_template_path.return_value = "/path/to/template"
        mock_exists.return_value = True

        with patch("builtins.open", mock_open(read_data="template content")):
            content = get_template_service_content()

        self.assertEqual(content, "template content")

    @patch("mmrelay.setup_utils.get_service_template_path")
    @patch("importlib.resources.files")
    def test_get_template_service_content_from_resources(
        self, mock_files, mock_get_template_path
    ):
        """
        Test that the service template content is retrieved from importlib.resources when the template file is not found.
        """
        mock_get_template_path.return_value = None

        # Mock importlib.resources
        mock_resource = MagicMock()
        mock_resource.read_text.return_value = "resource content"
        mock_files.return_value.joinpath.return_value = mock_resource

        content = get_template_service_content()

        self.assertEqual(content, "resource content")

    @patch("mmrelay.setup_utils.get_service_template_path")
    @patch("importlib.resources.files")
    def test_get_template_service_content_fallback(
        self, mock_files, mock_get_template_path
    ):
        """
        Test that get_template_service_content returns the default template when both the template file and resource are unavailable.
        """
        mock_get_template_path.return_value = None
        mock_files.side_effect = FileNotFoundError()

        content = get_template_service_content()

        # Should return default template
        self.assertIn("[Unit]", content)
        self.assertIn("Description=MMRelay - Meshtastic <=> Matrix Relay", content)

    @patch("subprocess.run")
    def test_is_service_active_true(self, mock_run):
        """
        Test that is_service_active returns True when the service is reported as active.
        """
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout.strip.return_value = "active"
        mock_run.return_value = mock_result

        result = is_service_active()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [SYSTEMCTL, "--user", "is-active", "mmrelay.service"],
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_is_service_active_false(self, mock_run):
        """Test is_service_active when service is not active."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        result = is_service_active()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_is_service_active_exception(self, mock_run):
        """
        Test that is_service_active returns False when a subprocess exception occurs.
        """
        mock_run.side_effect = OSError("Command not found")

        result = is_service_active()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_is_service_enabled_true(self, mock_run):
        """Test is_service_enabled when service is enabled."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout.strip.return_value = "enabled"
        mock_run.return_value = mock_result

        result = is_service_enabled()

        self.assertTrue(result)

    @patch("subprocess.run")
    def test_is_service_enabled_false(self, mock_run):
        """Test is_service_enabled when service is not enabled."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_run.return_value = mock_result

        result = is_service_enabled()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_start_service_success(self, mock_run):
        """
        Test that the service is started successfully when the systemctl command completes without error.
        """
        mock_run.return_value.returncode = 0

        result = start_service()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [SYSTEMCTL, "--user", "start", "mmrelay.service"], check=True
        )

    @patch("subprocess.run")
    def test_start_service_failure(self, mock_run):
        """
        Test that starting the service returns False when the systemctl command fails.
        """
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")

        result = start_service()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_reload_daemon_success(self, mock_run):
        """
        Test that the systemd user daemon reloads successfully and returns True.
        """
        mock_run.return_value.returncode = 0

        result = reload_daemon()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [SYSTEMCTL, "--user", "daemon-reload"], check=True
        )

    @patch("subprocess.run")
    def test_reload_daemon_failure(self, mock_run):
        """
        Test that `reload_daemon` returns False when reloading the systemd user daemon fails due to a subprocess error.
        """
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")

        result = reload_daemon()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_check_lingering_enabled_true(self, mock_run):
        """
        Test that check_lingering_enabled returns True when user lingering is enabled.
        """
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Linger=yes"

        with patch.dict(os.environ, {"USER": "testuser"}):
            result = check_lingering_enabled()

        self.assertTrue(result)

    @patch("subprocess.run")
    def test_check_lingering_enabled_false(self, mock_run):
        """
        Test that check_lingering_enabled returns False when user lingering is disabled.
        """
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Linger=no"

        with patch.dict(os.environ, {"USER": "testuser"}):
            result = check_lingering_enabled()

        self.assertFalse(result)

    @patch("subprocess.run")
    def test_enable_lingering_success(self, mock_run):
        """
        Test that enabling user lingering returns True when the command succeeds.
        """
        mock_run.return_value.returncode = 0

        with patch.dict(os.environ, {"USER": "testuser"}):
            result = enable_lingering()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["sudo", "loginctl", "enable-linger", "testuser"],
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_enable_lingering_failure(self, mock_run):
        """Test enabling lingering with failure."""
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Permission denied"

        with patch.dict(os.environ, {"USER": "testuser"}):
            result = enable_lingering()

        self.assertFalse(result)

    @patch("mmrelay.setup_utils.get_executable_path")
    @patch("mmrelay.setup_utils.get_template_service_content")
    @patch("mmrelay.setup_utils.get_user_service_path")
    @patch("pathlib.Path.home")
    def test_create_service_file_success(
        self, mock_home, mock_get_path, mock_get_content, mock_get_executable
    ):
        """
        Test that the service file is created successfully when all dependencies return valid values.
        """
        mock_get_executable.return_value = "/usr/local/bin/mmrelay"
        mock_get_content.return_value = "template content"

        # Mock the home directory path
        mock_home_path = MagicMock()
        mock_logs_dir = MagicMock()
        mock_home_path.__truediv__.return_value.__truediv__.return_value = mock_logs_dir
        mock_home.return_value = mock_home_path

        mock_path = MagicMock()
        mock_path.parent.mkdir = MagicMock()
        mock_path.write_text = MagicMock()
        mock_get_path.return_value = mock_path

        result = create_service_file()

        self.assertTrue(result)
        mock_path.write_text.assert_called_once()

    @patch("shutil.which")
    @patch("mmrelay.setup_utils.get_template_service_content")
    @patch("mmrelay.setup_utils.get_user_service_path")
    @patch("builtins.print")
    def test_create_service_file_no_executable(
        self, mock_print, mock_get_path, mock_get_content, mock_which
    ):
        """
        Test that creating a service file succeeds using python -m mmrelay fallback when mmrelay binary is not found.
        """
        # Mock mmrelay not found in PATH
        mock_which.return_value = None

        # Mock template content with placeholder
        mock_get_content.return_value = """[Unit]
Description=Test Service
[Service]
ExecStart=%h/meshtastic-matrix-relay/.pyenv/bin/python %h/meshtastic-matrix-relay/main.py --config %h/.mmrelay/config/config.yaml
"""

        # Mock service path
        mock_path = MagicMock()
        mock_get_path.return_value = mock_path

        result = create_service_file()

        # Should succeed with fallback
        self.assertTrue(result)

        # Should print fallback message
        mock_print.assert_any_call(
            "Warning: Could not find mmrelay executable in PATH. Using current Python interpreter.",
            file=sys.stderr,
        )

        # Should write service content with python -m mmrelay
        written_content = mock_path.write_text.call_args[0][0]
        self.assertIn("python -m mmrelay", written_content)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_executable_path")
    def test_service_needs_update_no_existing(
        self, mock_get_executable, mock_read_service
    ):
        """
        Test that service_needs_update returns True with the correct reason when the service file does not exist.
        """
        mock_read_service.return_value = None

        needs_update, reason = service_needs_update()

        self.assertTrue(needs_update)
        self.assertEqual(reason, "No existing service file found")

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_executable_path")
    @patch("mmrelay.setup_utils.get_template_service_path")
    def test_service_needs_update_executable_changed(
        self, mock_get_template, mock_get_executable, mock_read_service
    ):
        """
        Test that service_needs_update returns True when the executable path in the service file differs from the current executable.

        Verifies that the function detects when the service file's ExecStart path does not match the current executable and provides an appropriate reason.
        """
        mock_read_service.return_value = "ExecStart=/old/path/mmrelay"
        mock_get_executable.return_value = "/new/path/mmrelay"
        mock_get_template.return_value = "/path/to/template"

        needs_update, reason = service_needs_update()

        self.assertTrue(needs_update)
        self.assertIn("does not use an acceptable executable", reason)

    @patch("subprocess.run")
    def test_show_service_status_success(self, mock_run):
        """Test showing service status successfully."""
        mock_run.return_value.stdout = "Service is running"

        result = show_service_status()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [SYSTEMCTL, "--user", "status", "mmrelay.service"],
            check=True,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_show_service_status_failure(self, mock_run):
        """
        Test that show_service_status returns False when systemctl command fails.
        """
        mock_run.side_effect = subprocess.CalledProcessError(1, "systemctl")

        result = show_service_status()

        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
