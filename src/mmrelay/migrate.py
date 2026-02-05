"""
Migration utilities for MMRelay v1.2.x → v1.3.

Handles automatic migration from legacy and partial new layouts to unified
MMRELAY_HOME directory structure.

Migration rules:
- Atomic operations with rollback on failure
- Preserve old files as backups (with timestamp suffixes)
- Support dry-run mode for testing
- Track migration state to avoid re-running
- Clear deprecation warnings for legacy environment variables

Migration paths (v1.2.x → v1.3):

Legacy Layout (v1.2.9 and earlier):
  ~/.mmrelay/credentials.json  →  $MMRELAY_HOME/credentials.json
  ~/.mmrelay/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite
  ~/.mmrelay/meshtastic.sqlite-wal →  $MMRELAY_HOME/database/meshtastic.sqlite-wal
  ~/.mmrelay/meshtastic.sqlite-shm →  $MMRELAY_HOME/database/meshtastic.sqlite-shm
  ~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
  ~/.mmrelay/store/              →  $MMRELAY_HOME/store/
  ~/.mmrelay/plugins/custom/    →  $MMRELAY_HOME/plugins/custom/
  ~/.mmrelay/plugins/community/ →  $MMRELAY_HOME/plugins/community/

Partial New Layout (v1.2.10-1.2.11):
  ~/.mmrelay/config.yaml        →  $MMRELAY_HOME/config.yaml (or keep)
  ~/.mmrelay/credentials.json    →  $MMRELAY_HOME/credentials.json
  ~/.mmrelay/meshtastic.sqlite    →  $MMRELAY_HOME/database/meshtastic.sqlite
  ~/.mmrelay/data/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite (merge)
  ~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
  ~/.mmrelay/store/              →  $MMRELAY_HOME/store/
  ~/.mmrelay/plugins/custom/    →  $MMRELAY_HOME/plugins/custom/
  ~/.mmrelay/plugins/community/ →  $MMRELAY_HOME/plugins/community/

Plugin Data Migration (Three-Tier System):

  Plugin data tiers in v1.3:
    - Tier 1 (Code): $MMRELAY_HOME/plugins/custom/{name}/ or $MMRELAY_HOME/plugins/community/{name}/
    - Tier 2 (Filesystem): $MMRELAY_HOME/plugins/custom/{name}/data/ or $MMRELAY_HOME/plugins/community/{name}/data/
    - Tier 3 (Database): SQLite via store_plugin_data()

  Migration for gpxtracker (community plugin):
    Old: gpx_directory: "~/my_gpx_files"
    New: $MMRELAY_HOME/plugins/community/gpxtracker/data/
"""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mmrelay.log_utils import get_logger
from mmrelay.paths import get_home_dir, resolve_all_paths

# Migration state file
MIGRATION_STATE_FILE = "migration_completed.flag"
MIGRATION_VERSION = "1.3"
MIGRATION_STEPS_ORDER = [
    "credentials",
    "config",
    "database",
    "logs",
    "store",
    "plugins",
    "gpxtracker",
]


logger = get_logger("Migration")


class MigrationError(Exception):
    """Migration-specific error."""

    @classmethod
    def integrity_check_failed(cls, detail: str) -> "MigrationError":
        """Create error for database integrity check failure."""
        return cls(f"Database integrity check failed: {detail}")

    @classmethod
    def verification_failed(cls, detail: str) -> "MigrationError":
        """Create error for database verification failure."""
        return cls(f"Database verification failed: {detail}")

    @classmethod
    def step_failed(cls, step: str, detail: str) -> "MigrationError":
        """Create error for migration step failure."""
        return cls(f"{step} migration failed: {detail}")


def _path_is_within_home(path: Path, home: Path) -> bool:
    """
    Check whether a path is the same as or located inside the given home directory.

    Parameters:
        path (Path): Path to test. The function resolves the path (uses Path.resolve(), falling back to Path.absolute() on OSError).
        home (Path): Home directory to check against. The function resolves this path similarly.

    Returns:
        bool: `True` if `path` equals `home` or is contained within `home`, `False` otherwise.
    """
    try:
        resolved_path = path.resolve()
    except OSError:
        resolved_path = path.absolute()

    try:
        resolved_home = home.resolve()
    except OSError:
        resolved_home = home.absolute()

    return resolved_path == resolved_home or resolved_home in resolved_path.parents


def _dir_has_entries(path: Path) -> bool:
    """
    Check whether the given path is an existing directory that contains at least one entry.

    Returns:
        True if the path exists, is a directory, and contains at least one entry; False otherwise.
    """
    if not path.exists() or not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return False


def _find_legacy_data(legacy_root: Path) -> list[dict[str, str]]:
    """
    Locate known legacy MMRelay artifacts under a legacy root directory.

    Scans the provided legacy_root for commonly migrated items and reports each finding as a mapping with keys `"type"` and `"path"`. Reported types include: `credentials`, `config`, `database`, `logs`, `e2ee_store`, and `plugins`. Duplicate paths are suppressed.

    Parameters:
        legacy_root (Path): Root directory to scan for legacy artifacts.

    Returns:
        list[dict[str, str]]: A list of findings where each entry is a dict with:
            - `type`: artifact type (see above)
            - `path`: string path to the discovered artifact
    """
    findings: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    def add_finding(item_type: str, path: Path) -> None:
        """
        Record a discovery of a legacy artifact by type and path unless the path was already recorded.

        Parameters:
            item_type (str): Category label for the finding (e.g., "credentials", "logs").
            path (Path): Filesystem path to the discovered artifact.

        Notes:
            Appends a dict {"type": item_type, "path": str(path)} to the module-level `findings` list and adds the path string to the module-level `seen_paths` set; no action is taken if the path has been seen before.
        """
        path_str = str(path)
        if path_str in seen_paths:
            return
        findings.append({"type": item_type, "path": path_str})
        seen_paths.add(path_str)

    credentials = legacy_root / "credentials.json"
    if credentials.exists():
        add_finding("credentials", credentials)

    config_path = legacy_root / "config.yaml"
    if config_path.exists():
        add_finding("config", config_path)

    db_candidates = [
        legacy_root / "meshtastic.sqlite",
        legacy_root / "data" / "meshtastic.sqlite",
        legacy_root / "database" / "meshtastic.sqlite",
    ]
    db_sidecar_suffixes = [".sqlite-wal", ".sqlite-shm"]
    for candidate in db_candidates:
        if candidate.exists():
            add_finding("database", candidate)
        for suffix in db_sidecar_suffixes:
            sidecar = candidate.with_suffix(suffix)
            if sidecar.exists():
                add_finding("database", sidecar)

    logs_dir = legacy_root / "logs"
    if _dir_has_entries(logs_dir):
        add_finding("logs", logs_dir)

    store_dir = legacy_root / "store"
    if _dir_has_entries(store_dir):
        add_finding("e2ee_store", store_dir)

    plugins_dir = legacy_root / "plugins"
    if _dir_has_entries(plugins_dir):
        add_finding("plugins", plugins_dir)

    return findings


