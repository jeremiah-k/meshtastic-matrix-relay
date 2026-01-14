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
    log_service_commands,
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
    def test_log_service_commands(self, mock_print):
        """Test that log_service_commands prints to stdout (not logger)."""
        log_service_commands()

        # Verify all expected commands were printed
        expected_calls = [
            call("  systemctl --user start mmrelay.service    # Start of service"),
            call("  systemctl --user stop mmrelay.service     # Stop of service"),
            call("  systemctl --user restart mmrelay.service  # Restart of service"),
            call(
                "  systemctl --user status mmrelay.service   # Check of service status"
            ),
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
    @patch("mmrelay.setup_utils.log_service_commands")
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
    @patch("mmrelay.setup_utils.log_service_commands")
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
    @patch("mmrelay.setup_utils.logger")
    def test_get_template_service_content_file_read_error(
        self, mock_logger, mock_open, mock_exists, mock_get_path
    ):
        """Test get_template_service_content handles file read errors gracefully."""
        mock_get_path.return_value = "/path/to/template"
        mock_exists.return_value = True

        # This should fall back to importlib.resources or default template
        _ = get_template_service_content()

        # Should have attempted to read the file and caught the error
        mock_open.assert_called()
        # Should have logged error message
        mock_logger.exception.assert_called()

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
        self.assertIn("mmrelay.service", path)  # type: ignore[arg-type]

    @patch("os.path.exists")
    def test_get_template_service_path_not_found(self, mock_exists):
        """Test getting template service path when file is not found."""
        mock_exists.return_value = False

        path = get_template_service_path()

        self.assertIsNone(path)

    @patch("os.path.exists")
    def test_get_template_service_path_fallback_paths(self, mock_exists):
        """Test that get_template_service_path checks all fallback paths."""

        def exists_side_effect(path):
            """
            Return True if the given path string contains "share".

            Used as a helper side-effect function (e.g., for mocking os.path.exists) to simulate that only paths containing the substring "share" exist.

            Parameters:
                path (str): The path to test.

            Returns:
                bool: True when "share" is a substring of `path`, otherwise False.
            """
            return "share" in path

        mock_exists.side_effect = exists_side_effect

        path = get_template_service_path()

        self.assertIsNotNone(path)
        self.assertTrue(path.endswith("share/mmrelay/mmrelay.service"))  # type: ignore[optional-attr]

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

    @patch("shutil.which")
    def test_check_loginctl_not_available(self, mock_which):
        """Test that check_loginctl_available returns False when loginctl is not on PATH."""
        mock_which.return_value = None

        from mmrelay.setup_utils import check_loginctl_available

        result = check_loginctl_available()

        self.assertFalse(result)

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
    @patch("shutil.which")
    def test_enable_lingering_success(self, mock_which, mock_run):
        """
        Test that enabling user lingering returns True when the command succeeds.
        """
        mock_which.side_effect = lambda cmd: cmd
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
    @patch("mmrelay.setup_utils.logger")
    def test_create_service_file_no_executable(
        self, mock_logger, mock_get_path, mock_get_content, mock_which
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

        # Should log fallback message
        mock_logger.warning.assert_any_call(
            "Could not find mmrelay executable in PATH. Using current Python interpreter."
        )

        # Should write service content with python -m mmrelay
        written_content = mock_path.write_text.call_args[0][0]
        self.assertIn("-m mmrelay", written_content)

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

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path")
    @patch("os.path.getmtime")
    def test_service_needs_update_mtime(
        self, mock_getmtime, mock_get_template, mock_read_service
    ):
        """Test that service_needs_update returns True when the template is newer."""
        mock_read_service.return_value = (
            f"ExecStart={sys.executable} -m mmrelay\nEnvironment=PATH=%h/.local/bin"
        )
        mock_get_template.return_value = "/path/to/template"
        mock_getmtime.side_effect = [2, 1]  # template_mtime > service_mtime

        with patch("os.path.exists", return_value=True):
            needs_update, reason = service_needs_update()

        self.assertTrue(needs_update)
        self.assertEqual(
            reason, "Template service file is newer than installed service file"
        )

    @patch("subprocess.run")
    def test_show_service_status_success(self, mock_run):
        """Test showing service status successfully."""
        mock_run.return_value.stdout = "Service is running"

        result = show_service_status()

        self.assertTrue(result)
        mock_run.assert_called_once_with(
            [SYSTEMCTL, "--user", "status", "mmrelay.service"],
            check=False,
            capture_output=True,
            text=True,
        )

    @patch("subprocess.run")
    def test_show_service_status_failure(self, mock_run):
        """
        Test that show_service_status returns False when systemctl cannot run.
        """
        mock_run.side_effect = OSError("systemctl unavailable")

        result = show_service_status()

        self.assertFalse(result)

    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.runtime_utils.is_running_as_service")
    def test_wait_for_service_start_running_as_service(
        self, mock_is_running_as_service, mock_is_service_active
    ):
        """
        Test wait_for_service_start when running as service (lines 141-144).
        """
        # Mock running as service
        mock_is_running_as_service.return_value = True

        # Mock service becomes active after 6 seconds
        call_counter = {"count": 0}

        def mock_service_active_side_effect():
            # This will be called multiple times, return False first, then True
            """
            Return False on the first four calls and True thereafter.

            This side-effect function increments the outer `call_counter["count"]` each time it's invoked and returns True once the count reaches 5 (used to simulate a service becoming active after several checks).
            """
            call_counter["count"] += 1
            return call_counter["count"] >= 5

        mock_is_service_active.side_effect = mock_service_active_side_effect

        # Call the function - should complete early when service becomes active
        wait_for_service_start()

        # Verify is_service_active was called multiple times
        self.assertGreaterEqual(mock_is_service_active.call_count, 5)

    @patch("mmrelay.setup_utils.get_service_template_path")
    @patch("importlib.resources.files")
    @patch("builtins.open", new_callable=mock_open)
    @patch("mmrelay.setup_utils.get_template_service_path")
    @patch("mmrelay.setup_utils.get_resolved_exec_start")
    def test_get_template_service_content_fallback_to_default(
        self,
        mock_get_exec_start,
        mock_get_template_path,
        _mock_open,
        mock_resources,
        mock_get_helper_path,
    ):
        """
        Test get_template_service_content fallback error handling (lines 274-284).
        """
        # Mock all attempts to get template to fail
        mock_get_helper_path.return_value = None
        mock_resources.side_effect = ImportError("No module named 'mmrelay.tools'")
        mock_get_template_path.return_value = None
        mock_get_exec_start.return_value = "ExecStart=/usr/bin/python -m mmrelay"

        # Call the function
        result = get_template_service_content()

        # Should return default template
        self.assertIn("[Unit]", result)
        self.assertIn("Description=MMRelay", result)
        self.assertIn("ExecStart=/usr/bin/python -m mmrelay", result)

    @patch("mmrelay.setup_utils.get_template_service_content")
    @patch("mmrelay.setup_utils.get_executable_path")
    @patch("pathlib.Path.mkdir")
    @patch("pathlib.Path.write_text")
    def test_create_service_file_no_template(
        self, mock_write_text, mock_mkdir, mock_get_executable, mock_get_template
    ):
        """
        Test create_service_file when get_template_service_content returns None (lines 374-375).
        """
        # Mock template content to return None (error case)
        mock_get_template.return_value = None
        mock_get_executable.return_value = "/usr/bin/python -m mmrelay"

        # Call the function
        result = create_service_file()

        # Should return False due to missing template
        self.assertFalse(result)
        # write_text should not be called
        mock_write_text.assert_not_called()
        assert mock_mkdir.called

    @patch("os.path.exists")
    @patch("mmrelay.setup_utils.read_service_file")
    def test_service_needs_update_missing_path_environment(
        self, mock_read_service, mock_exists
    ):
        """
        Verifies that service_needs_update flags an update when the service file's PATH environment is missing common user-bin locations.

        Sets up a service file whose ExecStart uses the current Python interpreter (via `-m mmrelay`), mocks a template path and a located mmrelay executable, and asserts that service_needs_update returns `True` with a reason mentioning that the service PATH does not include common user-bin locations.
        """
        # Mock existing service file without proper PATH environment
        mock_exists.return_value = True
        mock_read_service.return_value = """[Unit]
    Description=MMRelay Service
    
    [Service]
    ExecStart={} -m mmrelay
    Restart=on-failure
    
    [Install]
    WantedBy=default.target
    """.format(
            sys.executable
        )

        # Mock template path and acceptable executables
        with (
            patch("mmrelay.setup_utils.get_template_service_path") as mock_get_template,
            patch("shutil.which") as mock_which,
            patch("mmrelay.setup_utils._quote_if_needed") as mock_quote,
        ):
            mock_get_template.return_value = "/template/path"
            mock_which.return_value = "/usr/bin/mmrelay"
            mock_quote.side_effect = lambda x: x  # No quoting needed

            result, reason = service_needs_update()

            # Should need update due to missing PATH environment
            self.assertTrue(result)
            self.assertIn(
                "Service PATH does not include common user-bin locations", reason
            )

    @patch.dict(os.environ, {}, clear=True)
    @patch("getpass.getuser")
    @patch("shutil.which")
    def test_check_lingering_enabled_no_username(self, mock_which, mock_getpass):
        """
        Test check_lingering_enabled when username cannot be determined (lines 549-556).
        """
        # Mock environment with no USER/USERNAME and getpass returns empty string
        mock_getpass.return_value = ""
        mock_which.return_value = "/usr/bin/loginctl"

        result = check_lingering_enabled()

        # Should return False when username cannot be determined
        self.assertFalse(result)

    @patch.dict(os.environ, {}, clear=True)
    @patch("subprocess.run")
    def test_enable_lingering_no_username(self, mock_subprocess):
        """
        Verify enable_lingering returns False and logs an error when the current username cannot be determined.

        This test stubs getpass to return an empty username and ensures enable_lingering:
        - does not attempt to enable lingering for a user,
        - returns False,
        - logs an error message indicating the username could not be determined.
        """
        # Mock subprocess to prevent actual execution
        mock_subprocess.return_value = subprocess.CompletedProcess(
            args=["sudo", "loginctl", "enable-linger", "test"], returncode=0
        )

        # Mock import and getuser call
        with (
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("getpass.getuser", return_value=""),
        ):
            result = enable_lingering()

            # Should return False when username cannot be determined
            self.assertFalse(result)
            # Should log error message
            mock_logger.error.assert_any_call(
                "Error enabling lingering: could not determine current user"
            )

    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_no_update_needed(
        self, mock_get_path, mock_read_service, mock_needs_update
    ):
        """
        Verify install_service returns True and logs a "no update needed" message when a user service file already exists and service_needs_update reports no update required.
        """
        # Mock existing service with no update needed
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (False, "Service is up to date")

        with (
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("builtins.input", return_value="n"),
        ):
            result = install_service()

            # Should complete successfully
            self.assertTrue(result)
            # Should log that no update is needed
            mock_logger.info.assert_any_call(
                "No update needed for the service file: %s", "Service is up to date"
            )

    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_lingering_setup_cancelled(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
    ):
        """
        Test install_service lingering setup user interaction (lines 683-685, 687-691, 707-709).
        """
        # Mock service exists but no update needed (to skip service update prompt)
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = True
        mock_lingering_enabled.return_value = False

        # Mock user input to cancel lingering setup
        with (
            patch("builtins.input", side_effect=EOFError()),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.create_service_file") as mock_create,
            patch("mmrelay.setup_utils.reload_daemon") as mock_reload,
            patch("mmrelay.setup_utils.is_service_enabled") as mock_enabled,
            patch("mmrelay.setup_utils.is_service_active") as mock_active,
        ):
            mock_create.return_value = True
            mock_reload.return_value = True
            mock_enabled.return_value = False
            mock_active.return_value = False

            result = install_service()

            # Should complete successfully
            self.assertTrue(result)
            mock_logger.info.assert_any_call(
                "\nInput cancelled. Skipping lingering setup."
            )

    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_enable_cancelled(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
    ):
        """
        Test install_service enable service user interaction (lines 701-703, 714).
        """
        # Mock service setup
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = False

        # Mock user input to cancel service enable
        with (
            patch("builtins.input", side_effect=EOFError()),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.is_service_active") as mock_active,
        ):
            mock_active.return_value = False

            result = install_service()

            # Should complete successfully
            self.assertTrue(result)
            # Should log that service enable was skipped
            mock_logger.info.assert_any_call(
                "\nInput cancelled. Skipping service enable."
            )

    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_restart_cancelled(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
    ):
        """
        Test install_service restart service user interaction (lines 721-743).
        """
        # Mock service setup - service is already running
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = True

        # Mock user input to cancel service restart
        with (
            patch("builtins.input", side_effect=EOFError()),
            patch("mmrelay.setup_utils.logger") as mock_logger,
        ):
            result = install_service()

            # Should complete successfully
            self.assertTrue(result)
            # Should log that service restart was skipped
            mock_logger.info.assert_any_call(
                "\nInput cancelled. Skipping service restart."
            )

    @patch("mmrelay.setup_utils.start_service")
    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_start_cancelled(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
        mock_start_service,
    ):
        """
        Verify install_service completes and logs that service start was skipped when the user cancels the start prompt.

        Sets up mocks representing an existing, enabled but inactive per-user service, simulates the user cancelling the start prompt by raising EOFError, and asserts that install_service() returns True and that logger.info was called with the cancellation message.
        """
        # Mock service setup - service is not running
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = False
        mock_start_service.return_value = True

        # Mock user input to cancel service start
        with (
            patch("builtins.input", side_effect=EOFError()),
            patch("mmrelay.setup_utils.logger") as mock_logger,
        ):
            result = install_service()

            # Should complete successfully
            self.assertTrue(result)
            # Should log that service start was skipped
            mock_logger.info.assert_any_call(
                "\nInput cancelled. Skipping service start."
            )

    @patch("subprocess.run")
    def test_show_service_status_os_error(self, mock_run):
        """
        Test show_service_status OSError handling (lines 817-819).
        """
        # Mock subprocess.run to raise OSError
        mock_run.side_effect = OSError("No such file or directory")

        result = show_service_status()

        # Should return False on OSError
        self.assertFalse(result)

    @patch("os.path.exists")
    @patch("mmrelay.setup_utils.logger")
    def test_get_template_service_path_not_found_prints_warning(
        self, mock_logger, mock_exists
    ):
        """Test that a warning is logged when the template service path is not found."""
        mock_exists.return_value = False

        path = get_template_service_path()

        self.assertIsNone(path)
        self.assertTrue(
            any(
                "Could not find mmrelay.service in any of these locations:"
                in call_args.args[0]
                for call_args in mock_logger.warning.call_args_list
            )
        )

    @patch("mmrelay.setup_utils.read_service_file", return_value=None)
    @patch("mmrelay.setup_utils.service_needs_update", return_value=(True, "reason"))
    @patch("mmrelay.setup_utils.create_service_file", return_value=False)
    def test_install_service_create_fails(
        self, _mock_create, _mock_needs_update, _mock_read
    ):
        """Test install_service when create_service_file fails."""
        result = install_service()
        self.assertFalse(result)

    @patch("mmrelay.setup_utils.read_service_file", return_value=None)
    @patch("mmrelay.setup_utils.service_needs_update", return_value=(True, "reason"))
    @patch("mmrelay.setup_utils.create_service_file", return_value=True)
    @patch("mmrelay.setup_utils.reload_daemon", return_value=False)
    @patch("mmrelay.setup_utils.logger")
    def test_install_service_reload_fails(
        self, mock_logger, _mock_reload, _mock_create, _mock_needs_update, _mock_read
    ):
        """Test install_service when reload_daemon fails."""
        with patch("builtins.input", return_value="y"):
            result = install_service()
        self.assertTrue(result)  # it should still succeed, but log a warning
        mock_logger.warning.assert_any_call(
            "Failed to reload systemd daemon. You may need to run 'systemctl --user daemon-reload' manually."
        )

    @patch("subprocess.run", side_effect=OSError("OS error"))
    def test_check_lingering_enabled_os_error(self, _mock_run):
        """Test check_lingering_enabled with OSError."""
        with patch.dict(os.environ, {"USER": "testuser"}):
            result = check_lingering_enabled()
        self.assertFalse(result)

    @patch("subprocess.run", side_effect=OSError("OS error"))
    def test_enable_lingering_os_error(self, _mock_run):
        """Test enable_lingering with OSError."""
        with patch.dict(os.environ, {"USER": "testuser"}):
            result = enable_lingering()
        self.assertFalse(result)

    @patch("shutil.which")
    def test_enable_lingering_loginctl_not_found(self, mock_which):
        """Test enable_lingering when loginctl is not found (lines 613-614)."""
        mock_which.return_value = None
        with patch.dict(os.environ, {"USER": "testuser"}):
            result = enable_lingering()
        self.assertFalse(result)

    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_update_successfully(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
        _mock_run,
        _mock_show_status,
        _mock_wait,
    ):
        """Test install_service when service file is updated successfully (lines 683-684)."""
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = "[Unit]\nDescription=Test Service\n"
        mock_needs_update.return_value = (True, "Update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = False

        with (
            patch("builtins.input", return_value="y"),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.create_service_file") as mock_create,
            patch("mmrelay.setup_utils.reload_daemon"),
        ):
            mock_create.return_value = True
            result = install_service()

            self.assertTrue(result)
            mock_logger.info.assert_any_call("Service file updated successfully")

    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_restart_service_successfully(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
        _mock_run,
        mock_show_status,
        mock_wait,
    ):
        """Test install_service when service is restarted successfully (line 748, 756-767)."""
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = None
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = True

        with (
            patch("builtins.input", return_value="y"),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.create_service_file") as mock_create,
            patch("mmrelay.setup_utils.reload_daemon"),
        ):
            mock_create.return_value = True
            result = install_service()

            self.assertTrue(result)
            mock_logger.info.assert_any_call("Service restarted successfully")
            mock_wait.assert_called_once()
            mock_show_status.assert_called_once()

    @patch("subprocess.run")
    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_restart_service_os_error(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
        mock_run,
    ):
        """
        Verifies that install_service returns True and logs an exception when restarting the service raises an OSError.

        Sets up mocks to simulate an existing, enabled, and active user service and makes the restart call raise OSError; asserts install_service completes successfully and logger.exception is called with "OS error while restarting service".
        """
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = None
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = True
        mock_run.side_effect = OSError("System error")

        with (
            patch("builtins.input", return_value="y"),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.create_service_file") as mock_create,
            patch("mmrelay.setup_utils.reload_daemon"),
        ):
            mock_create.return_value = True
            result = install_service()

            self.assertTrue(result)
            mock_logger.exception.assert_any_call("OS error while restarting service")

    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "systemctl"))
    @patch("mmrelay.setup_utils.is_service_active")
    @patch("mmrelay.setup_utils.is_service_enabled")
    @patch("mmrelay.setup_utils.check_loginctl_available")
    @patch("mmrelay.setup_utils.check_lingering_enabled")
    @patch("mmrelay.setup_utils.service_needs_update")
    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_user_service_path")
    def test_install_service_restart_service_calledprocesserror(
        self,
        mock_get_path,
        mock_read_service,
        mock_needs_update,
        mock_lingering_enabled,
        mock_loginctl_available,
        mock_service_enabled,
        mock_service_active,
        _mock_run,
        _mock_show_status,
        _mock_wait,
    ):
        """Test install_service CalledProcessError when restarting service (line 765)."""
        mock_get_path.return_value = Path(
            "/home/user/.config/systemd/user/mmrelay.service"
        )
        mock_read_service.return_value = None
        mock_needs_update.return_value = (False, "No update needed")
        mock_loginctl_available.return_value = False
        mock_lingering_enabled.return_value = True
        mock_service_enabled.return_value = True
        mock_service_active.return_value = True

        with (
            patch("builtins.input", return_value="y"),
            patch("mmrelay.setup_utils.logger") as mock_logger,
            patch("mmrelay.setup_utils.create_service_file") as mock_create,
            patch("mmrelay.setup_utils.reload_daemon"),
        ):
            mock_create.return_value = True
            result = install_service()

            self.assertTrue(result)
            mock_logger.exception.assert_any_call(
                "Error restarting service (exit code %d)", 1
            )


if __name__ == "__main__":
    unittest.main()
