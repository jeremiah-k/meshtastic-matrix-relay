#!/usr/bin/env python3
"""
Targeted coverage tests for node-name sync helpers in db_utils.

These tests exercise error-handling and branch paths that are easy to miss when
only testing the high-level API.
"""

import shutil
import sqlite3
import tempfile
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.db_utils as dbu
from mmrelay.constants.database import (
    NAMES_TABLE_LONGNAMES,
    PROTO_NODE_NAME_LONG,
)
from mmrelay.db_runtime import DatabaseManager
from mmrelay.db_utils import (
    NodeNameEntry,
    _collect_node_name_snapshot,
    _delete_name_by_id,
    _delete_stale_names_core,
    _format_node_id_sample,
    _InvalidNamesTableError,
    _merge_node_name_values,
    _name_table_matches_state,
    _name_tables_match_state,
    _normalize_node_name_value,
    _read_name_values_for_ids,
    _reset_db_manager,
    _sync_name_tables_atomic,
    _update_names_core,
    clear_db_path_cache,
    delete_longname,
    get_longname,
    get_shortname,
    initialize_database,
    save_longname,
    save_shortname,
    sync_name_tables_if_changed,
)


@pytest.fixture
def configured_temp_db() -> Generator[str, None, None]:
    """Provide a temporary configured database path for db_utils tests."""
    _reset_db_manager()
    clear_db_path_cache()
    temp_dir = tempfile.mkdtemp()
    db_path = f"{temp_dir}/test_meshtastic.sqlite"
    original_config = dbu.config
    dbu.config = {"database": {"path": db_path}}
    try:
        initialize_database()
        yield db_path
    finally:
        _reset_db_manager()
        clear_db_path_cache()
        dbu.config = original_config
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_delete_name_by_id_rejects_unknown_table(configured_temp_db: str) -> None:
    """Invalid names table identifiers should raise a clear error."""
    _ = configured_temp_db
    with pytest.raises(_InvalidNamesTableError):
        _delete_name_by_id("unknown_names_table", "!1")


def test_normalize_node_name_value_handles_non_string_values() -> None:
    """Normalization should treat non-string payload values as absent."""

    class _BadString:
        pass

    assert _normalize_node_name_value("Alpha") == "Alpha"
    assert _normalize_node_name_value("") is None
    assert _normalize_node_name_value(123) is None
    assert _normalize_node_name_value(_BadString()) is None


def test_normalize_node_name_value_preserves_string_subclass_values() -> None:
    """String subclasses should normalize as normal strings."""

    class _StringSubclass(str):
        pass

    assert _normalize_node_name_value(_StringSubclass("Bravo")) == "Bravo"


def test_read_name_values_rejects_unknown_table(configured_temp_db: str) -> None:
    """Unknown names tables should be rejected before querying SQLite."""
    _ = configured_temp_db
    with pytest.raises(_InvalidNamesTableError):
        _read_name_values_for_ids("unknown_names_table", {"!1"})


def test_read_name_values_empty_ids_returns_empty_dict(configured_temp_db: str) -> None:
    """Empty ID collections should short-circuit without querying SQLite."""
    _ = configured_temp_db
    assert _read_name_values_for_ids(NAMES_TABLE_LONGNAMES, set()) == {}


def test_read_name_values_returns_none_on_sqlite_error(configured_temp_db: str) -> None:
    """SQLite failures in drift checks should not raise to caller paths."""
    _ = configured_temp_db
    with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.run_sync.side_effect = sqlite3.Error("read failure")
        mock_get_manager.return_value = mock_manager
        result = _read_name_values_for_ids(NAMES_TABLE_LONGNAMES, {"!1"})
    assert result is None


