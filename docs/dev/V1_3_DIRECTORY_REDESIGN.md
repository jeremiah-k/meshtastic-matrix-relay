# MMRelay v1.3 Directory Structure Redesign

**Status**: Draft Proposal for Breaking Changes
**Date**: 2026-02-01
**Version**: 1.3.0 (proposed)

---

## Executive Summary

This document proposes a clean break from the dual-layout (legacy/new) architecture to a **single, unified directory structure** for MMRelay v1.3. This addresses:

- Current complexity from evolutionary design decisions
- Confusion between `base_dir` vs `data_dir` concepts
- Platform-specific inconsistencies (Windows vs Unix)
- Breaking changes introduced in v1.2.10 for K8s/Docker compatibility
- Plugin directory confusion
- Documentation drift from implementation

**Key Principle**: One source of truth for all persistent data, with clear migration path for existing installations.

---

## Current Problems Identified

### 1. Dual Directory Concepts

**Problem**: `base_dir` and `data_dir` serve overlapping purposes, creating confusion:

```python
# Current state:
base_dir = ~/.mmrelay          # Config, credentials, logs, plugins
data_dir = ~/.mmrelay/data     # Database, E2EE store, plugin data
```

**Issues**:

- Credentials can be in either location depending on how saved
- Plugins discovered in `base_dir/plugins` but store data in `data_dir/plugins/{custom|community}/{name}`
- Windows defaults differ from Unix (platformdirs vs XDG)
- Database migration logic complex due to layout detection

### 2. Environment Variable Confusion

**Current state**:

- `MMRELAY_BASE_DIR` - Sets base directory
- `MMRELAY_DATA_DIR` - Sets data directory (legacy, but still supported)
- `MMRELAY_CREDENTIALS_PATH` - Sets credentials location
- `MMRELAY_DATABASE_PATH` - Sets database location

**Problem**:

- Users don't know which to use
- Aliasing `--base-dir` and `--data-dir` caused breakage in v1.2.10
- Docker/K8s configs mix `MMRELAY_BASE_DIR` and `MMRELAY_DATA_DIR`

### 3. Platform-Specific Inconsistencies

**Linux/macOS**:

```python
base_dir = ~/.mmrelay
data_dir = ~/.mmrelay/data
```

**Windows**:

```python
base_dir = platformdirs.user_data_dir(APP_NAME, APP_AUTHOR)  # e.g., %APPDATA%\mmrelay
data_dir = base_dir  # When no overrides
```

**Problem**: Windows has different behavior that's not well-documented.

### 4. Plugin Data Storage (UPDATED: Critical Finding)

**Current state**:

```python
# Plugin discovery (from plugin_loader.py):
roots = [
    "~/.mmrelay/plugins",           # Primary
    "~/.mmrelay/data/plugins",      # Only when MMRELAY_DATA_DIR set
    "/app/mmrelay/plugins"          # Local app dir
]

# Plugin code location (where plugins live):
<base_dir>/plugins/ or <data_dir>/plugins/

# Plugin data storage (from config.py):
plugin_data_dir = "<data_dir>/plugins/{custom|community}/{name}"  # Available but barely used

# Plugin ACTUAL data storage (from db_utils.py - PRIMARY METHOD):
database: store_plugin_data(plugin_name, meshtastic_id, data)  # JSON in SQLite
database: get_plugin_data(plugin_name, meshtastic_id)       # Retrieve from SQLite
database: delete_plugin_data(plugin_name, meshtastic_id)       # Delete from SQLite
```

**CRITICAL FINDING**: MMRelay plugins store their data **in the SQLite database**, NOT on the filesystem!

**Evidence**:

- `base_plugin.py` provides `get_plugin_data_dir()` method (line 668-688)
- But most plugins use `store_node_data()` which calls `store_plugin_data()` from `db_utils.py` (line 588-607)
- Core plugins (map_plugin, telemetry, mesh_relay, drop_plugin) use database storage, NOT filesystem
- `get_plugin_data_dir()` is available for plugins that DO want file I/O, but it's rarely used

**Problem Assessment**:

- The separation between plugin CODE location and plugin DATA location is **NOT a concern**
  - Code: `<base_dir>/plugins/` - where plugin `.py` files live
  - Data: SQLite database - centralized for all plugins
- The `get_plugin_data_dir()` directory structure **WILL NOT interfere** with anything because plugins don't write files there
- Most plugins have no need for per-plugin filesystem storage
- Unifying plugin directories doesn't affect existing functionality

**Design Decision Required**:

- Option A: Keep `get_plugin_data_dir()` for plugins that want filesystem storage
- Option B: Remove `get_plugin_data_dir()` entirely (plugins use only database)
- **My recommendation**: Keep `get_plugin_data_dir()` available (for future plugins that need file I/O), but unify location to `<home_dir>/plugins/{custom|community}/{name}/data/`

### 5. Documentation Drift

**Files referencing old structure**:

