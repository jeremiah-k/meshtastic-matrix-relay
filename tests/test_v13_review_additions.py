"""
Additional tests for v1.3 features to fill coverage gaps in paths.py and migrate.py.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from mmrelay.migrate import (
    _cleanup_lock_file,
    _is_mmrelay_running,
    _register_lock_cleanup,
    migrate_plugins,
)
from mmrelay.paths import (
    UnknownPluginTypeError,
    get_config_paths,
    get_home_dir,
    get_legacy_dirs,
)


class TestPathsGaps:
    """Test gaps in paths.py."""

    def setup_method(self):
        from mmrelay.paths import reset_home_override

        reset_home_override()

    @patch("mmrelay.paths.sys.platform", "win32")
    @patch("mmrelay.paths.platformdirs.user_data_dir")
    def test_get_home_dir_windows_defaults(self, mock_user_data):
        """Test get_home_dir platform defaults on Windows."""
        mock_user_data.return_value = "C:\\Users\\test\\AppData\\Local\\mmrelay"
        with patch.dict(os.environ, {}, clear=True):
            result = get_home_dir()
            assert str(result) == "C:\\Users\\test\\AppData\\Local\\mmrelay"

    @patch("mmrelay.paths.sys.platform", "win32")
    @patch("mmrelay.paths.platformdirs.user_data_dir")
    def test_get_config_paths_windows_legacy(self, mock_user_data):
        """Test get_config_paths includes Windows legacy platform dir."""
        mock_user_data.return_value = "C:\\legacy\\appdata"

        # Patch Path.exists on the instance
        with patch("mmrelay.paths.Path.exists", return_value=True):
            with patch(
                "mmrelay.paths.get_home_dir", return_value=Path("C:\\new\\home")
            ):
                paths = get_config_paths()
                path_strs = [str(p) for p in paths]
                # We expect C:\legacy\appdata\config.yaml to be in candidates
                assert any("legacy" in s and "config.yaml" in s for s in path_strs)

    def test_get_legacy_dirs_docker(self):
        """Test detection of Docker legacy paths."""
        # Use a list of paths that "exist"
        existing_paths = ["/data", "/data/config.yaml", "/data/credentials.json"]

        def mock_exists(path_obj):
            return str(path_obj) in existing_paths

        with patch("mmrelay.paths.Path.exists", side_effect=mock_exists, autospec=True):
            with patch(
                "mmrelay.paths.get_home_dir", return_value=Path("/home/mmrelay")
            ):
                with patch("mmrelay.paths.Path.home", return_value=Path("/home/user")):
                    with patch(
                        "mmrelay.paths.platformdirs.user_data_dir",
                        return_value="/tmp/noexist",
                    ):
                        legacy = get_legacy_dirs()
                        assert Path("/data") in legacy

    def test_unknown_plugin_type(self):
        """Test UnknownPluginTypeError initialization."""
        err = UnknownPluginTypeError("invalid")
        assert "invalid" in str(err)


class TestMigrateGaps:
    """Test gaps in migrate.py."""

    @patch("mmrelay.migrate.sys.platform", "darwin")
    @patch("mmrelay.migrate.shutil.which", return_value="/usr/bin/pgrep")
    @patch("mmrelay.migrate.subprocess.run")
    @patch("mmrelay.migrate.Path.exists")
    def test_is_mmrelay_running_macos(self, mock_exists, mock_run, mock_which):
        """Test _is_mmrelay_running on macOS (no /proc)."""
        mock_exists.return_value = False  # No /proc/PID/cmdline

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234\n"
        mock_run.return_value = mock_result

        # current pid is NOT 1234
        with patch("os.getpid", return_value=5678):
            assert _is_mmrelay_running() is True

    @patch("mmrelay.migrate.sys.platform", "linux")
    @patch("mmrelay.migrate.shutil.which", return_value="/usr/bin/pgrep")
    @patch("mmrelay.migrate.subprocess.run")
    @patch("mmrelay.migrate.Path.exists")
    def test_is_mmrelay_running_linux_success(self, mock_exists, mock_run, mock_which):
        """Test _is_mmrelay_running on Linux with /proc."""
        mock_exists.return_value = True

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "1234\n"
        mock_run.return_value = mock_result

        with patch("os.getpid", return_value=5678):
            with patch("builtins.open", mock_open(read_data=b"python3 mmrelay main")):
                assert _is_mmrelay_running() is True

    @patch("mmrelay.migrate._current_lock_file")
    def test_cleanup_lock_file_error(self, mock_lock):
        """Test _cleanup_lock_file handles unlink error."""
        mock_lock.exists.return_value = True
        mock_lock.unlink.side_effect = OSError("Permission denied")

        # Should not raise
        _cleanup_lock_file()
        mock_lock.unlink.assert_called_once()

    @patch("mmrelay.migrate.sys.platform", "linux")
    @patch("mmrelay.migrate.signal.signal")
    @patch("mmrelay.migrate.atexit.register")
    def test_register_lock_cleanup(self, mock_atexit, mock_signal):
        """Test registration of lock cleanup handlers."""
        lock_path = Path("/tmp/migrate.lock")
        _register_lock_cleanup(lock_path)

        mock_atexit.assert_called_once()
        assert mock_signal.call_count >= 1

    def test_migrate_plugins_multiple_sources_warning(self):
        """Test migrate_plugins warns when multiple legacy roots have plugins."""
        with tempfile.TemporaryDirectory() as tmp_dir_str:
            tmp_path = Path(tmp_dir_str)
            root1 = tmp_path / "root1"
            root1.mkdir()
            (root1 / "plugins").mkdir()
            (root1 / "plugins" / "custom").mkdir()

            root2 = tmp_path / "root2"
            root2.mkdir()
            (root2 / "plugins").mkdir()
            (root2 / "plugins" / "community").mkdir()

            new_home = tmp_path / "home"
            new_home.mkdir()

            # We need to ensure _warn_multiple_sources sees both.
            # It uses candidate.exists() which is root / artifact_filename.
            # artifact_filename is "plugins".

            with patch("mmrelay.migrate.logger.warning") as mock_warn:
                # We need to make sure we don't return early.
                # migrate_plugins returns early if it finds ONE plugins dir?
                # No, it calls _warn_multiple_sources first.
                migrate_plugins([root1, root2], new_home)

                # Check if any warning was issued
                assert mock_warn.called
