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
import subprocess  # nosec B404
import sys
import tempfile
import unittest
from pathlib import Path
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
        Verify create_service_file logs the exception and indicates failure when writing the service file raises a PermissionError.
        """
        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch(
                "pathlib.Path.write_text",
                side_effect=PermissionError("Permission denied"),
            ):
                with patch("mmrelay.setup_utils.logger") as mock_logger:
                    result = create_service_file()
                    self.assertFalse(result)
                    mock_logger.exception.assert_called()

    def test_get_executable_path_not_found(self):
        """
        Test that get_executable_path returns the system Python executable with -m mmrelay when the "mmrelay" executable is not found.
        """
        with patch("shutil.which", return_value=None):
            with patch("mmrelay.setup_utils.logger"):  # Suppress warning log
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
        Test that get_template_service_content returns the default template and logs an error when reading the template file raises an IOError.
        """
        with patch(
            "mmrelay.setup_utils.get_template_service_path",
            return_value="/test/service.template",
        ):
            with patch("builtins.open", side_effect=IOError("Read error")):
                with patch("mmrelay.setup_utils.logger") as mock_logger:
                    result = get_template_service_content()
                    # Should return default template and log error
                    self.assertIn("[Unit]", result)
                    mock_logger.exception.assert_called()

    def test_create_service_file_write_permission_error(self):
        """
        Test that create_service_file returns False and logs an error when file writing fails due to a PermissionError.
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
                    with patch("mmrelay.setup_utils.logger") as mock_logger:
                        result = create_service_file()
                        self.assertFalse(result)
                        mock_logger.exception.assert_called()

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

                    with patch("mmrelay.setup_utils.logger") as mock_logger:
                        result = create_service_file()
                        self.assertTrue(result)  # Should succeed with fallback

                        # Check that fallback message was logged
                        mock_logger.warning.assert_any_call(
                            "Could not find mmrelay executable in PATH. Using current Python interpreter."
                        )

                        # Check that the ExecStart uses a python* -m mmrelay fallback
                        written_content = mock_path.write_text.call_args[0][0]
                        self.assertRegex(
                            written_content,
                            r"(?m)^ExecStart=.*\bpython(?:\d+(?:\.\d+)*)?\b\s+-m\s+mmrelay\b",
                        )

    def test_reload_daemon_command_failure(self):
        """
        Test that reload_daemon returns False and logs an error when the systemctl command fails with a CalledProcessError.
        """
        with patch("subprocess.run") as mock_run:
            # Mock subprocess.run to raise CalledProcessError (since check=True is used)
            mock_run.side_effect = subprocess.CalledProcessError(
                1, "systemctl", "Command failed"
            )

            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = reload_daemon()
                self.assertFalse(result)
                mock_logger.exception.assert_called()

    def test_reload_daemon_exception(self):
        """
        Test that reload_daemon returns False and logs an error when subprocess.run raises a FileNotFoundError.
        """
        with patch(
            "subprocess.run", side_effect=FileNotFoundError("systemctl not found")
        ):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = reload_daemon()
                self.assertFalse(result)
                mock_logger.exception.assert_called()

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
        Test that check_lingering_enabled returns False and logs an error when the loginctl command raises an exception.
        """
        with patch("shutil.which", return_value="/usr/bin/loginctl"):
            with patch("subprocess.run", side_effect=OSError("Command failed")):
                with patch("mmrelay.setup_utils.logger") as mock_logger:
                    result = check_lingering_enabled()
                    self.assertFalse(result)
                    mock_logger.exception.assert_called()

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
        Test that enable_lingering returns False and logs an error when the loginctl command fails with a non-zero exit code.
        """
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "Permission denied"

            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = enable_lingering()
                self.assertFalse(result)
                mock_logger.error.assert_called()

    def test_enable_lingering_exception(self):
        """
        Test that enable_lingering returns False and logs an error when subprocess.run raises an exception.
        """
        with patch("subprocess.run", side_effect=OSError("Command failed")):
            with patch("mmrelay.setup_utils.logger") as mock_logger:
                result = enable_lingering()
                self.assertFalse(result)
                mock_logger.exception.assert_called()

    def test_install_service_no_executable_uses_fallback(self):
        """
        Test that install_service succeeds using python -m mmrelay fallback when mmrelay binary is not found.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            with (
                patch("pathlib.Path.home", return_value=fake_home),
                patch("shutil.which", return_value=None),
                patch(
                    "mmrelay.setup_utils.get_template_service_content",
                    return_value="[Unit]\nTest",
                ),
                patch("mmrelay.setup_utils.get_user_service_path") as mock_service_path,
                patch("mmrelay.setup_utils.logger") as mock_logger,
                patch("builtins.input", return_value="n"),
            ):
                mock_path = MagicMock()
                mock_path.exists.return_value = False
                mock_service_path.return_value = mock_path
                result = install_service()
                self.assertTrue(result)
                mock_logger.warning.assert_any_call(
                    "Could not find mmrelay executable in PATH. Using current Python interpreter."
                )

    def test_install_service_create_file_failure(self):
        """
        Test that install_service returns False when service file creation fails.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_home = Path(tmpdir)
            with (
                patch("pathlib.Path.home", return_value=fake_home),
                patch(
                    "mmrelay.setup_utils.get_executable_path",
                    return_value="/usr/bin/mmrelay",
                ),
                patch(
                    "mmrelay.setup_utils.get_template_service_content",
                    return_value=(
                        "[Unit]\n"
                        "Description=MMRelay\n"
                        "[Service]\n"
                        "WorkingDirectory=%h/meshtastic-matrix-relay\n"
                        "ExecStart=/usr/bin/env mmrelay --config %h/.mmrelay/config/config.yaml\n"
                    ),
                ),
                patch("mmrelay.setup_utils.get_user_service_path") as mock_service_path,
                patch("mmrelay.setup_utils.logger"),
                patch("builtins.input", return_value="y"),
            ):
                mock_path = MagicMock()
                mock_path.exists.return_value = False
                mock_path.write_text.side_effect = PermissionError("Permission denied")
                mock_service_path.return_value = mock_path
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
                        "mmrelay.setup_utils.get_user_service_path"
                    ) as mock_service_path:
                        mock_path = MagicMock()
                        mock_path.exists.return_value = False
                        mock_service_path.return_value = mock_path
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
                                    with patch("mmrelay.setup_utils.logger"):
                                        result = install_service()
                                        # Should still return True even if reload fails
                                        self.assertTrue(result)

    def test_install_service_lingering_check_failure(self):
        """
        Test that install_service returns True and logs a message when lingering check fails and the user declines to enable lingering.
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
                                with patch("mmrelay.setup_utils.logger") as mock_logger:
                                    result = install_service()
                                    self.assertTrue(result)
                                    mock_logger.info.assert_called()

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
                                    with patch("mmrelay.setup_utils.logger"):
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
                                with patch("mmrelay.setup_utils.logger"):
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
                                with patch("mmrelay.setup_utils.logger"):
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