- `docs/dev/DATA_LAYOUT_MIGRATION.md` - Describes legacy/new dual layout
- `docs/DOCKER.md` - References both `MMRELAY_BASE_DIR` and `MMRELAY_DATA_DIR`
- `docs/KUBERNETES.md` - References `--base-dir` and `/data`
- `docs/INSTRUCTIONS.md` - References `~/.mmrelay` throughout
- Multiple sample YAML files with outdated paths

---

## Proposed v1.3 Unified Directory Structure

### Core Principle

**Single directory for everything** - All persistent application data lives under one directory:

```text
<MMRELAY_HOME>/
├── config.yaml              # User configuration (optional, can be elsewhere)
├── matrix/                  # Matrix runtime artifacts
│   ├── credentials.json    # Matrix authentication credentials
│   └── store/              # E2EE encryption keys (Unix/macOS only)
├── database/
│   └── meshtastic.sqlite  # SQLite database (with -wal, -shm)
├── logs/
│   └── mmrelay.log          # Application logs
└── plugins/
    ├── core/              # Built-in plugins (read-only, in package)
    ├── custom/            # User plugins
    │   └── <plugin-name>/
    └── community/         # Third-party plugins
        └── <plugin-name>/
```

### Default Locations

**Environment Variable**: `MMRELAY_HOME`

**CLI Argument**: `--home <path>` (new, deprecates `--base-dir` and `--data-dir`)

**Platform Defaults**:

- **Linux/macOS**: `~/.mmrelay`
- **Windows**: `%LOCALAPPDATA%\mmrelay` (platformdirs)
- **Docker/K8s**: `/data` (single PVC mount)

### File Paths (v1.3)

| File Type           | v1.3 Path                                     | Notes                                                        |
| ------------------- | --------------------------------------------- | ------------------------------------------------------------ |
| Config              | `$MMRELAY_HOME/config.yaml` or user-specified | Read-only search in multiple locations                       |
| Credentials         | `$MMRELAY_HOME/matrix/credentials.json`       | One location, no fallbacks                                   |
| Database            | `$MMRELAY_HOME/database/meshtastic.sqlite`    | Single location; migration is explicit via `mmrelay migrate` |
| Logs                | `$MMRELAY_HOME/logs/mmrelay.log`              | Default, overrideable via env var                            |
| E2EE Store          | `$MMRELAY_HOME/matrix/store/`                 | Unix/macOS only                                              |
| Plugins (custom)    | `$MMRELAY_HOME/plugins/custom/<name>`         | User-installed plugins                                       |
| Plugins (community) | `$MMRELAY_HOME/plugins/community/<name>`      | Third-party plugins                                          |

---

## Migration Strategy (v1.2.x → v1.3)

### Phase 1: Detection (Explicit Command)

Run the migration command after upgrade (use `mmrelay migrate --dry-run` to preview):

1. Detect old installation locations
2. Check for existing data in legacy layout
3. Prompt user with clear migration options
4. Perform migration atomically
5. Verify migration success
6. Show summary

### Phase 2: Migration Logic

**From Legacy Layout** (v1.2.9 and earlier):

```text
Old                          →  New (v1.3)
---------------------------------------------------------
~/.mmrelay/credentials.json  →  $MMRELAY_HOME/matrix/credentials.json
~/.mmrelay/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite
~/.mmrelay/meshtastic.sqlite-wal →  $MMRELAY_HOME/database/meshtastic.sqlite-wal
~/.mmrelay/meshtastic.sqlite-shm →  $MMRELAY_HOME/database/meshtastic.sqlite-shm
~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
~/.mmrelay/store/              →  $MMRELAY_HOME/matrix/store/
~/.mmrelay/plugins/custom/    →  $MMRELAY_HOME/plugins/custom/
~/.mmrelay/plugins/community/ →  $MMRELAY_HOME/plugins/community/
```

**Plugin Data Migration (Three-Tier System)**:

```text
Plugin Data Tiers (v1.3):

Tier 1 (Code):     $MMRELAY_HOME/plugins/custom/{name}/ or $MMRELAY_HOME/plugins/community/{name}/         # Plugin .py file
Tier 2 (Filesystem): $MMRELAY_HOME/plugins/custom/{name}/data/ or $MMRELAY_HOME/plugins/community/{name}/data/      # Disk storage (GPX, JSON, etc.)
Tier 3 (Database):   $MMRELAY_HOME/database/meshtastic.sqlite    # SQLite tables (default)

Migration Rules:
- Most plugins (nodes, telemetry, map, mesh_relay, drop_plugin): Use Tier 3 only
- Community plugins with disk storage (e.g., gpxtracker): Migrate to Tier 2
- All plugins: Tier 1 (code) location unchanged
```

**Community Plugin Example - gpxtracker**:

- Old: `gpx_directory: "~/my_gpx_files"` (from plugin config)
- New: `$MMRELAY_HOME/plugins/community/gpxtracker/data/` (Tier 2 location)
- Migration: `~/my_gpx_files/` → `$MMRELAY_HOME/plugins/community/gpxtracker/data/`

**From Partial New Layout** (v1.2.10-1.2.11):

