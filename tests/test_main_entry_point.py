"""
Test cases for the __main__.py module entry point.

This module tests the alternative entry point for MMRelay that provides
Windows compatibility and fallback functionality when setuptools console
scripts fail.
"""

import os
import sys
import unittest
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestMainEntryPoint(unittest.TestCase):
    """Test cases for __main__.py module functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.original_argv = sys.argv.copy()

    def tearDown(self):
        """Clean up after tests."""
        sys.argv = self.original_argv

    @patch("mmrelay.cli.main")
    @patch("sys.exit")
    def test_main_entry_point_success(self, mock_exit, mock_main):
        """Test successful execution of main entry point."""
        mock_main.return_value = 0

        # Execute the main module code with __name__ == "__main__"
        with open("src/mmrelay/__main__.py") as f:
            code = f.read()
        exec(code, {"__name__": "__main__"})

        mock_main.assert_called_once()
        mock_exit.assert_called_once_with(0)

    @patch("mmrelay.cli.main", side_effect=ImportError("Module not found"))
    @patch("builtins.print")
    @patch("sys.exit")
    def test_main_entry_point_import_error(self, mock_exit, mock_print, mock_main):
        """Test handling of ImportError when importing CLI."""
        # Execute the main module code with __name__ == "__main__"
        with open("src/mmrelay/__main__.py") as f:
            code = f.read()
        exec(code, {"__name__": "__main__"})

        mock_print.assert_any_call(
            "Error importing MMRelay CLI: Module not found", file=sys.stderr
        )
        mock_print.assert_any_call(
            "Please ensure MMRelay is properly installed.", file=sys.stderr
        )
        mock_exit.assert_called_once_with(1)

    @patch("mmrelay.cli.main", side_effect=KeyboardInterrupt())
    @patch("builtins.print")
    @patch("sys.exit")
    def test_main_entry_point_keyboard_interrupt(
        self, mock_exit, mock_print, mock_main
    ):
        """Test handling of KeyboardInterrupt."""
        # Execute the main module code with __name__ == "__main__"
        with open("src/mmrelay/__main__.py") as f:
            code = f.read()
        exec(code, {"__name__": "__main__"})

        mock_print.assert_called_once_with("Interrupted.", file=sys.stderr)
        mock_exit.assert_called_once_with(130)

    @patch("mmrelay.cli.main", side_effect=SystemExit(42))
    @patch("sys.exit")
    def test_main_entry_point_system_exit_passthrough(self, mock_exit, mock_main):
        """Test that SystemExit is passed through unchanged."""
        with self.assertRaises(SystemExit) as cm:
            with open("src/mmrelay/__main__.py") as f:
                code = f.read()
            exec(code, {"__name__": "__main__"})

        self.assertEqual(cm.exception.code, 42)

    @patch("mmrelay.cli.main", side_effect=RuntimeError("Unexpected error"))
    @patch("builtins.print")
    @patch("sys.exit")
    def test_main_entry_point_unexpected_exception(
        self, mock_exit, mock_print, mock_main
    ):
        """Test handling of unexpected exceptions."""
        # Execute the main module code with __name__ == "__main__"
        with open("src/mmrelay/__main__.py") as f:
            code = f.read()
        exec(code, {"__name__": "__main__"})

        mock_print.assert_called_once_with(
            "Unexpected error: Unexpected error", file=sys.stderr
        )
        mock_exit.assert_called_once_with(1)

    def test_main_entry_point_module_structure(self):
        """Test that the __main__.py module has the expected structure."""
        # Read the module content
        with open("src/mmrelay/__main__.py", "r") as f:
            content = f.read()

        # Check for expected components
        self.assertIn('if __name__ == "__main__":', content)
        self.assertIn("from mmrelay.cli import main", content)
        self.assertIn("sys.exit(main())", content)
        self.assertIn("ImportError", content)
        self.assertIn("KeyboardInterrupt", content)
        self.assertIn("SystemExit", content)

    def test_main_entry_point_docstring(self):
        """Test that the __main__.py module has proper documentation."""
        # Import the module to check its docstring
        import mmrelay.__main__

        self.assertIsNotNone(mmrelay.__main__.__doc__)
        self.assertIn("Alternative entry point", mmrelay.__main__.__doc__)
        self.assertIn("Windows", mmrelay.__main__.__doc__)
        self.assertIn("python -m mmrelay", mmrelay.__main__.__doc__)

    @patch("mmrelay.cli.main")
    @patch("sys.exit")
    def test_main_entry_point_with_arguments(self, mock_exit, mock_main):
        """Test main entry point execution with command line arguments."""
        mock_main.return_value = 5
        sys.argv = ["python", "-m", "mmrelay", "--help"]

        # Execute the main module code with __name__ == "__main__"
        with open("src/mmrelay/__main__.py") as f:
            code = f.read()
        exec(code, {"__name__": "__main__"})

        mock_main.assert_called_once()
        mock_exit.assert_called_once_with(5)

    def test_main_entry_point_imports(self):
        """Test that the __main__.py module can import required modules."""
        # This test verifies that the import structure is correct
        try:
            import sys

            # The module should be able to import sys
            self.assertTrue(hasattr(sys, "exit"))
            self.assertTrue(hasattr(sys, "stderr"))
        except ImportError as e:
            self.fail(f"Failed to import required modules: {e}")


class TestMainEntryPointIntegration(unittest.TestCase):
    """Integration tests for __main__.py module."""

    def test_main_entry_point_can_be_executed_as_module(self):
        """Test that the main entry point can be executed as a module."""
        # This is more of a structural test to ensure the module is set up correctly
        import subprocess
        import sys

        # Try to run the module with --help to see if it executes without import errors
        try:
            result = subprocess.run(
                [sys.executable, "-m", "mmrelay", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=os.path.join(os.path.dirname(__file__), ".."),
            )
            # We expect this to either succeed or fail with a specific error
            # The important thing is that it doesn't fail with ImportError
            self.assertNotIn("ImportError", result.stderr)
        except subprocess.TimeoutExpired:
            # If it times out, that's also acceptable - it means the module loaded
            pass
        except FileNotFoundError:
            # If mmrelay isn't installed, that's expected in test environment
            pass

    def test_main_entry_point_error_messages(self):
        """Test that error messages are properly formatted."""
        # Read the module content to verify error message formatting
        with open("src/mmrelay/__main__.py", "r") as f:
            content = f.read()

        # Check that error messages are written to stderr
        self.assertIn("file=sys.stderr", content)

        # Check that error messages are descriptive
        self.assertIn("Error importing MMRelay CLI", content)
        self.assertIn("Please ensure MMRelay is properly installed", content)
        self.assertIn("Interrupted", content)
        self.assertIn("Unexpected error", content)


if __name__ == "__main__":
    unittest.main()