class TestGetResolvedExecStartEmpty(unittest.TestCase):
    """Test get_resolved_exec_start with empty suffix (line 161)."""

    @patch("mmrelay.setup_utils.get_resolved_exec_cmd", return_value="mmrelay")
    def test_empty_suffix(self, mock_cmd):
        """Empty suffix should produce ExecStart without args."""
        from mmrelay.setup_utils import get_resolved_exec_start

        result = get_resolved_exec_start(args_suffix="   ")
        self.assertEqual(result, "ExecStart=mmrelay")


class TestServiceNeedsUpdateEdgeCases(unittest.TestCase):
    """Test service_needs_update edge cases for uncovered branches."""

    def _make_service_content(self, exec_start, environment=None, unit_section=None):
        lines = ["[Unit]"]
        if unit_section:
            lines.extend(unit_section)
        lines.append("Description=Test")
        lines.append("[Service]")
        lines.append("Type=simple")
        lines.append(exec_start)
        if environment:
            for env in environment:
                lines.append(env)
        lines.append("[Install]")
        lines.append("WantedBy=default.target")
        return "\n".join(lines)

    def _full_service(self, exec_start, **kwargs):
        defaults = {
            "environment": [
                "Environment=PATH=%h/.local/bin:%h/.local/pipx/venvs/mmrelay/bin:/usr/bin"
            ],
            "unit_section": [
                "After=network-online.target time-sync.target",
                "Wants=network-online.target time-sync.target",
            ],
        }
        for k, v in defaults.items():
            kwargs.setdefault(k, v)
        return self._make_service_content(exec_start, **kwargs)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_missing_execstart_line(self, mock_path, mock_read):
        """Service file missing ExecStart should need update (line 576)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = "[Unit]\nDescription=Test\n[Service]\nType=simple\n"
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("missing ExecStart", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_empty_execstart_value(self, mock_path, mock_read):
        """Empty ExecStart value should need update (line 580)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            "ExecStart=",
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("empty ExecStart", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_invalid_execstart_shlex(self, mock_path, mock_read):
        """Invalid ExecStart (unbalanced quotes) should need update (line 591-592)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            'ExecStart=/usr/bin/mmrelay "unbalanced',
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("invalid ExecStart", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_legacy_config_flag(self, mock_path, mock_read):
        """Service with --config flag should need update (line 599)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            "ExecStart=mmrelay --config /etc/config.yaml --home /test",
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("legacy", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_home_flag_with_dash_value(self, mock_path, mock_read):
        """--home followed by a flag-like value should not count as home flag (line 612->615)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            "ExecStart=mmrelay --home --other-flag",
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("missing home", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_home_equals_empty_value(self, mock_path, mock_read):
        """--home= with empty value should not count, but MMRELAY_HOME env should work (line 619-620)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._full_service(
            "ExecStart=/usr/bin/mmrelay --home= ",
            environment=["Environment=MMRELAY_HOME=/test"],
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_empty_environment_line(self, mock_path, mock_read):
        """Empty Environment= line should be handled (line 624->626, 627)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._full_service(
            "ExecStart=/usr/bin/mmrelay --home /test",
            environment=["Environment=", "Environment=MMRELAY_HOME=/test"],
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_env_launcher_with_double_dash(self, mock_path, mock_read):
        """env launcher with -- separator should skip it (line 660)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._full_service(
            "ExecStart=env -- mmrelay --home /test",
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_env_launcher_with_flag(self, mock_path, mock_read):
        """env launcher with -S flag should skip it (line 662)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._full_service(
            "ExecStart=env -S mmrelay --home /test",
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_env_without_equals(self, mock_path, mock_read):
        """Environment line token without '=' should be skipped (line 636)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._full_service(
            "ExecStart=/usr/bin/mmrelay --home /test",
            environment=["Environment=NOEQUALS MMRELAY_HOME=/test"],
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_unrecognizable_launcher(self, mock_path, mock_read):
        """Non-mmrelay, non-absolute launcher should be flagged (line 710)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            "ExecStart=unknown_launcher --home /test",
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertTrue(needs_update)
        self.assertIn("recognizable", reason)

    @patch("mmrelay.setup_utils.read_service_file")
    @patch("mmrelay.setup_utils.get_template_service_path", return_value=None)
    def test_path_with_percent_h_entry(self, mock_path, mock_read):
        """PATH with %h entry should normalize correctly (line 728)."""
        from mmrelay.setup_utils import service_needs_update

        mock_read.return_value = self._make_service_content(
            "ExecStart=env mmrelay --home /test",
            environment=[
                "Environment=PATH=%h/.local/bin:%h/.local/pipx/venvs/mmrelay/bin:/usr/bin"
            ],
            unit_section=[
                "After=network.target time-sync.target",
                "Wants=time-sync.target",
            ],
        )
        needs_update, reason = service_needs_update()
        self.assertFalse(needs_update)

    @patch("mmrelay.setup_utils.get_template_service_path")
    def test_mtime_oserror(self, mock_get_path):
        """OSError during stat check should trigger update (line 768-769)."""
        from mmrelay.setup_utils import service_needs_update

        with tempfile.NamedTemporaryFile(suffix=".service", delete=False) as f:
            f.write(
                b"[Unit]\nDescription=Test\nAfter=time-sync.target\nWants=time-sync.target\n"
            )
            f.write(
                b"[Service]\nExecStart=/usr/bin/mmrelay --home /test\nType=simple\n"
            )
            f.write(b"[Install]\nWantedBy=default.target\n")
            template_path = f.name

        try:
            mock_get_path.return_value = template_path

            service_content = (
                "[Unit]\nDescription=Test\nAfter=time-sync.target\nWants=time-sync.target\n"
                "[Service]\nExecStart=/usr/bin/mmrelay --home /test\nType=simple\n"
                "[Install]\nWantedBy=default.target\n"
            )

            with (
                patch(
                    "mmrelay.setup_utils.read_service_file",
                    return_value=service_content,
                ),
                patch("os.path.exists", return_value=True),
                patch("os.path.getmtime", side_effect=OSError("fail")),
            ):
                needs_update, reason = service_needs_update()
                self.assertTrue(needs_update)
                self.assertIn("Unable to stat", reason)
        finally:
            os.unlink(template_path)


class TestCheckLingeringLoginctlNotFound(unittest.TestCase):
    """Test check_lingering_enabled when loginctl is not found (line 833)."""

    @patch("mmrelay.setup_utils.shutil.which", return_value=None)
    @patch.dict(os.environ, {"USER": "testuser"}, clear=False)
    def test_loginctl_not_found_returns_false(self, mock_which):
        """check_lingering_enabled should return False when loginctl is not found."""
        result = check_lingering_enabled()
        self.assertFalse(result)


class TestInstallServiceErrorPaths(unittest.TestCase):
    """Test install_service error paths for uncovered branches."""

    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("mmrelay.setup_utils.start_service", return_value=False)
    @patch("mmrelay.setup_utils.is_service_enabled", return_value=False)
    @patch("mmrelay.setup_utils.check_lingering_enabled", return_value=True)
    @patch("mmrelay.setup_utils.check_loginctl_available", return_value=False)
    @patch("mmrelay.setup_utils.reload_daemon", return_value=True)
    @patch("mmrelay.setup_utils.create_service_file", return_value=True)
    @patch("mmrelay.setup_utils.service_needs_update", return_value=(True, "test"))
    @patch("mmrelay.setup_utils.read_service_file", return_value=None)
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("builtins.input", side_effect=["y", "y"])
    def test_start_service_fails_warns(
        self,
        mock_input,
        mock_wait,
        mock_show,
        mock_read,
        mock_needs,
        mock_create,
        mock_reload,
        mock_loginctl,
        mock_linger,
        mock_enabled,
        mock_start,
        mock_active,
    ):
        """install_service should log warning when start_service fails (line 1038)."""
        with patch("mmrelay.setup_utils.log_service_commands"):
            with patch("mmrelay.setup_utils.subprocess.run"):
                result = install_service()
        self.assertTrue(result)

    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("mmrelay.setup_utils.start_service", return_value=True)
    @patch("mmrelay.setup_utils.is_service_enabled", return_value=False)
    @patch("mmrelay.setup_utils.check_lingering_enabled", return_value=True)
    @patch("mmrelay.setup_utils.check_loginctl_available", return_value=False)
    @patch("mmrelay.setup_utils.reload_daemon", return_value=True)
    @patch("mmrelay.setup_utils.create_service_file", return_value=True)
    @patch("mmrelay.setup_utils.service_needs_update", return_value=(True, "test"))
    @patch("mmrelay.setup_utils.read_service_file", return_value=None)
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("builtins.input", side_effect=["y", "y"])
    @patch("mmrelay.setup_utils.subprocess.run")
    def test_enable_service_called_process_error(
        self,
        mock_run,
        mock_input,
        mock_wait,
        mock_show,
        mock_read,
        mock_needs,
        mock_create,
        mock_reload,
        mock_loginctl,
        mock_linger,
        mock_enabled,
        mock_start,
        mock_active,
    ):
        """install_service should handle CalledProcessError during enable (line 988-989)."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "enable")
        with patch("mmrelay.setup_utils.log_service_commands"):
            result = install_service()
        self.assertTrue(result)

    @patch("mmrelay.setup_utils.is_service_active", return_value=False)
    @patch("mmrelay.setup_utils.start_service", return_value=True)
    @patch("mmrelay.setup_utils.is_service_enabled", return_value=False)
    @patch("mmrelay.setup_utils.check_lingering_enabled", return_value=True)
    @patch("mmrelay.setup_utils.check_loginctl_available", return_value=False)
    @patch("mmrelay.setup_utils.reload_daemon", return_value=True)
    @patch("mmrelay.setup_utils.create_service_file", return_value=True)
    @patch("mmrelay.setup_utils.service_needs_update", return_value=(True, "test"))
    @patch("mmrelay.setup_utils.read_service_file", return_value=None)
    @patch("mmrelay.setup_utils.show_service_status")
    @patch("mmrelay.setup_utils.wait_for_service_start")
    @patch("builtins.input", side_effect=["y", "y"])
    @patch("mmrelay.setup_utils.subprocess.run")
    def test_enable_service_oserror(
        self,
        mock_run,
        mock_input,
        mock_wait,
        mock_show,
        mock_read,
        mock_needs,
        mock_create,
        mock_reload,
        mock_loginctl,
        mock_linger,
        mock_enabled,
        mock_start,
        mock_active,
    ):
        """install_service should handle OSError during enable (line 990-991)."""
        mock_run.side_effect = OSError("fail")
        with patch("mmrelay.setup_utils.log_service_commands"):
            result = install_service()
        self.assertTrue(result)


class TestStartServiceOSError(unittest.TestCase):
    """Test start_service OSError path (line 1073-1075)."""

    @patch(
        "mmrelay.setup_utils.subprocess.run",
        side_effect=OSError("No such file"),
    )
    def test_start_service_os_error(self, mock_run):
        """start_service should return False on OSError."""
        from mmrelay.setup_utils import start_service

        result = start_service()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
