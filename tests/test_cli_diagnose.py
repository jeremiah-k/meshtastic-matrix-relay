"""
Test cases for CLI diagnose functionality.

This module tests the new config diagnose command and related functionality
added for Windows compatibility improvements.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import _get_minimal_config_template, handle_config_diagnose


class TestHandleConfigDiagnose(unittest.TestCase):
    """Test cases for handle_config_diagnose function."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("builtins.print")
    def test_handle_config_diagnose_success_unix(self, mock_print):
        """Test successful config diagnose on Unix system."""
        # Execute with minimal mocking - just test that it runs without crashing
        with patch("sys.platform", "linux"):
            result = handle_config_diagnose(self.mock_args)

        # Verify it completed successfully
        self.assertEqual(result, 0)

        # Check that key diagnostic messages were printed
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        self.assertTrue(
            any(
                "MMRelay Configuration System Diagnostics" in str(msg)
                for msg in printed_messages
            )
        )
        self.assertTrue(
            any("Diagnostics complete!" in str(msg) for msg in printed_messages)
        )

    @patch("builtins.print")
    def test_handle_config_diagnose_windows_with_warnings(self, mock_print):
        """Test config diagnose on Windows with warnings."""
        # Execute with minimal mocking - just test that it runs without crashing
        with patch("sys.platform", "win32"):
            result = handle_config_diagnose(self.mock_args)

        # Verify it completed successfully
        self.assertEqual(result, 0)

        # Check that key diagnostic messages were printed
        printed_messages = [
            call.args[0] for call in mock_print.call_args_list if call.args
        ]
        self.assertTrue(any("Platform: win32" in str(msg) for msg in printed_messages))
        self.assertTrue(any("Windows: Yes" in str(msg) for msg in printed_messages))

    @patch("mmrelay.config.get_config_paths")
    @patch("builtins.print")
    def test_handle_config_diagnose_exception_handling(
        self, mock_print, mock_get_config_paths
    ):
        """Test config diagnose handles exceptions gracefully."""
        # Setup mock to raise exception
        mock_get_config_paths.side_effect = Exception("Test error")

        # Execute
        result = handle_config_diagnose(self.mock_args)

        # Verify
        self.assertEqual(result, 1)
        mock_print.assert_any_call("❌ Diagnostics failed: Test error")


class TestGetMinimalConfigTemplate(unittest.TestCase):
    """Test cases for _get_minimal_config_template function."""

    def test_get_minimal_config_template_returns_valid_yaml(self):
        """Test that minimal config template returns valid YAML."""
        # Execute
        template = _get_minimal_config_template()

        # Verify
        self.assertIsInstance(template, str)
        self.assertGreater(len(template), 0)

        # Check that it contains expected sections
        self.assertIn("matrix:", template)
        self.assertIn("meshtastic:", template)
        self.assertIn("matrix_rooms:", template)
        self.assertIn("logging:", template)

        # Verify it's valid YAML
        import yaml

        try:
            config_data = yaml.safe_load(template)
            self.assertIsInstance(config_data, dict)
            self.assertIn("matrix", config_data)
            self.assertIn("meshtastic", config_data)
        except yaml.YAMLError:
            self.fail("Minimal config template is not valid YAML")

    def test_get_minimal_config_template_contains_comments(self):
        """Test that minimal config template contains helpful comments."""
        # Execute
        template = _get_minimal_config_template()

        # Verify contains helpful comments
        self.assertIn("# MMRelay Configuration File", template)
        self.assertIn("# This is a minimal template", template)
        self.assertIn("# For complete configuration options", template)
        self.assertIn("# Windows:", template)
        self.assertIn("# For network connection", template)


if __name__ == "__main__":
    unittest.main()
