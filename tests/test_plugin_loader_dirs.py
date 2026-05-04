"""Tests for plugin loader: Plugin directory discovery."""

# Decomposed from test_plugin_loader.py

import os
import unittest
from unittest.mock import patch

import mmrelay.plugin_loader as pl


class TestPluginDirectories(unittest.TestCase):
    """Test cases for plugin directory discovery and creation."""

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.path.isdir")
    @patch("os.makedirs")
    def test_get_plugin_dirs_user_dir_success(
        self,
        mock_makedirs,
        mock_isdir,
        mock_get_app_path,
        mock_get_legacy_dirs,
        mock_get_home_dir,
    ):
        """Test successful user directory creation."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_home_dir.return_value = "/user/base"
        mock_get_legacy_dirs.return_value = []
        mock_get_app_path.return_value = "/app/path"
        mock_isdir.return_value = True

        dirs = _get_plugin_dirs("custom")

        self.assertIn("/user/base/plugins/custom", dirs)
        self.assertIn("/app/path/plugins/custom", dirs)
        mock_makedirs.assert_called_once_with(
            "/user/base/plugins/custom", exist_ok=True
        )

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.path.isdir")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.makedirs")
    def test_get_plugin_dirs_user_dir_permission_error(
        self,
        mock_makedirs,
        mock_logger,
        mock_isdir,
        mock_get_app_path,
        mock_get_legacy_dirs,
        mock_get_home_dir,
    ):
        """Test handling of permission error in user directory."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_home_dir.return_value = "/user/base"
        mock_get_legacy_dirs.return_value = []
        mock_get_app_path.return_value = "/app/path"
        mock_isdir.return_value = True
        mock_makedirs.side_effect = PermissionError("Permission denied")

        dirs = _get_plugin_dirs("custom")

        # Should only include local directory since user dir failed
        self.assertEqual(len(dirs), 1)
        self.assertIn("/app/path/plugins/custom", dirs)
        mock_logger.warning.assert_called()

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.path.isdir")
    @patch("mmrelay.plugin_loader.logger")
    @patch("os.makedirs")
    def test_get_plugin_dirs_missing_local_dir_is_skipped_without_noise(
        self,
        mock_makedirs,
        mock_logger,
        mock_isdir,
        mock_get_app_path,
        mock_get_legacy_dirs,
        mock_get_home_dir,
    ):
        """Missing package-local fallback directories should be skipped quietly."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_home_dir.return_value = "/user/base"
        mock_get_legacy_dirs.return_value = []
        mock_get_app_path.return_value = "/app/path"
        mock_isdir.return_value = False

        dirs = _get_plugin_dirs("custom")

        self.assertEqual(len(dirs), 1)
        self.assertIn("/user/base/plugins/custom", dirs)
        mock_makedirs.assert_called_once_with(
            "/user/base/plugins/custom", exist_ok=True
        )
        mock_logger.warning.assert_not_called()
        mock_logger.debug.assert_not_called()

    @patch("mmrelay.paths.get_home_dir")
    @patch("mmrelay.paths.get_legacy_dirs")
    @patch("mmrelay.plugin_loader.get_app_path")
    @patch("os.path.isdir")
    @patch("os.makedirs")
    def test_get_plugin_dirs_existing_local_dir_is_included(
        self,
        mock_makedirs,
        mock_isdir,
        mock_get_app_path,
        mock_get_legacy_dirs,
        mock_get_home_dir,
    ):
        """Existing package-local fallback directories should still be searched."""
        from mmrelay.plugin_loader import _get_plugin_dirs

        mock_get_home_dir.return_value = "/user/base"
        mock_get_legacy_dirs.return_value = []
        mock_get_app_path.return_value = "/app/path"
        mock_isdir.return_value = True

        dirs = _get_plugin_dirs("community")

        self.assertEqual(
            dirs,
            [
                "/user/base/plugins/community",
                "/app/path/plugins/community",
            ],
        )
        mock_makedirs.assert_called_once_with(
            "/user/base/plugins/community", exist_ok=True
        )
