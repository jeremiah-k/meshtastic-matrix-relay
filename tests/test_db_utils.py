#!/usr/bin/env python3
"""
Test suite for database utilities in MMRelay.

Tests the SQLite database operations including:
- Database initialization and schema creation
- Node name storage and retrieval (longnames/shortnames)
- Plugin data storage and retrieval
- Message mapping for Matrix/Meshtastic correlation
- Database path resolution and caching
- Configuration-based database paths
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.db_runtime import DatabaseManager
from mmrelay.db_utils import (
    _get_db_manager,
    _parse_bool,
    _parse_int,
    _reset_db_manager,
    async_prune_message_map,
    async_store_message_map,
    build_node_name_state,
    clear_db_path_cache,
    delete_plugin_data,
    delete_stale_longnames,
    delete_stale_shortnames,
    get_db_path,
    get_longname,
    get_message_map_by_matrix_event_id,
    get_message_map_by_meshtastic_id,
    get_plugin_data,
    get_plugin_data_for_node,
    get_shortname,
    initialize_database,
    prune_message_map,
    save_longname,
    save_shortname,
    store_message_map,
    store_plugin_data,
    sync_name_tables_if_changed,
    update_longnames,
    update_shortnames,
    wipe_message_map,
)


def _make_failing_cursor_proxy_side_effect(write_failed_flag: list[bool]):
    """
    Create a side effect for DatabaseManager.run_sync that fails once on first write.

    Returns a tuple of (side_effect_function, original_run_sync) for use with patch.object.
    The write_failed_flag is a mutable list containing a single bool to track failure state.
    """
    original_run_sync = DatabaseManager.run_sync

    class _FailingCursorProxy:
        def __init__(self, inner_cursor):
            self._inner_cursor = inner_cursor
            self._execute_calls = 0

        def execute(self, *args, **kwargs):
            self._execute_calls += 1
            result = self._inner_cursor.execute(*args, **kwargs)
            if self._execute_calls == 1 and not write_failed_flag[0]:
                write_failed_flag[0] = True
                raise sqlite3.Error("forced longname write failure")
            return result

        def __getattr__(self, attr):
            return getattr(self._inner_cursor, attr)

    def fail_on_first_write(self, func, *, write=False):
        if write and not write_failed_flag[0]:

            def wrapped(cursor):
                return func(_FailingCursorProxy(cursor))

            return original_run_sync(self, wrapped, write=write)
        return original_run_sync(self, func, write=write)

    return fail_on_first_write, original_run_sync


class TestDbUtils(unittest.TestCase):
    """Test cases for database utilities."""

    def setUp(self):
        """
        Prepare a temporary test environment by creating a unique directory and database file, clearing cached database paths, and patching the configuration to use the test database.
        """
        _reset_db_manager()

        # Create a temporary directory for test database
        self.test_dir = tempfile.mkdtemp()
        self.test_db_path = os.path.join(self.test_dir, "test_meshtastic.sqlite")

        # Clear any cached database path
        clear_db_path_cache()

        # Mock the config to use our test database
        self.mock_config = {"database": {"path": self.test_db_path}}

        # Patch the config in db_utils
        import mmrelay.db_utils

        mmrelay.db_utils.config = self.mock_config

    def tearDown(self):
        """
        Cleans up the test environment by clearing the database path cache and removing temporary files and directories created during the test.
        """
        _reset_db_manager()

        # Clear cache after each test
        clear_db_path_cache()

        # Clean up temporary files and directory
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_get_db_path_with_config(self):
        """
        Test that get_db_path() returns the database path specified in the configuration.
        """
        path = get_db_path()
        self.assertEqual(path, self.test_db_path)

    def test_get_db_path_caching(self):
        """
        Test that the database path returned by get_db_path() is cached after the first retrieval.

        Verifies that repeated calls to get_db_path() return the same path and match the expected test database path.
        """
        # First call should resolve and cache
        path1 = get_db_path()
        path2 = get_db_path()
        self.assertEqual(path1, path2)
        self.assertEqual(path1, self.test_db_path)

    def test_get_db_path_default(self):
        """
        Test that `get_db_path()` returns the default database path in the absence of configuration.

        Mocks the data directory to verify that the default path is constructed correctly when no configuration is set.
        """
        # Clear config to test default behavior
        import mmrelay.db_utils

        mmrelay.db_utils.config = None
        clear_db_path_cache()

        import os
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": temp_dir, "legacy_sources": []},
            ):
                path = get_db_path()
                expected_path = os.path.join(temp_dir, "meshtastic.sqlite")
                self.assertEqual(path, expected_path)

    def test_get_db_path_legacy_config(self):
        """
        Test that get_db_path() returns the correct database path when using a legacy configuration with the 'db.path' key.
        """
        # Use legacy db.path format
        legacy_config = {"db": {"path": self.test_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = legacy_config
        clear_db_path_cache()

        path = get_db_path()
        self.assertEqual(path, self.test_db_path)

    def test_initialize_database(self):
        """
        Verify that the database is initialized with the correct schema and required tables.

        Ensures that the database file is created, all expected tables exist, and the `message_map` table includes the `meshtastic_meshnet` column.
        """
        initialize_database()

        # Verify database file was created
        self.assertTrue(os.path.exists(self.test_db_path))

        # Verify tables were created
        with sqlite3.connect(self.test_db_path) as conn:
            cursor = conn.cursor()

            # Check longnames table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='longnames'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check shortnames table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='shortnames'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check plugin_data table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_data'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Check message_map table
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='message_map'"
            )
            self.assertIsNotNone(cursor.fetchone())

            # Verify message_map has meshtastic_meshnet column
            cursor.execute("PRAGMA table_info(message_map)")
            columns = [row[1] for row in cursor.fetchall()]
            self.assertIn("meshtastic_meshnet", columns)

    def test_longname_operations(self):
        """
        Tests saving and retrieving longnames by Meshtastic ID, including handling of non-existent entries.
        """
        initialize_database()

        # Test saving and retrieving longname
        meshtastic_id = "!12345678"
        longname = "Test User"

        save_longname(meshtastic_id, longname)
        retrieved_longname = get_longname(meshtastic_id)

        self.assertEqual(retrieved_longname, longname)

        # Test non-existent longname
        non_existent = get_longname("!nonexistent")
        self.assertIsNone(non_existent)

    def test_shortname_operations(self):
        """
        Test saving and retrieving shortnames by Meshtastic ID, including handling of non-existent entries.
        """
        initialize_database()

        # Test saving and retrieving shortname
        meshtastic_id = "!12345678"
        shortname = "TU"

        save_shortname(meshtastic_id, shortname)
        retrieved_shortname = get_shortname(meshtastic_id)

        self.assertEqual(retrieved_shortname, shortname)

        # Test non-existent shortname
        non_existent = get_shortname("!nonexistent")
        self.assertIsNone(non_existent)

    def test_update_longnames(self):
        """
        Tests that bulk updating of longnames from a dictionary of nodes correctly stores the longnames for each Meshtastic ID.
        """
        initialize_database()

        # Mock nodes data
        nodes = {
            "!12345678": {"user": {"id": "!12345678", "longName": "Alice Smith"}},
            "!87654321": {"user": {"id": "!87654321", "longName": "Bob Jones"}},
        }

        update_longnames(nodes)

        # Verify longnames were stored
        self.assertEqual(get_longname("!12345678"), "Alice Smith")
        self.assertEqual(get_longname("!87654321"), "Bob Jones")

    def test_update_shortnames(self):
        """
        Test that bulk updating of shortnames from a nodes dictionary correctly stores shortnames for each Meshtastic ID.
        """
        initialize_database()

        # Mock nodes data
        nodes = {
            "!12345678": {"user": {"id": "!12345678", "shortName": "AS"}},
            "!87654321": {"user": {"id": "!87654321", "shortName": "BJ"}},
        }

        update_shortnames(nodes)

        # Verify shortnames were stored
        self.assertEqual(get_shortname("!12345678"), "AS")
        self.assertEqual(get_shortname("!87654321"), "BJ")

    def test_update_shortnames_ignores_invalid_longname_field(self):
        """
        Invalid longName payloads must not block shortname updates for the same node.
        """
        initialize_database()
        save_shortname("!12345678", "OLD")

        nodes = {
            "!12345678": {
                "user": {
                    "id": "!12345678",
                    "longName": 123,
                    "shortName": "NEW",
                }
            }
        }

        self.assertTrue(update_shortnames(nodes))
        self.assertEqual(get_shortname("!12345678"), "NEW")

    def test_update_longnames_ignores_invalid_shortname_field(self):
        """
        Invalid shortName payloads must not block longname updates for the same node.
        """
        initialize_database()
        save_longname("!12345678", "Old Name")

        nodes = {
            "!12345678": {
                "user": {
                    "id": "!12345678",
                    "longName": "Updated Name",
                    "shortName": {"bad": "type"},
                }
            }
        }

        self.assertTrue(update_longnames(nodes))
        self.assertEqual(get_longname("!12345678"), "Updated Name")

    def test_build_node_name_state_is_sorted_and_stable(self):
        """Node-name state snapshot should be deterministic for change detection."""
        nodes = {
            "node_b": {"user": {"id": "!2", "longName": "Beta", "shortName": "B"}},
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
            "bad_node": {"user": {"longName": "Missing ID"}},
        }
        state = build_node_name_state(nodes)
        self.assertEqual(
            state,
            (
                ("!1", "Alpha", "A"),
                ("!2", "Beta", "B"),
            ),
        )

    def test_build_node_name_state_deduplicates_duplicate_ids(self):
        """Duplicate Meshtastic IDs should be merged into one stable state row."""
        nodes = {
            "node_first": {"user": {"id": "!1", "longName": None, "shortName": "A"}},
            "node_second": {
                "user": {"id": "!1", "longName": "Alpha", "shortName": None}
            },
            "node_other": {"user": {"id": "!2", "longName": "Beta", "shortName": "B"}},
        }

        state = build_node_name_state(nodes)

        self.assertEqual(
            state,
            (
                ("!1", "Alpha", "A"),
                ("!2", "Beta", "B"),
            ),
        )

    def test_sync_name_tables_if_changed_uses_canonical_snapshot_for_duplicate_ids(
        self,
    ):
        """Duplicate IDs should merge to the same canonical snapshot regardless of input order."""
        initialize_database()
        nodes = {
            "node_first": {"user": {"id": "!1", "shortName": "ONE"}},
            "node_second": {"user": {"id": "!1", "shortName": None}},
        }
        nodes_reversed = {
            "node_second": {"user": {"id": "!1", "shortName": None}},
            "node_first": {"user": {"id": "!1", "shortName": "ONE"}},
        }

        state = sync_name_tables_if_changed(nodes, previous_state=None)
        state_reversed = sync_name_tables_if_changed(
            nodes_reversed, previous_state=None
        )

        self.assertEqual(state, (("!1", None, "ONE"),))
        self.assertEqual(state_reversed, (("!1", None, "ONE"),))
        self.assertEqual(get_shortname("!1"), "ONE")

    def test_sync_name_tables_if_changed_skips_conflicting_duplicates(self):
        """Conflicting duplicate IDs skip the entire ID to preserve DB stability."""
        initialize_database()
        nodes = {
            "node_first": {"user": {"id": "!1", "shortName": "ONE"}},
            "node_second": {"user": {"id": "!1", "shortName": "TWO"}},
            "node_other": {"user": {"id": "!2", "longName": "Beta", "shortName": "B"}},
        }

        state = sync_name_tables_if_changed(nodes, previous_state=None)

        self.assertEqual(state, (("!2", "Beta", "B"),))
        self.assertIsNone(get_shortname("!1"))

    def test_sync_name_tables_if_changed_conflicts_preserve_existing_rows(self):
        """Conflicting duplicate IDs skip the entire ID; existing DB rows are preserved."""
        initialize_database()
        save_longname("!1", "Legacy Long")
        save_shortname("!1", "OLD")
        nodes = {
            "node_first": {"user": {"id": "!1", "shortName": "ONE"}},
            "node_second": {"user": {"id": "!1", "shortName": "TWO"}},
            "node_other": {"user": {"id": "!2", "longName": "Beta", "shortName": "B"}},
        }

        state = sync_name_tables_if_changed(nodes, previous_state=None)

        self.assertEqual(state, (("!2", "Beta", "B"),))
        self.assertEqual(get_longname("!1"), "Legacy Long")
        self.assertEqual(get_shortname("!1"), "OLD")

    def test_sync_name_tables_if_changed_skips_redundant_updates(self):
        """A matching state should skip upserts while still pruning stale rows."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)
        save_longname("!stale", "Stale Longname")
        save_shortname("!stale", "STL")
        self.assertEqual(get_longname("!stale"), "Stale Longname")
        self.assertEqual(get_shortname("!stale"), "STL")

        with (
            patch(
                "mmrelay.db_utils.save_longname", wraps=save_longname
            ) as mock_save_long,
            patch(
                "mmrelay.db_utils.save_shortname", wraps=save_shortname
            ) as mock_save_short,
        ):
            second_state = sync_name_tables_if_changed(
                nodes, previous_state=first_state
            )

        mock_save_long.assert_not_called()
        mock_save_short.assert_not_called()
        self.assertEqual(second_state, first_state)
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")
        self.assertIsNone(get_longname("!stale"))
        self.assertIsNone(get_shortname("!stale"))

    def test_sync_name_tables_if_changed_unchanged_snapshot_without_stale_rows(self):
        """Unchanged snapshots without stale rows should keep state/data unchanged."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        with (
            patch(
                "mmrelay.db_utils.save_longname", wraps=save_longname
            ) as mock_save_long,
            patch(
                "mmrelay.db_utils.save_shortname", wraps=save_shortname
            ) as mock_save_short,
        ):
            second_state = sync_name_tables_if_changed(
                nodes, previous_state=first_state
            )

        mock_save_long.assert_not_called()
        mock_save_short.assert_not_called()

        self.assertEqual(second_state, first_state)
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")
        self.assertIsNone(get_longname("!unknown"))
        self.assertIsNone(get_shortname("!unknown"))

    def test_sync_name_tables_if_changed_partial_snapshot_skips_pruning(self):
        """Unchanged partial snapshots should skip stale-row pruning for safety."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
            "bad_node": {"user": {"longName": "Missing ID"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)
        save_longname("!stale", "Stale Longname")
        save_shortname("!stale", "STL")
        self.assertEqual(get_longname("!stale"), "Stale Longname")
        self.assertEqual(get_shortname("!stale"), "STL")

        second_state = sync_name_tables_if_changed(nodes, previous_state=first_state)

        self.assertEqual(second_state, first_state)
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")
        self.assertEqual(get_longname("!stale"), "Stale Longname")
        self.assertEqual(get_shortname("!stale"), "STL")

    def test_sync_name_tables_if_changed_heals_missing_row_on_unchanged_state(self):
        """Unchanged snapshots should repair missing per-ID name rows when drift is detected."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        with sqlite3.connect(self.test_db_path) as conn:
            conn.execute("DELETE FROM longnames WHERE meshtastic_id = ?", ("!1",))

        self.assertIsNone(get_longname("!1"))
        second_state = sync_name_tables_if_changed(nodes, previous_state=first_state)

        self.assertEqual(second_state, first_state)
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")

    def test_sync_name_tables_if_changed_updates_on_change(self):
        """State changes should trigger table updates and return the new state."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        updated_nodes = {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha Prime", "shortName": "A1"}
            },
        }
        second_state = sync_name_tables_if_changed(
            updated_nodes, previous_state=first_state
        )

        self.assertNotEqual(second_state, first_state)
        self.assertEqual(
            second_state,
            (("!1", "Alpha Prime", "A1"),),
        )
        self.assertEqual(get_longname("!1"), "Alpha Prime")
        self.assertEqual(get_shortname("!1"), "A1")

    def test_sync_name_tables_if_changed_retries_after_write_failure(self):
        """Changed snapshots should not advance or partially persist when a names-table update fails."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        updated_nodes = {
            "node_a": {
                "user": {"id": "!1", "longName": "Alpha Prime", "shortName": "A1"}
            },
        }
        write_failed_flag = [False]
        side_effect, _ = _make_failing_cursor_proxy_side_effect(write_failed_flag)

        with patch.object(
            DatabaseManager,
            "run_sync",
            autospec=True,
            side_effect=side_effect,
        ):
            second_state = sync_name_tables_if_changed(
                updated_nodes, previous_state=first_state
            )

        self.assertTrue(write_failed_flag[0], "forced write failure was not triggered")
        self.assertEqual(second_state, first_state)
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")

        retry_state = sync_name_tables_if_changed(
            updated_nodes, previous_state=second_state
        )
        self.assertEqual(retry_state, (("!1", "Alpha Prime", "A1"),))
        self.assertEqual(get_longname("!1"), "Alpha Prime")
        self.assertEqual(get_shortname("!1"), "A1")

    def test_sync_name_tables_if_changed_retries_from_cold_start_on_write_failure(self):
        """A first-run write failure should keep state unset and avoid partial writes."""
        initialize_database()
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        write_failed_flag = [False]
        side_effect, _ = _make_failing_cursor_proxy_side_effect(write_failed_flag)

        with patch.object(
            DatabaseManager,
            "run_sync",
            autospec=True,
            side_effect=side_effect,
        ):
            state = sync_name_tables_if_changed(nodes, previous_state=None)

        self.assertTrue(write_failed_flag[0], "forced write failure was not triggered")
        self.assertIsNone(state)
        self.assertIsNone(get_longname("!1"))
        self.assertIsNone(get_shortname("!1"))

        retry_state = sync_name_tables_if_changed(nodes, previous_state=state)
        self.assertEqual(retry_state, (("!1", "Alpha", "A"),))
        self.assertEqual(get_longname("!1"), "Alpha")
        self.assertEqual(get_shortname("!1"), "A")

    def test_sync_name_tables_if_changed_handles_none_nodes(self):
        """None node snapshots should return previous_state without modifying the database."""
        initialize_database()

        state = sync_name_tables_if_changed(None, previous_state=None)

        self.assertIsNone(state)
        with sqlite3.connect(self.test_db_path) as conn:
            longname_rows = conn.execute("SELECT COUNT(*) FROM longnames").fetchone()
            shortname_rows = conn.execute("SELECT COUNT(*) FROM shortnames").fetchone()
        self.assertIsNotNone(longname_rows)
        self.assertIsNotNone(shortname_rows)
        self.assertEqual(longname_rows[0], 0)
        self.assertEqual(shortname_rows[0], 0)

    def test_sync_name_tables_if_changed_handles_non_dict_nodes(self):
        """Non-dict node snapshots should return previous_state without modifying the database."""
        initialize_database()

        bad_nodes: Any = []
        state = sync_name_tables_if_changed(bad_nodes, previous_state=None)

        self.assertIsNone(state)
        with sqlite3.connect(self.test_db_path) as conn:
            longname_rows = conn.execute("SELECT COUNT(*) FROM longnames").fetchone()
            shortname_rows = conn.execute("SELECT COUNT(*) FROM shortnames").fetchone()
        self.assertIsNotNone(longname_rows)
        self.assertIsNotNone(shortname_rows)
        self.assertEqual(longname_rows[0], 0)
        self.assertEqual(shortname_rows[0], 0)

    def test_update_names_preserve_zero_id_for_stale_tracking(self):
        """
        Test that numeric zero IDs are treated as valid IDs, not skipped as missing.
        """
        initialize_database()

        initial_nodes = {
            "node_zero": {"user": {"id": 0, "longName": "Zero", "shortName": "ZRO"}},
            "node_one": {"user": {"id": 1, "longName": "One", "shortName": "ONE"}},
        }
        update_longnames(initial_nodes)
        update_shortnames(initial_nodes)

        updated_nodes = {
            "node_zero": {
                "user": {"id": 0, "longName": "Zero Updated", "shortName": "Z0"}
            }
        }
        update_longnames(updated_nodes)
        update_shortnames(updated_nodes)

        self.assertEqual(get_longname("0"), "Zero Updated")
        self.assertEqual(get_shortname("0"), "Z0")
        self.assertIsNone(get_longname("1"))
        self.assertIsNone(get_shortname("1"))

    def test_update_names_clear_rows_when_name_is_missing(self):
        """
        Test that empty/None name values clear existing per-node rows.
        """
        initialize_database()

        initial_nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        update_longnames(initial_nodes)
        update_shortnames(initial_nodes)

        cleared_nodes = {
            "node_a": {"user": {"id": "!1", "longName": "", "shortName": None}},
        }
        update_longnames(cleared_nodes)
        update_shortnames(cleared_nodes)

        self.assertIsNone(get_longname("!1"))
        self.assertIsNone(get_shortname("!1"))

    def test_update_longnames_removes_stale_entries(self):
        """
        Test that update_longnames removes stale entries when nodes are removed from the device nodedb.

        Simulates a device nodedb being cleared or nodes leaving the mesh by:
        1. Adding multiple nodes to the database
        2. Calling update_longnames with only a subset of nodes
        3. Verifying that stale entries (nodes not in the new snapshot) are removed
        """
        initialize_database()

        # Initial nodes - 3 nodes in the mesh
        initial_nodes = {
            "!11111111": {"user": {"id": "!11111111", "longName": "Alice"}},
            "!22222222": {"user": {"id": "!22222222", "longName": "Bob"}},
            "!33333333": {"user": {"id": "!33333333", "longName": "Charlie"}},
        }
        update_longnames(initial_nodes)

        # Verify all 3 are stored
        self.assertEqual(get_longname("!11111111"), "Alice")
        self.assertEqual(get_longname("!22222222"), "Bob")
        self.assertEqual(get_longname("!33333333"), "Charlie")

        # Update with only 2 nodes (Charlie left the mesh)
        updated_nodes = {
            "!11111111": {"user": {"id": "!11111111", "longName": "Alice Updated"}},
            "!22222222": {"user": {"id": "!22222222", "longName": "Bob"}},
        }
        update_longnames(updated_nodes)

        # Verify Alice and Bob are still present (Alice updated)
        self.assertEqual(get_longname("!11111111"), "Alice Updated")
        self.assertEqual(get_longname("!22222222"), "Bob")
        # Verify Charlie was removed as stale entry
        self.assertIsNone(get_longname("!33333333"))

    def test_update_shortnames_removes_stale_entries(self):
        """
        Test that update_shortnames removes stale entries when nodes are removed from the device nodedb.

        Simulates a device nodedb being cleared or nodes leaving the mesh by:
        1. Adding multiple nodes to the database
        2. Calling update_shortnames with only a subset of nodes
        3. Verifying that stale entries (nodes not in the new snapshot) are removed
        """
        initialize_database()

        # Initial nodes - 3 nodes in the mesh
        initial_nodes = {
            "!11111111": {"user": {"id": "!11111111", "shortName": "ALI"}},
            "!22222222": {"user": {"id": "!22222222", "shortName": "BOB"}},
            "!33333333": {"user": {"id": "!33333333", "shortName": "CHA"}},
        }
        update_shortnames(initial_nodes)

        # Verify all 3 are stored
        self.assertEqual(get_shortname("!11111111"), "ALI")
        self.assertEqual(get_shortname("!22222222"), "BOB")
        self.assertEqual(get_shortname("!33333333"), "CHA")

        # Update with only 2 nodes (Charlie left the mesh)
        updated_nodes = {
            "!11111111": {"user": {"id": "!11111111", "shortName": "ALX"}},
            "!22222222": {"user": {"id": "!22222222", "shortName": "BOB"}},
        }
        update_shortnames(updated_nodes)

        # Verify Alice and Bob are still present (Alice updated)
        self.assertEqual(get_shortname("!11111111"), "ALX")
        self.assertEqual(get_shortname("!22222222"), "BOB")
        # Verify Charlie was removed as stale entry
        self.assertIsNone(get_shortname("!33333333"))

    def test_delete_stale_names_empty_current_ids_clears_tables(self):
        """
        Test that explicit stale-prune helpers clear stored names when passed an empty keep-set.
        """
        initialize_database()

        save_longname("!11111111", "Alice")
        save_longname("!22222222", "Bob")
        save_shortname("!11111111", "ALI")
        save_shortname("!22222222", "BOB")

        self.assertEqual(delete_stale_longnames(set()), 2)
        self.assertEqual(delete_stale_shortnames(set()), 2)

        self.assertIsNone(get_longname("!11111111"))
        self.assertIsNone(get_longname("!22222222"))
        self.assertIsNone(get_shortname("!11111111"))
        self.assertIsNone(get_shortname("!22222222"))

    def test_update_names_empty_nodes_preserves_existing(self):
        """
        Test that calling update_longnames/update_shortnames with empty dict does NOT wipe the database.

        This is important because an empty dict could mean either:
        - Device nodedb was actually cleared
        - Transient failure/disconnect where we don't have node data

        We choose to preserve existing data on empty input to avoid data loss.
        """
        initialize_database()

        # Add initial nodes
        initial_nodes = {
            "!11111111": {
                "user": {"id": "!11111111", "longName": "Alice", "shortName": "ALI"}
            },
        }
        update_longnames(initial_nodes)
        update_shortnames(initial_nodes)

        # Verify stored
        self.assertEqual(get_longname("!11111111"), "Alice")
        self.assertEqual(get_shortname("!11111111"), "ALI")

        # Call with empty dict (simulating transient issue)
        update_longnames({})
        update_shortnames({})

        # Verify data is still present (not wiped)
        self.assertEqual(get_longname("!11111111"), "Alice")
        self.assertEqual(get_shortname("!11111111"), "ALI")

    def test_update_names_incomplete_snapshot_preserves_existing_entries(self):
        """
        Test that incomplete node snapshots do not trigger stale-name pruning.
        """
        initialize_database()

        initial_nodes = {
            "!11111111": {
                "user": {
                    "id": "!11111111",
                    "longName": "Alice",
                    "shortName": "ALI",
                }
            },
            "!22222222": {
                "user": {
                    "id": "!22222222",
                    "longName": "Bob",
                    "shortName": "BOB",
                }
            },
            "!33333333": {
                "user": {
                    "id": "!33333333",
                    "longName": "Charlie",
                    "shortName": "CHA",
                }
            },
        }
        update_longnames(initial_nodes)
        update_shortnames(initial_nodes)

        incomplete_nodes = {
            "!11111111": {
                "user": {
                    "id": "!11111111",
                    "longName": "Alice Updated",
                    "shortName": "ALX",
                }
            },
            "node-without-user": {},
            "node-without-id": {
                "user": {
                    "longName": "Missing ID",
                    "shortName": "MID",
                }
            },
            "node-with-empty-id": {
                "user": {
                    "id": "",
                    "longName": "Empty ID",
                    "shortName": "EID",
                }
            },
        }
        update_longnames(incomplete_nodes)
        update_shortnames(incomplete_nodes)

        self.assertEqual(get_longname("!11111111"), "Alice Updated")
        self.assertEqual(get_shortname("!11111111"), "ALX")
        self.assertEqual(get_longname("!22222222"), "Bob")
        self.assertEqual(get_shortname("!22222222"), "BOB")
        self.assertEqual(get_longname("!33333333"), "Charlie")
        self.assertEqual(get_shortname("!33333333"), "CHA")
        self.assertIsNone(get_longname(""))
        self.assertIsNone(get_shortname(""))

    def test_plugin_data_operations(self):
        """
        Test storing, retrieving, and deleting plugin data for specific nodes and plugins in the database.

        Verifies that plugin data can be saved for a given plugin and node, retrieved individually or in bulk, and deleted, ensuring correct data persistence and removal.
        """
        initialize_database()

        plugin_name = "test_plugin"
        meshtastic_id = "!12345678"
        test_data = {"temperature": 25.5, "humidity": 60}

        # Store plugin data
        store_plugin_data(plugin_name, meshtastic_id, test_data)

        # Retrieve plugin data for specific node
        retrieved_data = get_plugin_data_for_node(plugin_name, meshtastic_id)
        self.assertEqual(retrieved_data, test_data)

        # Retrieve all plugin data
        all_data = get_plugin_data(plugin_name)
        self.assertEqual(len(all_data), 1)
        self.assertEqual(json.loads(all_data[0][0]), test_data)

        # Delete plugin data
        delete_plugin_data(plugin_name, meshtastic_id)
        retrieved_after_delete = get_plugin_data_for_node(plugin_name, meshtastic_id)
        self.assertEqual(retrieved_after_delete, [])

    def test_message_map_operations(self):
        """
        Verifies storing and retrieving message map entries by Meshtastic ID and Matrix event ID, ensuring all fields are correctly persisted and retrieved.
        """
        initialize_database()

        # Test data
        meshtastic_id = 12345
        matrix_event_id = "$event123:matrix.org"
        matrix_room_id = "!room123:matrix.org"
        meshtastic_text = "Hello from mesh"
        meshtastic_meshnet = "test_mesh"

        # Store message map
        store_message_map(
            meshtastic_id,
            matrix_event_id,
            matrix_room_id,
            meshtastic_text,
            meshtastic_meshnet,
        )

        # Retrieve by meshtastic_id
        result = get_message_map_by_meshtastic_id(meshtastic_id)
        self.assertIsNotNone(result)
        assert result is not None  # Type narrowing for pyright
        self.assertEqual(result[0], matrix_event_id)
        self.assertEqual(result[1], matrix_room_id)
        self.assertEqual(result[2], meshtastic_text)
        self.assertEqual(result[3], meshtastic_meshnet)

        # Retrieve by matrix_event_id
        result = get_message_map_by_matrix_event_id(matrix_event_id)
        self.assertIsNotNone(result)
        assert result is not None  # Type narrowing for pyright
        self.assertEqual(result[0], str(meshtastic_id))
        self.assertEqual(result[1], matrix_room_id)
        self.assertEqual(result[2], meshtastic_text)
        self.assertEqual(result[3], meshtastic_meshnet)

    def test_message_map_id_normalization(self):
        """
        Verify that int and str representations of the same Meshtastic ID map to the same row.
        """
        initialize_database()

        store_message_map(
            12345,
            "$event1:matrix.org",
            "!room:matrix.org",
            "text1",
        )

        result_int = get_message_map_by_meshtastic_id(12345)
        result_str = get_message_map_by_meshtastic_id("12345")

        self.assertIsNotNone(result_int)
        self.assertIsNotNone(result_str)
        self.assertEqual(result_int, result_str)

    def test_wipe_message_map(self):
        """
        Verifies that wiping the message map removes all entries from the database.

        This test initializes the database, inserts sample message map entries, confirms their existence, performs a wipe operation, and asserts that all entries have been deleted.
        """
        initialize_database()

        # Add some test data
        store_message_map(1, "$event1:matrix.org", "!room:matrix.org", "test1")
        store_message_map(2, "$event2:matrix.org", "!room:matrix.org", "test2")

        # Verify data exists
        self.assertIsNotNone(get_message_map_by_meshtastic_id(1))
        self.assertIsNotNone(get_message_map_by_meshtastic_id(2))

        # Wipe message map
        wipe_message_map()

        # Verify data is gone
        self.assertIsNone(get_message_map_by_meshtastic_id(1))
        self.assertIsNone(get_message_map_by_meshtastic_id(2))

    def test_prune_message_map(self):
        """
        Verify that pruning the message map retains only the specified number of most recent entries.

        This test inserts multiple message map entries, prunes the table to keep only the latest five, and asserts that only those entries remain.
        """
        initialize_database()

        # Add multiple entries
        for i in range(10):
            store_message_map(
                i, f"$event{i}:matrix.org", "!room:matrix.org", f"test{i}"
            )

        # Prune to keep only 5 entries
        prune_message_map(5)

        # Verify only recent entries remain
        with sqlite3.connect(self.test_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM message_map")
            count = cursor.fetchone()[0]
            self.assertEqual(count, 5)

            # Verify the kept entries are the most recent ones
            cursor.execute("SELECT meshtastic_id FROM message_map ORDER BY rowid")
            kept_ids = [row[0] for row in cursor.fetchall()]
            self.assertEqual(kept_ids, ["5", "6", "7", "8", "9"])

    def test_database_manager_reuses_connection(self):
        """
        Ensure that the database manager reuses the same SQLite connection for multiple operations within the same thread.
        """
        clear_db_path_cache()
        with patch("sqlite3.connect", wraps=sqlite3.connect) as mock_connect:
            initialize_database()
            store_plugin_data("plugin", "nodeA", {"value": 1})
            store_plugin_data("plugin", "nodeB", {"value": 2})
            # DatabaseManager may open an in-memory probe connection during startup
            # capability checks; assert reuse for the real configured database path.
            real_db_connect_calls = []
            for call in mock_connect.call_args_list:
                path = ""
                if call.args:
                    path = str(call.args[0])
                elif "database" in call.kwargs:
                    path = str(call.kwargs["database"])
                if path and os.path.abspath(path) == os.path.abspath(self.test_db_path):
                    real_db_connect_calls.append(call)
            self.assertEqual(len(real_db_connect_calls), 1)

    def test_async_store_and_prune_message_map(self):
        """
        Validate the async helpers for storing and pruning message map entries execute without blocking.
        """
        initialize_database()

        manager = _get_db_manager()

        async def exercise():
            """
            Store two message-map entries and prune the message map to keep only the most recent entry.

            Inserts two distinct message-map rows and then trims the table so only the newest row remains.
            """
            await async_store_message_map(
                "mesh1", "$event1:matrix.org", "!room:matrix.org", "text1"
            )
            await async_store_message_map(
                "mesh2", "$event2:matrix.org", "!room:matrix.org", "text2"
            )
            await async_prune_message_map(1)

        with patch("mmrelay.db_utils._get_db_manager", return_value=manager):
            asyncio.run(exercise())

        # Oldest entry should have been pruned
        self.assertIsNone(get_message_map_by_meshtastic_id("mesh1"))
        latest = get_message_map_by_meshtastic_id("mesh2")
        self.assertIsNotNone(latest)
        assert latest is not None  # Type narrowing for pyright
        self.assertEqual(latest[0], "$event2:matrix.org")

    def test_database_manager_keyboard_interrupt(self):
        """
        Test that DatabaseManager creation re-raises KeyboardInterrupt.

        This test verifies that KeyboardInterrupt exceptions are not caught
        by the fallback exception handler and are properly re-raised.
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise KeyboardInterrupt
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=KeyboardInterrupt("User interrupt"),
        ):
            with self.assertRaises(KeyboardInterrupt):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_database_manager_system_exit(self):
        """
        Test that DatabaseManager creation re-raises SystemExit.

        This test verifies that SystemExit exceptions are not caught
        by the fallback exception handler and are properly re-raised.
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise SystemExit
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=SystemExit("System shutdown"),
        ):
            with self.assertRaises(SystemExit):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_get_db_path_directory_creation_error(self):
        """
        Test that get_db_path() handles OSError/PermissionError when creating directories gracefully.

        This test verifies that when directory creation fails, the function logs a warning
        but continues execution, returning the configured path.
        """
        # Clear cache to ensure fresh resolution
        clear_db_path_cache()

        # Configure a path in a non-existent directory
        invalid_db_path = "/nonexistent/invalid/path/test.db"
        mock_config = {"database": {"path": invalid_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock os.makedirs to raise PermissionError
        with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                path = get_db_path()
                self.assertEqual(path, invalid_db_path)
                mock_logger.warning.assert_called_once()

    def test_get_db_path_legacy_directory_creation_error(self):
        """
        Test that get_db_path() handles OSError/PermissionError when creating directories for legacy config.

        This test verifies the same error handling for the legacy 'db.path' configuration format.
        """
        # Clear cache to ensure fresh resolution
        clear_db_path_cache()

        # Configure a legacy path in a non-existent directory
        invalid_db_path = "/nonexistent/legacy/path/test.db"
        mock_config = {"db": {"path": invalid_db_path}}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock os.makedirs to raise OSError
        with patch("os.makedirs", side_effect=OSError("No space left on device")):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                path = get_db_path()
                self.assertEqual(path, invalid_db_path)
                # Should have two warnings: one for directory creation failure, one for legacy config
                self.assertEqual(mock_logger.warning.call_count, 2)

    def test_get_db_path_data_directory_creation_error(self):
        """
        Verify get_db_path returns a default meshtastic.sqlite path and logs a warning when creating the default data directory fails.

        Mocks resolve_all_paths to point to a non-existent data directory and forces os.makedirs to raise PermissionError; asserts the returned path ends with "meshtastic.sqlite" and that a single warning was logged.
        """
        # Clear cache and remove any database config to force default path
        clear_db_path_cache()
        mock_config = {}

        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock resolve_all_paths and os.makedirs to raise PermissionError
        with patch(
            "mmrelay.db_utils.resolve_all_paths",
            return_value={"database_dir": "/nonexistent/data", "legacy_sources": []},
        ):
            with patch("os.makedirs", side_effect=PermissionError("Permission denied")):
                with patch("mmrelay.db_utils.logger") as mock_logger:
                    path = get_db_path()
                    self.assertTrue(path.endswith("meshtastic.sqlite"))
                    mock_logger.warning.assert_called_once()

    def test_database_manager_config_change_fallback(self):
        """
        Test that DatabaseManager creation falls back to old manager on config change failure.

        This test verifies that when a configuration change causes DatabaseManager
        creation to fail, the system continues using the previous working manager.
        """
        # Clear cache and create initial database manager
        clear_db_path_cache()
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Create initial manager
        from mmrelay.db_utils import _get_db_manager

        initial_manager = _get_db_manager()
        self.assertIsNotNone(initial_manager)

        # Change config to trigger manager recreation, but make it fail
        new_db_path = os.path.join(self.test_dir, "new_test.db")
        mock_config["database"]["path"] = new_db_path

        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=RuntimeError("Invalid configuration"),
        ):
            with patch("mmrelay.db_utils.logger") as mock_logger:
                # Should return the same manager (fallback)
                fallback_manager = _get_db_manager()
                self.assertEqual(initial_manager, fallback_manager)
                mock_logger.exception.assert_called_once()

    def test_database_manager_first_time_failure(self):
        """
        Test that DatabaseManager creation raises exception on first-time initialization failure.

        This test verifies that when no previous manager exists and creation fails,
        the exception is properly raised (no fallback possible).
        """
        # Reset any existing database manager
        _reset_db_manager()
        clear_db_path_cache()

        # Configure a database path
        mock_config = {"database": {"path": self.test_db_path}}
        import mmrelay.db_utils

        mmrelay.db_utils.config = mock_config

        # Mock DatabaseManager to raise RuntimeError
        with patch(
            "mmrelay.db_utils.DatabaseManager",
            side_effect=RuntimeError("Cannot create database"),
        ):
            with self.assertRaises(RuntimeError):
                from mmrelay.db_utils import _get_db_manager

                _get_db_manager()

    def test_parse_bool_function(self):
        """
        Test the _parse_bool function with various inputs.

        This test verifies that the function correctly parses boolean values
        from different input types and formats.
        """

        # Test boolean inputs
        self.assertTrue(_parse_bool(True, False))
        self.assertFalse(_parse_bool(False, True))

        # Test string inputs - true values
        self.assertTrue(_parse_bool("1", False))
        self.assertTrue(_parse_bool("true", False))
        self.assertTrue(_parse_bool("TRUE", False))
        self.assertTrue(_parse_bool("yes", False))
        self.assertTrue(_parse_bool("YES", False))
        self.assertTrue(_parse_bool("on", False))
        self.assertTrue(_parse_bool("ON", False))

        # Test string inputs - false values
        self.assertFalse(_parse_bool("0", True))
        self.assertFalse(_parse_bool("false", True))
        self.assertFalse(_parse_bool("FALSE", True))
        self.assertFalse(_parse_bool("no", True))
        self.assertFalse(_parse_bool("NO", True))
        self.assertFalse(_parse_bool("off", True))
        self.assertFalse(_parse_bool("OFF", True))

        # Test string inputs with whitespace
        self.assertTrue(_parse_bool("  true  ", False))
        self.assertFalse(_parse_bool("  false  ", True))

        # Test fallback for unrecognized values
        self.assertTrue(_parse_bool("unknown", True))
        self.assertFalse(_parse_bool("unknown", False))
        self.assertTrue(_parse_bool(None, True))
        self.assertFalse(_parse_bool(None, False))
        self.assertTrue(_parse_bool(123, True))
        self.assertFalse(_parse_bool(123, False))

    def test_parse_int_function(self):
        """
        Test the _parse_int function with various inputs.

        This test verifies that the function correctly parses integer values
        from different input types and falls back to default on failure.
        """

        # Test valid integer inputs
        self.assertEqual(_parse_int(42, 0), 42)
        self.assertEqual(_parse_int("42", 0), 42)
        self.assertEqual(_parse_int("-10", 0), -10)
        self.assertEqual(_parse_int("0", 99), 0)

        # Test invalid inputs - should return default
        self.assertEqual(_parse_int("not_a_number", 42), 42)
        self.assertEqual(_parse_int("", 99), 99)
        self.assertEqual(_parse_int(None, 10), 10)
        self.assertEqual(_parse_int([], 5), 5)
        self.assertEqual(_parse_int({}, 7), 7)

        # Test float strings - should fail and return default
        self.assertEqual(_parse_int("3.14", 0), 0)
        self.assertEqual(_parse_int("42.0", 99), 99)

    def test_initialize_database_sqlite_error(self):
        """
        Test that initialize_database() handles sqlite3.Error gracefully.

        This test verifies that when database initialization fails due to
        sqlite3.Error, the exception is logged and re-raised.
        """
        # Mock the database manager's run_sync method to raise sqlite3.Error
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.run_sync.side_effect = sqlite3.Error(
                "Database initialization failed"
            )
            mock_get_manager.return_value = mock_manager

            with patch("mmrelay.db_utils.logger") as mock_logger:
                with self.assertRaises(sqlite3.Error):
                    initialize_database()
                mock_logger.exception.assert_called_once_with(
                    "Database initialization failed"
                )

    def test_schema_upgrade_operational_errors(self):
        """
        Test that schema upgrade operations handle OperationalError gracefully.

        This test verifies that ALTER TABLE and CREATE INDEX operations
        that fail with OperationalError are ignored (safe no-op) by
        running initialize_database twice and checking that it succeeds.
        """
        # Initialize database first time - should succeed
        initialize_database()

        # Initialize database second time - should also succeed even though
        # ALTER TABLE and CREATE INDEX will fail with OperationalError
        # because column and index already exist
        try:
            initialize_database()
            # If we get here, OperationalError was handled correctly
        except sqlite3.OperationalError:
            self.fail("Schema upgrade should handle OperationalError gracefully")

    def test_get_db_path_legacy_database_root_level(self):
        """
        Test that get_db_path() returns legacy database path when database exists at root level of legacy directory.

        This test verifies lines 144-164: when default path doesn't exist and deprecation window is active,
        legacy directories are searched for existing databases.
        """
        # Clear cache and config to test default behavior with legacy database
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        # Create temporary directories
        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database at root level
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        self.assertEqual(path, legacy_db_path)

    def test_get_db_path_legacy_database_data_subdir(self):
        """
        Test that get_db_path() returns legacy database path when database exists in data/ subdirectory.

        This test verifies that the legacy path search includes the data/ subdirectory candidate.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database in data/ subdirectory
            os.makedirs(legacy_dir, exist_ok=True)
            data_subdir = os.path.join(legacy_dir, "data")
            os.makedirs(data_subdir, exist_ok=True)
            legacy_db_path = os.path.join(data_subdir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        self.assertEqual(path, legacy_db_path)

    def test_get_db_path_legacy_database_database_subdir(self):
        """
        Test that get_db_path() returns legacy database path when database exists in database/ subdirectory.

        This test verifies that the legacy path search includes the database/ subdirectory candidate.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database in database/ subdirectory
            os.makedirs(legacy_dir, exist_ok=True)
            database_subdir = os.path.join(legacy_dir, "database")
            os.makedirs(database_subdir, exist_ok=True)
            legacy_db_path = os.path.join(database_subdir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        self.assertEqual(path, legacy_db_path)

    def test_get_db_path_no_legacy_database_returns_default(self):
        """
        Test that get_db_path() returns default path when no legacy database exists.

        This test verifies that when legacy directories don't contain a database,
        the function returns the default path.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create empty legacy directory (no database)
            os.makedirs(legacy_dir, exist_ok=True)

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        expected_path = os.path.join(database_dir, "meshtastic.sqlite")
                        self.assertEqual(path, expected_path)

    def test_get_db_path_default_exists_skips_legacy_check(self):
        """
        Test that get_db_path() skips legacy check when default database path exists.

        This test verifies that if the default database path exists, the function
        returns it immediately without checking legacy directories (line 144 condition).
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create default database
            os.makedirs(database_dir, exist_ok=True)
            default_db_path = os.path.join(database_dir, "meshtastic.sqlite")
            with sqlite3.connect(default_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Create legacy database (should NOT be returned)
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        # Should return default path, NOT legacy path
                        self.assertEqual(path, default_db_path)

    def test_get_db_path_deprecation_inactive_skips_legacy_check(self):
        """
        Test that get_db_path() skips legacy check when deprecation window is inactive.

        This test verifies that when is_deprecation_window_active returns False,
        the function returns the default path without checking legacy directories.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database (should NOT be returned when deprecation inactive)
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window (inactive)
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=False
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        path = get_db_path()
                        # Should return default path, NOT legacy path
                        expected_path = os.path.join(database_dir, "meshtastic.sqlite")
                        self.assertEqual(path, expected_path)

    def test_get_db_path_legacy_database_logs_warning(self):
        """
        Test that get_db_path() logs a warning when legacy database is found.

        This test verifies that the deprecation warning is logged (lines 155-161)
        when a database is found in a legacy location.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        with patch("mmrelay.db_utils.logger") as mock_logger:
                            path = get_db_path()
                            self.assertEqual(path, legacy_db_path)
                            # Verify warning was logged
                            mock_logger.warning.assert_called_once()
                            call_args = mock_logger.warning.call_args[0]
                            self.assertIn(
                                "Database found in legacy location", call_args[0]
                            )
                            self.assertIn("mmrelay migrate", call_args[0])

    def test_get_db_path_first_legacy_directory_wins(self):
        """
        Test that get_db_path() returns database from first legacy directory that contains it.

        This test verifies that when multiple legacy directories have databases,
        the first one (in priority order) is returned.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir1 = os.path.join(temp_dir, "legacy1")
            legacy_dir2 = os.path.join(temp_dir, "legacy2")

            # Create databases in both legacy directories
            os.makedirs(legacy_dir1, exist_ok=True)
            legacy_db_path1 = os.path.join(legacy_dir1, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path1) as conn:
                conn.execute("CREATE TABLE test_table1 (id INTEGER)")

            os.makedirs(legacy_dir2, exist_ok=True)
            legacy_db_path2 = os.path.join(legacy_dir2, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path2) as conn:
                conn.execute("CREATE TABLE test_table2 (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs",
                        return_value=[legacy_dir1, legacy_dir2],
                    ):
                        path = get_db_path()
                        # Should return first legacy database
                        self.assertEqual(path, legacy_db_path1)

    def test_get_db_path_warning_logged_only_once(self):
        """
        Test that get_db_path() logs warning only once due to _db_path_logged cache.

        This test verifies that the deprecation warning is logged only on the first call,
        and subsequent cached calls do not log again (line 155 condition).
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            # Mock paths and deprecation window
            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs", return_value=[legacy_dir]
                    ):
                        with patch("mmrelay.db_utils.logger") as mock_logger:
                            # First call - should log warning
                            path1 = get_db_path()
                            self.assertEqual(path1, legacy_db_path)
                            self.assertEqual(mock_logger.warning.call_count, 1)

                            # Second call - should use cache, NOT log warning
                            path2 = get_db_path()
                            self.assertEqual(path2, legacy_db_path)
                            self.assertEqual(mock_logger.warning.call_count, 1)

    def test_get_db_path_warning_skip_when_already_logged(self):
        """
        Test that get_db_path() skips logging warning when _db_path_logged is already True.

        Forces re-resolution (clear cache) while keeping _db_path_logged = True
        to verify the else branch of the _db_path_logged guard.
        """
        clear_db_path_cache()
        import mmrelay.db_utils

        mmrelay.db_utils.config = None

        with tempfile.TemporaryDirectory() as temp_dir:
            database_dir = os.path.join(temp_dir, "database")
            legacy_dir = os.path.join(temp_dir, "legacy")

            # Create legacy database
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_db_path = os.path.join(legacy_dir, "meshtastic.sqlite")
            with sqlite3.connect(legacy_db_path) as conn:
                conn.execute("CREATE TABLE test_table (id INTEGER)")

            with patch(
                "mmrelay.db_utils.resolve_all_paths",
                return_value={"database_dir": database_dir, "legacy_sources": []},
            ):
                with patch(
                    "mmrelay.db_utils.is_deprecation_window_active", return_value=True
                ):
                    with patch(
                        "mmrelay.db_utils.get_legacy_dirs",
                        return_value=[legacy_dir],
                    ):
                        with patch("mmrelay.db_utils.logger") as mock_logger:
                            # Pre-set _db_path_logged to True, then clear only
                            # the cached path to force re-resolution through
                            # the legacy branch while skipping the warning.
                            import mmrelay.db_utils as db_mod

                            db_mod._cached_db_path = None
                            db_mod._cached_config_hash = None
                            db_mod._db_path_logged = True

                            path = get_db_path()
                            self.assertEqual(path, legacy_db_path)
                            # Warning should NOT have been logged
                            mock_logger.warning.assert_not_called()

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_delete_stale_longnames_database_error(self, mock_logger, mock_get_manager):
        """
        Test delete_stale_longnames handles sqlite3.Error gracefully.

        Covers lines 932-934: exception handling in _delete_stale_names.
        """
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database locked")

        result = delete_stale_longnames({"!test123"})

        # Should return 0 on error
        self.assertEqual(result, 0)
        # Should log exception
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error deleting stale", call_args[0])

    @patch("mmrelay.db_utils._get_db_manager")
    @patch("mmrelay.db_utils.logger")
    def test_delete_stale_shortnames_database_error(
        self, mock_logger, mock_get_manager
    ):
        """
        Test delete_stale_shortnames handles sqlite3.Error gracefully.

        Covers lines 932-934: exception handling in _delete_stale_names.
        """
        mock_manager = MagicMock()
        mock_get_manager.return_value = mock_manager
        mock_manager.run_sync.side_effect = sqlite3.Error("Database is full")

        result = delete_stale_shortnames({"!test456"})

        # Should return 0 on error
        self.assertEqual(result, 0)
        # Should log exception
        mock_logger.exception.assert_called_once()
        call_args = mock_logger.exception.call_args[0]
        self.assertIn("Database error deleting stale", call_args[0])


if __name__ == "__main__":
    unittest.main()
