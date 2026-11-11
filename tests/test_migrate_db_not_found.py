r"""Regression tests for reporting when no database was carried over.

``migrate_database`` returns ``action="not_found"`` when the legacy roots hold
no ``meshtastic.sqlite``. That is a no-op rather than a failure: a first-time
install never had a database, and a fresh home creates its own on first run.

The risk is silence. The runtime resolves the database from the new home via
``paths.get_database_path`` with no legacy fallback, so a completed step that
only says "no database files found" reads as though a cache moved when none did.
These tests pin that the result states no node-name cache was carried over, and
that a genuinely absent database is not materialized as an empty file which
would mask the situation on the next run.
"""

from __future__ import annotations

from pathlib import Path

from mmrelay.constants.app import (
    CONFIG_FILENAME,
    CREDENTIALS_FILENAME,
    DATABASE_FILENAME,
)
from mmrelay.migrate import migrate_database


class TestDatabaseStepReportsNoCacheCarried:
    """A missing legacy database is a no-op that must say what it means."""

    def test_result_states_no_node_name_cache_was_carried_over(
        self, tmp_path: Path
    ) -> None:
        """The reported layout: real legacy data, no database anywhere."""
        legacy = tmp_path / "Programs" / "MM Relay"
        legacy.mkdir(parents=True)
        (legacy / CONFIG_FILENAME).write_text("matrix:\n  homeserver: x\n")
        (legacy / CREDENTIALS_FILENAME).write_text("{}")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy], new_home)

        assert result["action"] == "not_found"
        # Still a success: there was no prior database to lose, so failing here
        # would break every first-time Windows migration that has no prior DB.
        assert result["success"] is True
        # But it must not imply a cache was carried over.
        assert "no node-name cache was carried over" in result["message"]

    def test_message_scopes_the_claim_to_scanned_locations(self, tmp_path: Path) -> None:
        """The message must not overstate what was actually checked.

        `get_db_path` honors a `database.path` or `db.path` override ahead of
        the resolved home, and those overrides are not among the scanned legacy
        locations. Without this qualifier the report would claim no cache was
        carried over even when the relay keeps using a configured database.
        """
        legacy = tmp_path / "Programs" / "MM Relay"
        legacy.mkdir(parents=True)
        (legacy / CONFIG_FILENAME).write_text("matrix: {}\n")
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy], new_home)

        assert "scanned legacy locations" in result["message"]
        assert "database.path" in result["message"]

    def test_absent_database_is_not_created_empty(self, tmp_path: Path) -> None:
        """The step must not materialize an empty database at the new home.

        An empty file would make the next run report ``already_migrated`` and
        hide the fact that nothing was ever carried over.
        """
        legacy = tmp_path / "Programs" / "MM Relay"
        legacy.mkdir(parents=True)
        (legacy / CONFIG_FILENAME).write_text("matrix: {}\n")
        new_home = tmp_path / "home"
        new_home.mkdir()

        migrate_database([legacy], new_home)

        assert not (new_home / "database" / DATABASE_FILENAME).exists()

    def test_empty_legacy_root_is_a_noop_success(self, tmp_path: Path) -> None:
        """Nothing to migrate is not a failure."""
        legacy = tmp_path / "Programs" / "MM Relay"
        legacy.mkdir(parents=True)
        new_home = tmp_path / "home"
        new_home.mkdir()

        result = migrate_database([legacy], new_home)

        assert result["success"] is True
        assert result["action"] == "not_found"

    def test_already_migrated_still_succeeds(self, tmp_path: Path) -> None:
        """The genuine no-op case keeps its distinct action."""
        legacy = tmp_path / "Programs" / "MM Relay"
        legacy.mkdir(parents=True)
        new_home = tmp_path / "home"
        (new_home / "database").mkdir(parents=True)
        (new_home / "database" / DATABASE_FILENAME).write_bytes(b"sqlite")

        result = migrate_database([legacy], new_home)

        assert result["success"] is True
        assert result["action"] == "already_migrated"