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
    - Tier 1 (Code): $MMRELAY_HOME/plugins/{name>/
    - Tier 2 (Filesystem): $MMRELAY_HOME/plugins/{name}/data/
    - Tier 3 (Database): SQLite via store_plugin_data()

  Migration for gpxtracker (community plugin):
    Old: gpx_directory: "~/my_gpx_files"
    New: $MMRELAY_HOME/plugins/gpxtracker/data/
"""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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

    pass


def _path_is_within_home(path: Path, home: Path) -> bool:
    """Return True if path resolves under the provided home directory."""
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
    """Return True if directory exists and contains at least one entry."""
    if not path.exists() or not path.is_dir():
        return False
    try:
        return any(path.iterdir())
    except OSError:
        return False


def _find_legacy_data(legacy_root: Path) -> list[dict[str, str]]:
    """Scan a legacy root for known data artifacts.

    Returns:
        list of dicts with keys: type, path
    """
    findings: list[dict[str, str]] = []
    seen_paths: set[str] = set()

    def add_finding(item_type: str, path: Path) -> None:
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
    """Verify that MMRelay data lives under a single HOME root (read-only).

    Returns:
        dict[str, Any]: Verification report including status and findings.
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
    """Print a human-readable migration verification report."""
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
    """Read migration state metadata from disk if present."""
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
    """Persist migration state metadata to disk."""
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
    """Check if migration has been completed."""
    state = _read_migration_state()
    if not state:
        return False
    return (
        state.get("version") == MIGRATION_VERSION and state.get("status") == "completed"
    )


def _mark_migration_completed(completed_steps: list[str] | None = None) -> None:
    """Mark migration as completed."""
    _write_migration_state(status="completed", completed_steps=completed_steps)
    logger.info("Migration completed and marked in: %s", _get_migration_state_path())


def _backup_file(src_path: Path, suffix: str = ".bak") -> Path:
    """Backup a file by adding timestamped suffix.

    Args:
        src_path: File to backup.
        suffix: File extension for backup (default: .bak).

    Returns:
        Path: Backup file path.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{src_path.name}{suffix}.{timestamp}"
    return src_path.with_name(backup_name)


def _get_most_recent_database(candidates: list[Path]) -> Path | None:
    """Find most recently modified database file including WAL/SHM sidecars.

    Args:
        candidates: List of candidate database files.

    Returns:
        Path or None: Most recently modified database path.
    """

    def get_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def get_base_path(path: Path) -> Path:
        if path.name.endswith("-wal") or path.name.endswith("-shm"):
            return path.with_name(path.name[:-4])
        return path

    # Group databases by main file and its sidecars
    db_groups: dict[Path, list[Path]] = {}
    for db_path in candidates:
        if not db_path.exists():
            continue

        # Extract base name (remove -wal, -shm suffix)
        base = get_base_path(db_path)

        # Skip orphaned WAL/SHM sidecars (no main database file exists)
        if not base.exists():
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
    """Migrate credentials.json to new location.

    Scans all legacy roots for credentials.json and migrates the first one found.

    Args:
        legacy_roots: List of legacy directory paths to scan for credentials.json.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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
    """Migrate config.yaml to new location.

    Scans all legacy roots for config.yaml and migrates the first one found.

    Args:
        legacy_roots: List of legacy directory paths to scan for config.yaml.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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
    """Migrate database and sidecars to new location.

    Scans all legacy roots for database files and migrateses most recent one.

    Args:
        legacy_roots: List of legacy directory paths to scan for database files.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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

    if not candidates:
        return {
            "success": True,
            "message": "No database files found in legacy location",
        }

    def get_base_path(path: Path) -> Path:
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
            if move:
                logger.info("Moving database file: %s", db_path)
                if dest.exists():
                    dest.unlink()
                shutil.move(str(db_path), str(dest))
            else:
                logger.info("Copying database file: %s", db_path)
                shutil.copy2(str(db_path), str(dest))
        except (OSError, IOError) as e:
            logger.error("Failed to migrate database file %s", db_path)
            return {
                "success": False,
                "error": str(e),
            }

    logger.info("Database migration complete")

    # Verify database integrity if main database file was copied/moved
    if not dry_run and not most_recent.name.endswith(("-wal", "-shm")):
        main_db = new_db_dir / most_recent.name
        try:
            conn = sqlite3.connect(str(main_db))
            cur = conn.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            conn.close()
            if result and result[0] != "ok":
                raise MigrationError(f"Database integrity check failed: {result[0]}")
            logger.info("Database integrity check passed")
        except sqlite3.DatabaseError as e:
            raise MigrationError(f"Database verification failed: {e}") from e

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
    """Migrate logs to new location.

    Scans all legacy roots for logs directory and migrateses first one found.

    Args:
        legacy_roots: List of legacy directory paths to scan for logs directory.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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
    """Migrate E2EE store to new location.

    Scans all legacy roots for store directory and migrateses first one found.

    Args:
        legacy_roots: List of legacy directory paths to scan for store directory.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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
    """Migrate plugins directory to new location.

    Scans all legacy roots for plugins directory and migrateses all found.

    Args:
        legacy_roots: List of legacy directory paths to scan for plugins directories.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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

    if new_plugins_dir.exists() and not force:
        logger.info("Backing up existing plugins directory: %s", new_plugins_dir)
        backup_path = _backup_file(new_plugins_dir)
        try:
            shutil.copytree(str(new_plugins_dir), str(backup_path))
        except (OSError, IOError) as e:
            logger.warning("Failed to backup plugins directory: %s", e)

    new_plugins_dir.mkdir(parents=True, exist_ok=True)

    migrated_types = []

    # Migrate custom plugins
    old_custom_dir = old_plugins_dir / "custom"
    if old_custom_dir.exists():
        new_custom_dir = new_plugins_dir / "custom"
        new_custom_dir.mkdir(parents=True, exist_ok=True)
        try:
            for item in old_custom_dir.iterdir():
                if item.is_dir():
                    dest = new_custom_dir / item.name
                    if dest.exists() and not force:
                        logger.info("Backing up existing custom plugin: %s", dest)
                        backup_path = _backup_file(dest)
                        try:
                            shutil.copytree(str(dest), str(backup_path))
                        except (OSError, IOError) as e:
                            logger.warning("Failed to backup custom plugin: %s", e)
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
            migrated_types.append("custom")
        except (OSError, IOError) as e:
            logger.warning("Failed to migrate custom plugins: %s", e)

    # Migrate community plugins
    old_community_dir = old_plugins_dir / "community"
    if old_community_dir.exists():
        new_community_dir = new_plugins_dir / "community"
        new_community_dir.mkdir(parents=True, exist_ok=True)
        try:
            for item in old_community_dir.iterdir():
                if item.is_dir():
                    dest = new_community_dir / item.name
                    if dest.exists() and not force:
                        logger.info("Backing up existing community plugin: %s", dest)
                        backup_path = _backup_file(dest)
                        try:
                            shutil.copytree(str(dest), str(backup_path))
                        except (OSError, IOError) as e:
                            logger.warning("Failed to backup community plugin: %s", e)
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
            migrated_types.append("community")
        except (OSError, IOError) as e:
            logger.warning("Failed to migrate community plugins: %s", e)

    if move:
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

    return {
        "success": True,
        "migrated_types": migrated_types,
        "old_path": str(old_plugins_dir),
        "new_path": str(new_plugins_dir),
        "action": "move" if move else "copy",
    }


def migrate_gpxtracker(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
    move: bool = False,
) -> dict[str, Any]:
    """Migrate gpxtracker's GPX files to Tier 2 location.

    Scans all legacy roots for gpx_directory configuration and migrateses GPX files.

    This is a community plugin with filesystem storage needs.

    Args:
        legacy_roots: List of legacy directory paths to scan for gpx_directory config.
        new_home: New home directory.
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration result with success status and details.
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
                with open(legacy_config, "r") as f:
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

    new_gpx_data_dir = new_home / "plugins" / "gpxtracker" / "data"

    if dry_run:
        logger.info(
            "[DRY RUN] Would migrate gpxtracker GPX files from %s to %s",
            old_gpx_dir if old_gpx_dir else "not configured",
            new_home / "plugins" / "gpxtracker" / "data",
        )
        return {
            "success": True,
            "old_path": str(old_gpx_dir) if old_gpx_dir else "not configured",
            "new_path": str(new_gpx_data_dir),
            "dry_run": True,
        }

    new_gpx_data_dir.mkdir(parents=True, exist_ok=True)

    migrated_count = 0

    # Expand ~ if needed
    expanded_old_gpx_dir = Path(old_gpx_dir).expanduser()
    if not expanded_old_gpx_dir.exists():
        logger.info(
            "Old GPX directory not found at expanded path: %s", expanded_old_gpx_dir
        )

    # Copy GPX files
    try:
        for gpx_file in expanded_old_gpx_dir.glob("*.gpx"):
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"{gpx_file.stem}_migrated_{timestamp}.gpx"
            dest_path = new_gpx_data_dir / new_name

            if dest_path.exists() and not force:
                logger.info("Backing up existing GPX file: %s", dest_path)
                backup_path = _backup_file(dest_path)
                try:
                    shutil.copy2(str(dest_path), str(backup_path))
                except (OSError, IOError) as e:
                    logger.warning("Failed to backup GPX file: %s", e)

            try:
                if move:
                    logger.info("Moving GPX file: %s", gpx_file)
                    shutil.move(str(gpx_file), str(dest_path))
                else:
                    logger.info("Copying GPX file: %s", gpx_file)
                    shutil.copy2(str(gpx_file), str(dest_path))
                logger.debug("Migrated GPX file: %s", gpx_file)
                migrated_count += 1
            except (OSError, IOError) as exc:
                logger.exception("Failed to migrate GPX file %s", gpx_file)
    except (OSError, IOError) as exc:
        logger.exception("Failed to migrate gpxtracker GPX files")
        return {
            "success": False,
            "error": str(exc),
            "old_path": str(expanded_old_gpx_dir),
        }

    return {
        "success": True,
        "migrated_count": migrated_count,
        "old_path": str(expanded_old_gpx_dir),
        "new_path": str(new_gpx_data_dir),
        "action": "move" if move else "copy",
    }


def is_migration_needed() -> bool:
    """Check if migration is needed (not already completed)."""
    if _is_migration_completed():
        return False
    paths_info = resolve_all_paths()
    return len(paths_info["legacy_sources"]) > 0


def perform_migration(
    dry_run: bool = False, force: bool = False, move: bool = False
) -> dict[str, Any]:
    """Perform full migration from legacy to v1.3 layout.

    Args:
        dry_run: If True, only report what would be done without making changes.
        force: If True, allow overwriting existing files without backup.
        move: If True, use MOVE operation instead of COPY.

    Returns:
        dict: Migration report with details of all migrations performed.
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
        report["migrations"].append({"type": step_name, "result": result})
        report["completed_steps"] = list(completed_steps)

    def _run_step(step_name: str, func, *args, **kwargs) -> dict[str, Any]:
        result = func(*args, **kwargs)
        _record_step(step_name, result)
        if not result.get("success", True):
            error_detail = (
                result.get("error") or result.get("message") or "Unknown error"
            )
            raise MigrationError(f"{step_name} migration failed: {error_detail}")
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
                    with open(legacy_config, "r") as f:
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
    """Rollback from a failed migration.

    This restores files from backup directories.

    Args:
        completed_steps: Optional ordered list of completed migration steps.

    Returns:
        dict: Rollback result with success status.
    """
    state_path = _get_migration_state_path()
    state = _read_migration_state()

    if completed_steps is None:
        if not state_path.exists():
            return {
                "success": False,
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