def test_read_name_values_returns_none_when_json_each_unavailable() -> None:
    """json_each lookup failures should return None through the SQLite error path."""

    class _FallbackCursor:
        def execute(
            self,
            sql: str,
            params: tuple[str] | tuple[str, ...],
        ) -> "_FallbackCursor":
            if "json_each" in sql:
                raise sqlite3.OperationalError("no such function: json_each")
            return self

        def fetchall(self) -> list[tuple[str, str]]:
            return []

    mock_manager = MagicMock()
    mock_manager.run_sync.side_effect = lambda func, write=False: func(
        _FallbackCursor()
    )
    with patch("mmrelay.db_utils._get_db_manager", return_value=mock_manager):
        result = _read_name_values_for_ids(NAMES_TABLE_LONGNAMES, {"!1", "!2"})

    assert result is None


def test_name_table_matches_state_handles_failed_read() -> None:
    """Failed reads should cause mismatch detection."""
    state = (NodeNameEntry("!1", "Alpha", "A"),)
    with patch("mmrelay.db_utils._read_name_values_for_ids", return_value=None):
        assert (
            _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            is False
        )


def test_name_tables_match_state_empty_state_is_true() -> None:
    """Empty snapshots should be treated as matching by definition."""
    assert _name_tables_match_state(()) is True


def test_name_tables_match_state_non_empty_state_checks_both_tables() -> None:
    """Non-empty snapshots should evaluate both names tables."""
    state = (NodeNameEntry("!1", "Alpha", "A"),)
    with patch(
        "mmrelay.db_utils._name_table_matches_state", side_effect=[True, True]
    ) as mock_match:
        assert _name_tables_match_state(state) is True
    assert mock_match.call_count == 2
    table_names = [call.kwargs.get("table") for call in mock_match.call_args_list]
    assert table_names == [dbu.NAMES_TABLE_LONGNAMES, dbu.NAMES_TABLE_SHORTNAMES]


