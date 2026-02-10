"""
Targeted test coverage for CLI functions in cli.py.

Tests covering:
- _apply_dir_overrides (lines 92-103, 120-187)
- _find_credentials_json_path (lines 780-822)
- handle_subcommand dispatch (lines 1587-1612)
- handle_config_paths (lines 1615-1736)
- handle_paths_command (lines 1789-1864)
- handle_doctor_command migration status (lines 1884-1969)
- handle_migrate_command (lines 2205-2251)
- handle_verify_migration_command import guard
- handle_doctor_command import guard
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.cli import (
    _apply_dir_overrides,
    _find_credentials_json_path,
    handle_config_paths,
    handle_doctor_command,
    handle_migrate_command,
    handle_paths_command,
    handle_subcommand,
    handle_verify_migration_command,
)
from mmrelay.constants.app import APP_DISPLAY_NAME


def _make_paths_info(**overrides):
    """
    Create a standard paths_info dict for testing.

    Provides a complete resolve_all_paths return value with sensible defaults,
    allowing individual fields to be overridden as needed.

    Parameters:
        **overrides: Key-value pairs to override in the base dict.

    Returns:
        dict: A complete paths_info dictionary.
    """
    base = {
        "home": "/custom/home",
        "matrix_dir": "/custom/home/matrix",
        "home_source": "--home",
        "cli_override": None,
        "legacy_sources": [],
        "credentials_path": "/custom/home/matrix/credentials.json",
        "database_dir": "/custom/home/database",
        "store_dir": "/custom/home/matrix/store",
        "logs_dir": "/custom/home/logs",
        "log_file": "/custom/home/logs/mmrelay.log",
        "plugins_dir": "/custom/home/plugins",
        "custom_plugins_dir": "/custom/home/plugins/custom",
        "community_plugins_dir": "/custom/home/plugins/community",
        "env_vars_detected": {},
    }
    base.update(overrides)
    return base


class TestApplyDirOverridesEarlyReturn(unittest.TestCase):
    """Tests for _apply_dir_overrides early return (lines 102-103)."""

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    def test_none_args_returns_early(self, mock_makedirs, mock_set_override):
        """Test _apply_dir_overrides returns early when args is None."""
        result = _apply_dir_overrides(None)
        self.assertIsNone(result)
        mock_set_override.assert_not_called()
        mock_makedirs.assert_not_called()

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    def test_no_override_flags_returns_early(self, mock_makedirs, mock_set_override):
        """Test _apply_dir_overrides returns early when no override flags are set."""
        args = MagicMock()
        args.home = None
        args.base_dir = None
        args.data_dir = None

        result = _apply_dir_overrides(args)
        self.assertIsNone(result)
        mock_set_override.assert_not_called()
        mock_makedirs.assert_not_called()


class TestApplyDirOverridesPriority(unittest.TestCase):
    """Tests for dir override priority logic (lines 120-187)."""

    def setUp(self):
        """
        Prepare test fixture with a mocked `args` object.

        Creates `self.args` as a MagicMock and sets its `home`, `base_dir`, and `data_dir` attributes to `None` to simulate no directory override flags.
        """
        self.args = MagicMock()
        self.args.home = None
        self.args.base_dir = None
        self.args.data_dir = None

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_home_flag_overrides_base_and_data(
        self, mock_print, _mock_makedirs, mock_set_override
    ):
        """Test --home flag overrides --base-dir and --data-dir."""
        self.args.home = "/custom/home"
        self.args.base_dir = "/old/base"
        self.args.data_dir = "/old/data"

        _apply_dir_overrides(self.args)

        mock_set_override.assert_called_once()
        self.assertEqual(
            mock_set_override.call_args[0][0], os.path.abspath("/custom/home")
        )
        self.assertEqual(mock_set_override.call_args[1]["source"], "--home")

        warning_calls = [c for c in mock_print.call_args_list if "overrides" in str(c)]
        self.assertTrue(len(warning_calls) > 0)

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_base_dir_flag_overrides_data_dir(
        self, mock_print, _mock_makedirs, mock_set_override
    ):
        """Test --base-dir flag overrides --data-dir."""
        self.args.home = None
        self.args.base_dir = "/my/base"
        self.args.data_dir = "/my/data"

        _apply_dir_overrides(self.args)

        mock_set_override.assert_called_once()
        self.assertEqual(mock_set_override.call_args[0][0], os.path.abspath("/my/base"))
        self.assertEqual(mock_set_override.call_args[1]["source"], "--base-dir")

        warning_calls = [c for c in mock_print.call_args_list if "overrides" in str(c)]
        self.assertTrue(len(warning_calls) > 0)

        deprecation_calls = [
            c for c in mock_print.call_args_list if "deprecated" in str(c)
        ]
        self.assertTrue(len(deprecation_calls) > 0)

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_data_dir_flag_used_alone(
        self, mock_print, _mock_makedirs, mock_set_override
    ):
        """Test --data-dir flag is used when alone."""
        self.args.home = None
        self.args.base_dir = None
        self.args.data_dir = "/my/data"

        _apply_dir_overrides(self.args)

        mock_set_override.assert_called_once()
        self.assertEqual(mock_set_override.call_args[0][0], os.path.abspath("/my/data"))
        self.assertEqual(mock_set_override.call_args[1]["source"], "--data-dir")

        warning_calls = [c for c in mock_print.call_args_list if "deprecated" in str(c)]
        self.assertTrue(len(warning_calls) > 0)

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    def test_home_flag_sets_home_override(self, _mock_makedirs, mock_set_override):
        """Test --home flag sets the paths HOME override."""
        self.args.home = "/custom/home"
        self.args.base_dir = None
        self.args.data_dir = None

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/custom/home")
        mock_set_override.assert_called_once_with(expected_path, source="--home")

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    def test_base_dir_flag_sets_home_override(self, _mock_makedirs, mock_set_override):
        """Test --base-dir flag sets the paths HOME override."""
        self.args.home = None
        self.args.base_dir = "/my/base"
        self.args.data_dir = None

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/my/base")
        mock_set_override.assert_called_once_with(expected_path, source="--base-dir")

    @patch("mmrelay.paths.set_home_override")
    @patch("os.makedirs")
    def test_data_dir_flag_sets_home_override(self, _mock_makedirs, mock_set_override):
        """Test --data-dir flag sets the paths HOME override."""
        self.args.home = None
        self.args.base_dir = None
        self.args.data_dir = "/my/data"

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/my/data")
        mock_set_override.assert_called_once_with(expected_path, source="--data-dir")


class TestFindCredentialsJsonPath(unittest.TestCase):
    """Tests for _find_credentials_json_path function."""

    @patch("mmrelay.config.get_credentials_search_paths")
    @patch("os.path.exists")
    def test_returns_path_when_found(self, mock_exists, mock_get_paths):
        """Test returns path when credentials found by search helper."""
        mock_get_paths.return_value = [
            "/path1/credentials.json",
            "/path2/credentials.json",
        ]
        mock_exists.side_effect = lambda p: p == "/path2/credentials.json"

        result = _find_credentials_json_path(None)

        self.assertEqual(result, "/path2/credentials.json")

    @patch("mmrelay.config.get_credentials_search_paths")
    @patch("os.path.exists")
    def test_returns_none_when_not_found(self, mock_exists, mock_get_paths):
        """Test returns None when credentials not found anywhere."""
        mock_get_paths.return_value = ["/path1/credentials.json"]
        mock_exists.return_value = False

        result = _find_credentials_json_path(None)

        self.assertIsNone(result)


class TestHandleSubcommandDispatch(unittest.TestCase):
    """Tests for handle_subcommand command dispatch (lines 1587-1612)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.cli.handle_service_command")
    def test_dispatch_to_service_command(self, mock_handle_service):
        """Test dispatch to service command."""
        self.args.command = "service"
        mock_handle_service.return_value = 0

        result = handle_subcommand(self.args)

        self.assertEqual(result, 0)
        mock_handle_service.assert_called_once_with(self.args)

    @patch("mmrelay.cli.handle_paths_command")
    def test_dispatch_to_paths_command(self, mock_handle_paths):
        """Test dispatch to paths command."""
        self.args.command = "paths"
        mock_handle_paths.return_value = 0

        result = handle_subcommand(self.args)

        self.assertEqual(result, 0)
        mock_handle_paths.assert_called_once_with(self.args)

    @patch("mmrelay.cli.handle_doctor_command")
    def test_dispatch_to_doctor_command(self, mock_handle_doctor):
        """Test dispatch to doctor command."""
        self.args.command = "doctor"
        mock_handle_doctor.return_value = 0

        result = handle_subcommand(self.args)

        self.assertEqual(result, 0)
        mock_handle_doctor.assert_called_once_with(self.args)

    @patch("mmrelay.cli.handle_verify_migration_command")
    def test_dispatch_to_verify_migration_command(self, mock_handle_verify):
        """Test dispatch to verify-migration command."""
        self.args.command = "verify-migration"
        mock_handle_verify.return_value = 0

        result = handle_subcommand(self.args)

        self.assertEqual(result, 0)
        mock_handle_verify.assert_called_once_with(self.args)

    @patch("mmrelay.cli.handle_migrate_command")
    def test_dispatch_to_migrate_command(self, mock_handle_migrate):
        """Test dispatch to migrate command."""
        self.args.command = "migrate"
        mock_handle_migrate.return_value = 0

        result = handle_subcommand(self.args)

        self.assertEqual(result, 0)
        mock_handle_migrate.assert_called_once_with(self.args)

    @patch("builtins.print")
    def test_unknown_command(self, mock_print):
        """Test unknown command prints error and returns 1."""
        self.args.command = "unknown"

        result = handle_subcommand(self.args)

        self.assertEqual(result, 1)
        mock_print.assert_called_once_with("Unknown command: unknown")


