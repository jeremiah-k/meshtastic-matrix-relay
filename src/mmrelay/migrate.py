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

import atexit
import json
import os
import shutil
import signal
import sqlite3
import subprocess  # nosec B404
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mmrelay.constants.app import CREDENTIALS_FILENAME, MATRIX_DIRNAME, STORE_DIRNAME
from mmrelay.log_utils import get_logger
from mmrelay.paths import resolve_all_paths

logger = get_logger("Migration")

# Global reference to current lock file for cleanup on signal
_current_lock_file: Path | None = None


def _looks_like_matrix_credentials(path: Path) -> bool:
    """
    Detects whether a file is a Matrix credentials JSON document.

    Performs strict validation to avoid false positives when scanning legacy locations:
    the file must be a JSON object containing the string keys "homeserver", "access_token",
    and "user_id". The "user_id" must start with "@" and contain a ":".

    Parameters:
        path (Path): Candidate credentials file path.

    Returns:
        true if the file contains valid Matrix credential keys with the expected formats, false otherwise.
    """
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return False

    if not isinstance(payload, dict):
        return False

    required = ("homeserver", "access_token", "user_id")
    for key in required:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return False

    # Keep fallback strict to avoid false positives from unrelated files.
    user_id = payload.get("user_id", "")
    if not user_id.startswith("@") or ":" not in user_id:
        return False

    return True


def _cleanup_lock_file() -> None:
    """
    Remove the migration lock file if present and clear the internal lock reference.

    This function is idempotent and suppresses filesystem errors while attempting removal, so it is safe to call multiple times.
    """
    global _current_lock_file
    if _current_lock_file is not None and _current_lock_file.exists():
        try:
            _current_lock_file.unlink()
            logger.debug("Cleaned up migration lock: %s", _current_lock_file)
        except (OSError, IOError):
            pass
        finally:
            _current_lock_file = None


def _register_lock_cleanup(lock_file: Path) -> None:
    """
    Register cleanup handlers for lock file on normal exit and signals.

    Parameters:
        lock_file: Path to the lock file to clean up
    """
    global _current_lock_file
    _current_lock_file = lock_file

    # Register for normal exit (including sys.exit())
    atexit.register(_cleanup_lock_file)

    # Register for common signals (Unix/Linux)
    if sys.platform != "win32":

        def _signal_handler(signum: int, _frame: object) -> None:
            """
            Clean up the migration lock file, then re-deliver the received signal so the process exits with the default handler.

            Parameters:
                signum (int): The received signal number.
                _frame (object): The current stack frame supplied by the signal handler (unused).
            """
            _cleanup_lock_file()
            # Re-raise the signal with default handler to exit properly
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(sig, _signal_handler)
            except (ValueError, OSError):
                # Signal may not be available or already handled
                pass