def verify_migration() -> dict[str, Any]:
    """
    Verify MMRelay runtime and legacy data locations and compile a migration readiness report.

    Inspects resolved runtime paths (credentials, database, logs, plugins, E2EE store), checks whether each artifact exists and is located inside MMRELAY_HOME, scans configured legacy sources for legacy data, and collects warnings and errors that indicate whether a migration is needed or safe to perform.

    Returns:
        dict[str, Any]: Verification report containing:
            - home: str path to MMRELAY_HOME
            - artifacts: list of artifact status dicts with keys `key`, `label`, `path` (or None), `exists`, `inside_home`, `not_applicable`
            - legacy_data: list of legacy source findings, each with `root` and discovered `items` (each item has `type` and `path`)
            - credentials_missing: bool indicating credentials.json is absent from MMRELAY_HOME
            - legacy_data_found: bool indicating any legacy artifacts were discovered outside HOME
            - split_roots: bool indicating presence of data both in HOME and legacy locations
            - warnings: list[str] of non-fatal observations
            - errors: list[str] of detected issues preventing a clean migration
            - ok: bool, True if no errors were detected
    """
    paths_info = resolve_all_paths()
    home = Path(paths_info["home"])

    credentials_path = Path(paths_info["credentials_path"])
    database_dir = Path(paths_info["database_dir"])
    database_path = database_dir / "meshtastic.sqlite"
    logs_dir = Path(paths_info["logs_dir"])
    plugins_dir = Path(paths_info["plugins_dir"])

    store_value = paths_info["store_dir"]
    store_dir: Path | None
    if isinstance(store_value, str) and store_value.startswith("N/A"):
        store_dir = None
    else:
        store_dir = Path(store_value)

    artifacts: list[dict[str, Any]] = [
        {
            "key": "credentials",
            "label": "credentials.json",
            "path": credentials_path,
            "required": True,
        },
        {
            "key": "database_dir",
            "label": "database dir",
            "path": database_dir,
            "required": False,
        },
        {
            "key": "database",
            "label": "database",
            "path": database_path,
            "required": False,
        },
        {
            "key": "e2ee_store",
            "label": "e2ee store",
            "path": store_dir,
            "required": False,
        },
        {
            "key": "logs",
            "label": "logs dir",
            "path": logs_dir,
            "required": False,
        },
        {
            "key": "plugins",
            "label": "plugins dir",
            "path": plugins_dir,
            "required": False,
        },
    ]

    artifact_statuses: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_path = artifact["path"]
        if artifact_path is None:
            artifact_statuses.append(
                {
                    "key": artifact["key"],
                    "label": artifact["label"],
                    "path": None,
                    "exists": False,
                    "inside_home": True,
                    "not_applicable": True,
                }
            )
            continue

        exists = artifact_path.exists()
        inside_home = _path_is_within_home(artifact_path, home)
        artifact_statuses.append(
            {
                "key": artifact["key"],
                "label": artifact["label"],
                "path": str(artifact_path),
                "exists": exists,
                "inside_home": inside_home,
                "not_applicable": False,
            }
        )

    legacy_findings: list[dict[str, Any]] = []
    for legacy_root in paths_info.get("legacy_sources", []):
        legacy_path = Path(legacy_root)
        findings = _find_legacy_data(legacy_path)
        if findings:
            legacy_findings.append({"root": str(legacy_path), "items": findings})

    credentials_missing = not credentials_path.exists()
    legacy_data_found = len(legacy_findings) > 0
    home_has_data = any(item["exists"] for item in artifact_statuses)
    split_roots = legacy_data_found and home_has_data

    warnings: list[str] = []
    for root in legacy_findings:
        for item in root["items"]:
            item_type = item["type"]
            item_path = item["path"]
            if item_type == "e2ee_store":
                warnings.append(f"Your E2EE store is still in {item_path}")
            else:
                warnings.append(f"Found legacy data at {item_path}")

    errors: list[str] = []
    if credentials_missing:
        errors.append("Missing credentials.json in MMRELAY_HOME")
    if legacy_data_found:
        errors.append("Legacy data exists outside MMRELAY_HOME")
    if split_roots:
        errors.append("Split roots detected (data exists in HOME and legacy locations)")
    for artifact in artifact_statuses:
        if artifact["path"] and not artifact["inside_home"]:
            errors.append(
                f"{artifact['label']} is outside MMRELAY_HOME: {artifact['path']}"
            )

    return {
        "home": str(home),
        "artifacts": artifact_statuses,
        "legacy_data": legacy_findings,
        "credentials_missing": credentials_missing,
        "legacy_data_found": legacy_data_found,
        "split_roots": split_roots,
        "warnings": warnings,
        "errors": errors,
        "ok": len(errors) == 0,
    }


def print_migration_verification(report: dict[str, Any]) -> None:
    """
    Prints a human-readable summary of a migration verification report.

    Parameters:
        report (dict): Verification report with keys:
            - home (str | Path): Resolved MMRELAY_HOME path.
            - artifacts (list[dict]): Runtime artifacts; each dict contains:
                - label (str)
                - path (str | Path)
                - exists (bool)
                - inside_home (bool)
                - not_applicable (bool) — when the artifact is irrelevant on the platform.
            - legacy_data (list[dict]): Legacy scan results; each dict contains:
                - root (str | Path)
                - items (list[dict]) with keys `type` and `path`.
            - ok (bool): True if verification passed, False otherwise.
            - errors (list[str]): List of verification error messages (present when ok is False).

    No return value.
    """
    print("\n" + "=" * 60)
    print("MMRelay Migration Verification (mmrelay verify-migration)")
    print("=" * 60)

    print("\n📍 MMRELAY_HOME:")
    print(f"   {report['home']}")

    print("\n📁 Runtime Artifacts:")
    for artifact in report["artifacts"]:
        label = artifact["label"]
        if artifact["not_applicable"]:
            print(f"   {label}: N/A (Windows)")
            continue

        path = artifact["path"]
        exists = "present" if artifact["exists"] else "missing"
        inside = "yes" if artifact["inside_home"] else "no"
        print(f"   {label}: {path}")
        print(f"     - exists: {exists}")
        print(f"     - inside HOME: {inside}")

    print("\n🧭 Legacy Data Scan:")
    if not report["legacy_data"]:
        print("   ✅ No legacy data found")
    else:
        for legacy_root in report["legacy_data"]:
            print(f"   ⚠️  Legacy root: {legacy_root['root']}")
            for item in legacy_root["items"]:
                print(f"     - {item['type']}: {item['path']}")

    if report["ok"]:
        print("\n✅ Migration verification PASSED")
    else:
        print("\n❌ Migration verification FAILED")
        for error in report["errors"]:
            print(f"   - {error}")


def _get_migration_state_path() -> Path:
    """Get path to migration state file."""
    return get_home_dir() / MIGRATION_STATE_FILE


def _read_migration_state() -> dict[str, Any] | None:
    """
    Read the persisted migration state from MMRELAY_HOME and return it as a dictionary.

    The function looks for the migration state file and, if present and readable, returns a dict describing the saved state (for example: `{"version": "1.3", "status": "completed", "timestamp": "...", "completed_steps": [...], "error": "..."}`). Legacy state files that contain only a version string are treated as a completed state and normalized to `{"version": <string>, "status": "completed"}`.

    Returns:
        dict: Migration state dictionary when a valid state file is present and parseable.
        None: If no state file exists, the file cannot be read, or its content is not a recognized format.
    """
    state_path = _get_migration_state_path()
    if not state_path.exists():
        return None

    try:
        content = state_path.read_text(encoding="utf-8").strip()
    except (OSError, IOError):
        logger.warning("Could not read migration state file: %s", state_path)
        return None

    if content == MIGRATION_VERSION:
        return {"version": MIGRATION_VERSION, "status": "completed"}

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Legacy format stored only the version string.
        return {"version": content, "status": "completed"}

    if isinstance(parsed, dict):
        return parsed
    logger.warning("Migration state file has unexpected content: %s", state_path)
    return None


def _write_migration_state(
    *, status: str, completed_steps: list[str] | None = None, error: str | None = None
) -> None:
    """
    Record the current migration state to the migration state file inside MMRELAY_HOME.

    Writes a JSON payload containing "version", "status", and "timestamp"; includes "completed_steps" and "error" when provided. The file path is determined by _get_migration_state_path(). I/O errors are caught and logged; the function does not raise on write failure.

    Parameters:
        status (str): Current migration status (e.g., "in-progress", "completed", "failed").
        completed_steps (list[str] | None): Ordered list of migration step names that have completed, if any.
        error (str | None): Error message to record when the migration failed, if any.
    """
    state_path = _get_migration_state_path()
    payload: dict[str, Any] = {
        "version": MIGRATION_VERSION,
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }
    if completed_steps is not None:
        payload["completed_steps"] = completed_steps
    if error:
        payload["error"] = error

    try:
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        logger.debug("Updated migration state: %s", payload)
    except (OSError, IOError):
        logger.exception("Failed to write migration state")