class TestHandleMigrateCommand(unittest.TestCase):
    """Tests for handle_migrate_command function (lines 2205-2251)."""

    def setUp(self):
        """
        Create self.args as a MagicMock and set migration-related flags `dry_run` and `force` to False.

        Provides a default `args` object for tests that require these migration flag attributes.
        """
        self.args = MagicMock()
        self.args.dry_run = False
        self.args.force = False

    @patch("mmrelay.migrate.perform_migration")
    @patch("builtins.print")
    def test_successful_migration(self, mock_print, mock_perform):
        """Test successful migration prints success message."""
        mock_perform.return_value = {
            "success": True,
            "migrations": [
                {"type": "credentials", "result": {"success": True, "message": "Done"}}
            ],
        }

        result = handle_migrate_command(self.args)

        self.assertEqual(result, 0)
        success_calls = [
            c for c in mock_print.call_args_list if "completed successfully" in str(c)
        ]
        self.assertTrue(len(success_calls) > 0)

    @patch("mmrelay.migrate.perform_migration")
    @patch("builtins.print")
    def test_failed_migration(self, mock_print, mock_perform):
        """Test failed migration prints error message."""
        mock_perform.return_value = {
            "success": False,
            "error": "Test error",
        }

        result = handle_migrate_command(self.args)

        self.assertEqual(result, 1)
        error_calls = [
            c for c in mock_print.call_args_list if "Migration failed" in str(c)
        ]
        self.assertTrue(len(error_calls) > 0)

    @patch("builtins.print")
    def test_import_error(self, mock_print):
        """Test ImportError when migrate module cannot be imported."""
        import builtins

        original_import = builtins.__import__

        def _block_migrate(name, *args, **kwargs):
            if name == "mmrelay.migrate":
                raise ImportError("mocked import error")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_migrate):
            result = handle_migrate_command(self.args)

        self.assertEqual(result, 1)
        error_calls = [
            c for c in mock_print.call_args_list if "Error importing" in str(c)
        ]
        self.assertTrue(len(error_calls) > 0)


