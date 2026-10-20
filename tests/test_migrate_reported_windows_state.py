r"""End-to-end check against the exact post-migration state from `mmrelay paths`.

Reported environment::

    HOME   C:\Users\Laura\AppData\Local\mmrelay\mmrelay
    Legacy C:\Users\Laura\AppData\Local\Programs\MM Relay   (still detected)

The legacy root still holds config/credentials/logs, which is why it keeps being
reported as a legacy source, but no ``meshtastic.sqlite`` is found in it and
none exists at the destination. The runtime reads the database strictly from the
new home, so nothing carried a node-name cache over and the operator has no
signal from the migration report that this happened.
"""

from __future__ import annotations

from pathlib import Path

from mmrelay.constants.app import (
    CONFIG_FILENAME,
    CREDENTIALS_FILENAME,
    DATABASE_FILENAME,
)
from mmrelay.migrate import migrate_database


def test_reported_layout_states_no_cache_was_carried(tmp_path: Path) -> None:
    """Reproduce the reported layout and assert the step says so explicitly."""
    new_home = tmp_path / "mmrelay" / "mmrelay"
    legacy = tmp_path / "Programs" / "MM Relay"
    new_home.mkdir(parents=True)
    legacy.mkdir(parents=True)

    # Real user data in the legacy root -- why it is still reported as legacy.
    (legacy / CONFIG_FILENAME).write_text("matrix:\n  homeserver: https://m\n")
    (legacy / "matrix").mkdir()
    (legacy / "matrix" / CREDENTIALS_FILENAME).write_text('{"user_id":"@bot:x"}')
    (legacy / "logs").mkdir()
    (legacy / "logs" / "mmrelay.log").write_text("old log\n")

    # No database anywhere: not in legacy, not at the destination.
    assert list(legacy.rglob(DATABASE_FILENAME)) == []
    assert not (new_home / "database" / DATABASE_FILENAME).exists()

    result = migrate_database([legacy], new_home)

    # Still a success: nothing existed to lose, and failing here would break
    # every first-time Windows migration that has no prior database.
    assert result["success"] is True
    assert result["action"] == "not_found"
    # The report must state the consequence instead of leaving it implicit.
    assert "no node-name cache was carried over" in result["message"]


def test_dry_run_reports_the_same_gap(tmp_path: Path) -> None:
    """A dry run surfaces the same fact, since a preview reports rather than acts."""
    new_home = tmp_path / "home"
    legacy = tmp_path / "Programs" / "MM Relay"
    new_home.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (legacy / CONFIG_FILENAME).write_text("matrix: {}\n")

    result = migrate_database([legacy], new_home, dry_run=True)

    assert result["success"] is True
    assert result["action"] == "not_found"
    assert "no node-name cache was carried over" in result["message"]