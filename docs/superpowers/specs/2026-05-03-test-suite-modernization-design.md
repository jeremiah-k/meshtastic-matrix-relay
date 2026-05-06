# Test Suite Modernization Design

Date: 2026-05-03

## Summary

The MMRelay test suite grew organically to 138 test files totaling ~106,426 lines and ~4,086 test functions at the baseline. After Phase 1 decomposition it stands at 149 files totaling ~106,054 lines and ~4,039 test functions. This document catalogs the structural issues and prescribes a modernization plan, tracking progress through four phases.

## Implementation Phases

### Phase 1 (Complete): Decompose Monolithic Files

Decomposed the three largest test files into domain-specific modules:

| Original File              | Lines | Result                                                              | Tests |
| -------------------------- | ----- | ------------------------------------------------------------------- | ----- |
| `test_plugin_loader.py`    | 7,076 | 13 domain files + `_plugin_loader_helpers.py`                       | 290   |
| `test_meshtastic_utils.py` | 5,797 | 9 domain files                                                      | 189   |
| `test_main.py`             | 4,557 | 9 domain files + helpers (`test_main.py` retained as 831-line core) | 79    |

All three original monoliths were decomposed: tests were extracted into domain-specific modules, with `test_main.py` retaining an 831-line core while its 79 tests moved to 9 domain files + helpers (converted to pure pytest style).

### Phase 2 (In Progress): Absorb Satellite Files

12 of 14 `test_meshtastic_utils_*` satellite files have been absorbed into their corresponding domain files. Fixture conflicts were resolved case-by-case by consolidating reset fixtures and adjusting autouse scope.

**Remaining satellites (2 files, ~3,594 lines):**

| File                                  | Lines | Notes                                                  |
| ------------------------------------- | ----- | ------------------------------------------------------ |
| `test_meshtastic_utils_coverage.py`   | 2,160 | Largest remaining supplement, catch-all coverage fills |
| `test_meshtastic_utils_edge_cases.py` | 1,434 | Catch-all edge cases                                   |

**Also still needs attention:**

| File                                     | Lines | Notes                                              |
| ---------------------------------------- | ----- | -------------------------------------------------- |
| `test_meshtastic_utils_connect_paths.py` | 1,445 | Uses `unittest.TestCase`; should convert to pytest |

**Absorbed (12 files):** `test_meshtastic_utils_message_paths.py`, `test_meshtastic_utils_callback_lifecycle.py`, `test_meshtastic_utils_client_cleanup_coverage.py`, `test_meshtastic_utils_event_guards_coverage.py`, `test_meshtastic_utils_health.py`, `test_meshtastic_utils_node_name_refresh.py`, `test_meshtastic_utils_probe_coverage.py`, `test_meshtastic_utils_reconnect_bootstrap_coverage.py`, `test_meshtastic_utils_reconnect_paths.py`, `test_meshtastic_utils_reconnect.py`, `test_meshtastic_utils_skew_drain_coverage.py`, `test_meshtastic_utils_async_helpers.py`.

### Phase 3 (In Progress): Remaining Tasks

- ~~Fix TestCase + pytest mix in `test_main.py`~~ Converted to pure pytest during decomposition
- ~~Fix TestCase + pytest mix in `test_db_runtime_security.py`~~ Converted to pure pytest
- Convert `test_meshtastic_utils_connect_paths.py` from `unittest.TestCase` to pure pytest (1 remaining TestCase file in meshtastic_utils domain)
- Clean up `conftest.py` — extract subsystems into separate modules
- Triage dead/skipped tests
- Remove redundant `sys.path` boilerplate

### Phase 4 (New): Lint/Type-Ignore Suppression Cleanup

82 `# type: ignore` suppressions and 30 `# noqa` suppressions remain across test files (plus 46 in `tests/helpers.py` which are largely unavoidable dynamic-attribute assignments). Many of the test-file suppressions are avoidable and mask real type issues.

**Approach:**

1. Audit each `# type: ignore` in test files. Classify as "avoidable" (fixable by adding proper types, using `Any`, or restructuring) or "necessary" (third-party stub limitations, dynamic attribute access inherent to the test pattern).
2. Fix avoidable suppressions. For necessary ones, narrow the ignore to a specific error code (e.g., `# type: ignore[attr-defined]` instead of bare `# type: ignore`).
3. Audit `# noqa` suppressions similarly. Many blanket `noqa` comments can be replaced with targeted `noqa: E402` etc.

**Success criteria:**