class TestHandleConfigPaths(unittest.TestCase):
    """Tests for handle_config_paths function (lines 1615-1736)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_home_directory(self, mock_print, mock_resolve):
        """Test prints HOME directory and source."""
        mock_resolve.return_value = _make_paths_info()

        result = handle_config_paths(self.args)

        self.assertEqual(result, 0)
        home_calls = [c for c in mock_print.call_args_list if "HOME" in str(c)]
        self.assertTrue(len(home_calls) > 0)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_shows_legacy_data_warning(self, mock_print, mock_exists, mock_resolve):
        """Test shows warning when legacy data is detected."""
        mock_resolve.return_value = _make_paths_info(
            legacy_sources=["/legacy1", "/legacy2"]
        )
        mock_exists.return_value = True

        result = handle_config_paths(self.args)

        self.assertEqual(result, 0)
        warning_calls = [
            c for c in mock_print.call_args_list if "Legacy data detected" in str(c)
        ]
        self.assertTrue(len(warning_calls) > 0)


class TestHandlePathsCommand(unittest.TestCase):
    """Tests for handle_paths_command function (lines 1789-1864)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_header(self, mock_print, mock_resolve):
        """Test prints header."""
        mock_resolve.return_value = _make_paths_info()

        result = handle_paths_command(self.args)

        self.assertEqual(result, 0)
        header_calls = [
            c
            for c in mock_print.call_args_list
            if f"{APP_DISPLAY_NAME} Path Configuration" in str(c)
        ]
        self.assertTrue(len(header_calls) > 0)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_shows_legacy_warning(self, mock_print, mock_exists, mock_resolve):
        """Test shows legacy data warning."""
        mock_resolve.return_value = _make_paths_info(legacy_sources=["/legacy1"])
        mock_exists.return_value = True

        result = handle_paths_command(self.args)

        self.assertEqual(result, 0)
        warning_calls = [
            c for c in mock_print.call_args_list if "Legacy data detected" in str(c)
        ]
        self.assertTrue(len(warning_calls) > 0)


