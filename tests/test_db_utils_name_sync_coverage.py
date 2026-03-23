#!/usr/bin/env python3
"""
Targeted coverage tests for node-name sync helpers in db_utils.

These tests exercise error-handling and branch paths that are easy to miss when
only testing the high-level API.
"""

import shutil
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import mmrelay.db_utils as dbu
from mmrelay.constants.database import NAMES_TABLE_LONGNAMES
from mmrelay.db_runtime import DatabaseManager
from mmrelay.db_utils import (
    NodeNameEntry,
    _collect_node_name_snapshot,
    _delete_name_by_id,
    _InvalidNamesTableError,
    _name_table_matches_state,
    _name_tables_match_state,
    _normalize_node_name_value,
    _read_name_values_for_ids,
    _reset_db_manager,
    clear_db_path_cache,
    get_longname,
    get_shortname,
    initialize_database,
    sync_name_tables_if_changed,
)


@pytest.fixture
def configured_temp_db() -> str:
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


def test_normalize_node_name_value_handles_non_string_subclass_values() -> None:
    """Custom object types should normalize to None without conversion attempts."""

    class _ExplodingString:
        pass

    assert _normalize_node_name_value(_ExplodingString()) is None


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


def test_sync_unchanged_snapshot_repair_failure_keeps_previous_state(
    configured_temp_db: str,
) -> None:
    """Repair failures on unchanged snapshots should keep prior state for retries."""
    nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(nodes, previous_state=None)

    with sqlite3.connect(configured_temp_db, timeout=5) as conn:
        conn.execute("DELETE FROM longnames WHERE meshtastic_id = ?", ("!1",))

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
    """Transient empty NodeDB snapshots should not delete existing name rows."""
    _ = configured_temp_db
    nodes = {
        "node_a": {"user": {"id": "!1", "longName": "Alpha", "shortName": "A"}},
    }
    first_state = sync_name_tables_if_changed(nodes, previous_state=None)
    assert first_state == (NodeNameEntry("!1", "Alpha", "A"),)
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"

    empty_state = sync_name_tables_if_changed({}, previous_state=first_state)
    assert empty_state == ()
    assert get_longname("!1") == "Alpha"
    assert get_shortname("!1") == "A"