- No bare `# type: ignore` without an error code in test files
- `# type: ignore` count in test files reduced by at least 50% (from 82 to 40 or fewer)
- `# noqa` count in test files reduced by at least 50% (from 30 to 15 or fewer)
- All remaining suppressions have a brief inline comment explaining why they are necessary

---

## Current State (Post-Phase 1 / Phase 2 In Progress)

| Metric                                     | Value (Baseline) | Value (Current)                |
| ------------------------------------------ | ---------------- | ------------------------------ |
| Total test files                           | 138              | 149                            |
| Total test functions                       | 4,086            | ~4,039                         |
| Total lines of test code                   | ~106,426         | ~106,054                       |
| Files using `unittest.TestCase`            | 51 (37%)         | Reduced (see Phase 3)          |
| Files using `@patch` decorators            | 59 (43%)         | Unchanged                      |
| Files using `@pytest.mark.parametrize`     | 10 (7%)          | Unchanged                      |
| Files mixing TestCase + pytest features    | 3                | 1 (`connect_paths`)            |
| Files with 0 test functions                | 1                | 1 (`test_e2ee_integration.py`) |
| Skipped test files                         | 3                | Unchanged                      |
| Files > 2,000 lines                        | 10               | 8                              |
| `_coverage.py` suffix (coverage gap fills) | 11               | 11                             |
| `_edge_cases.py` suffix                    | 8                | 8                              |
| `# type: ignore` in test files             | N/A              | 82                             |
| `# noqa` in test files                     | N/A              | 30                             |

## Issue #1: Monolithic Test Files

Three of the original four oversized files have been decomposed. `test_cli.py` (3,542 lines) remains the largest single test file and a candidate for future decomposition.

**Original state (resolved):**

| File                       | Lines | Tests | Lines/Test | Status               |
| -------------------------- | ----- | ----- | ---------- | -------------------- |
| `test_plugin_loader.py`    | 7,076 | 290   | 24.4       | Decomposed (Phase 1) |
| `test_meshtastic_utils.py` | 5,797 | 189   | 30.7       | Decomposed (Phase 1) |
| `test_main.py`             | 4,557 | 79    | 57.7       | Decomposed (Phase 1) |

**Remaining oversized files (> 2,000 lines):**

| File                                | Lines | Notes                                    |
| ----------------------------------- | ----- | ---------------------------------------- |
| `test_cli.py`                       | 3,542 | Largest single file; candidate for split |
| `test_matrix_utils_relay.py`        | 2,837 | Relay domain                             |
| `test_meshtastic_utils_messages.py` | 2,271 | Messages domain                          |
| `test_migrate.py`                   | 2,259 | Migration tests                          |
| `test_db_utils.py`                  | 2,224 | Database utilities                       |
| `test_weather_plugin.py`            | 2,200 | Weather plugin                           |
| `test_meshtastic_utils_coverage.py` | 2,160 | Satellite (Phase 2 target)               |
| `test_migrate_coverage.py`          | 2,106 | Migration coverage supplement            |

### Proposed decomposition: `test_plugin_loader.py` (7,076 lines)

Split into 8 domain files:

| File                              | Content                                                                          | Est. tests | Source classes                                                                          |
| --------------------------------- | -------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------- |
| `test_plugin_loader_core.py`      | Core loading, directory discovery, scheduling                                    | ~65        | `TestPluginLoader` (loading portion), scheduler tests from `TestDependencyInstallation` |
| `test_plugin_loader_git.py`       | All Git operations (clone, update, fetch, checkout)                              | ~56        | `TestGitOperations`, git tests from `TestPluginLoader` and `TestDependencyInstallation` |
| `test_plugin_loader_deps.py`      | Dependency installation, requirements, target management                         | ~55        | `TestDependencyInstallation` (dep portion), `TestPluginLoader` (auto-install portion)   |
| `test_plugin_loader_security.py`  | URL validation, host allowlisting, requirement filtering                         | ~41        | `TestURLValidation`, `TestPluginSecurityGuards`, `TestRequirementFiltering`             |
| `test_plugin_loader_collect.py`   | Requirements collection/parsing                                                  | ~10        | `TestCollectRequirements`                                                               |
| `test_plugin_loader_runner.py`    | Command runner, `_run` helper                                                    | ~11        | `TestCommandRunner` + standalone functions                                              |
| `test_plugin_loader_cache.py`     | Python cache cleaning, namespace detection                                       | ~16        | `TestCleanPythonCache`, `TestIsNamespacePackageDirectory`                               |
| `test_plugin_loader_community.py` | Community plugin security, state files, compare URLs, thread safety, exec module | ~18        | `TestCommunityPluginSecurityHelpers`, `TestExecPluginModuleThreadSafety`                |

