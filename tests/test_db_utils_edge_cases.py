#!/usr/bin/env python3
"""
Test suite for Database utilities edge cases and error handling in MMRelay.

Tests edge cases and error handling including:
- Database connection failures
- Corrupted database handling
- Concurrent access issues
- File permission errors
- Database migration edge cases
- Transaction rollback scenarios
- Memory constraints and large datasets
"""

import os
import shutil
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from mmrelay.db_utils import (
    _reset_db_manager,
    clear_db_path_cache,
    delete_plugin_data,
    get_db_path,
    get_longname,
    get_message_map_by_matrix_event_id,
    get_message_map_by_meshtastic_id,
    get_plugin_data_for_node,
    get_shortname,
    initialize_database,
    prune_message_map,
    save_longname,
    save_shortname,
    store_message_map,
    store_plugin_data,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)


@pytest.fixture(autouse=True)
def reset_db_utils_state() -> None:
    """Reset cached database state between tests."""
    clear_db_path_cache()
    _reset_db_manager()
    import mmrelay.db_utils

    mmrelay.db_utils.config = None
    yield
    clear_db_path_cache()
    _reset_db_manager()


class TestDBUtilsEdgeCases:
    """Test cases for Database utilities edge cases and error handling."""

    def test_get_db_path_permission_error(self):
        """
        Test that get_db_path returns a valid database path even if directory creation fails due to permission errors.
        """
        with patch(
            "mmrelay.db_utils.resolve_all_paths",
            return_value={"database_dir": "/readonly/data", "legacy_sources": []},
        ):
            with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
                # Should still return a path even if directory creation fails
                result = get_db_path()
                assert "meshtastic.sqlite" in result

    def test_get_db_path_custom_config_invalid_path(self):
        """
        Test that get_db_path returns a string path when a custom config specifies an invalid database path and directory creation fails.

        Simulates an OSError during directory creation and verifies that get_db_path handles the error gracefully by still returning a string path.
        """
        import mmrelay.db_utils

        mmrelay.db_utils.config = {
            "database": {"path": "/nonexistent/invalid/path/db.sqlite"}
        }

        with patch("os.makedirs", side_effect=OSError("Cannot create directory")):
            # Should handle the error gracefully
            result = get_db_path()
            assert isinstance(result, str)

    def test_initialize_database_connection_failure(self):
        """
        Verify initialize_database raises sqlite3.Error when DatabaseManager.run_sync fails and that logger.exception is invoked.
        """
        # Reset any cached database manager to ensure fresh state
        _reset_db_manager()

        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            # Make run_sync raise the sqlite3.Error to simulate connection failure during initialization
            mock_manager.run_sync.side_effect = sqlite3.Error("Connection failed")
            mock_get_manager.return_value = mock_manager

            with patch("mmrelay.db_utils.logger") as mock_logger:
                # Should raise exception on connection failure (fail fast)
                with pytest.raises(sqlite3.Error):
                    initialize_database()
                mock_logger.exception.assert_called()

    def test_initialize_database_corrupted_database(self):
        """
        Test that initialize_database raises sqlite3.DatabaseError when attempting to initialize a corrupted database file.
        """
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as temp_db:
            # Write invalid data to simulate corruption
            temp_db.write(b"corrupted database content")
            temp_db_path = temp_db.name

        try:
            with patch("mmrelay.db_utils.get_db_path", return_value=temp_db_path):
                with patch("mmrelay.db_utils.logger"):
                    # Should raise exception on corrupted database (fail fast)
                    with pytest.raises(sqlite3.DatabaseError):
                        initialize_database()
        finally:
            os.unlink(temp_db_path)

    def test_save_longname_database_locked(self):
        """
        Test that save_longname handles a locked database by simulating a database lock error.

        Verifies that the function does not crash or raise unhandled exceptions when an OperationalError indicating "database is locked" occurs during execution.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("database is locked")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle database lock gracefully
            save_longname("test_id", "test_name")

    def test_save_shortname_constraint_violation(self):
        """
        Test that save_shortname handles database constraint violations gracefully.

        Simulates a constraint violation during the save_shortname operation and verifies that the function does not raise an exception.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = sqlite3.IntegrityError(
                "constraint violation"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle constraint violation gracefully
            save_shortname("test_id", "test_name")

    def test_get_longname_connection_error(self):
        """
        Test that get_longname returns None when a database connection error occurs.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            # Make run_sync raise the sqlite3.Error to simulate connection failure
            mock_manager.run_sync.side_effect = sqlite3.Error("Connection failed")
            mock_get_manager.return_value = mock_manager

            result = get_longname("test_id")
            assert result is None

    def test_get_shortname_table_not_exists(self):
        """
        Test that get_shortname returns None when the database table does not exist.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            # Make run_sync raise the OperationalError
            mock_manager.run_sync.side_effect = sqlite3.OperationalError(
                "no such table"
            )
            mock_get_manager.return_value = mock_manager

            result = get_shortname("test_id")
            assert result is None

    def test_store_message_map_disk_full(self):
        """
        Test that store_message_map handles disk full (disk I/O error) conditions gracefully.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("disk I/O error")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle disk full error gracefully
            store_message_map("mesh_id", "matrix_id", "room_id", "text")

    def test_get_message_map_by_meshtastic_id_malformed_data(self):
        """
        Test that get_message_map_by_meshtastic_id returns None when the database returns malformed or incomplete data.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            # Return malformed data (missing columns - should have 4 but only has 1)
            mock_manager.run_sync.return_value = ("incomplete_data",)
            mock_get_manager.return_value = mock_manager

            result = get_message_map_by_meshtastic_id("test_id")
            # Should handle malformed data gracefully by returning None
            assert result is None

    def test_get_message_map_by_matrix_event_id_unicode_error(self):
        """
        Test that get_message_map_by_matrix_event_id returns None when a UnicodeDecodeError occurs during database query execution.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            # Make run_sync raise the UnicodeDecodeError
            mock_manager.run_sync.side_effect = UnicodeDecodeError(
                "utf-8", b"", 0, 1, "invalid"
            )
            mock_get_manager.return_value = mock_manager

            result = get_message_map_by_matrix_event_id("test_id")
            assert result is None

    def test_prune_message_map_large_dataset(self):
        """
        Test that prune_message_map can handle pruning operations when the database contains a very large number of records.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_cursor = MagicMock()

            # Simulate large dataset by making count very high
            mock_cursor.fetchone.return_value = (1000000,)
            mock_manager.run_sync.return_value = 900000  # Number of records pruned

            mock_get_manager.return_value = mock_manager

            # Should handle large datasets without error
            prune_message_map(100)

    def test_wipe_message_map_transaction_rollback(self):
        """
        Test that wipe_message_map properly handles transaction rollback when a database error occurs during execution.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = [
                None,
                sqlite3.Error("Transaction failed"),
            ]
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle transaction rollback
            wipe_message_map()

    def test_store_plugin_data_concurrent_access(self):
        """
        Test that store_plugin_data handles database locking due to concurrent access without crashing.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = (
                sqlite3.OperationalError("database is locked")
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle concurrent access gracefully
            store_plugin_data("test_plugin", "test_node", {"key": "value"})

    def test_get_plugin_data_json_decode_error(self):
        """
        Test that get_plugin_data_for_node returns an empty list when JSON decoding of plugin data fails.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cursor = mock_conn.cursor.return_value
            # Return invalid JSON
            mock_cursor.fetchone.return_value = ("invalid json {",)
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_plugin_data_for_node("test_plugin", "test_node")
            assert result == []

    def test_get_plugin_data_for_node_memory_error(self):
        """
        Test that get_plugin_data_for_node returns an empty list when a MemoryError occurs during data retrieval.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.fetchone.side_effect = MemoryError(
                "Out of memory"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            result = get_plugin_data_for_node("test_plugin", "test_node")
            assert result == []

    def test_delete_plugin_data_foreign_key_constraint(self):
        """
        Test that delete_plugin_data handles foreign key constraint violations gracefully when deleting plugin data.
        """
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_conn.cursor.return_value.execute.side_effect = sqlite3.IntegrityError(
                "foreign key constraint"
            )
            mock_connect.return_value.__enter__.return_value = mock_conn

            # Should handle foreign key constraint gracefully
            delete_plugin_data("test_plugin", "test_node")

    def test_update_longnames_empty_nodes(self):
        """
        Test that update_longnames handles None and empty list inputs without error.
        """
        # Should handle None gracefully
        update_longnames(None)  # type: ignore[arg-type]

        # Should handle empty list gracefully
        update_longnames([])  # type: ignore[arg-type]

    def test_update_shortnames_malformed_node_data(self):
        """
        Test that update_shortnames handles malformed node data without raising exceptions.

        This test verifies that update_shortnames can process node data with missing or None fields gracefully, ensuring robustness against incomplete or invalid input.
        """
        malformed_nodes = MagicMock()
        malformed_nodes.values.return_value = [
            {"user": {}},  # Missing 'id' in user
            {"user": {"id": "test_id"}},  # Missing 'shortName'
            {"user": {"id": "test_id2", "shortName": None}},  # None shortName
        ]

        # Should handle malformed data gracefully
        update_shortnames(malformed_nodes)

    def test_database_path_caching_race_condition(self):
        """
        Verify that database path caching remains consistent and robust when the cache is cleared between calls, simulating a race condition.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            # Simulate race condition by clearing cache between calls
            def side_effect_clear_cache(*_args, **_kwargs):
                """
                Clear the module's cached database path and return a test path-resolution dictionary.

                Returns:
                    dict: Mapping with keys `database_dir` (the test directory path) and `legacy_sources` (an empty list).
                """
                clear_db_path_cache()
                return {"database_dir": temp_dir, "legacy_sources": []}

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                side_effect=side_effect_clear_cache,
            ):
                path1 = get_db_path()
                path2 = get_db_path()
                # Should handle race condition gracefully
                assert isinstance(path1, str)
                assert isinstance(path2, str)

    def test_database_initialization_partial_failure(self):
        """
        Test that `initialize_database` raises an exception if a table creation fails during initialization.

        Simulates partial failure by causing one table creation to raise an error, and asserts that the function fails fast by raising the exception.
        """
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_get_manager.return_value = mock_manager

            # Make run_sync raise the exception when called
            mock_manager.run_sync.side_effect = sqlite3.Error("Table creation failed")

            # Should raise exception on table creation failure (fail fast)
            with pytest.raises(sqlite3.Error):
                initialize_database()

    def test_active_mtime_file_not_found(self):
        """Test _active_mtime handles files that don't exist."""
        from mmrelay.db_utils import _active_mtime

        # Test with a path that doesn't exist
        result = _active_mtime("/nonexistent/path/to/file.sqlite")
        assert result == 0.0

    def test_active_mtime_partial_files_exist(self):
        """Test _active_mtime when only some files (main, wal, shm) exist."""
        from mmrelay.db_utils import _active_mtime

        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as temp_db:
            temp_db_path = temp_db.name

        try:
            # Only main file exists (no wal or shm)
            result = _active_mtime(temp_db_path)
            assert result > 0.0

            # Create a wal file
            wal_path = f"{temp_db_path}-wal"
            with open(wal_path, "w") as f:
                f.write("wal content")

            # Now both main and wal exist
            result_with_wal = _active_mtime(temp_db_path)
            assert result_with_wal > 0.0

            os.unlink(wal_path)
        finally:
            if os.path.exists(temp_db_path):
                os.unlink(temp_db_path)

    def test_migrate_legacy_db_empty_candidates(self):
        """Test _migrate_legacy_db_if_needed with empty legacy_candidates."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        # Should return early without error when no candidates
        _migrate_legacy_db_if_needed(
            default_path="/some/path/db.sqlite", legacy_candidates=[]
        )

    def test_migrate_legacy_db_success(self):
        """Test successful migration of legacy database."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create wal and shm sidecars
            for suffix in ("-wal", "-shm"):
                with open(f"{legacy_path}{suffix}", "w") as f:
                    f.write(f"{suffix} content")

            _migrate_legacy_db_if_needed(
                default_path=default_path, legacy_candidates=[legacy_path]
            )

            # Verify migration happened
            assert os.path.exists(default_path)
            assert not os.path.exists(legacy_path)
            assert os.path.exists(f"{default_path}-wal")
            assert os.path.exists(f"{default_path}-shm")

    def test_migrate_legacy_db_os_error(self):
        """Test _migrate_legacy_db_if_needed handles OSError gracefully."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            with patch("shutil.move", side_effect=OSError("Permission denied")):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should log warning about failed migration
                    mock_logger.warning.assert_called_once()
                    warning_call = mock_logger.warning.call_args[0]
                    assert "Failed to migrate" in warning_call[0]

    def test_migrate_legacy_db_permission_error(self):
        """Test _migrate_legacy_db_if_needed handles PermissionError gracefully."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            with patch("shutil.move", side_effect=PermissionError("Access denied")):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should log warning about failed migration
                    mock_logger.warning.assert_called_once()
                    warning_call = mock_logger.warning.call_args[0]
                    assert "Failed to migrate" in warning_call[0]

    def test_migrate_legacy_db_sidecar_failure_with_rollback(self):
        """Test _migrate_legacy_db_if_needed handles sidecar migration failure and rollback."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create wal sidecar
            wal_path = f"{legacy_path}-wal"
            with open(wal_path, "w") as f:
                f.write("wal content")

            # Track calls to shutil.move to simulate sidecar failure
            real_shutil_move = shutil.move
            call_count = [0]

            def mock_move(src, dst):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call - move main db successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 2:
                    # Second call - sidecar move fails
                    raise OSError("Sidecar move failed")
                else:
                    # Rollback calls
                    return real_shutil_move(src, dst)

            with patch("shutil.move", side_effect=mock_move):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    result = _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should return legacy_path due to sidecar failure
                    assert result == legacy_path

                    # Should log sidecar failure warnings
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]
                    assert len(warning_calls) > 0

    def test_migrate_legacy_db_partial_rollback_failure(self):
        """Test _migrate_legacy_db_if_needed handles sidecar failure with rollback."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create wal sidecar (shm will be skipped since it doesn't exist)
            wal_path = f"{legacy_path}-wal"
            with open(wal_path, "w") as f:
                f.write("wal content")

            # Simulate: main db moves ok, sidecar fails, rollback succeeds
            real_shutil_move = shutil.move
            call_count = [0]

            def mock_move(src, dst):
                call_count[0] += 1
                if call_count[0] == 1:
                    # Move main db successfully using real shutil
                    return real_shutil_move(src, dst)
                elif call_count[0] == 2:
                    # Sidecar move fails
                    raise OSError("Sidecar move failed")
                elif call_count[0] == 3:
                    # Rollback main db successfully
                    return real_shutil_move(src, dst)
                else:
                    raise RuntimeError(
                        f"Unexpected call {call_count[0]}: {src} -> {dst}"
                    )

            with patch("shutil.move", side_effect=mock_move):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    result = _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should return legacy_path after rollback
                    assert result == legacy_path

                    # Should log warning about sidecar failure and successful rollback
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]
                    # We expect at least the sidecar failure warning
                    assert len(warning_calls) >= 1, (
                        f"Expected at least 1 warning call, got {len(warning_calls)}: "
                        f"{warning_calls}"
                    )

    def test_migrate_legacy_db_rollback_errors_logged(self):
        """Test _migrate_legacy_db_if_needed logs rollback errors when rollback fails (lines 128-141)."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create wal sidecar
            wal_path = f"{legacy_path}-wal"
            with open(wal_path, "w") as f:
                f.write("wal content")

            # Save reference to real shutil.move BEFORE patching
            real_shutil_move = shutil.move

            # Simulate: main db moves ok, sidecar fails, rollback also fails
            call_count = [0]

            def mock_move(src, dst):
                call_count[0] += 1
                if call_count[0] == 1:
                    # Move main db successfully using REAL shutil.move
                    return real_shutil_move(src, dst)
                elif call_count[0] == 2:
                    # Sidecar move fails
                    raise OSError("Sidecar move failed")
                elif call_count[0] == 3:
                    # Rollback main db also fails
                    raise PermissionError("Rollback failed - access denied")
                else:
                    raise RuntimeError(
                        f"Unexpected call {call_count[0]}: {src} -> {dst}"
                    )

            with patch("shutil.move", side_effect=mock_move):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    result = _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should return default_path (main db still moved)
                    assert result == default_path

                    # Debug: print all method calls to understand what's happening
                    # Check for "partial state" warning (lines 137-141)
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]

                    # Should have at least sidecar failure + rollback error + partial state warnings
                    assert (
                        len(warning_calls) >= 2
                    ), f"Expected at least 2 warning calls, got {len(warning_calls)}"

                    # Verify "partial state" warning was logged (lines 137-141)
                    partial_state_logged = False
                    for call in warning_calls:
                        args = call[1]  # positional arguments
                        if args and "partial state" in str(args[0]):
                            partial_state_logged = True
                            break

                    assert (
                        partial_state_logged
                    ), "Expected 'partial state' warning to be logged when rollback fails"

                    # Verify rollback error was logged (lines 128-136)
                    rollback_error_logged = False
                    for call in warning_calls:
                        args = call[1]
                        if args and "Failed to roll back" in str(args[0]):
                            rollback_error_logged = True
                            break

                    assert (
                        rollback_error_logged
                    ), "Expected 'Failed to roll back' warning to be logged"

    def test_migrate_legacy_db_sidecar_rollback_loop(self):
        """Test _migrate_legacy_db_if_needed rolls back sidecars that were moved before a later failure (lines 114-120)."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create BOTH wal and shm sidecars
            wal_path = f"{legacy_path}-wal"
            shm_path = f"{legacy_path}-shm"
            with open(wal_path, "w") as f:
                f.write("wal content")
            with open(shm_path, "w") as f:
                f.write("shm content")

            # Save reference to real shutil.move BEFORE patching
            real_shutil_move = shutil.move

            # Simulate: main db moves ok, first sidecar moves ok, second sidecar fails
            call_count = [0]

            def mock_move(src, dst):
                call_count[0] += 1
                if call_count[0] == 1:
                    # Move main db successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 2:
                    # First sidecar (-wal) moves successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 3:
                    # Second sidecar (-shm) fails
                    raise OSError("SHM sidecar move failed")
                elif call_count[0] == 4:
                    # Rollback main db successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 5:
                    # Rollback first sidecar (-wal) successfully
                    return real_shutil_move(src, dst)
                else:
                    raise RuntimeError(
                        f"Unexpected call {call_count[0]}: {src} -> {dst}"
                    )

            with patch("shutil.move", side_effect=mock_move):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    result = _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should return legacy_path after rollback
                    assert result == legacy_path

                    # Verify files are back at legacy location
                    assert os.path.exists(legacy_path)
                    assert os.path.exists(wal_path)
                    assert os.path.exists(shm_path)
                    assert not os.path.exists(default_path)

                    # Check warning calls
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]

                    # Should have sidecar failure + successful rollback warning
                    assert (
                        len(warning_calls) >= 1
                    ), f"Expected at least 1 warning call, got {len(warning_calls)}"

                    # Verify successful rollback message (lines 142-147)
                    rollback_success_logged = False
                    for call in warning_calls:
                        args = call[1]
                        if args and "rolled back due to sidecar failures" in str(
                            args[0]
                        ):
                            rollback_success_logged = True
                            break

                    assert (
                        rollback_success_logged
                    ), "Expected successful rollback warning to be logged"

    def test_migrate_legacy_db_sidecar_rollback_failure(self):
        """Test _migrate_legacy_db_if_needed when sidecar rollback fails (lines 117-118, 128-141)."""
        from mmrelay.db_utils import _migrate_legacy_db_if_needed

        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = os.path.join(temp_dir, "legacy.sqlite")
            default_path = os.path.join(temp_dir, "default.sqlite")

            # Create a legacy database file
            with open(legacy_path, "w") as f:
                f.write("legacy db content")

            # Create BOTH wal and shm sidecars
            wal_path = f"{legacy_path}-wal"
            shm_path = f"{legacy_path}-shm"
            with open(wal_path, "w") as f:
                f.write("wal content")
            with open(shm_path, "w") as f:
                f.write("shm content")

            # Save reference to real shutil.move BEFORE patching
            real_shutil_move = shutil.move

            # Simulate: main db moves ok, first sidecar moves ok, second sidecar fails, rollback of first sidecar also fails
            call_count = [0]

            def mock_move(src, dst):
                call_count[0] += 1
                if call_count[0] == 1:
                    # Move main db successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 2:
                    # First sidecar (-wal) moves successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 3:
                    # Second sidecar (-shm) fails
                    raise OSError("SHM sidecar move failed")
                elif call_count[0] == 4:
                    # Rollback main db successfully
                    return real_shutil_move(src, dst)
                elif call_count[0] == 5:
                    # Rollback first sidecar (-wal) fails - this is lines 117-118
                    raise PermissionError("Cannot rollback WAL sidecar")
                else:
                    raise RuntimeError(
                        f"Unexpected call {call_count[0]}: {src} -> {dst}"
                    )

            with patch("shutil.move", side_effect=mock_move):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    result = _migrate_legacy_db_if_needed(
                        default_path=default_path, legacy_candidates=[legacy_path]
                    )

                    # Should return legacy_path
                    assert result == legacy_path

                    # Check warning calls
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]

                    # Should have sidecar failure + sidecar rollback error + partial state warnings
                    assert (
                        len(warning_calls) >= 2
                    ), f"Expected at least 2 warning calls, got {len(warning_calls)}"

                    # Verify "partial state" warning was logged (lines 137-141)
                    partial_state_logged = False
                    for call in warning_calls:
                        args = call[1]
                        if args and "partial state" in str(args[0]):
                            partial_state_logged = True
                            break

                    assert (
                        partial_state_logged
                    ), "Expected 'partial state' warning when sidecar rollback fails"

                    # Verify sidecar rollback error was logged (lines 117-118, 128-136)
                    sidecar_rollback_error_logged = False
                    for call in warning_calls:
                        args = call[1]
                        # Check if any argument contains "sidecar" and "Failed to roll back"
                        args_str = " ".join(str(arg) for arg in args)
                        if "sidecar" in args_str and "Failed to roll back" in args_str:
                            sidecar_rollback_error_logged = True
                            break

                    assert (
                        sidecar_rollback_error_logged
                    ), "Expected sidecar rollback error to be logged"

    def test_get_db_path_new_layout_no_implicit_migration(self):
        """Test get_db_path uses new layout without implicitly migrating legacy DBs."""
        import mmrelay.db_utils

        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = os.path.join(temp_dir, "base")
            data_dir = os.path.join(base_dir, "data")
            default_path = os.path.join(data_dir, "meshtastic.sqlite")
            legacy_path = os.path.join(base_dir, "meshtastic.sqlite")

            os.makedirs(data_dir, exist_ok=True)

            # Create legacy database
            with open(legacy_path, "w") as f:
                f.write("legacy db")

            # Mock config and directories
            mmrelay.db_utils.config = {"database": {}}

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={
                    "database_dir": data_dir,
                    "legacy_sources": [base_dir],
                },
            ):
                clear_db_path_cache()
                result = get_db_path()

            # Should return default path without migrating legacy data
            assert result == default_path

    def test_get_db_path_legacy_multiple_databases(self):
        """Test get_db_path uses resolved database path when multiple legacy databases exist."""
        import mmrelay.db_utils

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "data")
            base_dir = temp_dir
            default_path = os.path.join(data_dir, "meshtastic.sqlite")
            legacy_base_path = os.path.join(base_dir, "meshtastic.sqlite")
            # Note: default_path and base_dir/data/meshtastic.sqlite are the same when
            # data_dir = base_dir/data
            # So we need to create files at 2 locations but they appear as 3 candidates
            os.makedirs(data_dir, exist_ok=True)

            # Create files at unique paths
            # default_path == legacy_data_path, so we get 2 unique physical files
            for path in [default_path, legacy_base_path]:
                with open(path, "w") as f:
                    f.write("legacy db")

            # Make default_path newer
            import time

            time.sleep(0.01)
            os.utime(default_path, None)

            mmrelay.db_utils.config = {"database": {}}

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={
                    "database_dir": data_dir,
                    "legacy_sources": [base_dir],
                },
            ):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    clear_db_path_cache()
                    result = get_db_path()

                    # Should use resolved database path (default_path)
                    assert result == default_path

                    # No warning expected
                    warning_calls = [
                        call
                        for call in mock_logger.method_calls
                        if call[0] == "warning"
                    ]
                    assert len(warning_calls) == 0

    def test_get_db_path_legacy_multiple_databases_warning(self):
        """Test get_db_path ignores newer legacy databases when new layout is disabled."""
        import mmrelay.db_utils

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "data")
            base_dir = temp_dir
            default_path = os.path.join(data_dir, "meshtastic.sqlite")
            legacy_base_path = os.path.join(base_dir, "meshtastic.sqlite")

            os.makedirs(data_dir, exist_ok=True)

            # Create database at default path first
            with open(default_path, "w") as f:
                f.write("default db")

            # Wait and create database at legacy path
            import time

            time.sleep(0.01)
            with open(legacy_base_path, "w") as f:
                f.write("legacy db")
            # Make legacy path newer
            os.utime(legacy_base_path, None)

            mmrelay.db_utils.config = {"database": {}}

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={
                    "database_dir": data_dir,
                    "legacy_sources": [base_dir],
                },
            ):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    clear_db_path_cache()
                    result = get_db_path()

                    # Should still use the resolved database path
                    assert result == default_path

                    # No warning expected
                    mock_logger.warning.assert_not_called()

    def test_get_db_path_legacy_single_database_not_default(self):
        """Test get_db_path returns resolved database path even if only legacy DB exists."""
        import mmrelay.db_utils

        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = os.path.join(temp_dir, "data")
            base_dir = temp_dir
            default_path = os.path.join(data_dir, "meshtastic.sqlite")
            # Use legacy_base_path which is at base_dir, not inside data/ subdirectory
            legacy_base_path = os.path.join(base_dir, "meshtastic.sqlite")

            os.makedirs(data_dir, exist_ok=True)

            # Create single legacy database at base_dir (not at default data_dir path)
            with open(legacy_base_path, "w") as f:
                f.write("legacy db")

            mmrelay.db_utils.config = {"database": {}}

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={
                    "database_dir": data_dir,
                    "legacy_sources": [base_dir],
                },
            ):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    clear_db_path_cache()
                    result = get_db_path()

                    # Should use resolved database path (default_path)
                    assert result == default_path
                    mock_logger.info.assert_not_called()