class TestHandleDoctorMigrationStatus(unittest.TestCase):
    """Tests for handle_doctor_command migration status (lines 1934-1955)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.migrate.verify_migration")
    @patch("mmrelay.migrate.is_migration_needed")
    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_migration_needed_when_true(
        self, mock_print, mock_resolve, mock_needed, mock_verify
    ):
        """Test prints migration recommendation when needed."""
        mock_needed.return_value = True
        mock_resolve.return_value = _make_paths_info(
            home="/home",
            matrix_dir="/home/matrix",
            home_source="default",
            credentials_path="/home/matrix/credentials.json",
            database_dir="/home/database",
            store_dir="/home/matrix/store",
            logs_dir="/home/logs",
            plugins_dir="/home/plugins",
        )
        mock_verify.return_value = {"warnings": [], "errors": []}
        self.args.migration = False

        result = handle_doctor_command(self.args)

        self.assertEqual(result, 0)
        warning_calls = [
            c for c in mock_print.call_args_list if "Migration RECOMMENDED" in str(c)
        ]
        self.assertTrue(len(warning_calls) > 0)

    @patch("mmrelay.migrate.verify_migration")
    @patch("mmrelay.migrate.is_migration_needed")
    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_no_migration_needed_when_false(
        self, mock_print, mock_resolve, mock_needed, mock_verify
    ):
        """Test prints no migration needed when not needed."""
        mock_needed.return_value = False
        mock_resolve.return_value = _make_paths_info(
            home="/home",
            matrix_dir="/home/matrix",
            home_source="default",
            credentials_path="/home/matrix/credentials.json",
            database_dir="/home/database",
            store_dir="/home/matrix/store",
            logs_dir="/home/logs",
            plugins_dir="/home/plugins",
        )
        mock_verify.return_value = {"warnings": [], "errors": []}
        self.args.migration = False

        result = handle_doctor_command(self.args)

        self.assertEqual(result, 0)
        warning_calls = [
            c for c in mock_print.call_args_list if "No migration needed" in str(c)
        ]
        self.assertTrue(len(warning_calls) > 0)


class TestHandleConfigPathsDetails(unittest.TestCase):
    """Tests for handle_config_paths detailed path display (lines 1628-1736)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_all_runtime_paths(self, mock_print, mock_resolve):
        """Test prints all runtime artifact paths."""
        mock_resolve.return_value = _make_paths_info()

        result = handle_config_paths(self.args)

        self.assertEqual(result, 0)
        all_paths = [
            "Credentials",
            "Database",
            "Store",
            "Logs",
            "Plugins",
        ]
        for path_type in all_paths:
            path_calls = [c for c in mock_print.call_args_list if path_type in str(c)]
            self.assertTrue(
                len(path_calls) > 0,
                f"No calls found for {path_type}",
            )


