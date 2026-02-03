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
"""

import os
import sys
import unittest
from pathlib import Path
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
)


class TestApplyDirOverridesEarlyReturn(unittest.TestCase):
    """Tests for _apply_dir_overrides early return (lines 102-103)."""

    @patch("mmrelay.paths.set_home_override")
    @patch("mmrelay.config")
    @patch("os.makedirs")
    def test_none_args_returns_early(
        self, mock_makedirs, mock_config, mock_set_override
    ):
        """Test _apply_dir_overrides returns early when args is None."""
        result = _apply_dir_overrides(None)
        self.assertIsNone(result)
        mock_set_override.assert_not_called()
        mock_config.assert_not_called()
        mock_makedirs.assert_not_called()

    @patch("mmrelay.paths.set_home_override")
    @patch("mmrelay.config")
    @patch("os.makedirs")
    def test_no_override_flags_returns_early(
        self, mock_makedirs, mock_config, mock_set_override
    ):
        """Test _apply_dir_overrides returns early when no override flags are set."""
        args = MagicMock()
        args.home = None
        args.base_dir = None
        args.data_dir = None

        result = _apply_dir_overrides(args)
        self.assertIsNone(result)
        mock_set_override.assert_not_called()
        mock_config.assert_not_called()
        mock_makedirs.assert_not_called()


class TestApplyDirOverridesPriority(unittest.TestCase):
    """Tests for dir override priority logic (lines 120-187)."""

    def setUp(self):
        self.args = MagicMock()
        self.args.home = None
        self.args.base_dir = None
        self.args.data_dir = None

    @patch("mmrelay.paths.set_home_override")
    @patch("mmrelay.config")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_home_flag_overrides_base_and_data(
        self, mock_print, mock_makedirs, mock_config, mock_set_override
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
    @patch("mmrelay.config")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_base_dir_flag_overrides_data_dir(
        self, mock_print, mock_makedirs, mock_config, mock_set_override
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
    @patch("mmrelay.config")
    @patch("os.makedirs")
    @patch("builtins.print")
    def test_data_dir_flag_used_alone(
        self, mock_print, mock_makedirs, mock_config, mock_set_override
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
    @patch("mmrelay.config")
    @patch("os.makedirs")
    def test_home_flag_sets_legacy_variables(
        self, mock_makedirs, mock_config, mock_set_override
    ):
        """Test --home flag sets both legacy custom_base_dir and custom_data_dir."""
        self.args.home = "/custom/home"
        self.args.base_dir = None
        self.args.data_dir = None

        mock_config.custom_base_dir = "/old/base"
        mock_config.custom_data_dir = "/old/data"

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/custom/home")
        self.assertEqual(mock_config.custom_base_dir, expected_path)
        self.assertEqual(mock_config.custom_data_dir, expected_path)

    @patch("mmrelay.paths.set_home_override")
    @patch("mmrelay.config")
    @patch("os.makedirs")
    def test_base_dir_flag_sets_legacy_variables(
        self, mock_makedirs, mock_config, mock_set_override
    ):
        """Test --base-dir flag sets both legacy custom_base_dir and custom_data_dir."""
        self.args.home = None
        self.args.base_dir = "/my/base"
        self.args.data_dir = None

        mock_config.custom_base_dir = "/old/base"
        mock_config.custom_data_dir = "/old/data"

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/my/base")
        self.assertEqual(mock_config.custom_base_dir, expected_path)
        self.assertEqual(mock_config.custom_data_dir, expected_path)

    @patch("mmrelay.paths.set_home_override")
    @patch("mmrelay.config")
    @patch("os.makedirs")
    def test_data_dir_flag_sets_legacy_variables(
        self, mock_makedirs, mock_config, mock_set_override
    ):
        """Test --data-dir flag sets both legacy custom_base_dir and custom_data_dir."""
        self.args.home = None
        self.args.base_dir = None
        self.args.data_dir = "/my/data"

        mock_config.custom_base_dir = "/old/base"
        mock_config.custom_data_dir = "/old/data"

        _apply_dir_overrides(self.args)

        expected_path = os.path.abspath("/my/data")
        self.assertEqual(mock_config.custom_base_dir, expected_path)
        self.assertEqual(mock_config.custom_data_dir, expected_path)


class TestFindCredentialsJsonPath(unittest.TestCase):
    """Tests for _find_credentials_json_path function (lines 780-822)."""

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("os.path.dirname")
    def test_returns_path_when_found_in_config_dir(
        self, mock_dirname, mock_exists, mock_resolve
    ):
        """Test returns path when credentials found in config directory."""
        config_path = "/config/config.yaml"
        mock_dirname.return_value = "/config"
        mock_resolve.return_value = {
            "credentials_path": "/home/credentials.json",
            "legacy_sources": [],
        }
        mock_exists.side_effect = lambda p: "credentials.json" in p

        result = _find_credentials_json_path(config_path)

        self.assertIsNotNone(result)
        self.assertEqual(result, "/config/credentials.json")

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    def test_returns_none_when_not_found(self, mock_exists, mock_resolve):
        """Test returns None when credentials not found anywhere."""
        mock_resolve.return_value = {
            "credentials_path": "/home/credentials.json",
            "legacy_sources": ["/legacy1", "/legacy2"],
        }
        mock_exists.return_value = False

        result = _find_credentials_json_path(None)

        self.assertIsNone(result)


class TestHandleSubcommandDispatch(unittest.TestCase):
    """Tests for handle_subcommand command dispatch (lines 1587-1612)."""

    def setUp(self):
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
        self.args = MagicMock()
        self.args.dry_run = False
        self.args.force = False
        self.args.move = False

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
        with patch(
            "mmrelay.migrate.perform_migration", side_effect=ImportError("Test error")
        ):
            result = handle_migrate_command(self.args)

        self.assertEqual(result, 1)
        error_calls = [
            c for c in mock_print.call_args_list if "Error importing" in str(c)
        ]
        self.assertTrue(len(error_calls) > 0)


class TestHandleConfigPaths(unittest.TestCase):
    """Tests for handle_config_paths function (lines 1615-1736)."""

    def setUp(self):
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_home_directory(self, mock_print, mock_resolve):
        """Test prints HOME directory and source."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": [],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {},
        }

        result = handle_config_paths(self.args)

        self.assertEqual(result, 0)
        home_calls = [c for c in mock_print.call_args_list if "HOME" in str(c)]
        self.assertTrue(len(home_calls) > 0)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_shows_legacy_data_warning(self, mock_print, mock_exists, mock_resolve):
        """Test shows warning when legacy data is detected."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": ["/legacy1", "/legacy2"],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {},
        }
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
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_header(self, mock_print, mock_resolve):
        """Test prints header."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": [],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {},
        }

        result = handle_paths_command(self.args)

        self.assertEqual(result, 0)
        header_calls = [
            c
            for c in mock_print.call_args_list
            if "MMRelay Path Configuration" in str(c)
        ]
        self.assertTrue(len(header_calls) > 0)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_shows_legacy_warning(self, mock_print, mock_exists, mock_resolve):
        """Test shows legacy data warning."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": ["/legacy1"],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {},
        }
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
        mock_resolve.return_value = {
            "home": "/home",
            "home_source": "default",
            "credentials_path": "/home/credentials.json",
            "database_dir": "/home/database",
            "store_dir": "/home/store",
            "logs_dir": "/home/logs",
            "plugins_dir": "/home/plugins",
            "legacy_sources": [],
            "env_vars_detected": {},
            "cli_override": None,
        }
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
        mock_resolve.return_value = {
            "home": "/home",
            "home_source": "default",
            "credentials_path": "/home/credentials.json",
            "database_dir": "/home/database",
            "store_dir": "/home/store",
            "logs_dir": "/home/logs",
            "plugins_dir": "/home/plugins",
            "legacy_sources": [],
            "env_vars_detected": {},
            "cli_override": None,
        }
        mock_verify.return_value = {"warnings": [], "errors": []}
        self.args.migration = False

        result = handle_doctor_command(self.args)

        self.assertEqual(result, 0)
        warning_calls = [
            c for c in mock_print.call_args_list if "No migration needed" in str(c)
        ]
        self.assertTrue(len(warning_calls) > 0)


class TestFindCredentialsJsonPathLegacy(unittest.TestCase):
    """Tests for _find_credentials_json_path legacy path discovery (lines 811-812)."""

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    @patch("builtins.print")
    def test_returns_legacy_path_when_found(
        self, mock_print, mock_exists, mock_resolve
    ):
        """Test returns path when credentials found in legacy location."""
        mock_resolve.return_value = {
            "credentials_path": "/home/credentials.json",
            "legacy_sources": ["/legacy1"],
        }
        mock_exists.side_effect = lambda p: "legacy1" in p and "credentials" in p

        result = _find_credentials_json_path(None)

        self.assertIsNotNone(result)
        if result is not None:
            self.assertIn("legacy1", result)

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("os.path.exists")
    def test_returns_home_path_when_no_legacy(self, mock_exists, mock_resolve):
        """Test returns home path when no legacy path exists."""
        mock_resolve.return_value = {
            "credentials_path": "/home/credentials.json",
            "legacy_sources": [],
        }
        mock_exists.side_effect = lambda p: "home/credentials" in p

        result = _find_credentials_json_path(None)

        self.assertEqual(result, "/home/credentials.json")


class TestHandleConfigPathsDetails(unittest.TestCase):
    """Tests for handle_config_paths detailed path display (lines 1628-1736)."""

    def setUp(self):
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_all_runtime_paths(self, mock_print, mock_resolve):
        """Test prints all runtime artifact paths."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": [],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {},
        }

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
        self.args = MagicMock()

    @patch("mmrelay.paths.resolve_all_paths")
    @patch("builtins.print")
    def test_prints_all_path_sections(self, mock_print, mock_resolve):
        """Test prints all path sections."""
        mock_resolve.return_value = {
            "home": "/custom/home",
            "home_source": "--home",
            "cli_override": None,
            "legacy_sources": [],
            "credentials_path": "/custom/home/credentials.json",
            "database_dir": "/custom/home/database",
            "store_dir": "/custom/home/store",
            "logs_dir": "/custom/home/logs",
            "log_file": "/custom/home/logs/mmrelay.log",
            "plugins_dir": "/custom/home/plugins",
            "custom_plugins_dir": "/custom/home/plugins/custom",
            "community_plugins_dir": "/custom/home/plugins/community",
            "env_vars_detected": {"MMRELAY_HOME": "/test"},
        }

        result = handle_paths_command(self.args)

        self.assertEqual(result, 0)
        env_calls = [
            c for c in mock_print.call_args_list if "Environment Variables" in str(c)
        ]
        self.assertTrue(len(env_calls) > 0)


class TestHandleMigrateCommandDetailed(unittest.TestCase):
    """Tests for handle_migrate_command detailed output (lines 2217-2275)."""

    def setUp(self):
        self.args = MagicMock()

    @patch("mmrelay.migrate.perform_migration")
    @patch("builtins.print")
    def test_prints_dry_run_message(self, mock_print, mock_perform):
        """Test prints dry run message when migration is dry run."""
        self.args.dry_run = True
        self.args.force = False
        self.args.move = False
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
        self.args.move = True
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
        move_calls = [c for c in mock_print.call_args_list if "Action: MOVE" in str(c)]
        copy_calls = [c for c in mock_print.call_args_list if "Action: COPY" in str(c)]
        self.assertTrue(len(move_calls) > 0)
        self.assertTrue(len(copy_calls) > 0)


if __name__ == "__main__":
    unittest.main()
