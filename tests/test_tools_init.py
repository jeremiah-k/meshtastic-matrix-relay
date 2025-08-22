"""Tests for mmrelay.tools module."""

import unittest
from unittest.mock import patch

from mmrelay.tools import get_sample_config_path, get_service_template_path


class TestToolsInit(unittest.TestCase):
    """Test cases for tools/__init__.py functions."""

    def test_get_sample_config_path_modern_python(self):
        """Test get_sample_config_path with modern Python (3.9+)."""
        # This should work on modern Python versions
        path = get_sample_config_path()
        self.assertIsInstance(path, str)
        self.assertIn("sample_config.yaml", path)

    def test_get_service_template_path_modern_python(self):
        """Test get_service_template_path with modern Python (3.9+)."""
        # This should work on modern Python versions
        path = get_service_template_path()
        self.assertIsInstance(path, str)
        self.assertIn("mmrelay.service", path)

    @patch("importlib.resources.files")
    def test_get_sample_config_path_fallback(self, mock_files):
        """Test get_sample_config_path fallback for older Python versions."""
        # Simulate AttributeError to trigger fallback
        mock_files.side_effect = AttributeError("files not available")

        path = get_sample_config_path()
        self.assertIsInstance(path, str)
        self.assertIn("sample_config.yaml", path)
        # Should use pathlib fallback
        self.assertTrue(path.endswith("sample_config.yaml"))

    @patch("importlib.resources.files")
    def test_get_service_template_path_fallback(self, mock_files):
        """Test get_service_template_path fallback for older Python versions."""
        # Simulate AttributeError to trigger fallback
        mock_files.side_effect = AttributeError("files not available")

        path = get_service_template_path()
        self.assertIsInstance(path, str)
        self.assertIn("mmrelay.service", path)
        # Should use pathlib fallback
        self.assertTrue(path.endswith("mmrelay.service"))


if __name__ == "__main__":
    unittest.main()