def _is_migration_completed() -> bool:
    """
    Determine whether the recorded migration state indicates the current migration version completed.

    Returns:
        True if the persisted migration state has `version` equal to `MIGRATION_VERSION` and `status` equal to `"completed"`, False otherwise.
    """
    state = _read_migration_state()
    if not state:
        return False
    return (
        state.get("version") == MIGRATION_VERSION and state.get("status") == "completed"
    )


def _mark_migration_completed(completed_steps: list[str] | None = None) -> None:
    """
    Record that the migration finished and persist its completion state.

    Writes a migration state file indicating status "completed", the current migration
    version, a timestamp, and an optional list of completed step names. Also logs
    the path to the written state file.

    Parameters:
        completed_steps (list[str] | None): Optional ordered list of migration step
            identifiers that were completed; stored in the persisted state.
    """
    _write_migration_state(status="completed", completed_steps=completed_steps)
    logger.info("Migration completed and marked in: %s", _get_migration_state_path())


def _backup_file(src_path: Path, suffix: str = ".bak") -> Path:
    """
    Create a timestamped backup filename for the given file by appending a suffix and timestamp.

    Parameters:
        src_path (Path): Original file path to back up (the backup is placed alongside this path).
        suffix (str): Suffix inserted after the original filename and before the timestamp (default: ".bak").

    Returns:
        Path: New backup file path with format "<original_name><suffix>.<YYYYMMDD_HHMMSS>" placed in the same directory as `src_path`.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{src_path.name}{suffix}.{timestamp}"
    return src_path.with_name(backup_name)


def _get_most_recent_database(candidates: list[Path]) -> Path | None:
    """
    Select the main SQLite database file whose file-group (main file plus WAL/SHM sidecars) was most recently modified.

    Given a list of file paths that may include SQLite main files and their `-wal`/`-shm` sidecars, this function groups files by their main database file, ignores sidecars that do not have an accompanying main file, and returns the Path of the main database whose group has the newest modification time.

    Parameters:
        candidates (list[Path]): Candidate paths that may include main database files and WAL/SHM sidecars.

    Returns:
        Path | None: The main database Path for the most recently modified group, or `None` if no valid database groups are found.
    """

    def get_mtime(path: Path) -> float:
        """
        Get a path's last modification time.

        Parameters:
            path (Path): Filesystem path to query.

        Returns:
            float: Modification time as seconds since the epoch, or `0.0` if the path cannot be stat'd.
        """
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def get_base_path(path: Path) -> Path:
        """
        Normalize a database-related Path to its main database file.

        If `path` points to a SQLite WAL or SHM sidecar (file name ending in `-wal` or `-shm`), return a Path with that suffix removed; otherwise return `path` unmodified.

        Parameters:
            path (Path): A filesystem path that may reference a main database file or a WAL/SHM sidecar.

        Returns:
            Path: The main database file Path for a sidecar input, or the original Path if no sidecar suffix is present.
        """
        if path.name.endswith("-wal") or path.name.endswith("-shm"):
            return path.with_name(path.name[:-4])
        return path

    # Group databases by main file and its sidecars
    db_groups: dict[Path, list[Path]] = {}
    for db_path in candidates:
        try:
            if not db_path.exists():
                continue
        except OSError:
            continue

        # Extract base name (remove -wal, -shm suffix)
        base = get_base_path(db_path)

        # Skip orphaned WAL/SHM sidecars (no main database file exists)
        try:
            if not base.exists():
                continue
        except OSError:
            continue

        if base not in db_groups:
            db_groups[base] = []

        db_groups[base].append(db_path)

    # Find group with most recent modification time
    if not db_groups:
        return None

    most_recent_group = max(
        db_groups.items(),
        key=lambda item: max(get_mtime(p) for p in item[1]),
    )
    if not most_recent_group:
        return None

    # Return main file from most recent group
    return most_recent_group[0]


def migrate_credentials(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate the first discovered legacy credentials.json into the new home directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan, searched in order, for credentials.json.
        new_home (Path): Destination home directory where credentials.json will be placed.
        dry_run (bool): If True, report intended action without modifying files.
        force (bool): If True, overwrite existing destination without creating a backup.
        move (bool): If True, move the file instead of copying it.

    Returns:
        dict: Migration result containing at minimum a `success` boolean and may include
        `old_path`, `new_path`, `action` ("move" or "copy"), `dry_run`, and an `error`
        message on failure.
    """
    old_creds: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "credentials.json"
        if candidate.exists():
            old_creds = candidate
            logger.info("Found credentials.json in legacy root: %s", old_creds)
            break

    if not old_creds or not old_creds.exists():
        return {
            "success": True,
            "message": "No credentials file found in legacy locations",
        }

    new_creds = new_home / "credentials.json"

    if dry_run:
        logger.info(
            "[DRY RUN] Would move credentials from %s to %s", old_creds, new_creds
        )
        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "move" if move else "copy",
            "dry_run": True,
        }

    if new_creds.exists() and not force:
        logger.info("Backing up existing credentials: %s", new_creds)
        backup_path = _backup_file(new_creds)
        try:
            shutil.copy2(str(new_creds), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup credentials: %s", e)

    try:
        if move:
            if new_creds.exists():
                if new_creds.is_dir():
                    shutil.rmtree(str(new_creds))
                else:
                    new_creds.unlink()
                logger.info("Removed existing destination: %s", new_creds)
            logger.info("Moving credentials from %s to %s", old_creds, new_creds)
            shutil.move(str(old_creds), str(new_creds))
        else:
            logger.info("Copying credentials from %s to %s", old_creds, new_creds)
            shutil.copy2(str(old_creds), str(new_creds))
        logger.info("Migrated credentials from %s to %s", old_creds, new_creds)
        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "move" if move else "copy",
        }
    except (OSError, IOError) as exc:
        logger.exception("Failed to migrate credentials")
        return {
            "success": False,
            "error": str(exc),
            "old_path": str(old_creds),
        }


