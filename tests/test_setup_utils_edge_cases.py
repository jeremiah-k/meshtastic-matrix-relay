#!/usr/bin/env python3
"""
Test suite for Setup utilities edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Service installation failures
- File permission errors
- System command failures
- Missing system dependencies
- Service file template errors
- User lingering configuration issues
- Path resolution edge cases
"""

import os
import subprocess  # nosec B404 - Used for controlled test environment operations
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.setup_utils import (
    _quote_if_needed,
    check_lingering_enabled,
    check_loginctl_available,
    create_service_file,
    enable_lingering,
    get_executable_path,
    get_template_service_content,
    install_service,
    reload_daemon,
)


class TestSetupUtilsEdgeCases(unittest.TestCase):
    """Test cases for Setup utilities edge cases and error handling."""

    def test_create_service_file_permission_error(self):
        """
        Test that create_service_file returns False when directory creation fails due to a PermissionError.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch(
                "pathlib.Path.write_text",
                side_effect=PermissionError("Permission denied"),
            ):
                with patch("builtins.print") as mock_print:
                    result = create_service_file()
                    self.assertFalse(result)
                    mock_print.assert_called()

    def test_get_executable_path_not_found(self):
        """
        Test that get_executable_path returns the system Python executable with -m mmrelay when the "mmrelay" executable is not found.
        """
        with patch("shutil.which", return_value=None):
            with patch("builtins.print"):  # Suppress warning print
                result = get_executable_path()
                # Should return quoted sys.executable -m mmrelay as fallback (quotes only if needed)
                self.assertEqual(
                    result, f"{_quote_if_needed(sys.executable)} -m mmrelay"
                )

    def test_get_executable_path_multiple_locations(self):
        """
        Test that get_executable_path returns the correct path when multiple executable locations exist.

        Verifies that get_executable_path prioritizes the expected location when multiple possible paths are available.
        """

        def mock_which(cmd):
            """
            Mock implementation of shutil.which that returns a fixed path for the "mmrelay" command.

            Parameters:
                cmd (str): The command to search for.

            Returns:
                str or None: The mocked path to "mmrelay" if requested, otherwise None.
            """
            if cmd == "mmrelay":
                return "/usr/local/bin/mmrelay"
            return None

        with patch("shutil.which", side_effect=mock_which):
            result = get_executable_path()
            self.assertEqual(result, "/usr/local/bin/mmrelay")

    def test_get_template_service_content_file_not_found(self):
        """
        Test that get_template_service_content returns the default template when the template file is not found.
        """
        with patch("mmrelay.setup_utils.get_template_service_path", return_value=None):
            result = get_template_service_content()
            # Should return default template
            self.assertIn("[Unit]", result)
            self.assertIn("Description=MMRelay - Meshtastic", result)

    def test_get_template_service_content_read_error(self):
        """
        Test that get_template_service_content returns the default template and prints an error when reading the template file raises an IOError.
        """
        with patch(
            "mmrelay.setup_utils.get_template_service_path",
            return_value="/test/service.template",
        ):
            with patch("builtins.open", side_effect=IOError("Read error")):
                with patch("builtins.print") as mock_print:
                    result = get_template_service_content()
                    # Should return default template and print error
                    self.assertIn("[Unit]", result)
                    mock_print.assert_called()

    def test_create_service_file_write_permission_error(self):
        """
        Test that create_service_file returns False and prints an error when file writing fails due to a PermissionError.
        """
        with patch("mmrelay.setup_utils.get_user_service_path") as mock_get_path:
            mock_path = MagicMock()
            mock_path.write_text.side_effect = PermissionError("Permission denied")
            mock_get_path.return_value = mock_path

            with patch(
                "mmrelay.setup_utils.get_template_service_content",
                return_value="[Unit]\nTest",
            ):
                with patch(
                    "mmrelay.setup_utils.get_executable_path",
                    return_value="/usr/bin/mmrelay",
                ):
                    with patch("builtins.print") as mock_print:
                        result = create_service_file()
                        self.assertFalse(result)
                        mock_print.assert_called()

    def test_create_service_file_no_executable_uses_fallback(self):
        """
        Test that create_service_file uses python -m mmrelay fallback when mmrelay binary is not found.
        """
        template_with_placeholder = """[Unit]
