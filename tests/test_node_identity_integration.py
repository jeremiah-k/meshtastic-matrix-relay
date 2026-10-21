"""Cover the node-id normalization across its real integration points.

Two gaps matter beyond the pure helper:

- `interface.nodes` is keyed by node-id string, so a normalized sender still
  reaches the in-memory NodeDB fallback.
- Name rows written before normalization are keyed by the raw decimal sender,
  so canonical lookups need a fallback or existing caches strand.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Callable, Generator
from pathlib import Path

import pytest

from mmrelay import db_utils
from mmrelay.meshtastic.node_identity import normalize_node_id


@contextlib.contextmanager
def _managed_connection(path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection that is always closed on exit.

    ``with sqlite3.connect(...)`` commits or rolls back the transaction but
    leaves the connection open, so the garbage collector finalizes it later
    and pytest surfaces a ResourceWarning against an unrelated test.
    """
    conn = sqlite3.connect(path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@pytest.fixture
def relay_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the relay at a throwaway home and yield its database path.

    Both the database manager and the resolved-path cache are cleared before
    and after, so a stale manager cannot keep serving a previous test's file
    and no connection is left open for the garbage collector to finalize
    later. Resetting between setup and use would close the manager while its
    connection is still tracked, which surfaces as a ResourceWarning in an
    unrelated test.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MMRELAY_HOME", str(home))
    db_utils.clear_db_path_cache()
    db_utils._reset_db_manager()
    db_utils.initialize_database()
    db_path = Path(db_utils.get_db_path())
    try:
        yield db_path
    finally:
        db_utils._reset_db_manager()
        db_utils.clear_db_path_cache()


def _insert_row(db_path: Path, key: str, longname: str, shortname: str) -> None:
    """Write rows under an explicit key, bypassing save_* normalization."""
    with _managed_connection(db_path) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS longnames "
            "(meshtastic_id TEXT PRIMARY KEY, longname TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS shortnames "
            "(meshtastic_id TEXT PRIMARY KEY, shortname TEXT)"
        )
        con.execute("INSERT OR REPLACE INTO longnames VALUES (?, ?)", (key, longname))
        con.execute("INSERT OR REPLACE INTO shortnames VALUES (?, ?)", (key, shortname))
        con.commit()


class TestLegacyDecimalKeyFallback:
    """Rows written before normalization stay resolvable."""

    def test_lookup_falls_back_to_legacy_decimal_key(self, relay_db: Path) -> None:
        """A row stored under the raw decimal key still resolves.

        Normalizing the lookup without a fallback would strand every name
        cached by an earlier version.
        """
        _insert_row(relay_db, "4134515116", "Legacy Node", "LN")

        assert db_utils.get_longname("!f66fb1ac") == "Legacy Node"
        assert db_utils.get_shortname("!f66fb1ac") == "LN"

    def test_canonical_key_wins_over_legacy(self, relay_db: Path) -> None:
        """When both keys hold a row, the canonical one is returned."""
        _insert_row(relay_db, "!f66fb1ac", "Canonical", "C")
        _insert_row(relay_db, "4134515116", "Legacy", "L")

        assert db_utils.get_longname("!f66fb1ac") == "Canonical"
        assert db_utils.get_shortname("!f66fb1ac") == "C"

    def test_save_writes_the_canonical_key(self, relay_db: Path) -> None:
        """New rows are written canonically, not under the decimal key."""
        db_utils.save_longname(4134515116, "Anna")
        db_utils.save_shortname(4134515116, "AN")

        with _managed_connection(relay_db) as con:
            long_keys = {
                r[0] for r in con.execute("SELECT meshtastic_id FROM longnames")
            }
            short_keys = {
                r[0] for r in con.execute("SELECT meshtastic_id FROM shortnames")
            }

        assert long_keys == {"!f66fb1ac"}
        assert short_keys == {"!f66fb1ac"}