def migrate_config(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Locate and migrate the first legacy `config.yaml` into the new home directory.

    Scans `legacy_roots` for the first existing `config.yaml` and copies or moves it to `new_home/config.yaml`, creating `new_home` if necessary.

    Parameters:
        legacy_roots (list[Path]): Directories to search for a legacy `config.yaml`.
        new_home (Path): Destination home directory where `config.yaml` should be placed.
        dry_run (bool): If True, report the intended action without modifying the filesystem.
        force (bool): If True, overwrite an existing destination without creating a backup.
        move (bool): If True, move the file from the legacy location instead of copying.

    Returns:
        dict: Result summary containing at least:
            - `success` (bool): Whether the migration step succeeded.
            - `old_path` (str, optional): Path to the discovered legacy config.
            - `new_path` (str, optional): Destination path in `new_home`.
            - `action` (str, optional): `"move"` or `"copy"`.
            - `dry_run` (bool, optional): Present when the call was a dry run.
            - `message` or `error` (str, optional): Informational message or error details.
    """
    old_config: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "config.yaml"
        if candidate.exists():
            old_config = candidate
            logger.info("Found config.yaml in legacy root: %s", old_config)
            break

    if not old_config or not old_config.exists():
        return {
            "success": True,
            "message": "No config.yaml found in legacy locations",
        }

    new_config = new_home / "config.yaml"

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate config from %s to %s", old_config, new_config
        )
        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "move" if move else "copy",
            "dry_run": True,
        }

    new_home.mkdir(parents=True, exist_ok=True)

    if new_config.exists() and not force:
        logger.info("Backing up existing config.yaml: %s", new_config)
        backup_path = _backup_file(new_config)
        try:
            shutil.copy2(str(new_config), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup config.yaml: %s", e)

    try:
        if move:
            if new_config.exists():
                if new_config.is_dir():
                    shutil.rmtree(str(new_config))
                else:
                    new_config.unlink()
                logger.info("Removed existing destination: %s", new_config)
            logger.info("Moving config from %s to %s", old_config, new_config)
            shutil.move(str(old_config), str(new_config))
        else:
            logger.info("Copying config from %s to %s", old_config, new_config)
            shutil.copy2(str(old_config), str(new_config))
        logger.info("Migrated config from %s to %s", old_config, new_config)
        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "move" if move else "copy",
        }
    except (OSError, IOError) as exc:
        logger.exception("Failed to migrate config.yaml")
        return {
            "success": False,
            "error": str(exc),
            "old_path": str(old_config),
        }


def migrate_database(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate the Meshtastic SQLite database (and its WAL/SHM sidecars) from legacy locations into the new home's database directory.

    Scans the provided legacy roots, picks the most recently modified valid database group (main file plus any sidecars), and copies those files into new_home/database. If a destination file exists it is backed up unless `force` is True. After copying, performs a SQLite integrity check on the main database file. If `move=True` and the integrity check passes, source files are deleted. Uses copy-then-delete pattern to prevent data loss.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy database files.
        new_home (Path): Destination MMRELAY home directory where a `database` subdirectory will be created.
        dry_run (bool): If True, report planned actions without modifying the filesystem.
        force (bool): If True, overwrite existing destination files without creating backups.
        move (bool): If True, move files instead of copying them. Source files are only deleted after successful integrity check.

    Returns:
        dict: Result summary including at minimum `success` (bool). On success includes `old_path` (source main DB path), `new_path` (destination database directory), and `action` (`"move"` or `"copy"`). May include `dry_run`, `message`, or `error` keys for additional context.
    """
    new_db_dir = new_home / "database"

    if dry_run:
        logger.info("[DRY RUN] Would migrate database to %s", new_db_dir)
        return {
            "success": True,
            "new_path": str(new_db_dir),
            "dry_run": True,
        }

    new_db_dir.mkdir(parents=True, exist_ok=True)

    candidates = []

    for legacy_root in legacy_roots:
        legacy_db = legacy_root / "meshtastic.sqlite"
        if legacy_db.exists():
            candidates.append(legacy_db)
            for suffix in ["-wal", "-shm"]:
                sidecar = legacy_db.with_suffix(f".sqlite{suffix}")
                if sidecar.exists():
                    candidates.append(sidecar)

        partial_data_dir = legacy_root / "data"
        if partial_data_dir.exists():
            partial_db = partial_data_dir / "meshtastic.sqlite"
            if partial_db.exists():
                candidates.append(partial_db)
                for suffix in ["-wal", "-shm"]:
                    sidecar = partial_db.with_suffix(f".sqlite{suffix}")
                    if sidecar.exists():
                        candidates.append(sidecar)

        legacy_db_dir = legacy_root / "database"
        if legacy_db_dir.exists():
            legacy_db = legacy_db_dir / "meshtastic.sqlite"
            if legacy_db.exists():
                candidates.append(legacy_db)
                for suffix in ["-wal", "-shm"]:
                    sidecar = legacy_db.with_suffix(f".sqlite{suffix}")
                    if sidecar.exists():
                        candidates.append(sidecar)

    if not candidates:
        return {
            "success": True,
            "message": "No database files found in legacy location",
        }

    def get_base_path(path: Path) -> Path:
        """
        Normalize a database-related Path to its main database file.

        If `path` points to a SQLite WAL or SHM sidecar (file name ending in `-wal` or `-shm`), return a Path with that suffix removed; otherwise return `path` unmodified.

        Parameters:
            path (Path): A filesystem path that may reference a main database file or a WAL/SHM sidecar.

        Returns:
            Path: The main database file Path for a sidecar input, or the original Path if no sidecar suffix is present.
        """
        if path.name.endswith("-wal") or path.name.endswith("-shm"):
            return path.with_name(path.name[:-4])
        return path

    most_recent = _get_most_recent_database(candidates)
    if not most_recent:
        return {
            "success": False,
            "message": "No valid database files found in legacy location",
        }

    selected_group = [
        candidate for candidate in candidates if get_base_path(candidate) == most_recent
    ]

    if not selected_group:
        return {
            "success": False,
            "message": "Most recent database group not found in legacy location",
        }

    logger.info("Migrating database from %s to %s", most_recent, new_db_dir)

    # Copy-then-delete pattern: Always copy first, verify, then delete sources only if verification succeeds.
    # This prevents data loss if integrity check fails after files are moved.
    for db_path in selected_group:
        dest = new_db_dir / db_path.name
        if dest.exists() and not force:
            logger.info("Backing up existing database: %s", dest)
            backup_path = _backup_file(dest)
            try:
                shutil.copy2(str(dest), str(backup_path))
            except (OSError, IOError) as e:
                logger.warning("Failed to backup database: %s", e)

        try:
            logger.info("Copying database file: %s", db_path)
            shutil.copy2(str(db_path), str(dest))
        except (OSError, IOError):
            logger.exception("Failed to copy database file %s", db_path)
            return {
                "success": False,
                "error": "Database file migration failed",
            }

    logger.info("Database files copied successfully")

    # Verify database integrity if main database file was copied/moved
    if not dry_run and not most_recent.name.endswith(("-wal", "-shm")):
        main_db = new_db_dir / most_recent.name
        try:
            with sqlite3.connect(str(main_db)) as conn:
                result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] != "ok":
                logger.error("Database integrity check failed: %s", result[0])
                logger.info("Cleaning up failed migration attempt")
                for db_path in selected_group:
                    dest = new_db_dir / db_path.name
                    if dest.exists():
                        try:
                            dest.unlink()
                            logger.debug("Deleted copied file: %s", dest)
                        except (OSError, IOError):
                            logger.warning("Failed to delete copied file: %s", dest)
                raise MigrationError.integrity_check_failed(result[0])
            logger.info("Database integrity check passed")
        except sqlite3.DatabaseError as e:
            logger.exception("Database verification failed")
            logger.info("Cleaning up failed migration attempt")
            for db_path in selected_group:
                dest = new_db_dir / db_path.name
                if dest.exists():
                    try:
                        dest.unlink()
                        logger.debug("Deleted copied file: %s", dest)
                    except (OSError, IOError):
                        logger.warning("Failed to delete copied file: %s", dest)
            raise MigrationError.verification_failed(str(e)) from e

    # If verification passed and move=True, delete source files
    if move:
        for db_path in selected_group:
            try:
                db_path.unlink()
                logger.info("Deleted source file after successful move: %s", db_path)
            except (OSError, IOError):
                logger.warning("Failed to delete source file: %s", db_path)

    return {
        "success": True,
        "old_path": str(most_recent),
        "new_path": str(new_db_dir),
        "action": "move" if move else "copy",
    }


