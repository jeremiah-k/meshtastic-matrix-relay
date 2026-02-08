"""
Migration utilities for MMRelay v1.2.x → v1.3.

Handles automatic migration from legacy and partial new layouts to unified
MMRELAY_HOME directory structure.

Migration rules:
- Move semantics with backup-before-overwrite safety
- Preserve old files as backups (with timestamp suffixes)
- Support dry-run mode for testing
- Clear deprecation warnings for legacy environment variables

Migration paths (v1.2.x → v1.3):

Legacy Layout (v1.2.9 and earlier):
  ~/.mmrelay/credentials.json  →  $MMRELAY_HOME/matrix/credentials.json
  ~/.mmrelay/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite
  ~/.mmrelay/meshtastic.sqlite-wal →  $MMRELAY_HOME/database/meshtastic.sqlite-wal
  ~/.mmrelay/meshtastic.sqlite-shm →  $MMRELAY_HOME/database/meshtastic.sqlite-shm
  ~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
  ~/.mmrelay/store/              →  $MMRELAY_HOME/matrix/store/
  ~/.mmrelay/plugins/custom/    →  $MMRELAY_HOME/plugins/custom/
  ~/.mmrelay/plugins/community/ →  $MMRELAY_HOME/plugins/community/

Partial New Layout (v1.2.10-1.2.11):
  ~/.mmrelay/config.yaml        →  $MMRELAY_HOME/config.yaml (or keep)
  ~/.mmrelay/credentials.json    →  $MMRELAY_HOME/matrix/credentials.json
  ~/.mmrelay/meshtastic.sqlite    →  $MMRELAY_HOME/database/meshtastic.sqlite
  ~/.mmrelay/data/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite (merge)
  ~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
  ~/.mmrelay/store/              →  $MMRELAY_HOME/matrix/store/
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

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mmrelay.constants.app import CREDENTIALS_FILENAME, MATRIX_DIRNAME, STORE_DIRNAME
from mmrelay.log_utils import get_logger
from mmrelay.paths import resolve_all_paths

logger = get_logger("Migration")


def _get_db_base_path(path: Path) -> Path:
    """Strip -wal/-shm suffix to get the main database file path."""
    if path.name.endswith("-wal") or path.name.endswith("-shm"):
        return path.with_name(path.name[:-4])
    return path


class MigrationError(Exception):
    """Migration-specific error."""

    @classmethod
    def integrity_check_failed(cls, detail: str) -> "MigrationError":
        """Create error for database integrity check failure."""
        return cls(f"Database integrity check failed: {detail}")

    @classmethod
    def verification_failed(cls, detail: str) -> "MigrationError":
        """
        Create a MigrationError representing a database verification failure.

        Parameters:
            detail (str): Human-readable detail describing the verification failure.

        Returns:
            MigrationError: Instance with a message indicating the database verification failure and the provided detail.
        """
        return cls(f"Database verification failed: {detail}")

    @classmethod
    def step_failed(cls, step: str, detail: str) -> "MigrationError":
        """
        Create a MigrationError representing a failure of a specific migration step.

        Parameters:
            step (str): Name of the migration step that failed.
            detail (str): Human-readable detail describing the failure.

        Returns:
            MigrationError: Error instance with message "<step> migration failed: <detail>".
        """
        exc = cls(f"{step} migration failed: {detail}")
        setattr(exc, "step", step)
        return exc


def _path_is_within_home(path: Path, home: Path) -> bool:
    """
    Determine whether a given path is the same as or located inside the specified home directory.

    The comparison is performed on resolved absolute locations of both `path` and `home`, with a best-effort resolution if exact resolution fails.

    Parameters:
        path (Path): Path to test.
        home (Path): Directory to treat as the home/root.

    Returns:
        bool: `True` if `path` equals `home` or is located within `home`, `False` otherwise.
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
    Return whether `path` exists, is a directory, and contains at least one entry.

    Returns:
        `True` if the path exists, is a directory, and has at least one entry; `False` otherwise.
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

    Scans legacy_root for common legacy items (credentials, config, database files and sidecars, logs, e2ee_store, and plugins) and returns a list of discovered artifacts. Each finding is a dict with keys "type" (one of: "credentials", "config", "database", "logs", "e2ee_store", "plugins") and "path" (string path to the artifact). Duplicate paths are suppressed.

    Parameters:
        legacy_root (Path): Root directory to scan for legacy artifacts.

    Returns:
        list[dict[str, str]]: List of findings; each entry contains:
            - "type": artifact category
            - "path": filesystem path to the discovered artifact as a string
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

    # Also detect legacy artifacts still present in HOME itself (v1.2 layout).
    same_home_legacy_items: list[dict[str, str]] = []
    legacy_home_credentials = home / CREDENTIALS_FILENAME
    if legacy_home_credentials.exists() and legacy_home_credentials != credentials_path:
        same_home_legacy_items.append(
            {"type": "credentials", "path": str(legacy_home_credentials)}
        )
    if store_dir is not None:
        legacy_home_store = home / STORE_DIRNAME
        if legacy_home_store.exists() and legacy_home_store != store_dir:
            same_home_legacy_items.append(
                {"type": "e2ee_store", "path": str(legacy_home_store)}
            )
    if same_home_legacy_items:
        legacy_findings.append({"root": str(home), "items": same_home_legacy_items})

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
        errors.append("Legacy data exists and migration is still required")
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


