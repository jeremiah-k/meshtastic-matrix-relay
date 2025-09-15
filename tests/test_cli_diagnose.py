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


class TestWindowsErrorHandling(unittest.TestCase):
    """Test cases for Windows-specific error handling in CLI."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("sys.platform", "win32")
    @patch("mmrelay.windows_utils.is_windows", return_value=True)
    @patch("mmrelay.windows_utils.get_windows_error_message")
    @patch("builtins.print")
    def test_windows_error_handling_in_main(self, mock_print, mock_get_error, mock_is_windows):
        """Test Windows-specific error handling in main function."""
        from mmrelay.cli import main

        # Mock get_windows_error_message to return a detailed message
        mock_get_error.return_value = "Detailed Windows error message with troubleshooting"

        # Mock parse_arguments to raise an exception
        with patch("mmrelay.cli.parse_arguments", side_effect=RuntimeError("Test error")):
            result = main()

        # Should return error code
        self.assertEqual(result, 1)

        # Should call Windows error message handler
        mock_get_error.assert_called_once()
        mock_print.assert_called_with("Error: Detailed Windows error message with troubleshooting")

    @patch("sys.platform", "linux")
    @patch("builtins.print")
    def test_non_windows_error_handling_in_main(self, mock_print):
        """Test non-Windows error handling in main function."""
        from mmrelay.cli import main

        # Mock parse_arguments to raise an exception
        with patch("mmrelay.cli.parse_arguments", side_effect=RuntimeError("Test error")):
            result = main()

        # Should return error code
        self.assertEqual(result, 1)

        # Should use standard error message
        mock_print.assert_called_with("Unexpected error: Test error")

    @patch("sys.platform", "win32")
    @patch("mmrelay.windows_utils.is_windows", return_value=True)
    @patch("mmrelay.windows_utils.get_windows_error_message")
    @patch("builtins.print")
    def test_windows_error_in_generate_config(self, mock_print, mock_get_error, mock_is_windows):
        """Test Windows-specific error handling in generate_sample_config."""
        from mmrelay.cli import generate_sample_config

        mock_get_error.return_value = "Windows file permission error with guidance"

        # Mock file operations to fail
        with patch("mmrelay.cli.get_config_paths", return_value=["/test/config.yaml"]), \
             patch("os.path.exists", return_value=False), \
             patch("mmrelay.cli.get_sample_config_path", side_effect=OSError("Permission denied")):

            result = generate_sample_config()

        # Should fail
        self.assertFalse(result)

        # Should provide Windows-specific guidance
        mock_get_error.assert_called()


class TestMinimalConfigTemplate(unittest.TestCase):
    """Test cases for minimal config template functionality."""

    def test_minimal_config_template_structure(self):
        """Test that minimal config template has proper structure."""
        from mmrelay.cli import _get_minimal_config_template

        template = _get_minimal_config_template()

        # Should be valid YAML
        import yaml
        config = yaml.safe_load(template)

        # Should have required sections
        self.assertIn("matrix", config)
        self.assertIn("meshtastic", config)
        self.assertIn("matrix_rooms", config)

        # Should have helpful comments
        self.assertIn("# MMRelay Configuration File", template)
        self.assertIn("# This is a minimal template", template)

    def test_minimal_config_template_contains_examples(self):
        """Test that minimal config template contains example values."""
        from mmrelay.cli import _get_minimal_config_template

        template = _get_minimal_config_template()

        # Should contain example values
        self.assertIn("matrix.example.org", template)
        self.assertIn("#your-room:matrix.example.org", template)
        self.assertIn("connection_type:", template)

        # Should contain guidance comments
        self.assertIn("# Use 'mmrelay auth login'", template)
        self.assertIn("# Windows:", template)


class TestConfigDiagnoseIntegration(unittest.TestCase):
    """Integration tests for config diagnose functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()

    @patch("builtins.print")
    def test_config_diagnose_basic_functionality(self, mock_print):
        """Test basic config diagnose functionality."""
        from mmrelay.cli import handle_config_diagnose

        result = handle_config_diagnose(self.mock_args)

        # Should complete successfully
        self.assertEqual(result, 0)

        # Should print diagnostic header
        printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
        self.assertTrue(any("MMRelay Configuration System Diagnostics" in str(msg) for msg in printed_messages))

    @patch("sys.platform", "win32")
    @patch("mmrelay.windows_utils.is_windows", return_value=True)
    @patch("mmrelay.windows_utils.test_config_generation_windows")
    @patch("builtins.print")
    def test_config_diagnose_windows_integration(self, mock_print, mock_windows_test, mock_is_windows):
        """Test config diagnose with Windows-specific tests."""
        from mmrelay.cli import handle_config_diagnose

        # Mock Windows test results
        mock_windows_test.return_value = {
            "overall_status": "ok",
            "sample_config_path": {"status": "ok"},
            "importlib_resources": {"status": "ok"},
        }

        result = handle_config_diagnose(self.mock_args)

        # Should complete successfully
        self.assertEqual(result, 0)

        # Should call Windows-specific tests
        mock_windows_test.assert_called_once()

        # Should print Windows-specific results
        printed_messages = [call.args[0] for call in mock_print.call_args_list if call.args]
        self.assertTrue(any("Windows: Yes" in str(msg) for msg in printed_messages))


if __name__ == "__main__":
    unittest.main()
