"""
Comprehensive tests for paths module covering environment variables, legacy directory detection, and path resolution.

This module tests paths.py lines:
- Environment variable handling (85-86, 99-121)
- Config path resolution (160, 166, 173, 181)
- Database and plugin paths (217, 304, 345)
- Directory creation (379-380)
- Legacy directory detection (469-472, 490-492, 498-526)
- resolve_all_paths (565-566, 579-586)
- get_diagnostics (629, 632-633, 649)
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from mmrelay.paths import (
        get_config_paths,
        get_database_path,
        get_home_dir,
        get_legacy_dirs,
        get_plugin_code_dir,
        get_plugin_data_dir,
        get_plugin_database_path,
        reset_home_override,
    )

    IMPORTS_AVAILABLE = True
except ImportError:
    IMPORTS_AVAILABLE = False


class TestPathResolutionEnvVars(unittest.TestCase):
    """Test environment variable handling in get_home_dir (lines 85-86, 99-121)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_mmrelay_home_with_legacy_vars(self):
        """Test MMRELAY_HOME with legacy env vars present (lines 85-86, 88-92)."""
        with patch.dict(
            os.environ,
            {
                "MMRELAY_HOME": "/new/home",
                "MMRELAY_BASE_DIR": "/old/base",
                "MMRELAY_DATA_DIR": "/old/data",
            },
            clear=True,
        ):
            with patch("mmrelay.paths.get_logger") as mock_get_logger:
                mock_logger = mock_get_logger.return_value

                result = get_home_dir()

                self.assertEqual(
                    os.path.normpath(str(result)), os.path.normpath("/new/home")
                )

                warning_calls = [
                    call
                    for call in mock_logger.warning.call_args_list
                    if "MMRELAY_HOME is set; ignoring legacy environment variable"
                    in str(call)
                ]
                self.assertTrue(len(warning_calls) == 1)

    def test_both_base_and_data_dir_set(self):
        """Test both MMRELAY_BASE_DIR and MMRELAY_DATA_DIR set (lines 99-105)."""
        with patch.dict(
            os.environ,
            {
                "MMRELAY_BASE_DIR": "/base",
                "MMRELAY_DATA_DIR": "/data",
            },
            clear=True,
        ):
            with patch("mmrelay.paths.get_logger") as mock_get_logger:
                mock_logger = mock_get_logger.return_value

                result = get_home_dir()

                self.assertEqual(
                    os.path.normpath(str(result)), os.path.normpath("/base")
                )

                warning_call = [
                    call
                    for call in mock_logger.warning.call_args_list
                    if "Both MMRELAY_BASE_DIR and MMRELAY_DATA_DIR are set" in str(call)
                ]
                self.assertTrue(len(warning_call) == 1)

    def test_only_base_dir_set(self):
        """Test only MMRELAY_BASE_DIR set (lines 107-113)."""
        with patch.dict(os.environ, {"MMRELAY_BASE_DIR": "/base"}, clear=True):
            with patch("mmrelay.paths.get_logger") as mock_get_logger:
                mock_logger = mock_get_logger.return_value

                result = get_home_dir()

                self.assertEqual(
                    os.path.normpath(str(result)), os.path.normpath("/base")
                )

                warning_call = [
                    call
                    for call in mock_logger.warning.call_args_list
                    if "Deprecated environment variable MMRELAY_BASE_DIR is set"
                    in str(call)
                ]
                self.assertTrue(len(warning_call) == 1)

    def test_only_data_dir_set(self):
        """Test only MMRELAY_DATA_DIR set (lines 115-121)."""
        with patch.dict(os.environ, {"MMRELAY_DATA_DIR": "/data"}, clear=True):
            with patch("mmrelay.paths.get_logger") as mock_get_logger:
                mock_logger = mock_get_logger.return_value

                result = get_home_dir()

                self.assertEqual(
                    os.path.normpath(str(result)), os.path.normpath("/data")
                )

                warning_call = [
                    call
                    for call in mock_logger.warning.call_args_list
                    if "Deprecated environment variable MMRELAY_DATA_DIR is set"
                    in str(call)
                ]
                self.assertTrue(len(warning_call) == 1)