def test_collect_node_name_snapshot_marks_invalid_entries_incomplete() -> None:
    """Malformed node rows should not crash collection and should mark partial snapshots."""
    nodes = {
        "not_dict": "bad",
        "user_not_dict": {"user": "bad"},
        "missing_id": {"user": {"longName": "No ID", "shortName": "N"}},
        "empty_id": {"user": {"id": "", "longName": "Empty", "shortName": "E"}},
        "invalid_id_dict": {
            "user": {"id": {"bad": 1}, "longName": "Bad", "shortName": "BD"}
        },
        "invalid_id_bool": {
            "user": {"id": True, "longName": "Bool", "shortName": "BL"}
        },
        "valid": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
    assert state == (("!1", "Alpha", "A"),)
    assert current_ids == {"!1"}
    assert snapshot_complete is False


def test_collect_node_name_snapshot_empty_dict_is_incomplete() -> None:
    """Empty snapshots are treated as incomplete to avoid accidental global pruning."""
    state, current_ids, snapshot_complete = _collect_node_name_snapshot({})
    assert state == ()
    assert current_ids == set()
    assert snapshot_complete is False


def test_sync_skips_non_string_name_payloads_without_deleting_rows(
    configured_temp_db: str,
) -> None:
    """Malformed non-string name payloads should preserve existing rows."""
    _ = configured_temp_db
    initial_nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(initial_nodes, previous_state=None)
    assert first_state == (NodeNameEntry("!1", "Alpha", "A"),)
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"

    malformed_nodes = {
        "node_a": {"user": {"id": "!1", "longName": 123, "shortName": "A"}}
    }
    second_state = sync_name_tables_if_changed(
        malformed_nodes,
        previous_state=first_state,
    )
    assert second_state == first_state
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"


def test_sync_unchanged_snapshot_repair_failure_keeps_previous_state(
    configured_temp_db: str,
) -> None:
    """Repair failures on unchanged snapshots should keep prior state for retries."""
    nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(nodes, previous_state=None)
    assert first_state is not None
    assert get_longname("!1") == "Alpha"

    delete_longname("!1")

    assert get_longname("!1") is None

    original_run_sync = DatabaseManager.run_sync
    repair_failure_triggered = False

    def fail_repair_write(self, func, *, write=False):
        nonlocal repair_failure_triggered
        if (
            write
            and getattr(func, "__name__", "") == "_sync"
            and not repair_failure_triggered
        ):
            repair_failure_triggered = True
            raise sqlite3.Error("forced write failure on repair")
        return original_run_sync(self, func, write=write)

    with patch.object(
        DatabaseManager,
        "run_sync",
        autospec=True,
        side_effect=fail_repair_write,
    ):
        second_state = sync_name_tables_if_changed(nodes, previous_state=first_state)

    assert repair_failure_triggered, "Repair failure path was not exercised"
    assert second_state == first_state
    assert get_longname("!1") is None


def test_sync_empty_snapshot_does_not_prune_existing_rows(
    configured_temp_db: str,
) -> None:
    """Empty snapshots should preserve prior state and avoid transient data loss."""
    _ = configured_temp_db
    nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(nodes, previous_state=None)
    assert first_state == (NodeNameEntry("!1", "Alpha", "A"),)
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"

    empty_state = sync_name_tables_if_changed({}, previous_state=first_state)
    assert empty_state == first_state
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"

    stable_empty_state = sync_name_tables_if_changed({}, previous_state=empty_state)
    assert stable_empty_state == first_state
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"


def test_sync_non_authoritative_empty_snapshot_does_not_arm_immediate_prune(
    configured_temp_db: str,
) -> None:
    """Conflict-only snapshots should not allow single-cycle empty snapshot pruning."""
    _ = configured_temp_db
    baseline_nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(baseline_nodes, previous_state=None)
    assert first_state == (NodeNameEntry("!1", "Alpha", "A"),)
    assert get_longname("!1") == "Alpha"

    conflicting_nodes = {
        "dup1": {"user": {"id": "!1", "shortName": "ONE"}},
        "dup2": {"user": {"id": "!1", "shortName": "TWO"}},
    }
    conflict_state = sync_name_tables_if_changed(
        conflicting_nodes,
        previous_state=first_state,
    )
    assert conflict_state == first_state
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"

    first_empty_after_conflict = sync_name_tables_if_changed(
        {},
        previous_state=conflict_state,
    )
    assert first_empty_after_conflict == first_state
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"


class TestFormatNodeIdSample:
    """Tests for _format_node_id_sample behavior."""

    def test_empty_collection_returns_empty_brackets(self) -> None:
        """Empty collection should return '[]'."""
        assert _format_node_id_sample([]) == "[]"
        assert _format_node_id_sample(set()) == "[]"

    def test_collection_under_limit_returns_formatted_list(self) -> None:
        """Collection under limit should return sorted formatted list."""
        ids = {"!3", "!1", "!2"}
        result = _format_node_id_sample(ids)
        assert result == "['!1', '!2', '!3']"

    def test_collection_over_limit_returns_sample_with_more(self) -> None:
        """Collection over limit should return sample with (+N more)."""
        ids = {f"!{i}" for i in range(25)}
        result = _format_node_id_sample(ids)
        assert "(+5 more)" in result
        assert result.startswith("['!0'")


class TestDeleteNameByIdSqliteError:
    """Tests for _delete_name_by_id sqlite3.Error handling."""

    def test_delete_name_by_id_sqlite_error_returns_false(
        self, configured_temp_db: str
    ) -> None:
        """sqlite3.Error should be caught, logged, and return False."""
        _ = configured_temp_db
        with patch("mmrelay.db_utils._get_db_manager") as mock_get_manager:
            mock_manager = MagicMock()
            mock_manager.run_sync.side_effect = sqlite3.Error("delete failed")
            mock_get_manager.return_value = mock_manager

            with patch("mmrelay.db_utils.logger") as mock_logger:
                result = _delete_name_by_id(NAMES_TABLE_LONGNAMES, "!1")
                assert result is False
                mock_logger.exception.assert_called_once()


class TestNameTableMatchesStateFalseConditions:
    """Tests for _name_table_matches_state return-false conditions."""

    def test_returns_false_when_expected_none_but_id_in_actual(self) -> None:
        """Returns False when expected_value is None but id_key is in actual_by_id."""
        state = (NodeNameEntry("!1", None, "A"),)
        with patch(
            "mmrelay.db_utils._read_name_values_for_ids",
            return_value={"!1": "Unexpected"},
        ):
            result = _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            assert result is False

    def test_returns_false_when_id_not_in_actual_and_expected_not_none(self) -> None:
        """Returns False when id_key not in actual_by_id (and expected_value is not None)."""
        state = (NodeNameEntry("!1", "Alpha", "A"),)
        with patch("mmrelay.db_utils._read_name_values_for_ids", return_value={}):
            result = _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            assert result is False

    def test_returns_false_when_actual_value_is_none(self) -> None:
        """Returns False when actual_value is None."""
        state = (NodeNameEntry("!1", "Alpha", "A"),)
        with patch(
            "mmrelay.db_utils._read_name_values_for_ids", return_value={"!1": None}
        ):
            result = _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            assert result is False

    def test_returns_false_when_actual_value_is_empty(self) -> None:
        """Returns False when actual_value is empty string."""
        state = (NodeNameEntry("!1", "Alpha", "A"),)
        with patch(
            "mmrelay.db_utils._read_name_values_for_ids", return_value={"!1": ""}
        ):
            result = _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            assert result is False

    def test_returns_false_when_expected_differs_from_actual(self) -> None:
        """Returns False when expected_value != actual_value."""
        state = (NodeNameEntry("!1", "Alpha", "A"),)
        with patch(
            "mmrelay.db_utils._read_name_values_for_ids", return_value={"!1": "Beta"}
        ):
            result = _name_table_matches_state(
                state,
                table=NAMES_TABLE_LONGNAMES,
                get_name=lambda entry: entry.long_name,
            )
            assert result is False


class TestCollectNodeNameSnapshotInvalidNameTypes:
    """Tests for _collect_node_name_snapshot handling invalid name types (lines 1038-1061)."""

    def test_non_string_long_name_logs_warning(self) -> None:
        """Non-string raw_long_name logs warning, sets to None, and sets snapshot_complete=False."""
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": 123, "shortName": "A"}},
        }
        with patch("mmrelay.db_utils.logger") as mock_logger:
            state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
            assert state == (NodeNameEntry("!1", None, "A"),)
            assert current_ids == {"!1"}
            assert snapshot_complete is False
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0]
            assert "non-string" in call_args[0]

    def test_non_string_short_name_logs_warning(self) -> None:
        """Non-string raw_short_name logs warning, sets to None, and sets snapshot_complete=False."""
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": 456}},
        }
        with patch("mmrelay.db_utils.logger") as mock_logger:
            state, current_ids, snapshot_complete = _collect_node_name_snapshot(nodes)
            assert state == (NodeNameEntry("!1", "Alpha", None),)
            assert current_ids == {"!1"}
            assert snapshot_complete is False
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args[0]
            assert "non-string" in call_args[0]