class TestNameTableIdKeys:
    """Canonical key first, legacy decimal key only when it differs."""

    def test_hex_input_yields_one_key(self) -> None:
        assert db_utils._name_table_id_keys("^all") == ("^all",)

    def test_int_input_yields_both_forms(self) -> None:
        assert db_utils._name_table_id_keys(4134515116) == ("!f66fb1ac", "4134515116")

    def test_hex_input_derives_the_legacy_decimal_key(self) -> None:
        """A hex id still probes the pre-normalization decimal key.

        Callers normally pass the already-normalized sender, so the fallback
        must be derived from the node number rather than from the input.
        """
        assert db_utils._name_table_id_keys("!f66fb1ac") == ("!f66fb1ac", "4134515116")

    @pytest.mark.parametrize("invalid", ["!zzzz", "!1", "!000000001", "!0000_001"])
    def test_malformed_hex_passes_through_without_a_decimal_key(
        self, invalid: str
    ) -> None:
        """Only eight-digit node ids can alias historical decimal rows."""
        assert db_utils._name_table_id_keys(invalid) == (invalid,)

    def test_invalid_hex_does_not_resolve_an_unrelated_decimal_row(
        self, relay_db: Path
    ) -> None:
        _insert_row(relay_db, "1", "Unrelated Node", "UN")
        assert db_utils.get_longname("!1") is None
        assert db_utils.get_shortname("!1") is None


class TestInterfaceNodesKeyCompatibility:
    """interface.nodes is keyed by node-id string, matching the normalized sender."""

    def test_normalized_int_matches_string_keyed_nodes(self) -> None:
        nodes: dict[str, dict[str, object]] = {
            "!f66fb1ac": {"user": {"id": "!f66fb1ac", "longName": "Anna"}}
        }
        sender = normalize_node_id(4134515116)

        assert sender in nodes
        assert nodes[sender]["user"]["longName"] == "Anna"  # type: ignore[index]

    def test_already_normalized_sender_is_unchanged(self) -> None:
        nodes: dict[str, dict[str, object]] = {
            "!f66fb1ac": {"user": {"id": "!f66fb1ac", "longName": "Anna"}}
        }
        sender = normalize_node_id("!f66fb1ac")

        assert sender in nodes

    def test_idempotent(self) -> None:
        once = normalize_node_id(4134515116)

        assert normalize_node_id(once) == once


class TestIntegerNodeIdSnapshotUpdates:
    """The independent name updates must use the same canonical snapshot key."""

    @pytest.mark.parametrize(
        ("update", "lookup", "table", "field", "value"),
        [
            (
                db_utils.update_longnames,
                db_utils.get_longname,
                "longnames",
                "longName",
                "Integer Long",
            ),
            (
                db_utils.update_shortnames,
                db_utils.get_shortname,
                "shortnames",
                "shortName",
                "IL",
            ),
        ],
    )
    def test_integer_user_id_writes_canonical_key(
        self,
        relay_db: Path,
        update: Callable[..., bool],
        lookup: Callable[..., str | None],
        table: str,
        field: str,
        value: str,
    ) -> None:
        """An integer user.id in a complete snapshot cannot strand its name."""
        nodes = {"!f66fb1ac": {"user": {"id": 4134515116, field: value}}}
        assert update(nodes)
        assert lookup("!f66fb1ac") == value
        with _managed_connection(relay_db) as con:
            keys = {
                r[0]
                for r in con.execute(
                    f"SELECT meshtastic_id FROM {table}"  # nosec B608 - table is a parametrized test literal
                )
            }
        assert keys == {"!f66fb1ac"}

    @pytest.mark.parametrize(
        ("update", "lookup", "table", "field", "value"),
        [
            (
                db_utils.update_longnames,
                db_utils.get_longname,
                "longnames",
                "longName",
                "Preserved Long",
            ),
            (
                db_utils.update_shortnames,
                db_utils.get_shortname,
                "shortnames",
                "shortName",
                "PL",
            ),
        ],
    )
    def test_integer_user_id_does_not_prune_canonical_row(
        self,
        relay_db: Path,
        update: Callable[..., bool],
        lookup: Callable[..., str | None],
        table: str,
        field: str,
        value: str,
    ) -> None:
        """An integer-only refresh must not delete existing canonical names."""
        _insert_row(relay_db, "!f66fb1ac", "Preserved Long", "PL")
        nodes = {"node": {"user": {"id": 4134515116, field: value}}}
        assert update(nodes)
        assert lookup("!f66fb1ac") == value
        with _managed_connection(relay_db) as con:
            keys = {
                r[0]
                for r in con.execute(
                    f"SELECT meshtastic_id FROM {table}"  # nosec B608 - table is a parametrized test literal
                )
            }
        assert keys == {"!f66fb1ac"}