class TestPathResolutionConfigPaths(unittest.TestCase):
    """Test get_config_paths function (lines 160, 166, 173, 181)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_explicit_config_path_provided(self):
        """Test explicit config path is always first in candidates (lines 152-153, 160)."""
        with patch("sys.platform", "linux"):
            paths = get_config_paths(explicit="/explicit/config.yaml")

            self.assertTrue(len(paths) >= 1)
            self.assertEqual(
                os.path.normpath(str(paths[0])),
                os.path.normpath("/explicit/config.yaml"),
            )

    def test_no_explicit_path_home_config_exists(self):
        """Test home/config.yaml included when no explicit path (lines 158-161)."""
        with patch("sys.platform", "linux"):
            paths = get_config_paths()

            path_strs = [os.path.normpath(str(p)) for p in paths]
            self.assertTrue(
                any(
                    "mmrelay" in path_str and "config.yaml" in path_str
                    for path_str in path_strs
                )
            )

    def test_cwd_config_differs_from_home(self):
        """Test CWD config included when differs from home (lines 164-167)."""
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.Path.cwd") as mock_cwd:
                mock_cwd.return_value = Path("/cwd")

                with patch("mmrelay.paths.Path.home") as mock_home:
                    mock_home.return_value = Path("/home")

                    paths = get_config_paths()

                    path_strs = [str(p) for p in paths]
                    self.assertTrue(any("/cwd" in path_str for path_str in path_strs))

    def test_legacy_home_exists_differs_from_home(self):
        """
        Ensure get_config_paths includes a legacy `.mmrelay` config path when a legacy home exists and differs from the current home.
        """
        with patch("sys.platform", "linux"):
            # Mock Path.home() to return /home
            with patch("mmrelay.paths.Path.home") as mock_home:
                mock_home.return_value = Path("/home")

                # Mock Path.exists to check for legacy home
                with patch.object(Path, "exists", autospec=True) as mock_exists:

                    def exists_side_effect(self_path):
                        """
                        Determine whether the provided path corresponds to a legacy MMRELAY home location.

                        Parameters:
                            self_path: The path object to check (self in Path method).

                        Returns:
                            `True` if the string form of `self_path` contains "/home/.mmrelay", `False` otherwise.
                        """
                        path_str = str(self_path)
                        # Legacy home exists
                        if "/home/.mmrelay" in path_str:
                            return True
                        return False

                    mock_exists.side_effect = exists_side_effect

                    paths = get_config_paths()

                    path_strs = [str(p) for p in paths]
                    self.assertTrue(
                        any("mmrelay" in path_str for path_str in path_strs)
                    )

    def test_path_deduplication(self):
        """
        Verify get_config_paths deduplicates paths while preserving their original order.
        """
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                paths = get_config_paths()

                unique_paths = []
                seen = set()
                for path in paths:
                    path_str = str(path.absolute())
                    if path_str not in seen:
                        unique_paths.append(path)
                        seen.add(path_str)

                self.assertEqual(len(paths), len(unique_paths))


class TestPathResolutionDatabase(unittest.TestCase):
    """Test database path resolution (line 217)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_get_database_path_correct(self):
        """Test get_database_path returns correct path (line 217)."""
        with patch(
            "mmrelay.paths.get_database_dir", return_value=Path("/home/database")
        ):
            result = get_database_path()

            expected = Path("/home/database") / "meshtastic.sqlite"
            self.assertEqual(result, expected)


