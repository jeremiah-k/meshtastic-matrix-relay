"""Tests for plugin loader: Python cache cleaning, namespace package detection."""

# Decomposed from test_plugin_loader.py

import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import mmrelay.plugin_loader as pl
from mmrelay.plugin_loader import _clean_python_cache


class TestCleanPythonCache(unittest.TestCase):
    """Test cases for _clean_python_cache function."""

    def setUp(self):
        """Create a temporary directory for cache cleaning tests."""
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_clean_python_cache_removes_pycache_directories(self):
        """Test that __pycache__ directories are removed."""
        # Create __pycache__ directories
        pycache1 = os.path.join(self.temp_dir, "subdir1", "__pycache__")
        pycache2 = os.path.join(self.temp_dir, "subdir2", "__pycache__")
        os.makedirs(pycache1, exist_ok=True)
        os.makedirs(pycache2, exist_ok=True)

        # Create some files in cache directories
        with open(os.path.join(pycache1, "test1.pyc"), "w"):
            pass
        with open(os.path.join(pycache2, "test2.pyc"), "w"):
            pass

        # Verify directories exist
        self.assertTrue(os.path.exists(pycache1))
        self.assertTrue(os.path.exists(pycache2))

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify directories are removed
        self.assertFalse(os.path.exists(pycache1))
        self.assertFalse(os.path.exists(pycache2))

    def test_clean_python_cache_removes_pyc_files(self):
        """Test that .pyc files are removed."""
        # Create .pyc files
        pyc1 = os.path.join(self.temp_dir, "test1.pyc")
        pyc2 = os.path.join(self.temp_dir, "subdir", "test2.pyc")
        os.makedirs(os.path.dirname(pyc2), exist_ok=True)
        with open(pyc1, "w"):
            pass
        with open(pyc2, "w"):
            pass

        # Verify files exist
        self.assertTrue(os.path.exists(pyc1))
        self.assertTrue(os.path.exists(pyc2))

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify files are removed
        self.assertFalse(os.path.exists(pyc1))
        self.assertFalse(os.path.exists(pyc2))

    def test_clean_python_cache_preserves_source_files(self):
        """Test that .py files are preserved."""
        # Create source files
        py1 = os.path.join(self.temp_dir, "test1.py")
        py2 = os.path.join(self.temp_dir, "subdir", "test2.py")
        os.makedirs(os.path.dirname(py2), exist_ok=True)
        with open(py1, "w"):
            pass
        with open(py2, "w"):
            pass

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify source files are preserved
        self.assertTrue(os.path.exists(py1))
        self.assertTrue(os.path.exists(py2))

    def test_clean_python_cache_handles_nonexistent_directory(self):
        """Test that function handles nonexistent directories gracefully."""
        nonexistent_dir = os.path.join(self.temp_dir, "nonexistent")

        # Should not raise exception
        _clean_python_cache(nonexistent_dir)

    def test_clean_python_cache_handles_permission_errors(self):
        """Test that function handles permission errors gracefully."""
        # Create a __pycache__ directory
        pycache = os.path.join(self.temp_dir, "__pycache__")
        os.makedirs(pycache, exist_ok=True)

        # Mock shutil.rmtree to raise PermissionError
        with patch("shutil.rmtree", side_effect=PermissionError("Permission denied")):
            # Should not raise exception
            _clean_python_cache(self.temp_dir)

    def test_clean_python_cache_handles_permission_errors_os_remove(self):
        """Test that function handles permission errors from os.remove gracefully."""
        # Create a .pyc file
        pyc_file = os.path.join(self.temp_dir, "test.pyc")
        with open(pyc_file, "w") as f:
            f.write("dummy")

        # Mock os.remove to raise PermissionError
        with patch("os.remove", side_effect=PermissionError("Permission denied")):
            # Should not raise exception
            _clean_python_cache(self.temp_dir)

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_debug_messages(self, mock_logger):
        """
        Verify that cleaning Python cache logs debug messages and includes a removal message for __pycache__ directories.

        The test creates a __pycache__ directory, invokes _clean_python_cache on the containing directory, and asserts that logger.debug was called and one of the debug messages contains "Removed Python cache directory".
        """
        # Create a __pycache__ directory
        pycache = os.path.join(self.temp_dir, "__pycache__")
        os.makedirs(pycache, exist_ok=True)

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify debug messages were logged
        mock_logger.debug.assert_called()

        # Check for cache directory removal message
        debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
        self.assertTrue(
            any("Removed Python cache directory" in msg for msg in debug_calls)
        )

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_summary_message(self, mock_logger):
        """Test that summary info message is logged when cache directories are removed."""
        # Create multiple __pycache__ directories
        for i in range(3):
            pycache = os.path.join(self.temp_dir, f"subdir{i}", "__pycache__")
            os.makedirs(pycache, exist_ok=True)

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify info message was logged for the summary
        mock_logger.info.assert_called_once()
        call_args = mock_logger.info.call_args[0][0]
        self.assertTrue(call_args.startswith("Cleaned"))
        self.assertIn("3 Python cache directories", call_args)

    @patch("mmrelay.plugin_loader.logger")
    def test_clean_python_cache_logs_combined_info_message(self, mock_logger):
        """Test that combined info message is logged when both cache directories and .pyc files are removed."""
        # Create __pycache__ directories
        pycache1 = os.path.join(self.temp_dir, "subdir1", "__pycache__")
        pycache2 = os.path.join(self.temp_dir, "subdir2", "__pycache__")
        os.makedirs(pycache1, exist_ok=True)
        os.makedirs(pycache2, exist_ok=True)

        # Create .pyc files
        pyc_file1 = os.path.join(self.temp_dir, "test1.pyc")
        pyc_file2 = os.path.join(self.temp_dir, "subdir3", "test2.pyc")
        os.makedirs(os.path.dirname(pyc_file2), exist_ok=True)
        with open(pyc_file1, "w") as f:
            f.write("dummy")
        with open(pyc_file2, "w") as f:
            f.write("dummy")

        # Clean cache
        _clean_python_cache(self.temp_dir)

        # Verify info message was logged for the combined summary
        mock_logger.info.assert_called_once()
        combined_message = mock_logger.info.call_args[0][0]
        self.assertTrue(combined_message.startswith("Cleaned"))
        self.assertIn("Python cache director", combined_message)
        self.assertIn(".pyc file", combined_message)
        self.assertIn(" and ", combined_message)  # Indicates both types were cleaned