### Decomposition: `test_meshtastic_utils.py` (5,797 lines) — Phase 1

Split into 9 domain files. Satellite file absorption is deferred to Phase 2.

| File                                     | Content                                                                                     | Tests | Source classes                                                                                   |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------------ |
| `test_meshtastic_utils_messages.py`      | `on_meshtastic_message`, `send_text_reply`, reactions, replies, portnums                    | 39    | `TestMeshtasticUtils` (messages), `TestMessageProcessingEdgeCases`, `TestTextReplyFunctionality` |
| `test_meshtastic_utils_connect.py`       | `connect_meshtastic`, serial/TCP/BLE connect, reconnect flag, startup drain, callback setup | 15+   | `TestMeshtasticUtils` (connect), `TestConnectMeshtasticEdgeCases`, `TestReconnectingFlagLogic`   |
| `test_meshtastic_utils_disconnect.py`    | `on_lost_meshtastic_connection`, BLE disconnect                                             | 10    | `TestConnectionLossHandling`                                                                     |
| `test_meshtastic_utils_metadata.py`      | `_get_device_metadata`, `_get_portnum_name`, `_get_packet_details`                          | 29    | `TestGetDeviceMetadata`, `TestGetPortnumName`, `TestGetPacketDetails`                            |
| `test_meshtastic_utils_ble.py`           | BLE-specific helpers, scan, discovery, exception classes                                    | 6     | `TestBleHelperFunctions`, `TestBLEExceptionHandling`                                             |
| `test_meshtastic_utils_async.py`         | `_submit_coro`, `fire_and_forget`, `_make_awaitable`                                        | 16    | `TestCoroutineSubmission`, `TestAsyncHelperUtilities`, `TestSubmitCoroActualImplementation`      |
| `test_meshtastic_utils_service.py`       | `is_running_as_service`, `serial_port_exists`                                               | 9     | `TestServiceDetection`, `TestSerialPortDetection`                                                |
| `test_meshtastic_utils_message_edge.py`  | Message processing edge cases                                                               | 33    | `TestMessageProcessingEdgeCases` (edge portions)                                                 |
| `test_meshtastic_utils_connect_paths.py` | Connection path edge cases, reconnect                                                       | 26    | Reconnect and connection path tests                                                              |

**Satellite files retained** (2 of 14 remain, ~3,594 lines): `test_meshtastic_utils_coverage.py`, `test_meshtastic_utils_edge_cases.py`. The other 12 have been absorbed into domain files (see Phase 2). Additionally, `test_meshtastic_utils_connect_paths.py` (1,445 lines) still uses `unittest.TestCase` and needs conversion.

## Issue #2: TestCase + Pytest Incompatibility

Three files originally mixed `unittest.TestCase` with pytest-specific features. Two have been resolved:

### `test_meshtastic_utils.py` (5,797 lines) — Resolved

Converted to pure pytest style during Phase 1 decomposition.

### `test_main.py` (4,557 lines) — Resolved

Converted to pure pytest style during Phase 1 decomposition. `TestMain(unittest.TestCase)` and its `@pytest.mark.parametrize` usage were converted to plain pytest functions.

### `test_db_runtime_security.py` (1,379 lines) — Resolved

Converted to pure pytest style. The `TestDatabaseManager` TestCase class and standalone async functions are now consistent.

### `test_meshtastic_utils_connect_paths.py` (1,445 lines) — Remaining

Still uses `unittest.TestCase`. Should be converted to pure pytest as part of Phase 2 cleanup.

## Issue #3: Coverage Gap File Fragmentation

11 `_coverage.py` and 8 `_edge_cases.py` files exist as scattered supplements:

**Meshtastic utils satellites (2 remaining of original 14):**

- `test_meshtastic_utils_coverage.py` (2,160 lines) - Largest remaining supplement
- `test_meshtastic_utils_edge_cases.py` (1,434 lines)

The other 12 meshtastic_utils satellites have been absorbed into domain files (see Phase 2).

**Other satellite files:**

- `test_migrate_coverage.py`, `test_paths_coverage.py`, `test_core_utils_coverage.py`, `test_cli_health_check_coverage.py`, `test_cli_system_health_coverage.py`
- `test_main_shutdown_coverage.py`, `test_db_utils_name_sync_coverage.py`, `test_db_utils_close_manager_coverage.py`
- `test_db_runtime_del_coverage.py`, `test_db_utils_edge_cases.py`, `test_message_queue_additional_coverage.py`
- `test_message_queue_edge_cases.py`, `test_setup_utils_edge_cases.py`, `test_setup_utils_improvements.py`
- `test_setup_utils_execstart_improvements.py`, `test_cli_edge_cases.py`, `test_cli_targeted.py`
- `test_plugin_loader_edge_cases.py`, `test_config_edge_cases.py`