class TestHandlePathsCommandDetails(unittest.TestCase):
    """Tests for handle_paths_command detailed output (lines 1799-1864)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_all_path_sections(self, mock_print, mock_resolve):
        """Test prints all path sections."""
        mock_resolve.return_value = _make_paths_info(
            env_vars_detected={"MMRELAY_HOME": "/test"}
        )

        result = handle_paths_command(self.args)

        self.assertEqual(result, 0)
        env_calls = [
            c for c in mock_print.call_args_list if "Environment Variables" in str(c)
        ]
        self.assertTrue(len(env_calls) > 0)


class TestHandleMigrateCommandDetailed(unittest.TestCase):
    """Tests for handle_migrate_command detailed output (lines 2217-2275)."""

    def setUp(self):
        """
        Create a MagicMock representing parsed CLI arguments and assign it to `self.args` for use by each test.

        This runs before each test method to provide a fresh, configurable `args` mock.
        """
        self.args = MagicMock()

    @patch("mmrelay.migrate.perform_migration")
    @patch("builtins.print")
    def test_prints_dry_run_message(self, mock_print, mock_perform):
        """Test prints dry run message when migration is dry run."""
        self.args.dry_run = True
        self.args.force = False
        mock_perform.return_value = {
            "success": True,
            "migrations": [
                {
                    "type": "test",
                    "result": {
                        "success": True,
                        "message": "Done",
                        "dry_run": True,
                        "action": "copy",
                    },
                }
            ],
        }

        result = handle_migrate_command(self.args)

        self.assertEqual(result, 0)
        dry_calls = [c for c in mock_print.call_args_list if "DRY RUN" in str(c)]
        self.assertTrue(len(dry_calls) > 0)

    @patch("mmrelay.migrate.perform_migration")
    @patch("builtins.print")
    def test_prints_action_details(self, mock_print, mock_perform):
        """Test prints action details (COPY, MOVE, SKIP)."""
        self.args.dry_run = False
        self.args.force = False
        mock_perform.return_value = {
            "success": True,
            "migrations": [
                {
                    "type": "store",
                    "result": {
                        "success": True,
                        "message": "Migrated",
                        "action": "move",
                    },
                },
                {
                    "type": "plugins",
                    "result": {
                        "success": True,
                        "message": "Migrated",
                        "action": "copy",
                    },
                },
            ],
        }

        result = handle_migrate_command(self.args)

        self.assertEqual(result, 0)
        move_calls = [c for c in mock_print.call_args_list if "action: MOVE" in str(c)]
        copy_calls = [c for c in mock_print.call_args_list if "action: COPY" in str(c)]
        self.assertTrue(len(move_calls) > 0)
        self.assertTrue(len(copy_calls) > 0)


class TestHandleVerifyMigrationCommandImportGuard(unittest.TestCase):
    """Tests for handle_verify_migration_command import guard."""

    def setUp(self):
        """Create args mock for tests."""
        self.args = MagicMock()

    @patch("mmrelay.migrate.print_migration_verification")
    @patch("mmrelay.migrate.verify_migration")
    def test_successful_verification(self, mock_verify, mock_print):
        """Test successful verification returns 0."""
        mock_verify.return_value = {"ok": True}
        mock_print.return_value = None

        result = handle_verify_migration_command(self.args)

        self.assertEqual(result, 0)
        mock_verify.assert_called_once()

    @patch("mmrelay.migrate.print_migration_verification")
    @patch("mmrelay.migrate.verify_migration")
    def test_failed_verification(self, mock_verify, mock_print):
        """Test failed verification returns 1."""
        mock_verify.return_value = {"ok": False}
        mock_print.return_value = None

        result = handle_verify_migration_command(self.args)

        self.assertEqual(result, 1)

    @patch("builtins.print")
    def test_import_error_returns_1(self, mock_print):
        """Test ImportError during import returns 1 and prints error."""
        import builtins

        original_import = builtins.__import__

        def _block_migrate(name, *args, **kwargs):
            if name == "mmrelay.migrate":
                raise ImportError("mocked import error")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_migrate):
            result = handle_verify_migration_command(self.args)

        self.assertEqual(result, 1)
        error_calls = [
            c for c in mock_print.call_args_list if "Error importing" in str(c)
        ]
        self.assertTrue(len(error_calls) > 0)


class TestHandleDoctorCommandImportGuard(unittest.TestCase):
    """Tests for handle_doctor_command import guard."""

    def setUp(self):
        """Create args mock for tests."""
        self.args = MagicMock()
        self.args.migration = False

    @patch("builtins.print")
    def test_import_error_returns_1(self, mock_print):
        """Test ImportError during import returns 1 and prints error."""
        import builtins

        original_import = builtins.__import__

        def _block_migrate(name, *args, **kwargs):
            if name in ("mmrelay.migrate", "mmrelay.paths"):
                raise ImportError("mocked import error")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_block_migrate):
            result = handle_doctor_command(self.args)

        self.assertEqual(result, 1)
        error_calls = [
            c for c in mock_print.call_args_list if "Error importing" in str(c)
        ]
        self.assertTrue(len(error_calls) > 0)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("mmrelay.migrate.is_migration_needed")
    @patch("mmrelay.migrate.verify_migration")
    @patch("builtins.print")
    def test_successful_doctor_no_migration(
        self, _mock_print, mock_verify, mock_needed, mock_resolve
    ):
        """Test successful doctor command returns 0."""
        mock_resolve.return_value = _make_paths_info(
            home="/home",
            matrix_dir="/home/matrix",
            home_source="default",
            credentials_path="/home/matrix/credentials.json",
            database_dir="/home/database",
            store_dir="/home/matrix/store",
            logs_dir="/home/logs",
            plugins_dir="/home/plugins",
        )
        mock_needed.return_value = False
        mock_verify.return_value = {"warnings": [], "errors": []}

        result = handle_doctor_command(self.args)

        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