def _is_mmrelay_running() -> bool:
    """
    Determine whether an MMRelay process appears to be running on the current host.

    On Unix-like systems this attempts to detect an existing mmrelay process; on Windows the check is skipped and the function returns `False`.

    Returns:
        `True` if an MMRelay process other than the current process appears to be running, `False` otherwise.
    """
    current_pid = os.getpid()

    # Try using pgrep on Unix systems
    if sys.platform != "win32":
        try:
            pgrep_path = shutil.which("pgrep")
            if not pgrep_path:
                return False
            result = subprocess.run(  # nosec B603,B607
                [pgrep_path, "-f", "mmrelay|python.*mmrelay"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                for pid_str in result.stdout.strip().split("\n"):
                    try:
                        pid = int(pid_str)
                        if pid != current_pid:
                            # Check if it's actually mmrelay via /proc (Linux only)
                            proc_cmdline = Path(f"/proc/{pid}/cmdline")
                            if proc_cmdline.exists():
                                try:
                                    with open(proc_cmdline, "rb") as f:
                                        cmdline = f.read().decode(
                                            "utf-8", errors="ignore"
                                        )
                                        if "mmrelay" in cmdline:
                                            return True
                                except (OSError, IOError):
                                    continue
                            else:
                                # On non-Linux (e.g. macOS), pgrep match is our best signal
                                # as /proc doesn't exist.
                                return True
                    except ValueError:
                        continue
        except (OSError, IOError, subprocess.TimeoutExpired, FileNotFoundError):
            pass
    else:
        # Windows: cannot reliably distinguish mmrelay from other Python processes
        logger.debug(
            "Running-instance detection not supported on Windows; skipping check"
        )
        return False

    return False


def _get_db_base_path(path: Path) -> Path:
    """Strip SQLite sidecar suffixes to get the main database file path."""
    name = path.name
    for suffix in ("-wal", "-shm", "-journal"):
        if name.endswith(suffix):
            return path.with_name(name.removesuffix(suffix))
    return path


class MigrationError(Exception):
    """Migration-specific error."""

    step: str = ""

    @classmethod
    def integrity_check_failed(cls, detail: str) -> "MigrationError":
        """Create error for database integrity check failure."""
        return cls(f"Database integrity check failed: {detail}")

    @classmethod
    def verification_failed(cls, detail: str) -> "MigrationError":
        """
        Create a MigrationError representing a database verification failure.

        Parameters:
            detail (str): Human-readable description of the verification failure.

        Returns:
            MigrationError: Error instance whose message is "Database verification failed: {detail}".
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
        exc.step = step
        return exc


class StagingPathMissingError(OSError):
    """Raised when a staging path is missing during finalize."""

    def __init__(self, path: Path) -> None:
        """
        Initialize the exception indicating an expected staging path is missing.

        Parameters:
            path (Path): The expected staging path that was not found; used to construct the exception message.
        """
        super().__init__(f"Staging path does not exist: {path}")


def _path_is_within_home(path: Path, home: Path) -> bool:
    """
    Check whether `path` is the same as or located inside the `home` directory.

    Performs resolution to absolute paths and falls back to non-resolving absolute paths if resolution fails.

    Parameters:
        path (Path): Path to test.
        home (Path): Directory to treat as the home/root.

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
        Record a discovered legacy artifact by type and path if it has not been recorded before.

        Appends an entry to the module-level `findings` list and adds the path string to the module-level `seen_paths` set. If the given path has already been recorded, the function does nothing.

        Parameters:
            item_type (str): Category label for the finding (e.g., "credentials", "logs").
            path (Path): Filesystem path to the discovered artifact.
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


def _warn_multiple_sources(
    legacy_roots: list[Path],
    artifact_name: str,
    artifact_filename: str,
    check_dir_entries: bool = False,
) -> None:
    """
    Warn if multiple legacy roots contain the same artifact.

    Parameters:
        legacy_roots: List of legacy root directories to check
        artifact_name: Human-readable name of the artifact (e.g., "credentials")
        artifact_filename: Filename to check for (e.g., "credentials.json")
        check_dir_entries: If True, also check if directory has entries
    """
    found_in: list[Path] = []
    for root in legacy_roots:
        candidate = root / artifact_filename
        try:
            if candidate.exists():
                if check_dir_entries and candidate.is_dir():
                    if _dir_has_entries(candidate):
                        found_in.append(root)
                else:
                    found_in.append(root)
        except (OSError, IOError):
            continue

    if len(found_in) > 1:
        logger.warning(
            "Multiple %s files found across legacy roots: %s. "
            "Using: %s. Other locations will be ignored.",
            artifact_name,
            [str(r) for r in found_in],
            found_in[0],
        )


def verify_migration() -> dict[str, Any]:
    """
    Builds a verification report describing MMRelay runtime artifacts, discovered legacy data, and whether migration or cleanup is required.

    The report inspects resolved runtime paths (credentials, database, logs, plugins, E2EE store), records whether each artifact exists and is located inside MMRELAY_HOME, scans configured legacy source roots and the HOME directory for legacy layout items, and collects warnings and errors indicating migration readiness or conflicts.

    Returns:
        dict[str, Any]: Verification report with keys:
            - home: str path to MMRELAY_HOME
            - artifacts: list[dict] status entries with `key`, `label`, `path` (or None), `exists`, `inside_home`, `not_applicable`
            - legacy_data: list[dict] legacy source findings, each with `root` and `items` (each item has `type` and `path`)
            - credentials_missing: bool, True if credentials.json is absent from MMRELAY_HOME
            - legacy_data_found: bool, True if any legacy artifacts were discovered outside HOME
            - split_roots: bool, True if data exists both in HOME and in legacy locations
            - cleanup_needed: list[str] legacy paths that can be removed because new locations contain data
            - migration_needed: list[str] legacy paths that should be migrated because new locations are missing/empty
            - warnings: list[str] non-fatal observations
            - errors: list[str] detected issues preventing a clean migration
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

    # Build a lookup of which new artifacts have actual content
    # For directories, we check if they have entries (not just exist)
    # For files, we check if they exist
    artifact_has_content: dict[str, bool] = {}
    for artifact in artifact_statuses:
        key = artifact["key"]
        path_str = artifact["path"]
        if path_str is None:
            artifact_has_content[key] = False
            continue

        path = Path(path_str)
        # For directory-type artifacts, check if they have entries
        if key in ("logs", "plugins", "e2ee_store"):
            artifact_has_content[key] = _dir_has_entries(path)
        else:
            # For file-type artifacts (credentials, database), check existence
            artifact_has_content[key] = path.exists()

    warnings: list[str] = []
    cleanup_needed: list[str] = (
        []
    )  # Legacy items where new artifact already has content
    migration_needed: list[str] = []  # Legacy items where new artifact is missing/empty

    for root in legacy_findings:
        for item in root["items"]:
            item_type = item["type"]
            item_path = item["path"]

            # Map legacy item types to artifact keys
            type_to_key = {
                "credentials": "credentials",
                "e2ee_store": "e2ee_store",
                "database": "database",
                "logs": "logs",
                "plugins": "plugins",
            }

            artifact_key = type_to_key.get(item_type)
            new_artifact_has_content = (
                artifact_has_content.get(artifact_key, False) if artifact_key else False
            )

            # Determine if this is cleanup (new location has data) or migration needed
            if item_type == "e2ee_store":
                if new_artifact_has_content:
                    cleanup_needed.append(item_path)
                    warnings.append(
                        f"Legacy E2EE store at {item_path} can be removed (new location has data)"
                    )
                else:
                    migration_needed.append(item_path)
                    warnings.append(f"Your E2EE store is still in {item_path}")
            else:
                if new_artifact_has_content:
                    cleanup_needed.append(item_path)
                    warnings.append(
                        f"Legacy {item_type} at {item_path} can be removed (new location has data)"
                    )
                else:
                    migration_needed.append(item_path)
                    warnings.append(f"Found legacy data at {item_path}")

    errors: list[str] = []
    if credentials_missing:
        errors.append("Missing credentials.json in MMRELAY_HOME")

    # Only report migration error if there's legacy data that actually needs migration
    # (i.e., the new artifact doesn't exist yet)
    if migration_needed:
        errors.append("Legacy data exists and migration is still required")
    elif cleanup_needed and not migration_needed:
        # All legacy items have corresponding new artifacts - this is cleanup, not migration failure
        # Don't treat as error, just informational
        pass

    if split_roots and migration_needed:
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
        "cleanup_needed": cleanup_needed,
        "migration_needed": migration_needed,
        "warnings": warnings,
        "errors": errors,
        "ok": len(errors) == 0,
    }


def print_migration_verification(report: dict[str, Any]) -> None:
    """
    Prints a concise, human-readable summary of a migration verification report.

    Parameters:
        report (dict): Verification report containing:
            - home (str | Path): Resolved MMRELAY_HOME path.
            - artifacts (list[dict]): Runtime artifacts; each dict contains:
                - label (str): Human-facing name of the artifact.
                - path (str | Path): Resolved filesystem path for the artifact.
                - exists (bool): Whether the artifact exists at `path`.
                - inside_home (bool): Whether `path` is located inside `home`.
                - not_applicable (bool): True when the artifact is irrelevant on the platform.
            - legacy_data (list[dict]): Legacy scan results; each dict contains:
                - root (str | Path): Legacy root path that was scanned.
                - items (list[dict]): Found legacy items, each with:
                    - type (str): Artifact type (e.g., "credentials", "database", "logs").
                    - path (str | Path): Location of the legacy item.
            - ok (bool): Overall verification status; True when no blocking issues were found.
            - errors (list[str]): Verification error messages present when `ok` is False.
            - cleanup_needed (list[str], optional): Legacy paths that can be removed when migration is not required.

    Notes:
        The function writes a human-oriented summary to standard output; it does not return a value.
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

    # Check for cleanup-only scenario (legacy exists but new artifact also exists)
    cleanup_needed = report.get("cleanup_needed", [])

    if report["ok"]:
        print("\n✅ Migration verification PASSED")
        if cleanup_needed:
            print("\n🧹 Cleanup Suggestions:")
            print("   The following legacy items can be safely removed:")
            for item_path in cleanup_needed:
                print(f"     - {item_path}")
            print("   (New location already has data)")
    else:
        print("\n❌ Migration verification FAILED")
        for error in report["errors"]:
            print(f"   - {error}")


STAGING_DIRNAME = ".migration_staging"
BACKUP_DIRNAME = ".migration_backups"
LOCK_FILENAME = ".migration.lock"

# Minimum free space required for migration (in bytes)
# Allowing for staging + backups with 50% safety margin
MIN_FREE_SPACE_BYTES = 500 * 1024 * 1024  # 500 MB minimum


def _get_staging_path(new_home: Path, unit_name: str) -> Path:
    """Get the staging path for a migration unit."""
    return new_home / STAGING_DIRNAME / unit_name


def _backup_file(src_path: Path, suffix: str = ".bak") -> Path:
    """
    Constructs a timestamped backup path for src_path inside a dedicated `.migration_backups`
    directory (the directory is created if missing) and returns that path.

    Parameters:
        src_path (Path): Original file or directory path to back up; only used to derive the backup name and parent directory.
        suffix (str): Suffix inserted after the original filename and before the timestamp (default: ".bak").

    Returns:
        Path: Path under `<src_path.parent>/.migration_backups/` with the format `<original_name><suffix>.<YYYYMMDD_HHMMSS>`.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = src_path.parent / BACKUP_DIRNAME
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_name = f"{src_path.name}{suffix}.{timestamp}"
    return backup_dir / backup_name


def _check_disk_space(
    path: Path, required_bytes: int | None = None
) -> tuple[bool, int]:
    """
    Determine whether the filesystem containing the given path has at least the required free space.

    Parameters:
        path (Path): File or directory path to check. If the path is a file or does not exist, its parent directory is used.
        required_bytes (int | None): Minimum required bytes. If omitted, defaults to MIN_FREE_SPACE_BYTES.

    Returns:
        tuple[bool, int]: Tuple of (has_sufficient_space, free_bytes_available).
            `has_sufficient_space` is `True` when the free space is greater than or equal to `required_bytes` multiplied by a 1.5 safety margin, `False` otherwise.
            If disk usage cannot be determined, `(True, 0)` is returned.
    """
    if required_bytes is None:
        required_bytes = MIN_FREE_SPACE_BYTES

    # Use parent directory if path is a file or doesn't exist
    check_path = path
    if not check_path.exists() or check_path.is_file():
        check_path = check_path.parent

    try:
        usage = shutil.disk_usage(str(check_path))
        # Add 50% safety margin
        required_with_margin = int(required_bytes * 1.5)
    except (OSError, IOError):
        # If we can't check disk space, assume it's OK and log warning
        logger.warning("Could not check disk space at %s", check_path)
        return (True, 0)
    else:
        return (usage.free >= required_with_margin, usage.free)


def _finalize_move(staging_path: Path, dest_path: Path) -> None:
    """
    Finalize a staged unit move into its final destination.

    Moves the staged file or directory at `staging_path` into `dest_path`. For files this uses an atomic replace when supported; for directories this performs a best-effort move (non-atomic) and replaces any existing destination. Callers should create backups before invoking this function.

    Parameters:
        staging_path (Path): Path where the artifact was staged.
        dest_path (Path): Final destination path for the artifact.

    Raises:
        StagingPathMissingError: If `staging_path` does not exist.
        OSError: If the filesystem operation fails during finalization.
    """
    if not staging_path.exists():
        raise StagingPathMissingError(staging_path)

    # Ensure parent of destination exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic replace for single files (on POSIX).
    # For directories: use best-effort approach (directory moves are not atomic).
    if staging_path.is_file():
        # For single files, use atomic replace (POSIX).
        # If destination exists as a different type (e.g., directory), remove it first
        if dest_path.exists() and dest_path.is_dir():
            shutil.rmtree(str(dest_path))
        staging_path.replace(dest_path)
    else:
        # For directories: use non-atomic approach
        # IMPORTANT: Backups should already be created by the caller before
        # calling _finalize_move. This is best-effort - true atomic directory
        # moves are not supported by most filesystems.
        if dest_path.exists():
            if dest_path.is_file():
                dest_path.unlink()
            else:
                shutil.rmtree(str(dest_path))
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
        Get the last modification time of a filesystem path.

        Parameters:
            path (Path): Path to check.

        Returns:
            float: Modification time in seconds since the epoch; `0.0` if the path cannot be accessed.
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

    Scans the provided legacy roots (and the target HOME) for an existing credentials.json,
    optionally falls back to ~/credentials.json if it appears to be valid Matrix credentials,
    and moves the first valid legacy file into new_home/matrix/credentials.json using a staging
    area and a timestamped backup of any existing destination. Supports dry-run and force-overwrite.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy credentials files in priority order.
        new_home (Path): Destination home directory where matrix/credentials.json will be placed.
        dry_run (bool): If True, report the intended action without modifying files.
        force (bool): If True, allow overwriting an existing destination (a backup is always created).

    Returns:
        dict: Structured migration result containing keys such as `success`, `action`, `old_path`, `new_path`,
              and optional `message` or `dry_run`.
    """
    # Warn if credentials exist in multiple legacy roots
    _warn_multiple_sources(legacy_roots, "credentials", CREDENTIALS_FILENAME)

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
                    "action": "already_at_target",
                    "message": "Credentials already at target location",
                }
            continue
        if candidate.exists():
            old_creds = candidate
            logger.info("Found credentials.json in legacy root: %s", old_creds)
            break

    # Backwards-compatibility fallback:
    # Some older flows could leave credentials at ~/credentials.json.
    # Only migrate this fallback location if it appears to be valid Matrix credentials.
    if not old_creds:
        home_root_candidate = Path.home() / CREDENTIALS_FILENAME
        if home_root_candidate.exists():
            if _looks_like_matrix_credentials(home_root_candidate):
                old_creds = home_root_candidate
                logger.warning(
                    "Found legacy credentials at home root (%s). "
                    "Migrating for backwards compatibility.",
                    old_creds,
                )
            else:
                logger.warning(
                    "Ignoring home-root credentials candidate %s: "
                    "missing required Matrix credential keys.",
                    home_root_candidate,
                )

    if not old_creds:
        if new_creds.exists():
            logger.info("Credentials already migrated to %s", new_creds)
            return {
                "success": True,
                "new_path": str(new_creds),
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No credentials file found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
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
    migration_succeeded = False

    try:
        staging_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Backup destination if it exists (ALWAYS)
        if new_creds.exists():
            backup_path = _backup_file(new_creds)
            logger.info("Backing up existing destination to %s", backup_path)
            if new_creds.is_dir():
                shutil.copytree(str(new_creds), str(backup_path))
            else:
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
        migration_succeeded = True
        logger.info("Migrated credentials to %s", new_creds)

        return {
            "success": True,
            "old_path": str(old_creds),
            "new_path": str(new_creds),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during credentials migration from %s", old_creds
        )
        raise MigrationError.step_failed(
            "credentials",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate credentials from %s", old_creds)
        raise MigrationError.step_failed("credentials", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_path.exists():
            staging_path.unlink(missing_ok=True)


def migrate_config(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Locate and migrate the first legacy config.yaml into the new home directory.

    Parameters:
        legacy_roots (list[Path]): Directories to search for a legacy `config.yaml`.
        new_home (Path): Destination home directory where `config.yaml` should be placed.
        dry_run (bool): If True, report the intended action without modifying the filesystem.
        force (bool): If True, overwrite an existing destination (a backup is always created before overwrite).

    Returns:
        dict: Migration result summary containing at least a `success` boolean and an `action` string.
            Optional keys include:
                - `old_path` (str): Source path of the migrated config (when applicable).
                - `new_path` (str): Destination path for the config (when applicable).
                - `dry_run` (bool): Present and True for dry-run responses.
                - `message` (str): Human-readable status message.

    Raises:
        MigrationError: If migration fails (permission errors or other failures are wrapped in a MigrationError).
    """
    # Warn if config exists in multiple legacy roots
    _warn_multiple_sources(legacy_roots, "config", "config.yaml")

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
                    "action": "already_at_target",
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
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No config.yaml found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
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
    migration_succeeded = False

    try:
        staging_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Backup destination if it exists (ALWAYS)
        if new_config.exists():
            backup_path = _backup_file(new_config)
            logger.info("Backing up existing destination to %s", backup_path)
            if new_config.is_dir():
                shutil.copytree(str(new_config), str(backup_path))
            else:
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
        migration_succeeded = True
        logger.info("Migrated config to %s", new_config)

        return {
            "success": True,
            "old_path": str(old_config),
            "new_path": str(new_config),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during config migration from %s", old_config
        )
        raise MigrationError.step_failed(
            "config",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate config from %s", old_config)
        raise MigrationError.step_failed("config", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_path.exists():
            staging_path.unlink(missing_ok=True)


def migrate_database(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate the Meshtastic SQLite database from detected legacy locations into the new home's database directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy database files.
        new_home (Path): Destination MMRELAY home directory where the database directory will be created.
        dry_run (bool): If True, report planned actions without modifying the filesystem.
        force (bool): If True, overwrite existing destination files (backups are created before overwrites).

    Returns:
        dict: A migration result containing at least the `success` boolean and an `action` code.
            Common keys:
            - success (bool): `True` when migration completed or nothing needed to be done, `False` on failure.
            - action (str): One of `"move"`, `"already_at_target"`, `"already_migrated"`, `"not_found"`, or `"skip_force_required"`.
            - old_path (str, optional): Path of the legacy database chosen for migration.
            - new_path (str, optional): Destination database directory path.
            - message (str, optional): Human-readable status or error message.
            - dry_run (bool, optional): Present and `True` when no changes were made due to a dry run.
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
                    "action": "already_at_target",
                    "message": "Database already at target location",
                }
            continue
        if legacy_db.exists():
            candidates.append(legacy_db)
            for suffix in ["-wal", "-shm", "-journal"]:
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
                for suffix in ["-wal", "-shm", "-journal"]:
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
                        "action": "already_at_target",
                        "message": "Database already at target location",
                    }
                continue
            if legacy_db.exists():
                candidates.append(legacy_db)
                for suffix in ["-wal", "-shm", "-journal"]:
                    sidecar = legacy_db.with_suffix(f".sqlite{suffix}")
                    if sidecar.exists():
                        candidates.append(sidecar)

    if not candidates:
        if (new_db_dir / "meshtastic.sqlite").exists():
            logger.info("Database already migrated to %s", new_db_dir)
            return {
                "success": True,
                "new_path": str(new_db_dir),
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No database files found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
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

    migration_succeeded = False

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Backup existing database files (ALWAYS)
        for db_path in selected_group:
            dest = new_db_dir / db_path.name
            # Skip if already at target to avoid self-backup/self-copy
            if db_path.resolve() == dest.resolve():
                continue
            if dest.exists():
                backup_path = _backup_file(dest)
                logger.info(
                    "Backing up existing database file %s to %s", dest, backup_path
                )
                if dest.is_dir():
                    shutil.copytree(str(dest), str(backup_path))
                else:
                    shutil.copy2(str(dest), str(backup_path))

        # 2. Copy to staging (using copy-verify-delete pattern)
        for db_path in selected_group:
            dest = new_db_dir / db_path.name
            staged = staging_dir / db_path.name
            # Skip if already at target to avoid self-copy
            if db_path.resolve() == dest.resolve():
                continue
            logger.debug("Staging database file %s at %s", db_path, staged)
            shutil.copy2(str(db_path), str(staged))

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
            # Skip if already at target to avoid self-overwrite
            if db_path.resolve() == dest.resolve():
                continue
            if dest.exists():
                dest.unlink()
            shutil.move(str(staged), str(dest))

        # 5. Delete sources after successful move
        for db_path in selected_group:
            dest = new_db_dir / db_path.name
            # Skip if already at target to avoid self-deletion
            if db_path.resolve() == dest.resolve():
                continue
            try:
                db_path.unlink()
                logger.info("Deleted source file after successful move: %s", db_path)
            except (OSError, IOError):
                logger.warning("Failed to delete source file: %s", db_path)

        migration_succeeded = True
        return {
            "success": True,
            "old_path": str(most_recent),
            "new_path": str(new_db_dir),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during database migration from %s", most_recent
        )
        raise MigrationError.step_failed(
            "database",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate database from %s", most_recent)
        raise MigrationError.step_failed("database", str(exc)) from exc
    finally:
        if migration_succeeded and staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def migrate_logs(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate log files from the first discovered legacy "logs" directory into the new home's "logs" directory.

    Searches the provided legacy roots for a "logs" directory, stages log files with timestamped names to avoid collisions, backs up any existing destination logs, moves staged files into new_home/logs, and removes the legacy directory if it becomes empty. Honors dry-run and force flags.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for a legacy "logs" directory.
        new_home (Path): Destination MMRELAY_HOME where logs should be placed.
        dry_run (bool): If True, report intended actions without performing them.
        force (bool): If True, overwrite existing destination files/directories.

    Returns:
        dict: Result of the migration containing keys such as:
            - success (bool): Whether the step completed successfully.
            - action (str): One of "move", "already_at_target", "already_migrated", "not_found", or "skip_force_required".
            - old_path (str, optional): Path of the legacy logs directory when applicable.
            - new_path (str, optional): Destination logs directory.
            - migrated_count (int, optional): Number of log files moved.
            - dry_run (bool, optional): Present and true for dry-run results.
            - message (str, optional): Human-readable status when applicable.
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
                    "action": "already_at_target",
                    "message": "Logs already at target location",
                }

    if not old_logs_dir or not old_logs_dir.exists():
        if _dir_has_entries(new_logs_dir):
            logger.info("Logs already migrated to %s", new_logs_dir)
            return {
                "success": True,
                "new_path": str(new_logs_dir),
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No logs directory found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
            "message": "Logs already exist at destination",
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
    migration_succeeded = False

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Backup existing destination (ALWAYS)
        if new_logs_dir.exists():
            backup_path = _backup_file(new_logs_dir)
            logger.info("Backing up existing logs directory to %s", backup_path)
            shutil.copytree(str(new_logs_dir), str(backup_path))

        # 2. Move files to staging with timestamped names
        migrated_count = 0
        for idx, log_file in enumerate(old_logs_dir.glob("*.log")):
            # Include microseconds and index to avoid filename collisions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            new_name = f"{log_file.stem}_migrated_{timestamp}_{idx}.log"
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

        migration_succeeded = True
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
    except PermissionError as exc:
        logger.exception(
            "Permission denied during logs migration from %s", old_logs_dir
        )
        raise MigrationError.step_failed(
            "logs",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate logs from %s", old_logs_dir)
        raise MigrationError.step_failed("logs", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_dir.exists():
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
                    "action": "already_at_target",
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
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No E2EE store directory found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
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
    migration_succeeded = False

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
        migration_succeeded = True
        logger.info("Migrated E2EE store to %s", new_store_dir)

        return {
            "success": True,
            "old_path": str(old_store_dir),
            "new_path": str(new_store_dir),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during E2EE store migration from %s", old_store_dir
        )
        raise MigrationError.step_failed(
            "store",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate E2EE store from %s", old_store_dir)
        raise MigrationError.step_failed("store", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_dir.exists():
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
                if not force:
                    logger.warning(
                        "Staging collision for %s plugin %s; use --force to overwrite",
                        tier_name,
                        dest,
                    )
                    errors.append(f"{tier_name} staged {dest}: exists")
                    continue
                # Only remove if force is True
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
    Migrate plugin tiers from legacy `plugins` directories into the new home `plugins` layout.

    Searches the provided legacy roots for a `plugins` directory, stages the `custom` and `community`
    tiers (if present), creates backups of existing destinations and the original legacy directory,
    and atomically moves staged content into place. Honors dry-run and force semantics.

    Parameters:
        legacy_roots (list[Path]): Legacy root directories to scan for a `plugins` directory.
        new_home (Path): Destination MMRELAY_HOME root where `plugins` will be created.
        dry_run (bool): If True, report intended actions without modifying the filesystem.
        force (bool): If True, allow overwriting existing destinations.

    Returns:
        dict: Migration result containing keys such as:
            - "success" (bool): Whether the step completed without error.
            - "action" (str): One of "move", "already_at_target", "already_migrated",
              "not_found", or "skip_force_required".
            - "old_path" (str, optional): Source legacy plugins directory, if applicable.
            - "new_path" (str, optional): Destination plugins directory.
            - "migrated_types" (list[str], optional): Plugin tiers migrated (e.g., ["custom","community"]).
            - "dry_run" (bool, optional): Present and True when dry_run was used.
    """
    # Warn if plugins exist in multiple legacy roots
    _warn_multiple_sources(legacy_roots, "plugins", "plugins", check_dir_entries=True)

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
                    "action": "already_at_target",
                    "message": "Plugins already at target location",
                }

    if not old_plugins_dir or not old_plugins_dir.exists():
        if _dir_has_entries(new_plugins_dir):
            logger.info("Plugins already migrated to %s", new_plugins_dir)
            return {
                "success": True,
                "new_path": str(new_plugins_dir),
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info("No plugins directory found in legacy locations")
        return {
            "success": True,
            "action": "not_found",
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
            "action": "skip_force_required",
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
    migration_succeeded = False

    def _raise_plugin_stage_errors(stage_errors: list[str]) -> None:
        """
        Raise an OSError when one or more plugin staging operations failed.

        Parameters:
                stage_errors (list[str]): List of error messages collected during plugin staging; messages are joined with "; " to form the OSError message.

        Raises:
                OSError: Contains the concatenated staging error messages.
        """
        raise OSError("; ".join(stage_errors))

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

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
            _raise_plugin_stage_errors(errors)

        # 3. Finalize: move staging to final destination
        _finalize_move(staging_dir, new_plugins_dir)
        migration_succeeded = True
        logger.info("Migrated plugins to %s", new_plugins_dir)

        # Move the old plugin directory to a backup location to keep the filesystem
        # clean while preserving the ability to roll back if needed. This avoids
        # leaving empty or partially-empty directories around after migration.
        if old_plugins_dir.exists():
            try:
                backup_path = _backup_file(old_plugins_dir, suffix="_pre_migration")
                shutil.move(str(old_plugins_dir), str(backup_path))
                logger.info(
                    "Moved old plugin directory to backup location: %s", backup_path
                )
            except (OSError, IOError) as e:
                logger.warning(
                    "Could not move old plugin directory to backup: %s. "
                    "You may manually remove it after confirming migration success.",
                    e,
                )

        return {
            "success": True,
            "migrated_types": migrated_types,
            "old_path": str(old_plugins_dir),
            "new_path": str(new_plugins_dir),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during plugins migration from %s", old_plugins_dir
        )
        raise MigrationError.step_failed(
            "plugins",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate plugins from %s", old_plugins_dir)
        raise MigrationError.step_failed("plugins", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def migrate_gpxtracker(
    legacy_roots: list[Path],
    new_home: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """
    Migrate GPX files used by the community gpxtracker plugin into the new plugins/community/gpxtracker/data directory.

    Parameters:
        legacy_roots (list[Path]): Directories to scan for legacy config.yaml entries to locate a configured `gpx_directory`.
        new_home (Path): Destination MMRELAY_HOME root where plugin data should be placed.
        dry_run (bool): If True, report the actions that would be taken without making any changes.
        force (bool): If True, allow overwriting existing destination files (existing destination is always backed up before overwrite).

    Returns:
        dict: Summary of the migration outcome. Common keys include:
            - `success` (bool): Whether the migration step completed successfully.
            - `action` (str): One of `'move'`, `'not_found'`, `'already_migrated'`, or `'already_at_target'`.
            - `migrated_count` (int): Number of GPX files moved (present when `action` is `'move'`).
            - `old_path` (str): Source GPX directory path when applicable.
            - `new_path` (str): Destination GPX data directory path.
            - `dry_run` (bool): Present and true when invoked in dry-run mode.
            - `message` (str): Human-readable status message when applicable.

    Raises:
        MigrationError: When migration fails (wrapped as a step failure with context).
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
                        "action": "already_at_target",
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
                "action": "already_migrated",
                "message": "Already migrated",
            }
        logger.info(
            "gpxtracker plugin not configured with gpx_directory or directory not found, skipping"
        )
        return {
            "success": True,
            "action": "not_found",
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
    migration_succeeded = False

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        # 1. Backup entire destination directory if it exists (ALWAYS)
        # This preserves non-.gpx files (README, metadata, subdirectories)
        if new_gpx_data_dir.exists() and any(new_gpx_data_dir.iterdir()):
            backup_path = _backup_file(new_gpx_data_dir)
            logger.info(
                "Backing up entire gpxtracker data directory to %s", backup_path
            )
            shutil.copytree(str(new_gpx_data_dir), str(backup_path))

        migrated_count = 0

        # 2. Stage GPX files with timestamped names
        for idx, gpx_file in enumerate(old_gpx_dir.glob("*.gpx")):
            # Include microseconds and index to avoid filename collisions
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            new_name = f"{gpx_file.stem}_migrated_{timestamp}_{idx}.gpx"
            dest_staged = staging_dir / new_name
            shutil.move(str(gpx_file), str(dest_staged))
            migrated_count += 1

        if migrated_count > 0:
            # 3. Finalize: move staging to final data dir
            new_gpx_data_dir.mkdir(parents=True, exist_ok=True)
            for staged_file in staging_dir.iterdir():
                final_dest = new_gpx_data_dir / staged_file.name

                # Remove existing file if present (backup already created above)
                if final_dest.exists():
                    final_dest.unlink()

                shutil.move(str(staged_file), str(final_dest))

        migration_succeeded = True
        logger.info("Migrated %d GPX files to %s", migrated_count, new_gpx_data_dir)

        return {
            "success": True,
            "migrated_count": migrated_count,
            "old_path": str(old_gpx_dir),
            "new_path": str(new_gpx_data_dir),
            "action": "move",
        }
    except PermissionError as exc:
        logger.exception(
            "Permission denied during gpxtracker migration from %s", old_gpx_dir
        )
        raise MigrationError.step_failed(
            "gpxtracker",
            f"Permission denied. Check file permissions for {exc.filename}: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to migrate gpxtracker GPX files from %s", old_gpx_dir)
        raise MigrationError.step_failed("gpxtracker", str(exc)) from exc
    finally:
        # Only remove staging if migration succeeded to preserve data on failure
        if migration_succeeded and staging_dir.exists():
            shutil.rmtree(str(staging_dir), ignore_errors=True)


def is_migration_needed() -> bool:
    """
    Check whether any legacy MMRelay data exists that should be migrated into the current HOME structure.

    Returns:
        True if legacy data that requires migration is present, False otherwise.
    """
    report = verify_migration()
    return bool(report.get("legacy_data_found", False))


def perform_migration(dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    """
    Orchestrates the end-to-end migration of legacy MMRelay data into the v1.3 home layout.

    Runs each per-artifact migrator in the defined order. Supports a dry-run mode that reports intended actions without mutating the filesystem.

    Parameters:
        dry_run (bool): If True, simulate the migration and report actions without making changes.
        force (bool): Controls whether existing destination entries are overwritten (force=True) or skipped (force=False). Backups are always created before any overwrite regardless of this flag.

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

    # Check if MMRelay is running (best-effort detection)
    if not dry_run:
        if _is_mmrelay_running():
            logger.warning(
                "MMRelay appears to be running. Migration while the application is active "
                "may cause file corruption or inconsistent state. "
                "Please stop MMRelay before running migration."
            )
            report["success"] = False
            report["error"] = "MMRelay is running"
            report["message"] = (
                "MMRelay appears to be running. Please stop the application before migration "
                "to prevent data corruption. If this is a false positive, use --force to proceed."
            )
            if not force:
                return report
            logger.warning(
                "Proceeding with migration despite running instance (--force)"
            )

    # Check disk space before starting migration
    if not dry_run:
        has_space, free_bytes = _check_disk_space(new_home)
        if not has_space:
            free_mb = free_bytes / (1024 * 1024)
            min_mb = MIN_FREE_SPACE_BYTES / (1024 * 1024)
            report["success"] = False
            report["error"] = "Insufficient disk space"
            report["message"] = (
                f"Insufficient disk space for migration. "
                f"Available: {free_mb:.0f} MB, Required: {min_mb:.0f} MB (with safety margin). "
                f"Free up disk space and try again."
            )
            return report
        logger.debug(
            "Disk space check passed: %d MB available",
            free_bytes // (1024 * 1024),
        )

    # Get new home directory
    try:
        if not dry_run:
            new_home.mkdir(parents=True, exist_ok=True)
    except (OSError, IOError) as e:
        report["success"] = False
        report["error"] = str(e)
        report["message"] = f"Failed to create new home directory: {e}"
        return report

    # Acquire migration lock to prevent concurrent migrations
    lock_file = new_home / LOCK_FILENAME
    if not dry_run:
        try:
            lock_file.touch(exist_ok=False)
            logger.debug("Acquired migration lock: %s", lock_file)
            # Register cleanup handlers for lock file
            _register_lock_cleanup(lock_file)
        except FileExistsError:
            report["success"] = False
            report["error"] = "Migration lock file exists"
            report["message"] = (
                f"Another migration appears to be in progress (lock file: {lock_file}). "
                "If you're certain no other migration is running, remove the lock file and try again."
            )
            return report
        except (OSError, IOError) as e:
            logger.warning("Could not create migration lock file: %s", e)
            # Continue anyway - lock is a safety feature, not a hard requirement

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
        try:
            result = func(*args, **kwargs)
        except Exception as exc:
            # Record the failure before re-raising so it appears in the report
            failure_result = {"success": False, "error": str(exc)}
            _record_step(step_name, failure_result)
            raise
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
        staged_note = (
            f" Staged data may be present in: {staging_dir}"
            if staging_dir.exists()
            else ""
        )
        # Extract step name before logger.exception to satisfy TRY401
        failed_step = getattr(exc, "step", "unknown")
        logger.exception(
            "Migration failed during step: %s.%s",
            failed_step,
            staged_note,
        )

        # Attempt automatic rollback if steps were completed
        if completed_steps and not dry_run:
            logger.info("Attempting automatic rollback of completed steps...")
            try:
                rollback_report = rollback_migration(
                    completed_steps=completed_steps,
                    migrations=report["migrations"],
                    new_home=new_home,
                    _legacy_roots=legacy_roots,
                )
                report["rollback"] = rollback_report
                if rollback_report["success"]:
                    logger.info("Automatic rollback completed successfully")
                else:
                    logger.warning(
                        "Automatic rollback completed with errors: %s",
                        rollback_report["errors"],
                    )
            except Exception as rollback_exc:
                logger.exception("Automatic rollback failed")
                report["rollback_error"] = str(rollback_exc)
        else:
            logger.warning("Please resolve the issue and re-run migration.")

        # Release migration lock on failure
        if not dry_run and lock_file.exists():
            try:
                lock_file.unlink()
                logger.debug("Released migration lock after failure: %s", lock_file)
            except (OSError, IOError) as e:
                logger.warning("Could not remove migration lock file: %s", e)

        return report

    logger.info(
        "Migration complete. Summary: %d migrations performed",
        len(report["migrations"]),
    )

    # Release migration lock
    if not dry_run and lock_file.exists():
        try:
            lock_file.unlink()
            logger.debug("Released migration lock: %s", lock_file)
        except (OSError, IOError) as e:
            logger.warning("Could not remove migration lock file: %s", e)

    return report


def rollback_migration(
    completed_steps: list[str],
    migrations: list[dict[str, Any]],
    new_home: Path,
    _legacy_roots: list[Path] | None = None,
) -> dict[str, Any]:
    """
    Restore completed migration steps by locating and restoring backups from the .migration_backups directory.

    Restores files or directories that were backed up before being overwritten during migration; if no backup exists for a step the function preserves the destination to avoid data loss. Cleans up the staging directory on successful rollback but does not remove backups.

    Parameters:
        completed_steps (list[str]): Names of steps that completed before the failure, processed in reverse order.
        migrations (list[dict[str, Any]]): Migration records containing per-step results (expected keys include "type" and "result" with "new_path" and "action").
        new_home (Path): Path to the MMRELAY_HOME where backups and staging reside.
        _legacy_roots (list[Path] | None): Unused; kept for API compatibility.

    Returns:
        dict: Rollback report containing:
            - "success" (bool): True if all attempted restores succeeded, False if any failed.
            - "timestamp" (str): ISO8601 timestamp of the rollback attempt.
            - "rolled_back_steps" (list): Entries for each step restored or skipped (includes restored_from/restored_to or skip reason).
            - "errors" (list): Any errors encountered during rollback.
    """
    rollback_report: dict[str, Any] = {
        "success": True,
        "timestamp": datetime.now().isoformat(),
        "rolled_back_steps": [],
        "errors": [],
    }

    logger.info("Starting automatic rollback of migration steps")

    def find_backup_for_step(_step_name: str, dest_path: Path) -> Path | None:
        """
        Locate the most recent backup for the given destination path.

        Searches for files or directories whose names begin with dest_path.name inside
        dest_path.parent/".migration_backups" and then falls back to new_home/".migration_backups".
        Returns the newest matching entry by modification time, or None if no backup is found.

        Parameters:
            _step_name (str): Step identifier (not used for lookup; provided for caller context).
            dest_path (Path): Destination path whose backups are being searched.

        Returns:
            Path | None: Path to the most recent matching backup, or `None` if none exists.
        """
        # Backup names follow pattern: <name>.bak.<timestamp> or <name>_pre_migration.<timestamp>
        # The backup is named after the destination that was backed up
        dest_name = dest_path.name

        # Search in order: destination's parent backup dir, then home backup dir
        backup_dirs = [
            dest_path.parent
            / BACKUP_DIRNAME,  # Primary: where _backup_file creates them
            new_home / BACKUP_DIRNAME,  # Fallback: for top-level files
        ]

        for backup_dir in backup_dirs:
            if not backup_dir.exists():
                continue

            # Look for backups with the destination name
            candidates = []
            for backup in backup_dir.iterdir():
                # Check if backup name starts with dest_name
                if backup.name.startswith(dest_name):
                    candidates.append(backup)

            if candidates:
                # Return most recent by modification time
                return max(candidates, key=lambda p: p.stat().st_mtime)

        return None

    def restore_from_backup(
        backup_path: Path, restore_path: Path, step_name: str
    ) -> bool:
        """
        Restore a file or directory from a backup into its original destination.

        Parameters:
            backup_path (Path): Path to the backup file or directory to restore.
            restore_path (Path): Destination path where the backup will be restored.
            step_name (str): Human-readable name for the migration step (used in logs).

        Returns:
            bool: True if the restore completed successfully, False otherwise.
        """
        try:
            # Create parent directories
            restore_path.parent.mkdir(parents=True, exist_ok=True)

            # Remove what's currently at restore location
            if restore_path.exists():
                if restore_path.is_dir():
                    shutil.rmtree(str(restore_path))
                else:
                    restore_path.unlink()

            # Copy from backup (keep backup intact for safety)
            if backup_path.is_dir():
                shutil.copytree(str(backup_path), str(restore_path))
            else:
                shutil.copy2(str(backup_path), str(restore_path))

            logger.info(
                "Restored %s from backup %s to %s", step_name, backup_path, restore_path
            )
        except (OSError, IOError, shutil.Error):
            logger.exception("Failed to restore %s from backup", step_name)
            return False
        else:
            return True

    # Process steps in reverse order (last completed first)
    for step_name in reversed(completed_steps):
        try:
            # Find the migration result for this step
            step_result = None
            for migration in migrations:
                if migration.get("type") == step_name:
                    step_result = migration.get("result", {})
                    break

            if not step_result:
                logger.warning(
                    "No result found for step '%s', skipping rollback", step_name
                )
                continue

            new_path = step_result.get("new_path")
            action = step_result.get("action")

            # Skip steps that didn't actually migrate anything
            if action in (
                "none",
                "already_at_target",
                "already_migrated",
                "not_found",
                "skip_force_required",
            ):
                logger.debug(
                    "Step '%s' had no migration action, skipping rollback", step_name
                )
                continue

            if not new_path:
                logger.debug("Step '%s' has no new_path, skipping rollback", step_name)
                continue

            new_path_obj = Path(new_path)

            # Find backup for this step
            backup_path = find_backup_for_step(step_name, new_path_obj)

            if backup_path:
                # Restore from backup to new location (undo the overwrite)
                if restore_from_backup(backup_path, new_path_obj, step_name):
                    rollback_report["rolled_back_steps"].append(
                        {
                            "step": step_name,
                            "restored_from": str(backup_path),
                            "restored_to": new_path,
                        }
                    )
                else:
                    rollback_report["errors"].append(
                        {
                            "step": step_name,
                            "error": f"Failed to restore from {backup_path}",
                        }
                    )
                    rollback_report["success"] = False
            else:
                # No backup exists - destination was new. The source was moved (deleted),
                # so removing from new_path would cause permanent data loss.
                logger.warning(
                    "No backup found for %s at %s; skipping removal to preserve data. "
                    "Manual cleanup may be needed.",
                    step_name,
                    new_path,
                )
                rollback_report["rolled_back_steps"].append(
                    {
                        "step": step_name,
                        "skipped": True,
                        "reason": "no backup, data preserved",
                        "path": new_path,
                    }
                )

        except (OSError, IOError, shutil.Error) as e:
            logger.exception("Failed to rollback step '%s'", step_name)
            rollback_report["errors"].append({"step": step_name, "error": str(e)})
            rollback_report["success"] = False

    # Clean up staging directory if rollback succeeded
    if rollback_report["success"]:
        staging_dir = new_home / STAGING_DIRNAME
        if staging_dir.exists():
            try:
                shutil.rmtree(str(staging_dir), ignore_errors=True)
                logger.debug("Cleaned up staging directory after rollback")
            except (OSError, IOError):
                pass

    # Note: We intentionally do NOT clean up backups after rollback
    # They serve as a safety net and can be manually removed later

    if rollback_report["success"] and not rollback_report["errors"]:
        logger.info("Rollback completed successfully")
    else:
        logger.warning(
            "Rollback completed with %d errors", len(rollback_report["errors"])
        )

    return rollback_report