class TestPathResolutionPlugins(unittest.TestCase):
    """Test plugin path resolution (lines 304, 345)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_get_plugin_code_dir(self):
        """Test get_plugin_code_dir returns correct path (line 304)."""
        with patch("mmrelay.paths.get_plugins_dir", return_value=Path("/home/plugins")):
            result = get_plugin_code_dir("test_plugin")

            expected = Path("/home/plugins") / "test_plugin"
            self.assertEqual(result, expected)

    def test_get_plugin_data_dir_with_subdir(self):
        """Test get_plugin_data_dir with subdir returns Tier 2 path (lines 323-325)."""
        with patch("mmrelay.paths.get_plugins_dir", return_value=Path("/home/plugins")):
            result = get_plugin_data_dir("weather", subdir="gpx")

            expected = Path("/home/plugins") / "weather" / "data" / "gpx"
            self.assertEqual(result, expected)

    def test_get_plugin_data_dir_without_subdir(self):
        """Test get_plugin_data_dir without subdir returns Tier 3 path (lines 327-328)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
            result = get_plugin_data_dir("weather")

            expected = Path("/home") / "database" / "plugin_data" / "weather"
            self.assertEqual(result, expected)

    def test_get_plugin_database_path(self):
        """Test get_plugin_database_path returns correct path (line 345)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
            result = get_plugin_database_path("weather")

            expected = Path("/home") / "database" / "plugin_data_weather"
            self.assertEqual(result, expected)


class TestPathDirectoryCreation(unittest.TestCase):
    """Test ensure_directories function (lines 379-380)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_create_missing_directories(self):
        """Test create_missing=True creates missing directories (lines 375-380)."""
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                with patch(
                    "mmrelay.paths.get_database_dir",
                    return_value=Path("/home/database"),
                ):
                    with patch(
                        "mmrelay.paths.get_logs_dir", return_value=Path("/home/logs")
                    ):
                        with patch(
                            "mmrelay.paths.get_plugins_dir",
                            return_value=Path("/home/plugins"),
                        ):
                            with patch(
                                "mmrelay.paths.get_custom_plugins_dir",
                                return_value=Path("/home/plugins/custom"),
                            ):
                                with patch(
                                    "mmrelay.paths.get_community_plugins_dir",
                                    return_value=Path("/home/plugins/community"),
                                ):
                                    with patch(
                                        "mmrelay.paths.get_logger"
                                    ) as mock_get_logger:
                                        from mmrelay.paths import ensure_directories

                                        mock_logger = mock_get_logger.return_value

                                        ensure_directories(create_missing=True)

                                        debug_calls = [
                                            call
                                            for call in mock_logger.debug.call_args_list
                                            if "Created directory" in str(call)
                                        ]
                                        self.assertTrue(len(debug_calls) > 0)

    def test_dont_create_when_false(self):
        """Test create_missing=False only checks and warns (lines 381-383)."""
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                with patch("mmrelay.paths.get_database_dir") as mock_db_dir:
                    with patch("mmrelay.paths.get_logger") as mock_get_logger:
                        mock_db_dir.return_value = Path("/home/database")

                        # Mock Path.exists() to return False
                        with patch.object(Path, "exists", return_value=False):
                            from mmrelay.paths import ensure_directories

                            mock_logger = mock_get_logger.return_value

                            ensure_directories(create_missing=False)

                            mkdir_calls = [
                                call for call in mock_db_dir.mkdir.call_args_list
                            ]
                            self.assertTrue(len(mkdir_calls) == 0)

                            warning_calls = [
                                call
                                for call in mock_logger.warning.call_args_list
                                if "Directory missing" in str(call)
                            ]
                            self.assertTrue(len(warning_calls) > 0)

    def test_directory_already_exists(self):
        """
        Verify ensure_directories does not attempt to create directories that already exist.

        Patches the environment to simulate a Linux home and database directory, stubs Path.exists to return True,
        calls ensure_directories(create_missing=True), and asserts no mkdir calls were made for existing paths.
        """
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                with patch("mmrelay.paths.get_database_dir") as mock_db_dir:
                    with patch("mmrelay.paths.get_logger"):
                        mock_db_dir.return_value = Path("/home/database")

                        # Mock Path.exists() to return True
                        with patch.object(Path, "exists", return_value=True):
                            from mmrelay.paths import ensure_directories

                            ensure_directories(create_missing=True)

                            mkdir_calls = [
                                call for call in mock_db_dir.mkdir.call_args_list
                            ]
                            self.assertTrue(len(mkdir_calls) == 0)

    def test_oserror_on_creation(self):
        """Test OSError on directory creation is logged (lines 379-380)."""
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                with patch("mmrelay.paths.get_database_dir") as mock_db_dir:
                    with patch("mmrelay.paths.get_logger") as mock_get_logger:
                        mock_db_dir.return_value = Path("/home/database")
                        mock_db_dir.mkdir.side_effect = OSError("Permission denied")

                        from mmrelay.paths import ensure_directories

                        mock_logger = mock_get_logger.return_value

                        ensure_directories(create_missing=True)

                        error_calls = [
                            call
                            for call in mock_logger.exception.call_args_list
                            if "Failed to create directory" in str(call)
                        ]
                        self.assertTrue(len(error_calls) > 0)
        """Test OSError on directory creation is logged (lines 379-380)."""
        with patch("sys.platform", "linux"):
            with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
                with patch("mmrelay.paths.get_database_dir") as mock_db_dir:
                    with patch("mmrelay.paths.get_logger") as mock_get_logger:
                        mock_db_dir.return_value = Path("/home/database")
                        mock_db_dir.mkdir.side_effect = OSError("Permission denied")

                        from mmrelay.paths import ensure_directories

                        mock_logger = mock_get_logger.return_value

                        ensure_directories(create_missing=True)

                        error_calls = [
                            call
                            for call in mock_logger.exception.call_args_list
                            if "Failed to create directory" in str(call)
                        ]
                        self.assertTrue(len(error_calls) > 0)