def migrate_logs(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate log files from the first discovered legacy "logs" directory into the new home's "logs" directory.

    Searches legacy_roots for a "logs" directory and copies (or moves) each *.log file into new_home/logs, renaming migrated files with a timestamp suffix. Creates backups of existing destination directories or files unless `force` is True. In dry-run mode, reports the intended action without modifying the filesystem.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for a legacy "logs" directory.
        new_home (Path): Destination MMRELAY_HOME where logs should be placed.
        dry_run (bool): If True, only report intended actions.
        force (bool): If True, overwrite existing files/directories without creating backups.
        move (bool): If True, move files instead of copying them.

    Returns:
        dict: Result summary containing keys such as:
            - "success" (bool): Whether the operation completed without fatal errors.
            - "migrated_count" (int): Number of log files migrated (present when logs found).
            - "old_path" (str): Path to the discovered legacy logs directory (when found).
            - "new_path" (str): Path to the destination logs directory.
            - "action" (str): "move" or "copy".
            - "dry_run" (bool): Present and True when called in dry-run mode.
            - "message" (str): Informational message when no logs directory was found.
    """
    old_logs_dir: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "logs"
        if candidate.exists():
            old_logs_dir = candidate
            logger.info("Found logs directory in legacy root: %s", old_logs_dir)
            break

    if not old_logs_dir or not old_logs_dir.exists():
        return {
            "success": True,
            "message": "No logs directory found in legacy locations",
        }

    new_logs_dir = new_home / "logs"

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate logs from %s to %s", old_logs_dir, new_logs_dir
        )
        return {
            "success": True,
            "old_path": str(old_logs_dir),
            "new_path": str(new_logs_dir),
            "action": "move" if move else "copy",
            "dry_run": True,
        }

    if new_logs_dir.exists() and not force:
        logger.info("Backing up existing logs directory: %s", new_logs_dir)
        backup_path = _backup_file(new_logs_dir)
        try:
            shutil.copytree(str(new_logs_dir), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup logs directory: %s", e)

    new_logs_dir.mkdir(parents=True, exist_ok=True)

    migrated_count = 0

    for log_file in old_logs_dir.glob("*.log"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        new_name = f"{log_file.stem}_migrated_{timestamp}.log"
        dest = new_logs_dir / new_name
        if dest.exists() and not force:
            logger.info("Backing up existing log file: %s", dest)
            backup_path = _backup_file(dest)
            try:
                shutil.copy2(str(dest), str(backup_path))
            except (OSError, IOError) as e:
                logger.warning("Failed to backup log file: %s", e)
        try:
            if move:
                shutil.move(str(log_file), str(dest))
            else:
                shutil.copy2(str(log_file), str(dest))
            logger.debug("Migrated log: %s", log_file)
            migrated_count += 1
        except (OSError, IOError) as e:
            logger.warning("Failed to migrate log %s: %s", log_file, e)

    return {
        "success": True,
        "migrated_count": migrated_count,
        "old_path": str(old_logs_dir),
        "new_path": str(new_logs_dir),
        "action": "move" if move else "copy",
    }


def migrate_store(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate the E2EE store directory from legacy roots into the new home's `store` directory.

    If the current platform is Windows, the function skips migration and returns success because E2EE is not supported. It searches legacy_roots for the first existing `store` directory and either copies or moves it to `new_home/store`. If a destination exists and `force` is False, a timestamped backup is created before overwriting. When `dry_run` is True, no filesystem changes are made and the function reports the intended action.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for a legacy `store` directory.
        new_home (Path): Target home directory where `store` will be placed.
        dry_run (bool): If True, only report intended actions without modifying files.
        force (bool): If True, overwrite existing destination without creating a backup.
        move (bool): If True, move the directory instead of copying it.

    Returns:
        dict: Result of the migration. Common keys:
            - `success` (bool): Whether the operation completed (or would complete for dry run).
            - `message` (str): Informational message (present for skips or no-op cases).
            - `old_path` (str): Source path of the migrated store (when applicable).
            - `new_path` (str): Destination path (when applicable).
            - `action` (str): `"move"` or `"copy"`.
            - `dry_run` (bool): Echoes the dry_run flag when applicable.
            - `error` (str): Error message on failure.
    """
    if sys.platform == "win32":
        return {
            "success": True,
            "message": "E2EE not supported on Windows, skipping store migration",
        }

    old_store_dir: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "store"
        if candidate.exists():
            old_store_dir = candidate
            logger.info("Found store directory in legacy root: %s", old_store_dir)
            break

    if not old_store_dir or not old_store_dir.exists():
        return {
            "success": True,
            "message": "No E2EE store directory found in legacy locations",
        }

    new_store_dir = new_home / "store"

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate store from %s to %s", old_store_dir, new_store_dir
        )
        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "move" if move else "copy",
            "dry_run": True,
        }

    new_home.mkdir(parents=True, exist_ok=True)

    if new_store_dir.exists() and not force:
        logger.info("Backing up existing store directory: %s", new_store_dir)
        backup_path = _backup_file(new_store_dir)
        try:
            shutil.copytree(str(new_store_dir), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup store directory: %s", e)

    try:
        if move:
            if new_store_dir.exists():
                shutil.rmtree(str(new_store_dir))
                logger.info(
                    "Removing existing store directory for move: %s", new_store_dir
                )
            shutil.move(str(old_store_dir), str(new_store_dir))
            logger.info("Moving store from %s to %s", old_store_dir, new_store_dir)
        else:
            if new_store_dir.exists():
                shutil.rmtree(str(new_store_dir))
                logger.info(
                    "Removing existing store directory for copy: %s", new_store_dir
                )
            shutil.copytree(str(old_store_dir), str(new_store_dir))
            logger.info("Copying store from %s to %s", old_store_dir, new_store_dir)
        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "move" if move else "copy",
        }
    except (OSError, IOError) as exc:
        logger.exception("Failed to migrate E2EE store")
        return {
            "success": False,
            "error": str(exc),
            "old_path": str(old_store_dir),
        }


