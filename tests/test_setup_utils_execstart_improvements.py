"""Tests for ExecStart handling improvements in setup_utils.py."""

import unittest
from unittest.mock import patch

from mmrelay.setup_utils import (
    _quote_if_needed,
    get_resolved_exec_cmd,
    get_resolved_exec_start,
)


class TestExecStartImprovements(unittest.TestCase):
    """Test ExecStart handling improvements."""

    def test_quote_if_needed_with_spaces(self):
        """Test _quote_if_needed function with paths containing spaces."""
        path_with_spaces = "/path with spaces/python"
        result = _quote_if_needed(path_with_spaces)
        self.assertEqual(result, '"/path with spaces/python"')

    def test_quote_if_needed_without_spaces(self):
        """Test _quote_if_needed function with paths without spaces."""
        path_without_spaces = "/usr/bin/python"
        result = _quote_if_needed(path_without_spaces)
        self.assertEqual(result, "/usr/bin/python")

    @patch("shutil.which")
    def test_get_resolved_exec_cmd_found(self, mock_which):
        """Test get_resolved_exec_cmd when mmrelay binary is found."""
        mock_which.return_value = "/usr/local/bin/mmrelay"

        result = get_resolved_exec_cmd()

        self.assertEqual(result, "/usr/local/bin/mmrelay")

    @patch("shutil.which")
    @patch("sys.executable", "/usr/bin/python")
    def test_get_resolved_exec_cmd_not_found(self, mock_which):
        """Test get_resolved_exec_cmd when mmrelay binary is not found."""
        mock_which.return_value = None

        result = get_resolved_exec_cmd()

        self.assertEqual(result, "/usr/bin/python -m mmrelay")

    @patch("shutil.which")
    @patch("sys.executable", "/path with spaces/python")
    def test_get_resolved_exec_cmd_with_spaces_in_python(self, mock_which):
        """Test get_resolved_exec_cmd with spaces in Python path."""
        mock_which.return_value = None

        result = get_resolved_exec_cmd()

        self.assertEqual(result, '"/path with spaces/python" -m mmrelay')

    @patch("mmrelay.setup_utils.get_resolved_exec_cmd")
    def test_get_resolved_exec_start_default_args(self, mock_get_cmd):
        """Test get_resolved_exec_start with default arguments."""
        mock_get_cmd.return_value = "/usr/local/bin/mmrelay"

        result = get_resolved_exec_start()

        expected = "ExecStart=/usr/local/bin/mmrelay --config %h/.mmrelay/config.yaml --logfile %h/.mmrelay/logs/mmrelay.log"
        self.assertEqual(result, expected)

    @patch("mmrelay.setup_utils.get_resolved_exec_cmd")
    def test_get_resolved_exec_start_custom_args(self, mock_get_cmd):
        """Test get_resolved_exec_start with custom arguments."""
        mock_get_cmd.return_value = "/usr/local/bin/mmrelay"

        result = get_resolved_exec_start(" --custom-arg")

        expected = "ExecStart=/usr/local/bin/mmrelay --custom-arg"
        self.assertEqual(result, expected)

    def test_quote_if_needed_consistency(self):
        """Test that _quote_if_needed is used consistently."""
        # This test ensures the fix for spaces in sys.executable is working
        path_with_spaces = "/path with spaces/python"
        result = _quote_if_needed(path_with_spaces)
        self.assertEqual(result, '"/path with spaces/python"')

        # Test that the fix is applied in service logic
        from mmrelay.setup_utils import get_resolved_exec_cmd

        with patch("shutil.which", return_value=None):
            with patch("sys.executable", path_with_spaces):
                result = get_resolved_exec_cmd()
                self.assertEqual(result, '"/path with spaces/python" -m mmrelay')


if __name__ == "__main__":
    unittest.main()