Description=Test Service
[Service]
ExecStart=%h/meshtastic-matrix-relay/.pyenv/bin/python %h/meshtastic-matrix-relay/main.py --config %h/.mmrelay/config/config.yaml
"""
        with patch("shutil.which", return_value=None):  # mmrelay not in PATH
            with patch(
                "mmrelay.setup_utils.get_template_service_content",
                return_value=template_with_placeholder,
            ):
                with patch(
                    "mmrelay.setup_utils.get_user_service_path"
                ) as mock_get_path:
                    mock_path = MagicMock()
                    mock_get_path.return_value = mock_path

                    with patch("builtins.print") as mock_print:
                        result = create_service_file()
                        self.assertTrue(result)  # Should succeed with fallback

                        # Check that fallback message was printed
                        mock_print.assert_any_call(
                            "Warning: Could not find mmrelay executable in PATH. Using current Python interpreter.",
                            file=sys.stderr,
                        )

                        # Check that the ExecStart uses a python* -m mmrelay fallback
                        written_content = mock_path.write_text.call_args[0][0]
                        self.assertRegex(
                            written_content,
                            r"(?m)^ExecStart=.*\bpython(?:\d+(?:\.\d+)*)?\b\s+-m\s+mmrelay\b",
                        )

    def test_reload_daemon_command_failure(self):
        """
        Test that reload_daemon returns False and prints an error when the systemctl command fails with a CalledProcessError.
        """
        with patch("subprocess.run") as mock_run:
            # Mock subprocess.run to raise CalledProcessError (since check=True is used)
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "systemctl", "Command failed"
            )

            with patch("builtins.print") as mock_print:
                result = reload_daemon()
                self.assertFalse(result)
                mock_print.assert_called()

    def test_reload_daemon_exception(self):
        """
        Test that reload_daemon returns False and prints an error when subprocess.run raises a FileNotFoundError.
        """
        with patch(
            "subprocess.run", side_effect=FileNotFoundError("systemctl not found")
        ):
            with patch("builtins.print") as mock_print:
                result = reload_daemon()
                self.assertFalse(result)
                mock_print.assert_called()

    def test_check_loginctl_available_not_found(self):
        """
        Test that check_loginctl_available returns False when the loginctl command is not found.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            result = check_loginctl_available()
            self.assertFalse(result)

    def test_check_loginctl_available_command_failure(self):
        """
        Test that check_loginctl_available returns False when subprocess.run raises an exception during the loginctl availability check.
        """
        with patch("shutil.which", return_value="/usr/bin/loginctl"):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = OSError("Command failed")
                result = check_loginctl_available()
                self.assertFalse(result)

    def test_check_lingering_enabled_command_failure(self):
        """
        Test that check_lingering_enabled returns False and prints an error when the loginctl command raises an exception.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Command failed")
            with patch("builtins.print") as mock_print:
                result = check_lingering_enabled()
                self.assertFalse(result)
                mock_print.assert_called()

    def test_check_lingering_enabled_parsing_error(self):
        """
        Test that check_lingering_enabled returns False when the loginctl output cannot be parsed correctly.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "invalid output format"

            with patch.dict(os.environ, {"USER": "testuser"}):
                result = check_lingering_enabled()
                self.assertFalse(result)

    def test_enable_lingering_command_failure(self):
        """
        Test that enable_lingering returns False and prints an error when the loginctl command fails with a non-zero exit code.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "Permission denied"

            with patch("builtins.print") as mock_print:
                result = enable_lingering()
                self.assertFalse(result)
                mock_print.assert_called()

    def test_enable_lingering_exception(self):
        """
        Test that enable_lingering returns False and prints an error when subprocess.run raises an exception.
        """
        with patch("subprocess.run", side_effect=OSError("Command failed")):
            with patch("builtins.print") as mock_print:
                result = enable_lingering()
                self.assertFalse(result)
                mock_print.assert_called()

    def test_install_service_no_executable_uses_fallback(self):
        """
        Test that install_service succeeds using python -m mmrelay fallback when mmrelay binary is not found.
        """
        with patch("shutil.which", return_value=None):  # mmrelay not in PATH
            with patch(
                "mmrelay.setup_utils.get_template_service_content",
                return_value="[Unit]\nTest",
            ):
                with patch(
                    "mmrelay.setup_utils.read_service_file", return_value=None
                ):  # No existing service
                    with patch("builtins.print") as mock_print:
                        with patch(
                            "builtins.input", return_value="n"
                        ):  # Mock input to avoid stdin issues
                            result = install_service()
                            self.assertTrue(result)  # Should succeed with fallback
                            mock_print.assert_any_call(
                                "Warning: Could not find mmrelay executable in PATH. Using current Python interpreter.",
                                file=sys.stderr,
                            )

    def test_install_service_create_file_failure(self):
        """
        Test that install_service returns False when service file creation fails.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=False):
                with patch("builtins.print"):
                    with patch(
                        "builtins.input", return_value="y"
                    ):  # Mock input to avoid stdin issues
                        result = install_service()
                        self.assertFalse(result)

    def test_install_service_daemon_reload_failure(self):
        """
        Test that install_service returns True even if daemon reload fails.

        This test simulates a failure in the daemon reload step during service installation and verifies that install_service still returns True, reflecting user choice to decline further action.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=False):
                    with patch(
                        "mmrelay.setup_utils.read_service_file", return_value=None
                    ):
                        with patch(
                            "mmrelay.setup_utils.service_needs_update",
                            return_value=(True, "test"),
                        ):
                            with patch(
                                "mmrelay.setup_utils.check_loginctl_available",
                                return_value=False,
                            ):
                                with patch(
                                    "builtins.input", return_value="n"
                                ):  # Mock all input prompts to return "n"
                                    with patch("builtins.print"):
                                        result = install_service()
                                        # Should still return True even if reload fails
                                        self.assertTrue(result)

    def test_install_service_lingering_check_failure(self):
        """
        Test that install_service returns True and prints a message when lingering check fails and the user declines to enable lingering.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=True):
                    with patch(
                        "mmrelay.setup_utils.check_loginctl_available",
                        return_value=True,
                    ):
                        with patch(
                            "mmrelay.setup_utils.check_lingering_enabled",
                            return_value=False,
                        ):
                            with patch("builtins.input", return_value="n"):
                                with patch("builtins.print") as mock_print:
                                    result = install_service()
                                    self.assertTrue(result)
                                    mock_print.assert_called()

    def test_install_service_enable_lingering_failure(self):
        """
        Test that install_service returns True when enabling lingering fails after user consents.

        Simulates the scenario where the user agrees to enable lingering, but the operation fails, and verifies that install_service still reports success.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=True):
                    with patch(
                        "mmrelay.setup_utils.check_loginctl_available",
                        return_value=True,
                    ):
                        with patch(
                            "mmrelay.setup_utils.check_lingering_enabled",
                            return_value=False,
                        ):
                            with patch("builtins.input", return_value="y"):
                                with patch(
                                    "mmrelay.setup_utils.enable_lingering",
                                    return_value=False,
                                ):
                                    with patch("builtins.print"):
                                        result = install_service()
                                        self.assertTrue(result)  # Should still succeed

    def test_install_service_user_interaction_eof(self):
        """
        Test that install_service returns True when user input raises EOFError during lingering enabling prompt.

        Simulates an EOFError occurring when prompting the user to enable lingering, verifying that install_service completes successfully without raising an exception.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=True):
                    with patch(
                        "mmrelay.setup_utils.check_loginctl_available",
                        return_value=True,
                    ):
                        with patch(
                            "mmrelay.setup_utils.check_lingering_enabled",
                            return_value=False,
                        ):
                            with patch("builtins.input", side_effect=EOFError()):
                                with patch("builtins.print"):
                                    result = install_service()
                                    self.assertTrue(result)

    def test_install_service_user_interaction_keyboard_interrupt(self):
        """
        Test that install_service returns True when user input raises KeyboardInterrupt during the lingering enable prompt.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=True):
                    with patch(
                        "mmrelay.setup_utils.check_loginctl_available",
                        return_value=True,
                    ):
                        with patch(
                            "mmrelay.setup_utils.check_lingering_enabled",
                            return_value=False,
                        ):
                            with patch(
                                "builtins.input", side_effect=KeyboardInterrupt()
                            ):
                                with patch("builtins.print"):
                                    result = install_service()
                                    self.assertTrue(result)

    def test_service_template_placeholder_replacement(self):
        """
        Verify that service template placeholders are correctly replaced with actual executable paths and user home directory expansions during service file creation.
        """
        template = """
        WorkingDirectory=%h/meshtastic-matrix-relay
        ExecStart=%h/meshtastic-matrix-relay/.pyenv/bin/python %h/meshtastic-matrix-relay/main.py
        --config %h/.mmrelay/config/config.yaml
        """

        with patch(
            "mmrelay.setup_utils.get_template_service_content", return_value=template
        ):
            with patch(
                "shutil.which",
                return_value="/usr/bin/mmrelay",
            ):
                with patch(
                    "mmrelay.setup_utils.get_user_service_path"
                ) as mock_get_path:
                    mock_path = MagicMock()
                    mock_get_path.return_value = mock_path

                    result = create_service_file()
                    self.assertTrue(result)

                    # Check that placeholders were replaced
                    written_content = mock_path.write_text.call_args[0][0]
                    self.assertNotIn("%h/meshtastic-matrix-relay", written_content)
                    self.assertIn("/usr/bin/mmrelay", written_content)
                    self.assertIn("--config %h/.mmrelay/config.yaml", written_content)


if __name__ == "__main__":
    unittest.main()