def migrate_plugins(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate plugins from legacy plugin directories into the new home plugins layout.

    Parameters:
        legacy_roots (list[Path]): Legacy root directories to scan for a `plugins` directory.
        new_home (Path): Destination MMRELAY_HOME where `plugins` will be created or updated.
        dry_run (bool): If True, only report the intended actions without modifying the filesystem.
        force (bool): If True, overwrite existing destinations without creating backups.
        move (bool): If True, move plugin directories from legacy locations; otherwise copy them.

    Returns:
        dict: Migration result containing at least:
            - `success` (bool): Whether the operation completed (or would complete for dry runs).
            - `old_path` (str): Path to the discovered legacy plugins directory (if any).
            - `new_path` (str): Path to the destination plugins directory.
            - `action` (str): `"move"` or `"copy"`.
            - `migrated_types` (list[str], optional): Which plugin tiers were migrated (`"custom"`, `"community"`).
            - `dry_run` (bool, optional): Present and True when invoked in dry-run mode.
            - `message` / `error` (str, optional): Informational or error message when applicable.
    """
    old_plugins_dir: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "plugins"
        if candidate.exists():
            old_plugins_dir = candidate
            logger.info("Found plugins directory in legacy root: %s", old_plugins_dir)
            break

    if not old_plugins_dir or not old_plugins_dir.exists():
        return {
            "success": True,
            "message": "No plugins directory found in legacy locations",
        }

    new_plugins_dir = new_home / "plugins"

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate plugins from %s to %s",
            old_plugins_dir,
            new_plugins_dir,
        )
        return {
            "success": True,
            "old_path": str(old_plugins_dir),
            "new_path": str(new_plugins_dir),
            "action": "move" if move else "copy",
            "dry_run": True,
        }

    errors: list[str] = []

    if new_plugins_dir.exists() and not force:
        logger.info("Backing up existing plugins directory: %s", new_plugins_dir)
        backup_path = _backup_file(new_plugins_dir)
        try:
            shutil.copytree(str(new_plugins_dir), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup plugins directory: %s", e)
            errors.append(f"plugins backup: {e}")
    elif not new_plugins_dir.exists() and not force:
        backup_path = _backup_file(new_plugins_dir)
        try:
            backup_path.mkdir(parents=True, exist_ok=True)
            logger.info("Created empty plugins backup directory: %s", backup_path)
        except (OSError, IOError) as e:
            logger.warning("Failed to create plugins backup directory: %s", e)
            errors.append(f"plugins backup dir: {e}")

    try:
        new_plugins_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        logger.warning("Failed to create plugins directory: %s", e)
        errors.append(f"plugins dir: {e}")

    migrated_types: list[str] = []

    # Migrate custom plugins
    old_custom_dir = old_plugins_dir / "custom"
    if old_custom_dir.exists():
        new_custom_dir = new_plugins_dir / "custom"
        try:
            new_custom_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, IOError) as e:
            logger.warning("Failed to create custom plugins directory: %s", e)
            errors.append(f"custom dir: {e}")
        try:
            for item in old_custom_dir.iterdir():
                if not item.is_dir():
                    continue
                dest = new_custom_dir / item.name
                if dest.exists() and not force:
                    logger.info("Backing up existing custom plugin: %s", dest)
                    backup_path = _backup_file(dest)
                    try:
                        shutil.copytree(str(dest), str(backup_path))
                    except (OSError, IOError) as e:
                        logger.warning("Failed to backup custom plugin: %s", e)
                        errors.append(f"custom backup {dest}: {e}")
                try:
                    if move:
                        if dest.exists():
                            shutil.rmtree(str(dest))
                            logger.debug(
                                "Removing existing custom plugin for move: %s", dest
                            )
                        shutil.move(str(item), str(dest))
                    else:
                        if dest.exists():
                            shutil.rmtree(str(dest))
                        shutil.copytree(str(item), str(dest))
                    logger.debug("Migrated custom plugin: %s", item)
                except (OSError, IOError) as e:
                    logger.warning("Failed to migrate custom plugin %s: %s", item, e)
                    errors.append(f"custom {item}: {e}")
            migrated_types.append("custom")
        except (OSError, IOError) as e:
            logger.warning("Failed to migrate custom plugins: %s", e)
            errors.append(f"custom: {e}")

    # Migrate community plugins
    old_community_dir = old_plugins_dir / "community"
    if old_community_dir.exists():
        new_community_dir = new_plugins_dir / "community"
        try:
            new_community_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, IOError) as e:
            logger.warning("Failed to create community plugins directory: %s", e)
            errors.append(f"community dir: {e}")
        try:
            for item in old_community_dir.iterdir():
                if not item.is_dir():
                    continue
                dest = new_community_dir / item.name
                if dest.exists() and not force:
                    logger.info("Backing up existing community plugin: %s", dest)
                    backup_path = _backup_file(dest)
                    try:
                        shutil.copytree(str(dest), str(backup_path))
                    except (OSError, IOError) as e:
                        logger.warning("Failed to backup community plugin: %s", e)
                        errors.append(f"community backup {dest}: {e}")
                try:
                    if move:
                        if dest.exists():
                            shutil.rmtree(str(dest))
                            logger.debug(
                                "Removing existing community plugin for move: %s", dest
                            )
                        shutil.move(str(item), str(dest))
                    else:
                        if dest.exists():
                            shutil.rmtree(str(dest))
                        shutil.copytree(str(item), str(dest))
                    logger.debug("Migrated community plugin: %s", item)
                except (OSError, IOError) as e:
                    logger.warning("Failed to migrate community plugin %s: %s", item, e)
                    errors.append(f"community {item}: {e}")
            migrated_types.append("community")
        except (OSError, IOError) as e:
            logger.warning("Failed to migrate community plugins: %s", e)
            errors.append(f"community: {e}")

    failed = len(errors) > 0

    if move and not failed:
        for plugin_dir in (old_custom_dir, old_community_dir):
            if plugin_dir.exists():
                try:
                    if not any(plugin_dir.iterdir()):
                        plugin_dir.rmdir()
                except (OSError, IOError) as e:
                    logger.debug(
                        "Failed to remove empty plugin directory %s: %s",
                        plugin_dir,
                        e,
                    )
                    errors.append(f"cleanup {plugin_dir}: {e}")

        if old_plugins_dir.exists():
            try:
                if not any(old_plugins_dir.iterdir()):
                    old_plugins_dir.rmdir()
            except (OSError, IOError) as e:
                logger.debug(
                    "Failed to remove empty plugins directory %s: %s",
                    old_plugins_dir,
                    e,
                )
                errors.append(f"cleanup {old_plugins_dir}: {e}")

    success = len(errors) == 0

    result = {
        "success": success,
        "migrated_types": migrated_types,
        "old_path": str(old_plugins_dir),
        "new_path": str(new_plugins_dir),
        "action": "move" if move else "copy",
    }
    if errors:
        result["error"] = "; ".join(errors)
    return result


def migrate_gpxtracker(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """
    Migrate GPX files used by the community gpxtracker plugin into the new plugins/community/gpxtracker/data location.

    Scans legacy roots for a `gpx_directory` setting in legacy config.yaml files and copies any `*.gpx` files found into `new_home/plugins/community/gpxtracker/data`, creating per-file timestamped names. Creates backups of existing destination files unless `force` is True. Operates in dry-run mode if requested.

    Parameters:
        legacy_roots (list[Path]): Legacy directories to scan for a `config.yaml` containing `community-plugins.gpxtracker.gpx_directory`.
        new_home (Path): Destination MMRELAY_HOME root where plugin data should be placed.
        dry_run (bool): If True, report planned actions without making filesystem changes.
        force (bool): If True, overwrite existing destination files without creating backups.
        move (bool): If True, move files instead of copying them. This is ignored for
            gpxtracker migrations, which always copy for rollback safety.

    Returns:
        dict: Result summary. Typical keys:
            - `success` (bool): `True` on success, `False` on failure.
            - `migrated_count` (int): Number of GPX files migrated (when `success` is True).
            - `old_path` (str): Source GPX directory path that was scanned.
            - `new_path` (str): Destination data directory path.
            - `action` (str): `"move"` or `"copy"`.
            - `dry_run` (bool): Present when dry-run was requested.
            - `error` (str): Present on failure with an error message.
    """
    old_gpx_dir: Path | None = None

    roots_to_scan = list(legacy_roots)
    if new_home not in roots_to_scan:
        roots_to_scan.append(new_home)

    for legacy_root in roots_to_scan:
        legacy_config = legacy_root / "config.yaml"
        if legacy_config.exists():
            try:
                import yaml
            except ImportError as e:
                logger.warning("Failed to import yaml: %s", e)
                break

            try:
                with open(legacy_config, "r", encoding="utf-8") as f:
                    config_data = yaml.safe_load(f)
                    if isinstance(config_data, dict):
                        plugins_section = config_data.get("community-plugins", {})
                        if isinstance(plugins_section, dict):
                            gpx_section = plugins_section.get("gpxtracker", {})
                            if isinstance(gpx_section, dict):
                                gpx_path_str = gpx_section.get("gpx_directory")
                                if gpx_path_str:
                                    old_gpx_dir = Path(gpx_path_str).expanduser()
                                    if old_gpx_dir.exists():
                                        logger.info(
                                            "Found gpxtracker directory in legacy config: %s",
                                            old_gpx_dir,
                                        )
                                        break
            except (OSError, yaml.YAMLError) as e:
                logger.warning("Failed to read legacy config %s: %s", legacy_config, e)

    if not old_gpx_dir or not old_gpx_dir.exists():
        return {
            "success": True,
            "message": "gpxtracker plugin not configured with gpx_directory, skipping migration",
        }

    new_gpx_data_dir = new_home / "plugins" / "community" / "gpxtracker" / "data"
    if move:
        logger.info("gpxtracker migration uses copy-only to preserve rollback safety")

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate gpxtracker GPX files from %s to %s",
            old_gpx_dir if old_gpx_dir else "not configured",
            new_home / "plugins" / "community" / "gpxtracker" / "data",
        )
        return {
            "success": True,
            "old_path": str(old_gpx_dir) if old_gpx_dir else "not configured",
            "new_path": str(new_gpx_data_dir),
            "dry_run": True,
        }

    new_gpx_data_dir.mkdir(parents=True, exist_ok=True)

    migrated_count = 0
    errors: list[str] = []

    # Expand ~ if needed
    expanded_old_gpx_dir = Path(old_gpx_dir).expanduser()
    if not expanded_old_gpx_dir.exists():
        logger.info(
            "Old GPX directory not found at expanded path: %s", expanded_old_gpx_dir
        )

    try:
        if expanded_old_gpx_dir.resolve() == new_gpx_data_dir.resolve():
            logger.info(
                "gpxtracker source directory matches destination; skipping migration"
            )
            return {
                "success": True,
                "migrated_count": 0,
                "old_path": str(expanded_old_gpx_dir),
                "new_path": str(new_gpx_data_dir),
                "action": "copy",
                "message": "gpxtracker source equals destination, skipping",
            }
    except OSError:
        if expanded_old_gpx_dir.absolute() == new_gpx_data_dir.absolute():
            logger.info(
                "gpxtracker source directory matches destination; skipping migration"
            )
            return {
                "success": True,
                "migrated_count": 0,
                "old_path": str(expanded_old_gpx_dir),
                "new_path": str(new_gpx_data_dir),
                "action": "copy",
                "message": "gpxtracker source equals destination, skipping",
            }

    # Copy GPX files
    try:
        for gpx_file in expanded_old_gpx_dir.glob("*.gpx"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"{gpx_file.stem}_migrated_{timestamp}.gpx"
            dest_path = new_gpx_data_dir / new_name

            backup_failed = False
            if dest_path.exists() and not force:
                logger.info("Backing up existing GPX file: %s", dest_path)
                backup_path = _backup_file(dest_path)
                try:
                    shutil.copy2(str(dest_path), str(backup_path))
                except (OSError, IOError) as e:
                    logger.warning("Failed to backup GPX file: %s", e)
                    backup_failed = True

            if backup_failed:
                logger.info("Skipping GPX file due to backup failure: %s", dest_path)
                errors.append(f"backup failed for {dest_path}")
                continue

            try:
                logger.info("Copying GPX file: %s", gpx_file)
                shutil.copy2(str(gpx_file), str(dest_path))
                logger.debug("Migrated GPX file: %s", gpx_file)
                migrated_count += 1
            except (OSError, IOError) as e:
                logger.exception("Failed to migrate GPX file %s", gpx_file)
                errors.append(f"copy failed for {gpx_file}: {e}")
    except (OSError, IOError) as exc:
        logger.exception("Failed to migrate gpxtracker GPX files")
        return {
            "success": False,
            "error": str(exc),
            "old_path": str(expanded_old_gpx_dir),
        }

    if errors:
        return {
            "success": False,
            "error": "; ".join(errors),
            "migrated_count": migrated_count,
            "old_path": str(expanded_old_gpx_dir),
            "new_path": str(new_gpx_data_dir),
            "action": "copy",
        }

    return {
        "success": True,
        "migrated_count": migrated_count,
        "old_path": str(expanded_old_gpx_dir),
        "new_path": str(new_gpx_data_dir),
        "action": "copy",
    }


def is_migration_needed() -> bool:
    """
    Determine whether a migration from legacy layouts to the current home structure is required.

    Returns:
        True if migration has not been marked completed and legacy sources are present, False otherwise.
    """
    if _is_migration_completed():
        return False
    paths_info = resolve_all_paths()
    return len(paths_info["legacy_sources"]) > 0


def perform_migration(
    dry_run: bool = False, force: bool = False, move: bool = False
) -> dict[str, Any]:
    """
    Orchestrates the end-to-end migration of legacy MMRelay data into the v1.3 home layout.

    Runs each per-artifact migrator in the defined order, persists incremental migration state, and attempts rollback from backups on failure. Supports a dry-run mode that reports intended actions without mutating the filesystem.

    Parameters:
        dry_run (bool): If True, simulate the migration and report actions without making changes.
        force (bool): If True, allow overwriting existing destinations without creating backups.
        move (bool): If True, move files/directories instead of copying them.

    Returns:
        dict: Migration report containing at least the keys:
            - "dry_run": the dry_run input value
            - "timestamp": ISO timestamp of the run
            - "migrations": list of per-step results
            - "completed_steps": list of step names that completed
            - "success": `true` if migration (or dry run) completed successfully, `false` otherwise
            - "message": human-readable status message
            - "error": error message when failure occurred (optional)
            - "rollback": rollback summary if a rollback was performed (optional)
    """
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "timestamp": datetime.now().isoformat(),
        "migrations": [],
        "completed_steps": [],
    }
    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")
    else:
        logger.info("Starting migration to v1.3 directory structure")

    # Get authoritative path resolution using unified HOME + legacy model
    paths_info = resolve_all_paths()
    new_home = Path(paths_info["home"])
    legacy_roots = [Path(legacy_str) for legacy_str in paths_info["legacy_sources"]]

    if not legacy_roots:
        report["success"] = True
        report["message"] = "No legacy installation detected"
        report["migrations"] = []
        return report

    logger.info("Starting migration from legacy layout to v1.3 unified home directory")

    # Get new home directory
    try:
        if not dry_run:
            new_home.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        report["success"] = False
        report["error"] = str(e)
        report["message"] = f"Failed to create new home directory: {e}"
        return report

    completed_steps: list[str] = []

    def _record_step(step_name: str, result: dict[str, Any]) -> None:
        """
        Record a migration step result into the overall report.

        Appends an entry with the step type and its result to the enclosing `report["migrations"]` list and updates `report["completed_steps"]` to a snapshot of `completed_steps`. This function mutates the shared `report` object.

        Parameters:
            step_name (str): Identifier for the migration step (e.g., "credentials", "database").
            result (dict[str, Any]): Result details produced by the step, including keys like `success`, `action`, `old_path`, `new_path`, and optional `error`.
        """
        report["migrations"].append({"type": step_name, "result": result})
        report["completed_steps"] = list(completed_steps)

    def _run_step(
        step_name: str,
        func: Callable[..., dict[str, Any]],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Execute a migration step, record its result, update progress state, and raise on failure.

        Calls the provided step function with the given arguments, records the returned result via _record_step, appends the step to the in-memory completed_steps and report, and persists an "in_progress" migration state when not running a dry run.

        Parameters:
            step_name (str): Logical name of the migration step.
            func (callable): Function implementing the migration step; must return a dict-like result.
            *args: Positional arguments forwarded to `func`.
            **kwargs: Keyword arguments forwarded to `func`.

        Returns:
            dict[str, Any]: The result returned by `func`.

        Raises:
            MigrationError: If the step result indicates failure (result["success"] is False or falsy). The exception message includes the step name and an error detail extracted from `result["error"]` or `result["message"]`.
        """
        result = func(*args, **kwargs)
        _record_step(step_name, result)
        if not result.get("success", True):
            error_detail = (
                result.get("error") or result.get("message") or "Unknown error"
            )
            raise MigrationError.step_failed(step_name, error_detail)
        completed_steps.append(step_name)
        report["completed_steps"] = list(completed_steps)
        if not dry_run:
            _write_migration_state(
                status="in_progress", completed_steps=completed_steps
            )
        return result

    try:
        if not dry_run:
            _write_migration_state(
                status="in_progress", completed_steps=completed_steps
            )

        # Migrate credentials
        _run_step(
            "credentials",
            migrate_credentials,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate config
        _run_step(
            "config",
            migrate_config,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate database
        _run_step(
            "database",
            migrate_database,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate logs
        _run_step(
            "logs",
            migrate_logs,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate store (E2EE keys)
        _run_step(
            "store",
            migrate_store,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate plugins
        _run_step(
            "plugins",
            migrate_plugins,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
            move=move,
        )

        # Migrate gpxtracker (if configured)
        gpx_configured = False
        roots_to_scan = list(legacy_roots)
        if new_home not in roots_to_scan:
            roots_to_scan.append(new_home)
        for legacy_root in roots_to_scan:
            legacy_config = legacy_root / "config.yaml"
            if legacy_config.exists():
                try:
                    import yaml
                except ImportError as e:
                    logger.warning("Failed to import yaml: %s", e)
                    continue

                try:
                    with open(legacy_config, "r", encoding="utf-8") as f:
                        config_data = yaml.safe_load(f)
                        if isinstance(config_data, dict):
                            plugins_section = config_data.get("community-plugins", {})
                            if isinstance(plugins_section, dict):
                                gpx_section = plugins_section.get("gpxtracker", {})
                                if isinstance(gpx_section, dict) and gpx_section.get(
                                    "gpx_directory"
                                ):
                                    gpx_configured = True
                                    break
                except (OSError, yaml.YAMLError) as e:
                    logger.warning(
                        "Failed to read legacy config %s: %s", legacy_config, e
                    )

        if gpx_configured or any(
            (legacy_root / "plugins").exists() for legacy_root in legacy_roots
        ):
            _run_step(
                "gpxtracker",
                migrate_gpxtracker,
                legacy_roots,
                new_home,
                dry_run=dry_run,
                force=force,
                move=move,
            )

        # Mark migration as complete (skip for dry run)
        if not dry_run:
            _mark_migration_completed(completed_steps=completed_steps)
            report["message"] = "Migration completed successfully"
        else:
            report["message"] = "Dry run complete - no changes made"

        report["success"] = True
    except MigrationError as exc:
        report["success"] = False
        report["error"] = str(exc)
        report["message"] = "Migration failed"
        if not dry_run:
            _write_migration_state(
                status="failed",
                completed_steps=completed_steps,
                error=str(exc),
            )
            rollback_result = rollback_migration(completed_steps=completed_steps)
            report["rollback"] = rollback_result
        return report
    except (OSError, IOError, sqlite3.DatabaseError) as exc:
        report["success"] = False
        report["error"] = str(exc)
        report["message"] = "Migration failed"
        if not dry_run:
            _write_migration_state(
                status="failed",
                completed_steps=completed_steps,
                error=str(exc),
            )
            rollback_result = rollback_migration(completed_steps=completed_steps)
            report["rollback"] = rollback_result
        return report
    except Exception as exc:
        logger.exception("Unexpected error during migration")
        if not dry_run:
            _write_migration_state(
                status="failed",
                completed_steps=completed_steps,
                error=str(exc),
            )
            rollback_result = rollback_migration(completed_steps=completed_steps)
            report["rollback"] = rollback_result
        raise

    logger.info(
        "Migration complete. Summary: %d migrations performed",
        len(report["migrations"]),
    )

    return report


def rollback_migration(completed_steps: list[str] | None = None) -> dict[str, Any]:
    """
    Restore files and directories from backups created during a migration to undo a failed migration.

    If `completed_steps` is omitted, the function reads the migration state file to determine which steps completed and will be rolled back; if no state is available it rolls back all known steps. Backups are searched by glob patterns (e.g. `plugins.bak.*`, `config.yaml.bak.*`) and the most recent matching backup is restored for each step.

    Parameters:
        completed_steps (list[str] | None): Optional ordered list of migration step names that completed and should be rolled back.
            If None, the migration state file is used to infer completed steps or all configured steps are considered.

    Returns:
        dict[str, Any]: Summary of the rollback with these keys:
            - "success" (bool): `true` if all requested restorations and cleanup succeeded, `false` if any errors occurred.
            - "message" (str): Short human-readable summary of the rollback outcome.
            - "restored_count" (int): Number of files or directories successfully restored from backups.
            - "errors" (list[str]): List of error messages encountered during rollback (empty when "success" is `true`).
    """
    state_path = _get_migration_state_path()
    state = _read_migration_state()

    if completed_steps is None:
        if not state_path.exists():
            # No migration was performed, so rollback is trivially successful
            return {
                "success": True,
                "message": "No migration to rollback - migration state file not found",
            }
        if state and isinstance(state.get("completed_steps"), list):
            completed_steps = state["completed_steps"]
        else:
            completed_steps = list(MIGRATION_STEPS_ORDER)

    completed_steps_list = completed_steps or []
    steps_to_rollback = [
        step for step in completed_steps_list if step in MIGRATION_STEPS_ORDER
    ]
    steps_to_rollback.reverse()

    new_home = get_home_dir()
    restored_count = 0
    rollback_errors: list[str] = []

    def restore_file(backup_glob: str, dest_path: Path) -> None:
        """
        Restore the most recent backup matching a glob pattern into the given destination path.

        Finds backup files in the destination directory using `backup_glob`, copies the newest match to `dest_path`, increments the enclosing scope's `restored_count` on success, and records any I/O errors into `rollback_errors`.
        Parameters:
            backup_glob (str): Glob pattern (applied in `dest_path.parent`) used to locate backup files.
            dest_path (Path): Target file path to restore into; the parent directory is searched for backups.
        """
        nonlocal restored_count
        backups = sorted(dest_path.parent.glob(backup_glob), reverse=True)
        if not backups:
            logger.warning("No backups found for %s", dest_path.name)
            return
        backup = backups[0]
        try:
            shutil.copy2(str(backup), str(dest_path))
            logger.info("Restored %s from: %s", dest_path.name, backup)
            restored_count += 1
        except (OSError, IOError) as e:
            logger.warning(
                "Failed to restore %s backup %s: %s", dest_path.name, backup, e
            )
            rollback_errors.append(f"{dest_path.name}: {e}")

    def restore_dir(backup_glob: str, dest_dir: Path, label: str) -> None:
        """
        Restore the destination directory from the most recent backup matching `backup_glob`.

        Attempts to remove `dest_dir` if it exists and then copies the newest matching backup directory into `dest_dir`. On success increments the enclosing `restored_count`. On failure records a descriptive error into the enclosing `rollback_errors` list and does not raise.

        Parameters:
            backup_glob (str): Glob pattern (applied to `dest_dir.parent`) used to find backup directories.
            dest_dir (Path): Target directory to restore into.
            label (str): Human-readable label used in log messages and error records.
        """
        nonlocal restored_count
        backups = sorted(dest_dir.parent.glob(backup_glob), reverse=True)
        if not backups:
            logger.warning("No backups found for %s directory", label)
            return
        backup = backups[0]
        try:
            if dest_dir.exists():
                shutil.rmtree(str(dest_dir))
            shutil.copytree(str(backup), str(dest_dir))
            logger.info("Restored %s directory from: %s", label, backup)
            restored_count += 1
        except (OSError, IOError) as e:
            logger.warning(
                "Failed to restore %s directory backup %s: %s", label, backup, e
            )
            rollback_errors.append(f"{label}: {e}")

    for step in steps_to_rollback:
        if step == "plugins":
            restore_dir("plugins.bak.*", new_home / "plugins", "plugins")
        elif step == "store":
            if sys.platform == "win32":
                logger.info("Skipping store rollback on Windows")
                continue
            restore_dir("store.bak.*", new_home / "store", "store")
        elif step == "logs":
            restore_dir("logs.bak.*", new_home / "logs", "logs")
        elif step == "database":
            db_dir = new_home / "database"
            restore_file("meshtastic.sqlite.bak.*", db_dir / "meshtastic.sqlite")
            restore_file(
                "meshtastic.sqlite-wal.bak.*", db_dir / "meshtastic.sqlite-wal"
            )
            restore_file(
                "meshtastic.sqlite-shm.bak.*", db_dir / "meshtastic.sqlite-shm"
            )
        elif step == "config":
            restore_file("config.yaml.bak.*", new_home / "config.yaml")
        elif step == "credentials":
            restore_file("credentials.json.bak.*", new_home / "credentials.json")
        else:
            logger.debug("No rollback action defined for step: %s", step)

    rollback_ok = len(rollback_errors) == 0

    if rollback_ok and state_path.exists():
        try:
            state_path.unlink()
            logger.info("Removed migration state file (migration rolled back)")
        except (OSError, IOError) as e:
            logger.warning("Failed to remove migration state file: %s", e)
            rollback_ok = False
            rollback_errors.append(f"state_file: {e}")

    message = (
        f"Rollback complete. Restored {restored_count} items from backups"
        if rollback_ok
        else "Rollback completed with errors"
    )

    return {
        "success": rollback_ok,
        "message": message,
        "restored_count": restored_count,
        "errors": rollback_errors,
    }