class TestIsNamespacePackageDirectory(unittest.TestCase):
    """Tests for _is_namespace_package_directory detection logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

    def test_implicit_namespace_no_init_py(self):
        """Directory without __init__.py should be detected as implicit namespace."""
        ns_dir = os.path.join(self.temp_dir, "mypackage")
        os.makedirs(ns_dir)
        self.assertTrue(pl._is_namespace_package_directory(ns_dir))

    def test_pkgutil_namespace(self):
        """Directory with pkgutil.extend_path in __init__.py should be namespace."""
        ns_dir = os.path.join(self.temp_dir, "google")
        os.makedirs(ns_dir)
        with open(os.path.join(ns_dir, "__init__.py"), "w") as f:
            f.write(
                "__path__ = __import__('pkgutil').extend_path(__path__, __name__)\n"
            )
        self.assertTrue(pl._is_namespace_package_directory(ns_dir))

    def test_pkg_resources_namespace(self):
        """Directory with pkg_resources.declare_namespace should be namespace."""
        ns_dir = os.path.join(self.temp_dir, "zope")
        os.makedirs(ns_dir)
        with open(os.path.join(ns_dir, "__init__.py"), "w") as f:
            f.write("__import__('pkg_resources').declare_namespace(__name__)\n")
        self.assertTrue(pl._is_namespace_package_directory(ns_dir))

    def test_regular_package_not_namespace(self):
        """Directory with normal __init__.py should NOT be detected as namespace."""
        pkg_dir = os.path.join(self.temp_dir, "requests")
        os.makedirs(pkg_dir)
        with open(os.path.join(pkg_dir, "__init__.py"), "w") as f:
            f.write("__version__ = '2.28.0'\n")
        self.assertFalse(pl._is_namespace_package_directory(pkg_dir))

    def test_symlink_not_namespace(self):
        """Symlinked directory should not be treated as namespace package."""
        real_dir = os.path.join(self.temp_dir, "real")
        link_dir = os.path.join(self.temp_dir, "link")
        os.makedirs(real_dir)
        os.symlink(real_dir, link_dir)
        self.assertFalse(pl._is_namespace_package_directory(link_dir))

    def test_nonexistent_path_not_namespace(self):
        """Non-existent path should not be treated as namespace package."""
        self.assertFalse(
            pl._is_namespace_package_directory(os.path.join(self.temp_dir, "nope"))
        )

    def test_unreadable_init_py_fails_safe(self):
        """Unreadable __init__.py should fail safe (return False)."""
        pkg_dir = os.path.join(self.temp_dir, "badpkg")
        os.makedirs(pkg_dir)
        init_path = os.path.join(pkg_dir, "__init__.py")
        with open(init_path, "w") as f:
            f.write("some content\n")
        with patch("builtins.open", side_effect=OSError("permission denied")):
            self.assertFalse(pl._is_namespace_package_directory(pkg_dir))