class TestMergeNodeNameValuesEqual:
    """Tests for _merge_node_name_values when values are equal (lines 1118-1119)."""

    def test_returns_existing_when_both_equal_non_none(self) -> None:
        """Returns existing_value when both are equal non-None strings."""
        result = _merge_node_name_values("Alpha", "Alpha")
        assert result == "Alpha"

    def test_returns_incoming_when_existing_is_none(self) -> None:
        """Returns incoming_value when existing_value is None."""
        result = _merge_node_name_values(None, "Alpha")
        assert result == "Alpha"

    def test_returns_existing_when_incoming_is_none(self) -> None:
        """Returns existing_value when incoming_value is None."""
        result = _merge_node_name_values("Alpha", None)
        assert result == "Alpha"


class TestSyncNameTablesAtomicShortNameDeletion:
    """Tests for _sync_name_tables_atomic short_name deletion (lines 1159-1161)."""

    def test_short_name_none_executes_delete_sql(self, configured_temp_db: str) -> None:
        """When short_name is None and snapshot_complete=True, the short delete SQL is executed."""
        _ = configured_temp_db
        save_longname("!1", "Alpha")
        save_shortname("!1", "A")

        state = (NodeNameEntry("!1", "Alpha", None),)
        current_ids = {"!1"}

        result = _sync_name_tables_atomic(state, current_ids, snapshot_complete=True)
        assert result is True
        assert get_shortname("!1") is None