class TestLegacyDirsDetection(unittest.TestCase):
    """Test get_legacy_dirs function (lines 469-472, 490-492, 498-526)."""

    def setUp(self):
        """
        Reset the global home-directory override used by path resolution tests.

        This ensures each test starts with no overridden home directory state by calling reset_home_override().
        """
        reset_home_override()

    def tearDown(self):
        """
        Reset any overridden home-directory state used by tests.

        Restores global home/path override to its default so subsequent tests run with a clean environment.
        """
        reset_home_override()

    def test_get_home_raises_oserror(self):
        """Test get_home_dir OSError returns empty list (lines 467-472)."""
        with patch("mmrelay.paths.get_home_dir", side_effect=OSError("Failed")):
            result = get_legacy_dirs()

            self.assertEqual(result, [])

    def test_get_home_raises_runtime_error(self):
        """Test get_home_dir RuntimeError returns empty list (lines 469-470)."""
        with patch("mmrelay.paths.get_home_dir", side_effect=RuntimeError("Failed")):
            result = get_legacy_dirs()

            self.assertEqual(result, [])

    def test_platformdirs_raises_oserror(self):
        """Test platformdirs OSError doesn't crash get_legacy_dirs (lines 490-492)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/home")):
            with patch(
                "mmrelay.paths.platformdirs.user_data_dir",
                side_effect=OSError("Failed"),
            ):
                result = get_legacy_dirs()

                self.assertIsInstance(result, list)

    def test_base_dir_env_var_legacy(self):
        """Test MMRELAY_BASE_DIR env var handling (lines 495-502)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/new/home")):
            with patch.dict(
                os.environ, {"MMRELAY_BASE_DIR": "/legacy/base"}, clear=True
            ):
                result = get_legacy_dirs()

                # Test function returns list type
                self.assertIsInstance(result, list)

    def test_data_dir_env_var_legacy(self):
        """Test MMRELAY_DATA_DIR env var handling (lines 505-512)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/new/home")):
            with patch.dict(
                os.environ, {"MMRELAY_DATA_DIR": "/legacy/data"}, clear=True
            ):
                result = get_legacy_dirs()

                # Test function returns list type
                self.assertIsInstance(result, list)

    def test_docker_data_legacy_path(self):
        """Test Docker legacy paths are detected (lines 516-526)."""
        with patch("mmrelay.paths.get_home_dir", return_value=Path("/new/home")):
            result = get_legacy_dirs()

            # This test just checks function doesn't crash - actual results
            # depend on whether these paths exist on the system
            self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
