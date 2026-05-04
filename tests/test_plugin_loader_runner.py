"""Tests for plugin loader: Command runner (_run helper)."""

# Decomposed from test_plugin_loader.py

import subprocess
import unittest
from unittest.mock import patch

from mmrelay.plugin_loader import _run


class TestCommandRunner(unittest.TestCase):
    """Verify helper command execution behavior."""

    def test_run_retries_on_failure(self):
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = [
                subprocess.CalledProcessError(1, ["git", "status"]),
                subprocess.CompletedProcess(args=["git", "status"], returncode=0),
            ]
            result = _run(["git", "status"], retry_attempts=2, retry_delay=0)
            self.assertIsInstance(result, subprocess.CompletedProcess)
            self.assertEqual(mock_subprocess.call_count, 2)

    def test_run_raises_after_max_attempts(self):
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.side_effect = subprocess.CalledProcessError(1, ["git"])
            with self.assertRaises(subprocess.CalledProcessError):
                _run(["git"], retry_attempts=2, retry_delay=0)
            self.assertEqual(mock_subprocess.call_count, 2)

    def test_run_type_error_not_list(self):
        """Test _run raises TypeError for non-list command."""
        with self.assertRaises(TypeError) as cm:
            _run("git status")  # type: ignore[arg-type]
        self.assertIn("cmd must be a list of str", str(cm.exception))

    def test_run_value_error_empty_list(self):
        """Test _run raises ValueError for empty command list."""
        with self.assertRaises(ValueError) as cm:
            _run([])
        self.assertIn("Command list cannot be empty", str(cm.exception))

    def test_run_type_error_non_string_args(self):
        """Test _run raises TypeError for non-string arguments."""
        with self.assertRaises(TypeError) as cm:
            _run(["git", 123])  # type: ignore[list-item]
        self.assertIn("all command arguments must be strings", str(cm.exception))

    def test_run_value_error_shell_true(self):
        """Test _run raises ValueError for shell=True."""
        with self.assertRaises(ValueError) as cm:
            shell_flag = True
            _run(["git", "status"], shell=shell_flag)  # nosec B604
        self.assertIn("shell=True is not allowed in _run", str(cm.exception))

    def test_run_value_error_empty_args(self):
        """Test _run raises ValueError for empty/whitespace arguments."""
        with self.assertRaises(ValueError) as cm:
            _run(["git", ""])
        self.assertIn("command arguments cannot be empty/whitespace", str(cm.exception))

        with self.assertRaises(ValueError) as cm:
            _run(["git", "   "])
        self.assertIn("command arguments cannot be empty/whitespace", str(cm.exception))

    def test_run_sets_text_default(self):
        """Test _run sets text=True by default."""
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = subprocess.CompletedProcess(
                args=["echo", "test"], returncode=0, stdout="test"
            )
            _run(["echo", "test"])
            # Check that text=True was set in the call
            call_kwargs = mock_subprocess.call_args[1]
            self.assertTrue(call_kwargs.get("text", False))

    def test_run_preserves_text_setting(self):
        """Test _run preserves existing text setting."""
        with patch("subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = subprocess.CompletedProcess(
                args=["echo", "test"], returncode=0, stdout=b"test"
            )
            _run(["echo", "test"], text=False)
            # Check that text=False was preserved
            call_kwargs = mock_subprocess.call_args[1]
            self.assertFalse(call_kwargs.get("text", True))