class TestSyncNameTablesAtomicDebugLogging:
    """Tests for _sync_name_tables_atomic debug logging (lines 1185-1224)."""

    def test_debug_logs_emitted_when_enabled(self, configured_temp_db: str) -> None:
        """Debug logs are emitted when logger.isEnabledFor(logging.DEBUG)."""
        _ = configured_temp_db
        state = (NodeNameEntry("!1", "Alpha", "A"),)
        current_ids = {"!1"}

        with patch("mmrelay.db_utils.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True

            result = _sync_name_tables_atomic(
                state, current_ids, snapshot_complete=True
            )
            assert result is True

            assert mock_logger.debug.call_count >= 1
            assert any(
                "long_upserts=" in str(call)
                for call in mock_logger.debug.call_args_list
            )

    def test_debug_logs_for_upserts_clears_and_pruned(
        self, configured_temp_db: str
    ) -> None:
        """Test all the debug log messages for upserts, clears, and pruned IDs."""
        _ = configured_temp_db
        save_longname("!stale", "Stale")
        save_shortname("!stale", "STL")

        state = (NodeNameEntry("!1", None, None),)
        current_ids = {"!1"}

        with patch("mmrelay.db_utils.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True

            result = _sync_name_tables_atomic(
                state, current_ids, snapshot_complete=True
            )
            assert result is True

            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("long_upserts=" in call for call in debug_calls)


class TestSyncNameTablesIfChangedDebugLoggingIdDelta:
    """Tests for sync_name_tables_if_changed debug logging for ID delta (lines 1272-1286)."""

    def test_debug_log_for_initial_snapshot(self, configured_temp_db: str) -> None:
        """Debug log for initial snapshot (previous_state is None)."""
        _ = configured_temp_db
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }

        with patch("mmrelay.db_utils.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True

            state = sync_name_tables_if_changed(nodes, previous_state=None)
            assert state is not None

            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("snapshot initialized" in call.lower() for call in debug_calls)

    def test_debug_log_for_added_and_removed_ids(self, configured_temp_db: str) -> None:
        """Debug log for added_ids and removed_ids when state changes."""
        _ = configured_temp_db
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        updated_nodes = {
            "node_b": {"user": {"id": "!2", "longName": "Beta", "shortName": "B"}},
        }

        with patch("mmrelay.db_utils.logger") as mock_logger:
            mock_logger.isEnabledFor.return_value = True

            sync_name_tables_if_changed(updated_nodes, previous_state=first_state)

            debug_calls = [str(call) for call in mock_logger.debug.call_args_list]
            assert any("added=" in call and "removed=" in call for call in debug_calls)


class TestSyncNameTablesIfChangedReturnPreviousOnDeleteError:
    """Tests for sync_name_tables_if_changed returning previous_state on delete error (lines 1308-1309)."""

    def test_returns_previous_when_longnames_deleted_is_none(
        self, configured_temp_db: str
    ) -> None:
        """Returns previous_state when longnames_deleted is None."""
        _ = configured_temp_db
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        with patch(
            "mmrelay.db_utils._delete_stale_names", return_value=None
        ) as mock_delete:
            second_state = sync_name_tables_if_changed(
                nodes, previous_state=first_state
            )
            assert second_state == first_state
            assert mock_delete.call_count >= 1

    def test_returns_previous_when_shortnames_deleted_is_none(
        self, configured_temp_db: str
    ) -> None:
        """Returns previous_state when shortnames_deleted is None."""
        _ = configured_temp_db
        nodes = {
            "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
        }
        first_state = sync_name_tables_if_changed(nodes, previous_state=None)

        call_count = [0]

        def delete_side_effect(table_name, current_ids, *, return_none_on_error=False):
            call_count[0] += 1
            if call_count[0] == 1:
                return 0
            return None

        with patch(
            "mmrelay.db_utils._delete_stale_names", side_effect=delete_side_effect
        ):
            second_state = sync_name_tables_if_changed(
                nodes, previous_state=first_state
            )
            assert second_state == first_state


class TestUpdateNamesCoreValueError:
    """Tests for _update_names_core ValueError for unsupported name_key (lines 1364-1365)."""

    def test_raises_value_error_for_unsupported_name_key(
        self, configured_temp_db: str
    ) -> None:
        """Raises ValueError when name_key not in _DB_COLUMN_BY_PROTO_NODE_NAME_FIELD."""
        _ = configured_temp_db
        nodes = {"node_a": {"user": {"id": "!1", "longName": "Alpha"}}}

        with pytest.raises(ValueError, match="Unsupported node name key"):
            _update_names_core(
                nodes,
                name_key="invalid_key",
                save_name=save_longname,
                delete_name=delete_longname,
                delete_stale_names=lambda ids: 0,
            )


class TestUpdateNamesCoreIterationAndStaleDeletion:
    """Tests for _update_names_core iteration and stale deletion (lines 1375-1394)."""

    def test_delete_name_called_when_normalized_name_is_none(
        self, configured_temp_db: str
    ) -> None:
        """Delete_name is called when normalized_name is None."""
        _ = configured_temp_db
        save_longname("!1", "Existing")

        nodes = {"node_a": {"user": {"id": "!1", "longName": ""}}}

        mock_delete = MagicMock(return_value=True)
        with patch("mmrelay.db_utils.delete_longname", mock_delete):
            result = _update_names_core(
                nodes,
                name_key=PROTO_NODE_NAME_LONG,
                save_name=save_longname,
                delete_name=mock_delete,
                delete_stale_names=lambda ids: 0,
            )
            assert result is True
            mock_delete.assert_called_once_with("!1")

    def test_all_saves_ok_becomes_false_when_delete_fails(
        self, configured_temp_db: str
    ) -> None:
        """All_saves_ok becomes False when delete_name fails."""
        _ = configured_temp_db
        save_longname("!1", "Existing")

        nodes = {"node_a": {"user": {"id": "!1", "longName": ""}}}

        mock_delete = MagicMock(return_value=False)
        result = _update_names_core(
            nodes,
            name_key=PROTO_NODE_NAME_LONG,
            save_name=save_longname,
            delete_name=mock_delete,
            delete_stale_names=lambda ids: 0,
        )
        assert result is False

    def test_stale_delete_count_none_sets_all_saves_ok_to_false(
        self, configured_temp_db: str
    ) -> None:
        """Stale_delete_count None sets all_saves_ok to False."""
        _ = configured_temp_db
        nodes = {"node_a": {"user": {"id": "!1", "longName": "Alpha"}}}

        result = _update_names_core(
            nodes,
            name_key=PROTO_NODE_NAME_LONG,
            save_name=save_longname,
            delete_name=delete_longname,
            delete_stale_names=lambda ids: None,
        )
        assert result is False


class TestDeleteStaleNamesCoreDeletedIdsSet:
    """Tests for _delete_stale_names_core updating deleted_ids set (lines 1544-1545)."""

    def test_deleted_ids_set_updated_with_deleted_chunk_ids(
        self, configured_temp_db: str
    ) -> None:
        """Deleted_ids set is updated with deleted chunk IDs when provided."""
        _ = configured_temp_db
        save_longname("!1", "Alpha")
        save_longname("!2", "Beta")
        save_longname("!3", "Charlie")

        manager = dbu._get_db_manager()

        deleted_ids: set[str] = set()

        def _delete_test(cursor: sqlite3.Cursor) -> int:
            return _delete_stale_names_core(
                cursor,
                NAMES_TABLE_LONGNAMES,
                {"!1"},
                deleted_ids=deleted_ids,
            )

        result = manager.run_sync(_delete_test, write=True)
        assert result == 2
        assert "!2" in deleted_ids
        assert "!3" in deleted_ids
        assert "!1" not in deleted_ids