STAGING_DIRNAME = ".migration_staging"
BACKUP_DIRNAME = ".migration_backups"


def _get_staging_path(new_home: Path, unit_name: str) -> Path:
    """Get the staging path for a migration unit."""
    return new_home / STAGING_DIRNAME / unit_name


def _backup_file(src_path: Path, suffix: str = ".bak") -> Path:
    """
    Create a timestamped backup of the given path in a dedicated backup directory.

    Parameters:
        src_path (Path): Original file or directory path to back up.
        suffix (str): Suffix inserted after the original filename and before the timestamp.

    Returns:
        Path: New backup path with format ".migration_backups/<original_name><suffix>.<YYYYMMDD_HHMMSS>".
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = src_path.parent / BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{src_path.name}{suffix}.{timestamp}"
    return backup_dir / backup_name


def _finalize_move(staging_path: Path, dest_path: Path) -> None:
    """
    Atomically finalize a staged move by renaming the staging path to the final destination.

    Parameters:
        staging_path (Path): The path where the unit was staged.
        dest_path (Path): The final destination path.

    Raises:
        OSError: If validation or finalization fails.
    """
    if not staging_path.exists():
        raise OSError(f"Staging path does not exist: {staging_path}")

    # Ensure parent of destination exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic rename (on POSIX)
    if dest_path.exists():
        if dest_path.is_dir():
            shutil.rmtree(str(dest_path))
        else:
            dest_path.unlink()

    shutil.move(str(staging_path), str(dest_path))
    logger.debug("Finalized move from %s to %s", staging_path, dest_path)


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

    # Group databases by main file and its sidecars
    db_groups: dict[Path, list[Path]] = {}
    for db_path in candidates:
        try:
            if not db_path.exists():
                continue
        except OSError:
            continue

        # Extract base name (remove -wal, -shm suffix)
        base = _get_db_base_path(db_path)

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

    # Return main file from most recent group
    return most_recent_group[0]


def migrate_credentials(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate the first discovered legacy credentials.json into the new HOME matrix directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy credentials files.
        new_home (Path): Destination home directory where matrix/credentials.json will be placed.
        dry_run (bool): If True, report intended action without modifying files.
        force (bool): If True, overwrite existing destination (backups are always created).

    Returns:
        dict: Migration result.
    """
    new_creds = new_home / MATRIX_DIRNAME / CREDENTIALS_FILENAME
    old_creds: Path | None = None

    roots_to_scan = list(legacy_roots)
    if new_home not in roots_to_scan:
        roots_to_scan.append(new_home)

    for legacy_root in roots_to_scan:
        candidate = legacy_root / CREDENTIALS_FILENAME
        if candidate.resolve() == new_creds.resolve():
            if candidate.exists():
                logger.info(
                    "Credentials already at target location, no migration needed: %s",
                    new_creds,
                )
                return {
                    "success": True,
                    "old_path": str(candidate),
                    "new_path": str(new_creds),
                    "action": "none",
                    "message": "Credentials already at target location",
                }
            continue
        if candidate.exists():
            old_creds = candidate
            logger.info("Found credentials.json in legacy root: %s", old_creds)
            break

    if not old_creds:
        if new_creds.exists():
            logger.info("Credentials already migrated to %s", new_creds)
            return {
                "success": True,
                "new_path": str(new_creds),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No credentials file found in legacy locations")
        return {
            "success": True,
            "message": "No credentials file found in legacy locations",
        }

    # If destination exists, require --force and ALWAYS backup
    if new_creds.exists() and not force:
        logger.warning(
            "Credentials already exist at destination, skipping: %s. Use --force to overwrite.",
            new_creds,
        )
        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "none",
            "message": "Credentials already exist at destination",
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would move credentials from %s to %s", old_creds, new_creds
        )
        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_path = _get_staging_path(new_home, "credentials")
    staging_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup destination if it exists (ALWAYS)
        if new_creds.exists():
            backup_path = _backup_file(new_creds)
            logger.info("Backing up existing destination to %s", backup_path)
            shutil.copy2(str(new_creds), str(backup_path))

        # 2. Move to staging
        if staging_path.exists():
            if staging_path.is_dir():
                shutil.rmtree(str(staging_path))
            else:
                staging_path.unlink()

        shutil.move(str(old_creds), str(staging_path))
        logger.debug("Staged credentials at %s", staging_path)

        # 3. Finalize
        _finalize_move(staging_path, new_creds)
        logger.info("Migrated credentials to %s", new_creds)

        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate credentials from %s", old_creds)
        raise MigrationError.step_failed("credentials", str(exc)) from exc