These files exist because the parent files grew too large to easily add tests to. Folding them into their decomposed domain files eliminates the fragmentation.

## Issue #4: Dead/Orphaned Files

- **`test_e2ee_integration.py`** (382 lines, 0 test functions) - Appears to be a manual runner script, not a test. Should be either converted to real tests or removed.
- **Skipped test files**: `test_migrate_rollback_components.py`, `test_migrate_coverage.py`, `test_cli_windows_integration.py` contain `@pytest.mark.skip` tests that need triage.

## Issue #5: conftest.py Complexity

`conftest.py` (1,679 lines) handles too many concerns:

- Session-wide `sys.modules` mocking (lines 37-81)
- 8 mock exception classes
- 8 mock isinstance() classes
- SQLite connection provenance tracking (~130 lines)
- BLE future cleanup (~90 lines)
- 13 fixtures (6 autouse)
- pytest hooks for connection leak reporting

Recommend extracting into:

- `tests/mocks.py` - sys.modules mocking and mock classes
- `tests/sqlite_provenance.py` - SQLite connection tracking
- `tests/ble_cleanup.py` - BLE cleanup utilities

## Issue #6: sys.path Boilerplate

Multiple test files repeat `sys.path.insert(0, ...)` for src/ access:

- `test_plugin_loader.py:28`
- `test_meshtastic_utils.py:30`
- `test_main.py` (indirectly via conftest)
- `test_cli.py:34-35`

This is already handled by `conftest.py:12-14`, so the per-file inserts are redundant and should be removed.

## Implementation Order

1. ~~**Decompose `test_plugin_loader.py`** (7,076 → ~13 files)~~ ✅ Phase 1 Complete
2. ~~**Decompose `test_meshtastic_utils.py`** (5,797 → ~9 files)~~ ✅ Phase 1 Complete
3. ~~**Decompose `test_main.py`** (4,557 → ~9 files + core)~~ ✅ Phase 1 Complete
4. ~~**Fix TestCase + pytest mix in `test_main.py`**~~ ✅ Done during decomposition
5. ~~**Fix TestCase + pytest mix in `test_db_runtime_security.py`**~~ ✅ Done
6. **Absorb remaining meshtastic satellite files** — Fold 2 remaining satellites into domain files (Phase 2, in progress)
7. **Convert `test_meshtastic_utils_connect_paths.py`** from TestCase to pytest (Phase 3)
8. **Clean up conftest.py** — Extract subsystems into separate modules (Phase 3)
9. **Triage dead/skipped tests** — Clean up orphaned code (Phase 3)
10. **Remove sys.path boilerplate** — Clean redundant imports (Phase 3)
11. **Lint/type-ignore suppression cleanup** — Audit and reduce `# type: ignore` and `# noqa` in test files (Phase 4, new)

## Success Criteria

### Phase 1 (Complete)

- `test_plugin_loader.py` decomposed into 13 domain files + helpers
- `test_meshtastic_utils.py` decomposed into 9 domain files
- `test_main.py` decomposed into 9 domain files + core
- All tests pass with same coverage
- No warnings or errors in test output

### Phase 2 (In Progress)

- 12 of 14 satellite files absorbed into domain files
- Remaining 2 satellites (`_coverage.py`, `_edge_cases.py`) folded or justified
- `test_meshtastic_utils_connect_paths.py` converted from TestCase to pytest
- All tests pass with same coverage

### Phase 3 (Remaining)

- No files mixing `unittest.TestCase` with pytest features
- `conftest.py` extracted into focused modules
- Dead/skipped tests triaged and resolved
- No redundant `sys.path` boilerplate

### Phase 4 (New: Lint/Type-Ignore Cleanup)

- No bare `# type: ignore` in test files (all have error codes)
- `# type: ignore` count reduced by at least 50% (82 → 40 or fewer)
- `# noqa` count reduced by at least 50% (30 → 15 or fewer)
- All remaining suppressions have inline justification comments

### Full Modernization

- All files < 2,000 lines
- No `_coverage.py` or `_edge_cases.py` files remaining as catch-all supplements
- All tests pass with same coverage
- No warnings or errors in test output