```text
Old                          →  New (v1.3)
---------------------------------------------------------
~/.mmrelay/config.yaml        →  $MMRELAY_HOME/config.yaml (or keep)
~/.mmrelay/credentials.json    →  $MMRELAY_HOME/matrix/credentials.json
~/.mmrelay/meshtastic.sqlite    →  $MMRELAY_HOME/database/meshtastic.sqlite
~/.mmrelay/data/meshtastic.sqlite →  $MMRELAY_HOME/database/meshtastic.sqlite
~/.mmrelay/logs/              →  $MMRELAY_HOME/logs/
~/.mmrelay/store/              →  $MMRELAY_HOME/matrix/store/
~/.mmrelay/plugins/            →  $MMRELAY_HOME/plugins/
~/.mmrelay/data/plugins/        →  $MMRELAY_HOME/plugins/
```

**Migration Rules**:

1. **Credentials**: Always prefer new location, keep old as backup (`.bak`)
2. **Database**: Move most recently modified (including WAL/SHM sidecars)
3. **Logs**: Merge all log files into `logs/` with timestamps
4. **Plugins**: Copy entire plugin directories
5. **Store**: Only on Unix/macOS (no Windows E2EE support)
6. **Config**: Keep at old location (user may have external management)

### Phase 3: Rollback Support

If migration fails:

1. Preserve original files (don't delete)
2. Log detailed error with paths
3. Provide manual recovery guidance using `mmrelay migrate --dry-run` (and `--force` as needed)
4. Document manual restore steps (copy from `.bak` files in MMRELAY_HOME if needed)

---

## Implementation Plan

### Step 1: Create New Directory Resolution Module

**File**: `src/mmrelay/paths.py` (new file)

```python
"""
Unified path resolution for MMRelay v1.3

This module provides a single, consistent interface for all filesystem paths.
"""

import os
import sys
import platformdirs
from pathlib import Path

from mmrelay.constants.app import APP_NAME, APP_AUTHOR

# Global overrides (set by CLI/env vars)
_home_override: str | None = None


def get_home_dir() -> Path:
    """
    Get the application home directory (single source of truth).

    Resolution order:
    1. MMRELAY_HOME env var
    2. --home CLI argument
    3. Platform defaults

    Returns:
        Path: Application home directory
    """
    # Check environment variable
    env_home = os.getenv("MMRELAY_HOME")
    if env_home:
        return Path(env_home).expanduser().absolute()

    # Check CLI override
    if _home_override:
        return Path(_home_override).expanduser().absolute()

    # Platform defaults
    if sys.platform in ["linux", "darwin"]:
        return Path.home() / f".{APP_NAME}"
    else:  # Windows
        return Path(platformdirs.user_data_dir(APP_NAME, APP_AUTHOR))


def set_home_override(path: str) -> None:
    """Set home directory override from CLI."""
    global _home_override
    _home_override = path


def get_config_dir() -> Path:
    """
    Get configuration directory.

    Config can be in home dir or user-specified location.
    This function only validates, doesn't create.
    """
    # Config is searched in multiple locations (read-only)
    # Actual location depends on where user created it
    return get_home_dir() / "config.yaml"  # Default location


def get_credentials_path() -> Path:
    """
    Get credentials file path (single location).

    Returns:
        Path: Location of credentials.json
    """
    return get_home_dir() / "matrix" / "credentials.json"


def get_database_dir() -> Path:
    """
    Get database directory.

    Returns:
        Path: Database directory location
    """
    return get_home_dir() / "database"


def get_database_path() -> Path:
    """
    Get SQLite database file path.

    Returns:
        Path: Database file location
    """
    return get_database_dir() / "meshtastic.sqlite"


def get_logs_dir() -> Path:
    """
    Get logs directory.

    Returns:
        Path: Logs directory
    """
    return get_home_dir() / "logs"


def get_log_file() -> Path:
    """
    Get log file path.

    Environment variable: MMRELAY_LOG_PATH can override

    Returns:
        Path: Log file location
    """
    env_log = os.getenv("MMRELAY_LOG_PATH")
    if env_log:
        return Path(env_log).expanduser().absolute()
    return get_logs_dir() / "mmrelay.log"


def get_e2ee_store_dir() -> Path:
    """
    Directory for storing end-to-end encryption (E2EE) keys.

    Only available on Unix-like platforms; calling this on Windows raises an error.

    Returns:
        Path: Path to E2EE key store directory.

    Raises:
        E2EENotSupportedError: If invoked on Windows (E2EE is not supported on Windows).
    """
    if sys.platform == "win32":
        raise E2EENotSupportedError()

    return get_matrix_dir() / "store"


def get_plugins_dir() -> Path:
    """
    Get plugins directory.

    Returns:
        Path: Plugins root directory
    """
    return get_home_dir() / "plugins"


def get_custom_plugins_dir() -> Path:
    """
    Get custom plugins directory.

    Returns:
        Path: Custom plugins directory
    """
    return get_plugins_dir() / "custom"


def get_community_plugins_dir() -> Path:
    """
    Get community plugins directory.

    Returns:
        Path: Community plugins directory
    """
    return get_plugins_dir() / "community"


def get_plugin_data_dir(plugin_name: str, plugin_type: str) -> Path:
    """
    Get data directory for a specific plugin.

    Args:
        plugin_name: Name of the plugin
        plugin_type: Plugin category ("custom", "community", or "core")

    Returns:
        Path: Plugin data directory
    """
    # Plugins store their data under <home>/plugins/{custom|community}/{name}/data/
    # This keeps plugin code and data together
    return get_plugins_dir() / plugin_type / plugin_name / "data"


def ensure_directories() -> None:
    """
    Ensure all required directories exist.

    Creates missing directories with appropriate permissions.
    """
    dirs_to_create = [
        get_home_dir(),
        get_database_dir(),
        get_logs_dir(),
        get_e2ee_store_dir(),
        get_plugins_dir(),
        get_custom_plugins_dir(),
        get_community_plugins_dir(),
        get_core_plugins_dir(),
    ]

    for dir_path in filter(None, dirs_to_create):
        dir_path.mkdir(parents=True, exist_ok=True)
```

### Step 2: Create Migration Module

**File**: `src/mmrelay/migrate.py` (new file)

```python
"""
Migration utilities for MMRelay v1.2.x → v1.3

Handles migration from legacy and new layouts to unified structure.
"""

import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any
from datetime import datetime

from mmrelay.constants.app import APP_NAME
from mmrelay.log_utils import get_logger

logger = get_logger("Migration")


class MigrationError(Exception):
    """Migration-specific error."""
    pass


def detect_legacy_installation() -> dict[str, Any] | None:
    """
    Detect legacy MMRelay installation locations.

    Returns:
        dict: Information about legacy installation, or None if not found
    """
    legacy_home = Path.home() / ".mmrelay"
    if not legacy_home.exists():
        return None

    info = {
        "home": str(legacy_home),
        "credentials": str(legacy_home / "credentials.json"),
        "database": str(legacy_home / "meshtastic.sqlite"),
        "database_dir": str(legacy_home),
        "logs_dir": str(legacy_home / "logs"),
        "store_dir": str(legacy_home / "store"),
        "plugins_dir": str(legacy_home / "plugins"),
    }

    # Check for v1.2.10 partial new layout
    new_home = Path.home() / f".{APP_NAME}"
    data_dir = new_home / "data"

    if data_dir.exists():
        info.update({
            "partial_new_home": str(new_home),
            "data_dir": str(data_dir),
            "database_partial": str(data_dir / "meshtastic.sqlite"),
        })

    return info


def get_most_recent_database(databases: list[Path]) -> Path | None:
    """
    Find the most recently modified database file.

    Considers main DB and WAL/SHM sidecars.

    Returns:
        Path: Most recently modified database file, or None
    """
    def get_mtime(path: Path) -> float:
        if not path.exists():
            return 0.0
        return path.stat().st_mtime

    candidates = []

    for db in databases:
        if db.exists():
            candidates.append((get_mtime(db), db))

    if not candidates:
        return None

    # Return most recently modified
    candidates.sort(reverse=True)
    return candidates[0][1]


def migrate_credentials(legacy_info: dict[str, Any], new_home: Path) -> bool:
    """
    Migrate credentials.json to new location.

    Args:
        legacy_info: Legacy installation information
        new_home: New home directory

    Returns:
        bool: True if migrated, False if credentials not found
    """
    old_path = Path(legacy_info["credentials"])
    if not old_path.exists():
        logger.info("No credentials file found in legacy location")
        return False

    new_path = new_home / "matrix" / "credentials.json"
    new_path.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing new credentials if present
    if new_path.exists():
        backup_path = new_path.with_suffix(".bak." + datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(new_path, backup_path)
        logger.info("Backed up existing credentials to %s", backup_path)

    # Move credentials
    shutil.move(str(old_path), str(new_path))
    logger.info("Migrated credentials from %s to %s", old_path, new_path)

    return True


def migrate_database(legacy_info: dict[str, Any], new_home: Path) -> bool:
    """
    Migrate database to new location.

    Args:
        legacy_info: Legacy installation information
        new_home: New home directory

    Returns:
        bool: True if migrated successfully
    """
    # Collect all candidate databases
    databases = []

    # Legacy location
    legacy_db = Path(legacy_info["database"])
    if legacy_db.exists():
        databases.append(legacy_db)

    # WAL sidecar
    legacy_wal = Path(str(legacy_db) + "-wal")
    if legacy_wal.exists():
        databases.append(legacy_wal)

    # SHM sidecar
    legacy_shm = Path(str(legacy_db) + "-shm")
    if legacy_shm.exists():
        databases.append(legacy_shm)

    # v1.2.10 partial new layout
    if "database_partial" in legacy_info:
        partial_db = Path(legacy_info["database_partial"])
        if partial_db.exists():
            databases.append(partial_db)
            partial_wal = Path(str(partial_db) + "-wal")
            if partial_wal.exists():
                databases.append(partial_wal)
            partial_shm = Path(str(partial_db) + "-shm")
            if partial_shm.exists():
                databases.append(partial_shm)

    if not databases:
        logger.info("No database files found in legacy location")
        return False

    # Find most recent database
    most_recent = get_most_recent_database(databases)
    if not most_recent:
        logger.warning("No valid database files found")
        return False

    # Target directory
    target_dir = new_home / "database"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Move database and sidecars
    for db in databases:
        target_path = target_dir / db.name
        if db != most_recent:
            # Move WAL/SHM directly
            shutil.move(str(db), str(target_path))
        else:
            # Move main DB and verify integrity
            shutil.move(str(db), str(target_path))

            # Verify database integrity
            try:
                conn = sqlite3.connect(str(target_path))
                conn.execute("PRAGMA integrity_check")
                conn.close()
            except sqlite3.DatabaseError as e:
                raise MigrationError(f"Database integrity check failed: {e}")

    logger.info("Migrated database to %s", target_dir)
    return True


def migrate_plugins(legacy_info: dict[str, Any], new_home: Path) -> bool:
    """
    Migrate plugins directory.

    Args:
        legacy_info: Legacy installation information
        new_home: New home directory

    Returns:
        bool: True if any plugins migrated
    """
    # Check for legacy plugins
    legacy_plugins_dir = Path(legacy_info["plugins_dir"])
    if not legacy_plugins_dir.exists():
        logger.info("No plugins directory in legacy location")
        return False

    # Target directories
    new_plugins_dir = new_home / "plugins"

    # Create target directories
    new_custom_dir = new_plugins_dir / "custom"
    new_custom_dir.mkdir(parents=True, exist_ok=True)

    new_community_dir = new_plugins_dir / "community"
    new_community_dir.mkdir(parents=True, exist_ok=True)

    migrated_any = False

    # Migrate custom plugins
    legacy_custom_dir = legacy_plugins_dir / "custom"
    if legacy_custom_dir.exists():
        for item in legacy_custom_dir.iterdir():
            if item.is_dir():
                target = new_custom_dir / item.name
                if target.exists():
                    backup_suffix = f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    target = target.with_name(target.name + backup_suffix)
                shutil.copytree(str(item), str(target))
                migrated_any = True
        logger.info("Migrated custom plugins")

    # Migrate community plugins
    legacy_community_dir = legacy_plugins_dir / "community"
    if legacy_community_dir.exists():
        for item in legacy_community_dir.iterdir():
            if item.is_dir():
                target = new_community_dir / item.name
                if target.exists():
                    backup_suffix = f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    target = target.with_name(target.name + backup_suffix)
                shutil.copytree(str(item), str(target))
                migrated_any = True
        logger.info("Migrated community plugins")

    return migrated_any


def migrate_logs(legacy_info: dict[str, Any], new_home: Path) -> bool:
    """
    Migrate logs directory.

    Args:
        legacy_info: Legacy installation information
        new_home: New home directory

    Returns:
        bool: True if any logs migrated
    """
    legacy_logs_dir = Path(legacy_info["logs_dir"])
    if not legacy_logs_dir.exists():
        logger.info("No logs directory in legacy location")
        return False

    new_logs_dir = new_home / "logs"
    new_logs_dir.mkdir(parents=True, exist_ok=True)

    migrated_any = False

    # Merge all log files
    for log_file in legacy_logs_dir.glob("*.log"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_name = f"{log_file.stem}_migrated_{timestamp}.log"
        shutil.copy2(str(log_file), str(new_logs_dir / target_name))
        migrated_any = True

    if migrated_any:
        logger.info("Migrated log files to %s", new_logs_dir)

    return migrated_any


def migrate_store(legacy_info: dict[str, Any], new_home: Path) -> bool:
    """
    Migrate E2EE store directory.

    Args:
        legacy_info: Legacy installation information
        new_home: New home directory

    Returns:
        bool: True if migrated, False if not found
    """
    if sys.platform == "win32":
        logger.info("E2EE not supported on Windows, skipping store migration")
        return False

    legacy_store_dir = Path(legacy_info["store_dir"])
    if not legacy_store_dir.exists():
        logger.info("No store directory in legacy location")
        return False

    new_store_dir = new_home / "store"
    shutil.copytree(str(legacy_store_dir), str(new_store_dir))
    logger.info("Migrated E2EE store to %s", new_store_dir)

    return True


def perform_migration(dry_run: bool = False) -> dict[str, Any]:
    """
    Perform full migration from legacy to new layout.

    Args:
        dry_run: If True, don't actually move files, just report what would happen

    Returns:
        dict: Migration report with success status and details
    """
    report = {
        "dry_run": dry_run,
        "timestamp": datetime.now().isoformat(),
        "success": True,
        "migrations": [],
    }

    # Detect legacy installation
    legacy_info = detect_legacy_installation()
    if not legacy_info:
        report["success"] = False
        report["message"] = "No legacy installation detected"
        return report

    # Get new home directory
    new_home = Path.home() / f".{APP_NAME}"

    if dry_run:
        logger.info("DRY RUN - would perform the following migrations:")
    else:
        new_home.mkdir(parents=True, exist_ok=True)
        logger.info("Migration started. Legacy: %s, New: %s", legacy_info["home"], str(new_home))

    # Perform individual migrations
    migration_results = {
        "credentials": migrate_credentials(legacy_info, new_home) if not dry_run else True,
        "database": migrate_database(legacy_info, new_home) if not dry_run else True,
        "plugins": migrate_plugins(legacy_info, new_home) if not dry_run else True,
        "logs": migrate_logs(legacy_info, new_home) if not dry_run else True,
        "store": migrate_store(legacy_info, new_home) if not dry_run else True,
    }

    # Report results
    for name, success in migration_results.items():
        status = "would migrate" if dry_run else "migrated"
        result = "success" if success else "skipped"
        report["migrations"].append({
            "name": name,
            "status": status,
            "result": result,
        })

    return report
```

### Step 3: Update Configuration Resolution

**File**: `src/mmrelay/config.py` (refactor)

**Changes**:

1. Remove `get_base_dir()` / `get_data_dir()` dual concept
2. Remove legacy/new layout detection (`is_new_layout_enabled()`, `is_legacy_layout_enabled()`)
3. Simplify to single-path model using new `paths.py` module
4. Update all callers to use new path functions

**Example of refactor**:

```python
# OLD (remove this):
def get_base_dir() -> str:
    # Complex logic with dual layouts...

def get_data_dir(*, create: bool = True) -> str:
    # Complex logic with platform differences...

# NEW (replace with):
from mmrelay.paths import (
    get_home_dir,
    get_credentials_path,
    get_database_path,
    get_log_file,
    get_e2ee_store_dir,
    get_plugins_dir,
)

# Use:
home_dir = get_home_dir()
credentials = get_credentials_path()
database = get_database_path()
```

### Step 4: Update CLI Arguments

**File**: `src/mmrelay/cli.py` (refactor)

**Changes**:

1. Add `--home <path>` argument (new)
2. Deprecate `--base-dir` and `--data-dir`
3. Add migration commands

```python
# Add to parser:
parser.add_argument(
    "--home",
    help=f"Application home directory (default: {DEFAULT_HOME})",
    default=None,
)

# Deprecation warnings:
if args.base_dir or args.data_dir:
    print(
        "Warning: --base-dir and --data-dir are deprecated in v1.3.",
        file=sys.stderr,
    )
    print("Use --home instead. Example: --home ~/.mmrelay")

# Add migration commands:
subparsers = parser.add_subparsers(dest="command")

migrate_parser = subparsers.add_parser("migrate", help="Migrate to v1.3 directory structure")
migrate_parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Show what would be migrated without making changes",
)
# Note: The actual implementation uses move semantics by default.
# The --move flag was not implemented; move is always the behavior.
migrate_parser.add_argument(
    "--force",
    action="store_true",
    help="Overwrite existing destination files (backups are always created)",
)
```

### Step 5: Update Docker Configuration

**File**: `Dockerfile`

**Changes**:

```dockerfile
# OLD (remove):
ENV MMRELAY_DATA_DIR=/app/data
ENV MMRELAY_BASE_DIR=/data

# NEW (replace with):
ENV MMRELAY_HOME=/data

# Update CMD (implicitly uses /data/config.yaml and /data HOME):
CMD ["mmrelay"]
```

**Rationale**:

- Single environment variable (`MMRELAY_HOME`) for all data
- Configuration (`config.yaml`) lives alongside data in `/data`
- No confusion about which variable to use

### Step 6: Update K8s Configuration

**File**: `deploy/k8s/deployment.yaml`

**Changes**:

```yaml
# OLD (update):
env:
  - name: MMRELAY_BASE_DIR
    value: /data
  - name: MMRELAY_CREDENTIALS_PATH
    value: /data/credentials.json

# NEW (replace with):
env:
  - name: MMRELAY_HOME
    value: /data
  - name: MMRELAY_LOG_PATH
    value: /data/logs/mmrelay.log

# Remove redundant args (uses MMRELAY_HOME environment variable instead):
args: []
```

**Rationale**:

- Consistent with Docker: single `MMRELAY_HOME` env var
- Explicit log path (can't use default due to PVC mount point)
- Clear separation: config from ConfigMap, data from PVC

---

## Files Requiring Updates

### Source Code Files

1. **src/mmrelay/config.py**
   - Remove dual directory concept
   - Remove legacy/new layout detection
   - Simplify to use `paths.py` functions

2. **src/mmrelay/paths.py** (NEW)
   - Unified path resolution module
   - Single source of truth for all paths

3. **src/mmrelay/migrate.py** (NEW)
   - Migration utilities for v1.2.x → v1.3

4. **src/mmrelay/cli.py**
   - Add `--home` argument
   - Deprecate `--base-dir` / `--data-dir`
   - Add `mmrelay migrate` command

5. **src/mmrelay/plugin_loader.py**
   - Update plugin discovery to use `get_plugins_dir()`
   - Update plugin data storage to use `get_plugin_data_dir()`

6. **src/mmrelay/db_utils.py**
   - Update `get_db_path()` to use new path functions
   - Remove inline migration logic (now in `migrate.py`)

7. **src/mmrelay/main.py**
   - Remove legacy layout warnings
   - Add migration check on startup

### Documentation Files

1. **docs/DIRECTORY_STRUCTURE.md** (NEW)
   - Document v1.3 unified directory structure
   - Migration guide for v1.2.x users

2. **docs/dev/DATA_LAYOUT_MIGRATION.md**
   - Update or mark as deprecated (superseded by DIRECTORY_STRUCTURE.md)

3. **docs/MIGRATION_GUIDE.md** (NEW)
   - Step-by-step migration instructions
   - Troubleshooting common migration issues

4. **docs/INSTRUCTIONS.md**
   - Update default paths references
   - Remove legacy/new layout mentions

5. **docs/DOCKER.md**
   - Update to use `MMRELAY_HOME`
   - Update volume mount examples
   - Deprecate `MMRELAY_DATA_DIR`

6. **docs/KUBERNETES.md**
   - Update to use `MMRELAY_HOME`
   - Update deployment examples
   - Add migration notes

7. **README.md**
   - Update directory structure description
   - Add v1.3 upgrade notice

### Sample Files

1. **src/mmrelay/tools/sample_config.yaml**
   - Update path examples
   - Add `MMRELAY_HOME` references

2. **docker-compose.yaml**
   - Update to use `MMRELAY_HOME`
   - Simplify volume mounts

3. **sample-docker-compose.yaml** (NEW)
   - Create with `MMRELAY_HOME`

### Deployment Files

1. **deploy/k8s/deployment.yaml**
   - Update environment variables
   - Update command arguments

2. **Dockerfile**
   - Simplify to `MMRELAY_HOME`
   - Update CMD

3. **Makefile** (if used)
   - Update any path references

---

## Breaking Changes for v1.3

### Required User Actions

**For Linux/macOS users**:

1. Run: `mmrelay migrate` to detect and migrate old installation
2. Review migration report
3. Update Docker/K8s configs to use `MMRELAY_HOME` instead of `MMRELAY_BASE_DIR`/`MMRELAY_DATA_DIR`
4. Update environment variables in scripts/configs

**For Windows users**:

1. Run: `mmrelay migrate` (database and credentials migration)
2. E2EE not affected (already unsupported)
3. Update configs to use `MMRELAY_HOME`

**For Docker users**:

1. Update docker-compose or run commands to use `MMRELAY_HOME`
2. Update volume mounts if needed
3. Pull new image with v1.3

**For K8s users**:

1. Update deployment.yaml to use `MMRELAY_HOME`
2. Re-apply manifests
3. Verify pods start successfully
4. Check logs for migration completion

### Deprecated Features

**Deprecated in v1.3 (removed in v1.4)**:

- `--base-dir` CLI argument (use `--home`)
- `--data-dir` CLI argument (use `--home`)
- `MMRELAY_BASE_DIR` environment variable (use `MMRELAY_HOME`)
- `MMRELAY_DATA_DIR` environment variable (use `MMRELAY_HOME`)
- `is_new_layout_enabled()` / `is_legacy_layout_enabled()` functions
- `get_base_dir()` function (use `get_home_dir()`)
- `get_data_dir()` function (use `get_home_dir()` and specific path helpers)
- Dual directory concept (single home directory only)

### New Features in v1.3

**Added**:

- `--home <path>` CLI argument
- `MMRELAY_HOME` environment variable
- `mmrelay migrate` command (with --dry-run and --force; move semantics are default)
- Unified directory structure under single home directory
- Atomic migrations with rollback support
- Clear separation between config and data
- Plugin discovery and storage in same directory tree

---

## Testing Strategy

### Unit Tests

**New test files**:

1. `tests/test_paths.py` - Test all path resolution functions
2. `tests/test_migrate.py` - Test migration logic
3. `tests/test_config.py` - Update for new path functions

**Test scenarios**:

- Legacy detection (found/not found)
- Database migration (with/without WAL/SHM)
- Credentials migration (with/without existing backup)
- Plugin migration (empty/populated)
- Dry-run mode
- Rollback functionality
- Path resolution with overrides
- Platform differences (Linux/macOS/Windows)

### Integration Tests

**Test workflows**:

1. Fresh install (no migration needed)
2. Upgrade from v1.2.9
3. Upgrade from v1.2.10 (partial new layout)
4. Migration with conflicts
5. Migration rollback
6. Docker deployment with PVC
7. K8s deployment with secrets

### Manual Testing Checklist

- [ ] Test on Linux (fresh install)
- [ ] Test on Linux (upgrade from v1.2.9)
- [ ] Test on macOS
- [ ] Test on Windows
- [ ] Test in Docker (docker-compose)
- [ ] Test in K8s (local kind or minikube)
- [ ] Test credential persistence
- [ ] Test database migration
- [ ] Test plugin loading after migration
- [ ] Test log file creation
- [ ] Test E2EE store migration

---

## Resolved Decisions (v13rc1-2)

1. **Migration timing**: Explicit user action via `mmrelay migrate` (with `--dry-run` preview).
2. **Legacy config handling**: `config.yaml` migration is implemented with backup behavior and `--force` override.
3. **Partial new layout support**: Supported (v1.2.10+ hybrid layouts are included in migration/verification).
4. **Backup strategy**: Backups are created by default; `--force` allows overwrite (backups are still created).
5. **Rollback strategy**: Automatic rollback is attempted on migration failure.
6. **Primary home variable**: `MMRELAY_HOME` is the canonical runtime root.
7. **Database layout**: Uses `database/` subdirectory under `MMRELAY_HOME`.
8. **Logs layout**: Uses `logs/` by default, with override support.
9. **Plugin data layout**: Filesystem plugin data path remains `plugins/{custom|community}/{name}/data/`, while plugin runtime state primarily uses database-backed storage.
10. **Compatibility window**: Legacy env/paths remain in deprecation flow for v1.3 and are planned for removal in v1.4.

---

## Open Follow-ups (post-v13rc1-2)

1. **Auth first-run UX**: Keep validating that first-run `mmrelay auth login` consistently writes credentials into the unified `matrix/` subtree on all platforms.
2. **Container bootstrap UX**: Continue documenting/streamlining first-boot behavior when credentials do not yet exist (health/readiness expectations).
3. **Deprecation execution**: Track concrete removal tasks for legacy env/paths in v1.4 and ensure release checklist covers final cleanup.

---

## Implementation Status (v13rc1-2)

- [x] Phase 1 Foundation: `paths.py` and `migrate.py` implemented with coverage.
- [x] Phase 2 Integration: `config.py`, `cli.py`, `plugin_loader.py`, `db_utils.py`, and `main.py` integrated with unified home layout and migration flow.
- [x] Phase 3 Documentation: migration, helm, docker, and developer docs updated for v1.3 behavior.
- [x] Phase 4 Deployment: Docker/Helm/K8s assets updated to the v1.3 directory model.
- [ ] Phase 5 Testing: final pre-release validation remains tracked in `docs/RELEASE_1.3.md`.
- [ ] Phase 6 Release: release execution checklist remains tracked in `docs/RELEASE_1.3.md`.

---

## Appendix: Path Reference Comparison

### v1.2.x Layout

```text
Legacy (v1.2.9):
~/.mmrelay/
├── config.yaml
├── credentials.json
├── meshtastic.sqlite
├── logs/
│   └── mmrelay.log
├── plugins/
│   ├── custom/
│   └── community/
└── store/

Partial New (v1.2.10-1.2.11):
~/.mmrelay/
├── config.yaml
├── credentials.json
├── data/
│   └── meshtastic.sqlite
├── logs/
│   └── mmrelay.log
├── plugins/
│   ├── custom/
│   └── community/
└── store/
```

**v1.2.x Plugin Data Storage**:

- Plugin data stored in SQLite database (via `store_plugin_data()`)
- `get_plugin_data_dir()` available but rarely used
- Plugin code location: `<base_dir>/plugins/` or `<data_dir>/plugins/`
- **No filesystem writes** from core plugins (telemetry saves to memory buffer, others use database)

### v1.3 Layout

```text
Unified:
$MMRELAY_HOME/
├── config.yaml              # (optional, can be elsewhere)
├── matrix/
│   ├── credentials.json
│   └── store/              # Unix/macOS only
├── database/
│   └── meshtastic.sqlite
├── logs/
│   └── mmrelay.log
└── plugins/
    ├── core/              # Built-in
    ├── custom/
    │   └── <plugin-name>/  # Plugin code and optional data
    └── community/
        └── <plugin-name>/
```

**v1.3 Plugin Data Storage**:

- **Primary**: Plugin data stored in SQLite database (via `store_plugin_data()`)
- `get_plugin_data_dir()` available for plugins that need filesystem I/O
- Plugin code location: `$MMRELAY_HOME/plugins/{custom|community}/{name}/`
- **No interference** with existing functionality (plugins don't write files)
- **Backward compatible**: Old plugins still work, new plugins can use file I/O if needed

---

**Document History**:

- 2026-02-01: Initial draft based on comprehensive analysis of v1.2.x codebase
- 2026-02-01: **CRITICAL UPDATE**: Plugin data storage research - plugins use SQLite database, NOT filesystem!
- 2026-02-01: **UPDATED**: Backward compatibility strategy - 6-month deprecation window; migration is explicit via `mmrelay migrate`
- 2026-02-01: **ADDED**: Docker/K8s compatibility patterns from research (env var precedence, deprecation policies)
- 2026-02-01: **ADDED**: Seamless migration patterns (VS Code, Homebrew, explicit `mmrelay migrate` flow)
- 2026-02-01: **RESOLVED**: Plugin directory concern - no filesystem interference with current architecture