def migrate_config(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Locate and migrate the first legacy `config.yaml` into the new home directory.

    Parameters:
        legacy_roots (list[Path]): Directories to search for a legacy `config.yaml`.
        new_home (Path): Destination home directory where `config.yaml` should be placed.
        dry_run (bool): If True, report the intended action without modifying the filesystem.
        force (bool): If True, overwrite an existing destination (backups are always created).

    Returns:
        dict: Migration result summary.
    """
    new_config = new_home / "config.yaml"
    old_config: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "config.yaml"
        if candidate.resolve() == new_config.resolve():
            if candidate.exists():
                logger.info(
                    "Config already at target location, no migration needed: %s",
                    new_config,
                )
                return {
                    "success": True,
                    "old_path": str(candidate),
                    "new_path": str(new_config),
                    "action": "none",
                    "message": "Config already at target location",
                }
            continue
        if candidate.exists():
            old_config = candidate
            logger.info("Found config.yaml in legacy root: %s", old_config)
            break

    if not old_config:
        if new_config.exists():
            logger.info("Config already migrated to %s", new_config)
            return {
                "success": True,
                "new_path": str(new_config),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No config.yaml found in legacy locations")
        return {
            "success": True,
            "message": "No config.yaml found in legacy locations",
        }

    # If destination exists, require --force and ALWAYS backup
    if new_config.exists() and not force:
        logger.warning(
            "Config already exists at destination, skipping: %s. Use --force to overwrite.",
            new_config,
        )
        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "none",
            "message": "Config already exists at destination",
        }

    if dry_run:
        logger.info("[DRY RUN] Would move config from %s to %s", old_config, new_config)
        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_path = _get_staging_path(new_home, "config")
    staging_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup destination if it exists (ALWAYS)
        if new_config.exists():
            backup_path = _backup_file(new_config)
            logger.info("Backing up existing destination to %s", backup_path)
            shutil.copy2(str(new_config), str(backup_path))

        # 2. Move to staging
        if staging_path.exists():
            if staging_path.is_dir():
                shutil.rmtree(str(staging_path))
            else:
                staging_path.unlink()

        shutil.move(str(old_config), str(staging_path))
        logger.debug("Staged config at %s", staging_path)

        # 3. Finalize
        _finalize_move(staging_path, new_config)
        logger.info("Migrated config to %s", new_config)

        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate config from %s", old_config)
        raise MigrationError.step_failed("config", str(exc)) from exc


def migrate_database(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate the Meshtastic SQLite database from legacy locations into the new home's database directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy database files.
        new_home (Path): Destination MMRELAY home directory.
        dry_run (bool): If True, report planned actions without modifying the filesystem.
        force (bool): If True, overwrite existing destination (backups are always created).

    Returns:
        dict: Migration result.
    """
    new_db_dir = new_home / "database"
    candidates = []

    for legacy_root in legacy_roots:
        legacy_db = legacy_root / "meshtastic.sqlite"
        if legacy_db.resolve() == (new_db_dir / "meshtastic.sqlite").resolve():
            if legacy_db.exists() and len(legacy_roots) == 1:
                logger.info(
                    "Database already at target location, no migration needed: %s",
                    new_db_dir,
                )
                return {
                    "success": True,
                    "old_path": str(legacy_db),
                    "new_path": str(new_db_dir),
                    "action": "none",
                    "message": "Database already at target location",
                }
            continue
        if legacy_db.exists():
            candidates.append(legacy_db)
            for suffix in ["-wal", "-shm"]:
                sidecar = legacy_db.with_suffix(f".sqlite{suffix}")
                if sidecar.exists():
                    candidates.append(sidecar)

        partial_data_dir = legacy_root / "data"
        if partial_data_dir.exists():
            partial_db = partial_data_dir / "meshtastic.sqlite"
            if partial_db.resolve() == (new_db_dir / "meshtastic.sqlite").resolve():
                continue
            if partial_db.exists():
                candidates.append(partial_db)
                for suffix in ["-wal", "-shm"]:
                    sidecar = partial_db.with_suffix(f".sqlite{suffix}")
                    if sidecar.exists():
                        candidates.append(sidecar)

        legacy_db_dir = legacy_root / "database"
        if legacy_db_dir.exists():
            legacy_db = legacy_db_dir / "meshtastic.sqlite"
            if legacy_db.resolve() == (new_db_dir / "meshtastic.sqlite").resolve():
                if legacy_db.exists() and len(legacy_roots) == 1:
                    logger.info(
                        "Database already at target location, no migration needed: %s",
                        new_db_dir,
                    )
                    return {
                        "success": True,
                        "old_path": str(legacy_db),
                        "new_path": str(new_db_dir),
                        "action": "none",
                        "message": "Database already at target location",
                    }
                continue
            if legacy_db.exists():
                candidates.append(legacy_db)
                for suffix in ["-wal", "-shm"]:
                    sidecar = legacy_db.with_suffix(f".sqlite{suffix}")
                    if sidecar.exists():
                        candidates.append(sidecar)

    if not candidates:
        if (new_db_dir / "meshtastic.sqlite").exists():
            logger.info("Database already migrated to %s", new_db_dir)
            return {
                "success": True,
                "new_path": str(new_db_dir),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No database files found in legacy locations")
        return {
            "success": True,
            "message": "No database files found in legacy locations",
        }

    # Skip if target already exists and not forcing
    if (new_db_dir / "meshtastic.sqlite").exists() and not force:
        logger.warning(
            "Database already exists at destination, skipping: %s. Use --force to overwrite.",
            new_db_dir,
        )
        return {
            "success": True,
            "new_path": str(new_db_dir),
            "action": "none",
            "message": "Database already at target location",
        }

    if dry_run:
        logger.info("[DRY RUN] Would move database to %s", new_db_dir)
        return {
            "success": True,
            "new_path": str(new_db_dir),
            "action": "move",
            "dry_run": True,
        }

    most_recent = _get_most_recent_database(candidates)
    if not most_recent:
        return {
            "success": False,
            "message": "No valid database files found in legacy location",
        }

    selected_group = [
        candidate
        for candidate in candidates
        if _get_db_base_path(candidate) == most_recent
    ]
    if not selected_group:
        return {
            "success": False,
            "message": "Most recent database group not found in legacy location",
        }
    if most_recent not in selected_group:
        selected_group.insert(0, most_recent)
    for suffix in ("-wal", "-shm"):
        sidecar = most_recent.with_name(f"{most_recent.name}{suffix}")
        if sidecar.exists() and sidecar not in selected_group:
            selected_group.append(sidecar)

    # Proceed with migration
    staging_dir = _get_staging_path(new_home, "database")
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup existing database files (ALWAYS)
        for db_path in selected_group:
            dest = new_db_dir / db_path.name
            if db_path.resolve() == dest.resolve():
                continue
            if dest.exists():
                backup_path = _backup_file(dest)
                logger.info(
                    "Backing up existing database file %s to %s", dest, backup_path
                )
                shutil.copy2(str(dest), str(backup_path))

        # 2. Copy to staging (using copy-verify-delete pattern)
        for db_path in selected_group:
            dest = staging_dir / db_path.name
            logger.debug("Staging database file %s at %s", db_path, dest)
            shutil.copy2(str(db_path), str(dest))

        # 3. Verify integrity in staging
        main_db_staged = staging_dir / most_recent.name
        if not most_recent.name.endswith(("-wal", "-shm")):
            try:
                db_uri = f"{main_db_staged.resolve().as_uri()}?mode=ro"
                with sqlite3.connect(db_uri, uri=True) as conn:
                    result = conn.execute("PRAGMA integrity_check").fetchone()
                if result and result[0] != "ok":
                    raise MigrationError.integrity_check_failed(result[0])
                logger.info("Database integrity check passed in staging")
            except sqlite3.DatabaseError as e:
                raise MigrationError.verification_failed(str(e)) from e

        # 4. Finalize: move staging to final
        new_db_dir.mkdir(parents=True, exist_ok=True)
        for db_path in selected_group:
            dest = new_db_dir / db_path.name
            staged = staging_dir / db_path.name
            if dest.exists():
                dest.unlink()
            shutil.move(str(staged), str(dest))

        # 5. Delete sources after successful move
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
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate database from %s", most_recent)
        raise MigrationError.step_failed("database", str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def migrate_logs(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate log files from the first discovered legacy "logs" directory into the new home's "logs" directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for a legacy "logs" directory.
        new_home (Path): Destination MMRELAY_HOME where logs should be placed.
        dry_run (bool): If True, only report intended actions.
        force (bool): If True, overwrite existing files/directories (backups are always created).

    Returns:
        dict: Migration result.
    """
    old_logs_dir: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "logs"
        if candidate.exists():
            old_logs_dir = candidate
            logger.info("Found logs directory in legacy root: %s", old_logs_dir)
            break

    new_logs_dir = new_home / "logs"

    if old_logs_dir:
        # Add same-path guard
        if old_logs_dir.resolve() == new_logs_dir.resolve():
            if _dir_has_entries(old_logs_dir):
                logger.info(
                    "Logs already at target location, no migration needed: %s",
                    new_logs_dir,
                )
                return {
                    "success": True,
                    "old_path": str(old_logs_dir),
                    "new_path": str(new_logs_dir),
                    "action": "none",
                    "message": "Logs already at target location",
                }

    if not old_logs_dir or not old_logs_dir.exists():
        if _dir_has_entries(new_logs_dir):
            logger.info("Logs already migrated to %s", new_logs_dir)
            return {
                "success": True,
                "new_path": str(new_logs_dir),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No logs directory found in legacy locations")
        return {
            "success": True,
            "message": "No logs directory found in legacy locations",
        }

    # Skip if target already exists and not forcing
    if new_logs_dir.exists() and not force:
        logger.warning(
            "Logs already at target location, no migration needed: %s. Use --force to overwrite.",
            new_logs_dir,
        )
        return {
            "success": True,
            "old_path": str(old_logs_dir),
            "new_path": str(new_logs_dir),
            "action": "none",
            "message": "Logs already exists at destination",
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would move logs from %s to %s", old_logs_dir, new_logs_dir
        )
        return {
            "success": True,
            "old_path": str(old_logs_dir),
            "new_path": str(new_logs_dir),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_dir = _get_staging_path(new_home, "logs")
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup existing destination (ALWAYS)
        if new_logs_dir.exists():
            backup_path = _backup_file(new_logs_dir)
            logger.info("Backing up existing logs directory to %s", backup_path)
            shutil.copytree(str(new_logs_dir), str(backup_path))

        # 2. Move files to staging with timestamped names
        migrated_count = 0
        for log_file in old_logs_dir.glob("*.log"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"{log_file.stem}_migrated_{timestamp}.log"
            dest_staged = staging_dir / new_name
            shutil.move(str(log_file), str(dest_staged))
            migrated_count += 1

        # 3. Finalize: Move staging content to final logs dir
        new_logs_dir.mkdir(parents=True, exist_ok=True)
        for staged_file in staging_dir.iterdir():
            final_dest = new_logs_dir / staged_file.name
            if final_dest.exists():
                final_dest.unlink()
            shutil.move(str(staged_file), str(final_dest))

        logger.info("Migrated %d logs to %s", migrated_count, new_logs_dir)

        # Cleanup legacy dir if empty
        try:
            if not any(old_logs_dir.iterdir()):
                old_logs_dir.rmdir()
        except (OSError, IOError):
            pass

        return {
            "success": True,
            "migrated_count": migrated_count,
            "old_path": str(old_logs_dir),
            "new_path": str(new_logs_dir),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate logs from %s", old_logs_dir)
        raise MigrationError.step_failed("logs", str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def migrate_store(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate the E2EE store directory from legacy roots into the new home's `matrix/store` directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for a legacy `store` directory.
        new_home (Path): Target home directory.
        dry_run (bool): If True, only report intended actions without modifying files.
        force (bool): If True, overwrite existing destination (backups are always created).

    Returns:
        dict: Migration result.
    """
    if sys.platform == "win32":
        return {
            "success": True,
            "message": "E2EE not supported on Windows, skipping store migration",
        }

    new_store_dir = new_home / MATRIX_DIRNAME / STORE_DIRNAME
    old_store_dir: Path | None = None

    roots_to_scan = list(legacy_roots)
    if new_home not in roots_to_scan:
        roots_to_scan.append(new_home)

    for legacy_root in roots_to_scan:
        candidate = legacy_root / STORE_DIRNAME
        if candidate.resolve() == new_store_dir.resolve():
            if _dir_has_entries(candidate):
                logger.info(
                    "Store already at target location, no migration needed: %s",
                    new_store_dir,
                )
                return {
                    "success": True,
                    "old_path": str(candidate),
                    "new_path": str(new_store_dir),
                    "action": "none",
                    "message": "Store already at target location",
                }
            continue
        if candidate.exists():
            old_store_dir = candidate
            logger.info("Found store directory in legacy root: %s", old_store_dir)
            break

    if not old_store_dir:
        if _dir_has_entries(new_store_dir):
            logger.info("E2EE store already migrated to %s", new_store_dir)
            return {
                "success": True,
                "new_path": str(new_store_dir),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No E2EE store directory found in legacy locations")
        return {
            "success": True,
            "message": "No E2EE store directory found in legacy locations",
        }

    # Skip if target already exists and not forcing
    if new_store_dir.exists() and not force:
        logger.warning(
            "Store directory already exists at destination, skipping: %s. Use --force to overwrite.",
            new_store_dir,
        )
        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "none",
            "message": "Store directory already exists at destination",
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would move store from %s to %s", old_store_dir, new_store_dir
        )
        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_dir = _get_staging_path(new_home, "store")
    staging_dir.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup existing destination (ALWAYS)
        if new_store_dir.exists():
            backup_path = _backup_file(new_store_dir)
            logger.info("Backing up existing store directory to %s", backup_path)
            shutil.copytree(str(new_store_dir), str(backup_path))

        # 2. Move to staging
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir))
        shutil.move(str(old_store_dir), str(staging_dir))
        logger.debug("Staged store directory at %s", staging_dir)

        # 3. Finalize: move staging to final destination
        _finalize_move(staging_dir, new_store_dir)
        logger.info("Migrated E2EE store to %s", new_store_dir)

        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate E2EE store from %s", old_store_dir)
        raise MigrationError.step_failed("store", str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def _migrate_plugin_tier(
    old_dir: Path,
    new_dir: Path,
    tier_name: str,
    force: bool,
    errors: list[str],
) -> bool:
    """
    Migrate a single tier of plugins (e.g. custom or community).

    Searches the source directory for plugin folders and moves each into the destination.
    Updates the provided errors list with any failure messages.

    Parameters:
        old_dir (Path): Source directory containing plugin folders.
        new_dir (Path): Destination directory for the plugin tier.
        tier_name (str): Label used for logging and error reporting (e.g. "custom").
        force (bool): If True, overwrite existing destinations.
        errors (list[str]): List to append error messages to.

    Returns:
        bool: True if any plugins were successfully migrated, False otherwise.
    """
    if not old_dir.exists():
        return False
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        logger.warning("Failed to create %s plugins directory: %s", tier_name, e)
        errors.append(f"{tier_name} dir: {e}")
        return False

    migrated = False
    try:
        for item in old_dir.iterdir():
            if not item.is_dir():
                continue
            dest = new_dir / item.name
            if dest.exists():
                # In staging, we shouldn't have collisions unless staging was dirty
                if dest.is_dir():
                    shutil.rmtree(str(dest))
                else:
                    dest.unlink()

            try:
                shutil.move(str(item), str(dest))
                logger.debug("Staged %s plugin: %s", tier_name, item)
                migrated = True
            except (OSError, IOError) as e:
                logger.warning("Failed to stage %s plugin %s: %s", tier_name, item, e)
                errors.append(f"{tier_name} {item}: {e}")
    except (OSError, IOError) as e:
        logger.warning("Failed to stage %s plugins: %s", tier_name, e)
        errors.append(f"{tier_name}: {e}")

    # Cleanup old tier dir if empty
    try:
        if old_dir.exists() and not any(old_dir.iterdir()):
            old_dir.rmdir()
    except (OSError, IOError):
        pass

    return migrated


def migrate_plugins(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate plugins from legacy plugin directories into the new home plugins layout.

    Parameters:
        legacy_roots (list[Path]): Legacy root directories to scan for a `plugins` directory.
        new_home (Path): Destination MMRELAY_HOME root for plugins.
        dry_run (bool): If True, only report the intended actions without modifying the filesystem.
        force (bool): If True, overwrite existing destinations (backups are always created).

    Returns:
        dict: Migration result.
    """
    old_plugins_dir: Path | None = None

    for legacy_root in legacy_roots:
        candidate = legacy_root / "plugins"
        if candidate.exists():
            old_plugins_dir = candidate
            logger.info("Found plugins directory in legacy root: %s", old_plugins_dir)
            break

    new_plugins_dir = new_home / "plugins"

    if old_plugins_dir:
        # Add same-path guard
        if old_plugins_dir.resolve() == new_plugins_dir.resolve():
            if _dir_has_entries(old_plugins_dir):
                logger.info(
                    "Plugins already at target location, no migration needed: %s",
                    new_plugins_dir,
                )
                return {
                    "success": True,
                    "old_path": str(old_plugins_dir),
                    "new_path": str(new_plugins_dir),
                    "action": "none",
                    "message": "Plugins already at target location",
                }

    if not old_plugins_dir or not old_plugins_dir.exists():
        if _dir_has_entries(new_plugins_dir):
            logger.info("Plugins already migrated to %s", new_plugins_dir)
            return {
                "success": True,
                "new_path": str(new_plugins_dir),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info("No plugins directory found in legacy locations")
        return {
            "success": True,
            "message": "No plugins directory found in legacy locations",
        }

    # Skip if target already exists and not forcing
    if new_plugins_dir.exists() and not force:
        logger.warning(
            "Plugins directory already exists at destination, skipping: %s. Use --force to overwrite.",
            new_plugins_dir,
        )
        return {
            "success": True,
            "old_path": str(old_plugins_dir),
            "new_path": str(new_plugins_dir),
            "action": "none",
            "message": "Plugins already at target location",
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would move plugins from %s to %s",
            old_plugins_dir,
            new_plugins_dir,
        )
        return {
            "success": True,
            "old_path": str(old_plugins_dir),
            "new_path": str(new_plugins_dir),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_dir = _get_staging_path(new_home, "plugins")
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Backup existing destination (ALWAYS)
        if new_plugins_dir.exists():
            backup_path = _backup_file(new_plugins_dir)
            logger.info("Backing up existing plugins directory to %s", backup_path)
            shutil.copytree(str(new_plugins_dir), str(backup_path))

        errors: list[str] = []
        migrated_types: list[str] = []

        # 2. Stage tiers
        old_custom_dir = old_plugins_dir / "custom"
        if _migrate_plugin_tier(
            old_custom_dir,
            staging_dir / "custom",
            "custom",
            force,
            errors,
        ):
            migrated_types.append("custom")

        old_community_dir = old_plugins_dir / "community"
        if _migrate_plugin_tier(
            old_community_dir,
            staging_dir / "community",
            "community",
            force,
            errors,
        ):
            migrated_types.append("community")

        if errors:
            raise OSError("; ".join(errors))

        # 3. Finalize: move staging to final destination
        _finalize_move(staging_dir, new_plugins_dir)
        logger.info("Migrated plugins to %s", new_plugins_dir)

        # Cleanup legacy directory if empty
        try:
            if not any(old_plugins_dir.iterdir()):
                old_plugins_dir.rmdir()
        except (OSError, IOError):
            pass

        return {
            "success": True,
            "migrated_types": migrated_types,
            "old_path": str(old_plugins_dir),
            "new_path": str(new_plugins_dir),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate plugins from %s", old_plugins_dir)
        raise MigrationError.step_failed("plugins", str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def migrate_gpxtracker(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate GPX files for the community gpxtracker plugin into the new plugins/community/gpxtracker/data directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy `config.yaml` entries.
        new_home (Path): Destination MMRELAY_HOME root for plugin data.
        dry_run (bool): If True, report actions without making changes.
        force (bool): If True, overwrite existing destination files (backups are always created).

    Returns:
        dict: Summary of the migration outcome.
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
                logger.warning("Failed to import yaml for GPX tracker detection: %s", e)
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

    new_gpx_data_dir = new_home / "plugins" / "community" / "gpxtracker" / "data"

    if old_gpx_dir:
        # Expansion and path guard
        expanded_old_gpx_dir = old_gpx_dir.expanduser()
        try:
            if expanded_old_gpx_dir.resolve() == new_gpx_data_dir.resolve():
                if _dir_has_entries(expanded_old_gpx_dir):
                    logger.info(
                        "gpxtracker source already at target location; skipping migration"
                    )
                    return {
                        "success": True,
                        "migrated_count": 0,
                        "old_path": str(expanded_old_gpx_dir),
                        "new_path": str(new_gpx_data_dir),
                        "action": "none",
                        "message": "gpxtracker already at target location",
                    }
        except OSError:
            pass

    if not old_gpx_dir or not old_gpx_dir.exists():
        if _dir_has_entries(new_gpx_data_dir):
            logger.info("GPX tracker data already migrated to %s", new_gpx_data_dir)
            return {
                "success": True,
                "new_path": str(new_gpx_data_dir),
                "action": "none",
                "message": "Already migrated",
            }
        logger.info(
            "gpxtracker plugin not configured with gpx_directory or directory not found, skipping"
        )
        return {
            "success": True,
            "message": "gpxtracker plugin not configured with gpx_directory, skipping migration",
        }

    if dry_run:
        logger.info(
            "[DRY RUN] Would move gpxtracker GPX files from %s to %s",
            old_gpx_dir,
            new_gpx_data_dir,
        )
        return {
            "success": True,
            "old_path": str(old_gpx_dir),
            "new_path": str(new_gpx_data_dir),
            "action": "move",
            "dry_run": True,
        }

    # Proceed with migration
    staging_dir = _get_staging_path(new_home, "gpxtracker")
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        migrated_count = 0
        errors: list[str] = []

        # 1. Stage GPX files with timestamped names
        for gpx_file in old_gpx_dir.glob("*.gpx"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"{gpx_file.stem}_migrated_{timestamp}.gpx"
            dest_staged = staging_dir / new_name
            shutil.move(str(gpx_file), str(dest_staged))
            migrated_count += 1

        if migrated_count > 0:
            # 2. Finalize: move staging to final data dir
            new_gpx_data_dir.mkdir(parents=True, exist_ok=True)
            for staged_file in staging_dir.iterdir():
                final_dest = new_gpx_data_dir / staged_file.name

                # ALWAYS backup if destination exists
                if final_dest.exists():
                    backup_path = _backup_file(final_dest)
                    logger.info(
                        "Backing up existing GPX file %s to %s", final_dest, backup_path
                    )
                    shutil.copy2(str(final_dest), str(backup_path))
                    final_dest.unlink()

                shutil.move(str(staged_file), str(final_dest))

        logger.info("Migrated %d GPX files to %s", migrated_count, new_gpx_data_dir)

        return {
            "success": True,
            "migrated_count": migrated_count,
            "old_path": str(old_gpx_dir),
            "new_path": str(new_gpx_data_dir),
            "action": "move",
        }
    except Exception as exc:
        logger.exception("Failed to migrate gpxtracker GPX files from %s", old_gpx_dir)
        raise MigrationError.step_failed("gpxtracker", str(exc)) from exc
    finally:
        if staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def is_migration_needed() -> bool:
    """
    Determine whether a migration from legacy layouts to the current home structure is required.

    Returns:
        True if legacy data is found that needs to be moved to the current home structure.
    """
    report = verify_migration()
    return bool(report.get("legacy_data_found", False))


def perform_migration(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """
    Orchestrates the end-to-end migration of legacy MMRelay data into the v1.3 home layout.

    Runs each per-artifact migrator in the defined order. Supports a dry-run mode that reports intended actions without mutating the filesystem.

    Parameters:
        dry_run (bool): If True, simulate the migration and report actions without making changes.
        force (bool): If True, allow overwriting existing destinations without creating backups.

    Returns:
        dict: Migration report containing at least the keys:
            - "dry_run": the dry_run input value
            - "timestamp": ISO timestamp of the run
            - "migrations": list of per-step results
            - "completed_steps": list of step names that completed
            - "success": `true` if migration (or dry run) completed successfully, `false` otherwise
            - "message": human-readable status message
            - "error": error message when failure occurred (optional)
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

    # If migration is needed but no external legacy roots found, it means same-home legacy data exists
    if not legacy_roots and is_migration_needed():
        legacy_roots = [new_home]

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
        Record a migration step's result into the shared migration report.

        Appends an entry {"type": step_name, "result": result} to the module-level `report["migrations"]` list and updates `report["completed_steps"]` to a snapshot of the current `completed_steps`. This function mutates the shared `report` object.

        Parameters:
            step_name (str): Identifier for the migration step (e.g., "credentials", "database").
            result (dict[str, Any]): Result details produced by the step (e.g., `success`, `action`, `old_path`, `new_path`, `error`).
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
        Run a named migration step, record its outcome, update in-memory progress.

        Calls the provided step function with the given arguments and records its returned result. On successful result, the step name is appended to the in-memory completed steps. On failure, a MigrationError is raised.

        Parameters:
            step_name (str): Logical name of the migration step.
            func (Callable[..., dict[str, Any]]): Function that performs the step and returns a dict-like result.
            *args: Positional arguments forwarded to `func`.
            **kwargs: Keyword arguments forwarded to `func`.

        Returns:
            dict[str, Any]: The result returned by `func`.

        Raises:
            MigrationError: If the step result indicates failure (i.e., the returned result has a falsy `success` value). The exception includes the step name and an error detail.
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
        return result

    try:
        # Migrate credentials
        _run_step(
            "credentials",
            migrate_credentials,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate config
        _run_step(
            "config",
            migrate_config,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate database
        _run_step(
            "database",
            migrate_database,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate logs
        _run_step(
            "logs",
            migrate_logs,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate store (E2EE keys)
        _run_step(
            "store",
            migrate_store,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate plugins
        _run_step(
            "plugins",
            migrate_plugins,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        # Migrate gpxtracker (always runs; no-ops if not configured)
        _run_step(
            "gpxtracker",
            migrate_gpxtracker,
            legacy_roots,
            new_home,
            dry_run=dry_run,
            force=force,
        )

        if not dry_run:
            report["message"] = "Migration completed successfully"
        else:
            report["message"] = "Dry run complete - no changes made"

        report["success"] = True
    except (MigrationError, OSError, IOError, sqlite3.DatabaseError) as exc:
        report["success"] = False
        report["error"] = str(exc)
        report["message"] = "Migration failed"

        # Log detailed failure info
        staging_dir = new_home / STAGING_DIRNAME
        logger.error(
            "Migration failed during step: %s", getattr(exc, "step", "unknown")
        )
        logger.error("Error details: %s", exc)
        if staging_dir.exists():
            logger.error("Staged data may be present in: %s", staging_dir)
        logger.error("Please resolve the issue and re-run migration.")

        return report

    logger.info(
        "Migration complete. Summary: %d migrations performed",
        len(report["migrations"]),
    )

    return report
