# v1.3 Migration System Improvements Plan

## Context

This document outlines systematic improvements to the MMRelay v1.3 migration system based on the [Migration Guide for v1.3](../MIGRATION_1.3.md). The audit identified 5 key findings, with Finding #4 (limited plugin data migration) deemed acceptable since gpxtracker is the only community plugin with Tier 2 filesystem storage needs.

## Reference Documents

- [Data Layout Migration Notes](DATA_LAYOUT_MIGRATION.md) - v1.2.x dual-layout system
- [v1.3 Directory Redesign](V1_3_DIRECTORY_REDESIGN.md) - Unified MMRELAY_HOME structure
- [Migration Guide for v1.3](../MIGRATION_1.3.md) - User-facing migration documentation

## Audit Findings Summary

| #   | Finding                          | Priority | Status       |
| --- | -------------------------------- | -------- | ------------ |
| 1   | Missing `config.yaml` migration  | High     | Completed    |
| 2   | No automatic rollback on failure | High     | Completed    |
| 3   | Incomplete rollback coverage     | High     | Completed    |
| 4   | Limited plugin data migration    | N/A      | Acceptable\* |
| 5   | Redundant logic in `config.py`   | Medium   | Completed    |

\*Finding #4 is acceptable because gpxtracker is currently the only community plugin using Tier 2 filesystem storage.

---

## Improvement Plan

### 1. Implement `config.yaml` Migration (High Priority)

**Problem**: The `perform_migration()` function does not explicitly migrate `config.yaml` from legacy locations to `MMRELAY_HOME`, despite the docstring at line 27 of `migrate.py` documenting this migration path.

**Impact**: Users upgrading from v1.2.x may not have their configuration automatically moved, forcing manual intervention or risking configuration not being found if they rely on `MMRELAY_HOME`.

**Implementation Requirements**:

1.1. Create `migrate_config()` function in `migrate.py`:

- Signature: `migrate_config(legacy_roots: list[Path], new_home: Path, dry_run: bool = False, force: bool = False) -> dict[str, Any]`
- Scan all legacy roots for `config.yaml`
- Migrate first found config to `new_home/config.yaml`
- Support dry-run mode
- Support both copy and move operations
- Create timestamped backup if target exists (unless `force=True`)
- Return result dict with success status

  1.2. Integrate `migrate_config()` into `perform_migration()`:

- Add config migration step after credentials migration
- Check success and fail-fast if migration fails
- Record migration result in report

  1.3. Update `rollback_migration()`:

- Add restoration logic for `config.yaml`
- Restore from `config.yaml.bak.*` backup files
- Use most recent backup (sorted reverse by timestamp)

  1.4. Add comprehensive test coverage:

- `test_migrate_config_success()`
- `test_migrate_config_no_legacy_config()`
- `test_migrate_config_dry_run()`
- `test_migrate_config_with_existing_target()`
- `test_rollback_migrate_config()`

**Design Decisions**:

- **Backup behavior**: Always create timestamped backup (`.bak.YYYYMMDD_HHMMSS`) before overwriting
- **Priority order**: Config migration should occur early in `perform_migration()` (after credentials, before database)
- **Conflict resolution**: If multiple legacy configs exist, use first found (same pattern as credentials)

---

### 2. Enable Automatic Rollback on Migration Failure (High Priority)

**Problem**: When any migration step in `perform_migration()` fails, the process stops and returns an error but does not attempt to roll back changes made by previous successful steps. This can leave the filesystem in a partial/broken state.

**Impact**: Failed migrations create an inconsistent state where some data is in `MMRELAY_HOME` while some remains in legacy locations, requiring complex manual recovery.

**Implementation Requirements**:

2.1. Wrap `perform_migration()` with transactional rollback:

- Add try-except block around migration steps
- Track which steps completed successfully
- On exception or failure, automatically call enhanced `rollback_migration()`

  2.2. Add migration state tracking:

- Maintain list of completed steps during migration
- Include step information in migration report
- Use this to determine what needs rollback

  2.3. Enhance `rollback_migration()` for automatic invocation:

- Accept list of completed steps
- Roll back only steps that actually completed
- Log rollback actions clearly
- Remove migration state file only after successful rollback

  2.4. Add test coverage for rollback scenarios:

- `test_perform_migration_partial_failure_rollback()`
- `test_perform_migration_database_failure_rolls_back_creds()`
- `test_rollback_migration_no_completed_steps()`
- `test_rollback_migration_partial_steps()`

**Design Decisions**:

- **Rollback trigger**: Automatic on any step failure (success=False in result)
- **Rollback scope**: Only revert steps that completed before failure
- **State file**: Keep migration state file on failure, remove only after successful rollback
- **User notification**: Log clear messages about rollback being initiated

---

### 3. Enhance Rollback Coverage (High Priority)

**Problem**: The `rollback_migration()` function only attempts to restore `credentials.json` and `meshtastic.sqlite`. It does not handle:

- `logs/` directory
- `store/` directory (E2EE keys)
- `plugins/` directory (custom and community)
- GPX tracker data
- Any other migrated components

**Impact**: Partial rollbacks leave filesystem inconsistent, with some components restored and others in new location.

**Implementation Requirements**:

3.1. Restore `logs/` directory:

- Find `logs.bak.*` backup directories
- Restore from most recent backup
- Use `shutil.rmtree()` to replace existing logs directory
- Handle case where no backup exists (log warning)

  3.2. Restore `store/` directory:

- Find `store.bak.*` backup directories
- Restore from most recent backup
- Use `shutil.rmtree()` to replace existing store directory
- Handle Windows platform (E2EE not supported, skip store)

  3.3. Restore `plugins/` directory:

- Find `plugins.bak.*` backup directories
- Restore entire plugins directory structure
- Use `shutil.rmtree()` to replace existing plugins directory
- Preserve custom/ and community/ subdirectory structure

  3.4. Restore `config.yaml` (from Finding #1):

- Find `config.yaml.bak.*` backup files
- Restore most recent backup to new home
- Handle case where no backup exists

  3.5. Add comprehensive test coverage:

- `test_rollback_logs()`
- `test_rollback_store()`
- `test_rollback_plugins()`
- `test_rollback_config()`
- `test_rollback_no_backups()`
- `test_rollback_all_components()`

**Design Decisions**:

- **Backup format**: Use `shutil.copytree()` for directory backups to preserve structure
- **Backup naming**: Use `.bak.YYYYMMDD_HHMMSS` suffix for directories too
- **Restore priority**: Restore in reverse order of migration (last migrated, first restored)
- **Error handling**: If a specific component has no backup, log warning and continue with other components

---

### 4. Refactor `config.py` to Use `paths.py` Exclusively (Medium Priority)

**Problem**: `src/mmrelay/config.py` contains significant legacy path resolution logic that duplicates functionality now cleanly implemented in `paths.py`. This increases risk of inconsistent path resolution across the application.

**Impact**: Code maintainability and bug risk - two parallel path resolution systems must be kept in sync.

**Implementation Requirements**:

4.1. Audit all path resolution in `config.py`:

- Identify all functions returning filesystem paths
- Map each to corresponding `paths.py` function
- Note any custom logic not in `paths.py`

  4.2. Create migration strategy:

- Deprecate `get_base_dir()` - replace with `paths.get_home_dir()`
- Deprecate `get_data_dir()` - replace with `paths.get_home_dir()`
- Remove `custom_base_dir` / `custom_data_dir` module-level variables
- Remove `is_new_layout_enabled()` / `is_legacy_layout_enabled()`
- Remove `_get_env_base_dir()` / `_get_env_data_dir()` helpers

  4.3. Preserve backward compatibility during deprecation window:

- Keep existing function signatures
- Add `@functools.lru_cache` or module-level state for single deprecation warning per function
- Use `paths.py` functions internally
- Emit deprecation warnings consistently: "Use paths.get_home_dir() instead. Support will be removed in v1.4."

  4.4. Update all `config.py` consumers:

- Audit internal imports and usages
- Direct calls to `paths.get_home_dir()` where appropriate
- Update documentation strings referencing `get_base_dir()` / `get_data_dir()`

  4.5. Add test coverage for deprecation:

- `test_config_base_dir_deprecation_warning()`
- `test_config_data_dir_deprecation_warning()`
- `test_config_paths_consistency_with_paths_module()`

**Design Decisions**:

- **Deprecation timing**: v1.4 release will remove these functions entirely
- **Warning format**: Standard deprecation warning with removal version
- **Compatibility**: No breaking changes during v1.3 deprecation window
- **Internal implementation**: All deprecated functions immediately delegate to `paths.py`

---

### 5. Generic Plugin Data Migration (Future Consideration)

**Problem**: Only `migrate_gpxtracker()` exists for community plugin data migration. Other plugins that might use Tier 2 filesystem storage are not covered by a generic migration path.

**Current Status**: Acceptable - gpxtracker is the only community plugin with Tier 2 storage needs.

**Future Considerations** (when more plugins use Tier 2 storage):

5.1. Design plugin data migration discovery:

- Scan all legacy configs for plugin configurations
- Identify plugins with filesystem storage (`_data_dir` or similar patterns)
- Build migration task list dynamically

  5.2. Create generic `migrate_plugin_data()` function:

- Accept plugin name and legacy configuration
- Detect and migrate all filesystem artifacts
- Follow same patterns as `migrate_gpxtracker()`

  5.3. Plugin registration system:

- Plugins could declare their migration needs
- Migration system uses plugin metadata to determine migration steps

**Note**: This is explicitly **deferred** until more community plugins require Tier 2 filesystem storage.

---

## Implementation Sequence

### Phase 1: Critical Safety Improvements (Blocks Release)

1. Implement Finding #1: `config.yaml` migration
2. Implement Finding #2: Automatic rollback on failure
3. Implement Finding #3: Enhanced rollback coverage

### Phase 2: Technical Debt Cleanup

4. Implement Finding #5: Refactor `config.py` to use `paths.py`

### Phase 3: Future Capabilities

5. Implement Finding #4: Generic plugin data migration (when needed)

---

## Testing Strategy

### Unit Tests

- Each new function gets dedicated test class in `tests/test_migrate.py`
- Follow existing test patterns (Arrange-Act-Assert)
- Use `tmp_path` fixture for filesystem isolation
- Test both success and failure paths

### Integration Tests

- Test full migration flow with multiple legacy directories
- Test rollback with partial failures
- Test migration on various platform layouts (Linux, macOS, Windows)

### Edge Cases

- Migration with conflicting files (both legacy and new exist)
- Migration with permission errors
- Migration with symlinks in legacy locations
- Rollback when backup files are missing

---

## Documentation Updates Required

1. **User-facing migration guide** (`docs/MIGRATION_1.3.md`):
   - Document config.yaml migration behavior
   - Document automatic rollback feature
   - Update troubleshooting section for partial failures

2. **Developer documentation** (`docs/dev/`):
   - Update this plan document as items are completed
   - Document new migration architecture
   - Update `TESTING_GUIDE.md` with migration test patterns

3. **Deprecation notices**:
   - Add deprecation warnings to `config.py` docstrings
   - Document v1.4 breaking changes in release notes

---

## Review TODOs (PR Feedback)

- [x] `src/mmrelay/cli_utils.py`: add defensive type checks for `matrix_cfg` and `section_cfg` before calling `.get()` to avoid AttributeError when malformed config uses non-dict values.
- [x] `src/mmrelay/migrate.py`: remove redundant exception args in `logger.exception` for state writes; narrow broad `Exception` catch to `MigrationError` plus I/O/DB errors and re-raise unexpected exceptions after logging.
- [x] `tests/test_migrate_targeted.py`: remove unused `mock_print` patch or assert it to avoid ARG002.
- [x] `tests/test_config_edge_cases.py`: normalize/cast paths to strings before `"AppData"` membership checks; assert `DeprecationWarning` is emitted for `get_data_dir()` before checking message.
- [x] `tests/test_config.py`: rename unused `mock_makedirs` to `_mock_makedirs` in `test_get_log_dir_linux` to avoid ARG002.
- [x] `docs/dev/V1_3_MIGRATION_IMPROVEMENTS_PLAN.md`: update `Last Updated` to `2026-02-04`.
- [x] `src/mmrelay/matrix_utils.py`: refactor `connect_matrix` into smaller helpers (credentials resolution, client init/login, initial sync/room setup) to reduce complexity.

---

## Success Criteria

- [ ] All 3 high-priority findings (#1, #2, #3) are implemented
- [ ] All new functions have comprehensive test coverage
- [ ] No warnings in test suite execution
- [ ] Documentation updated for all user-visible changes
- [ ] Backward compatibility maintained during v1.3 deprecation window
- [ ] Linting and type checking pass (`.trunk/trunk check --all`, `mypy src/ --strict`)

---

## Open Questions

1. **Rollback granularity**: Should each migration step create its own backup, or should we create a snapshot of all components before starting? Current design: per-component backups.

2. **Migration state persistence**: Should migration state track attempted vs completed steps, or only completed steps? Current design: track completed steps.

3. **Config file conflict**: If both `MMRELAY_HOME/config.yaml` and legacy `config.yaml` exist, what should happen? Current design: backup target, overwrite (unless dry-run).

4. **Plugin data discovery**: Should we attempt to discover plugin data patterns automatically, or require plugins to declare migration needs? Current design: deferred until more plugins use Tier 2 storage.

---

**Document Version**: 1.2
**Last Updated**: 2026-02-05
**Audit Reference**: ../MIGRATION_1.3.md
