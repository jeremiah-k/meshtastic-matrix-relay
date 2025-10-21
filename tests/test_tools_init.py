"""Tests for mmrelay.tools module."""

import unittest
from unittest.mock import patch

from mmrelay.tools import get_sample_config_path, get_service_template_path


class TestToolsInit(unittest.TestCase):
    """Test cases for tools/__init__.py functions."""

    def test_get_sample_config_path_modern_python(self):
        """Test get_sample_config_path with modern Python (3.10+)."""
        # This should work on modern Python versions
        path = get_sample_config_path()
        self.assertIsInstance(path, str)
        self.assertIn("sample_config.yaml", path)

    def test_get_service_template_path_modern_python(self):
        """Test get_service_template_path with modern Python (3.10+)."""
        # This should work on modern Python versions
        path = get_service_template_path()
        self.assertIsInstance(path, str)
        self.assertIn("mmrelay.service", path)


if __name__ == "__main__":
    unittest.main()
