# Test Suite Modernization Design

Date: 2026-05-03

## Summary

The MMRelay test suite has grown organically to 138 test files totaling ~106,426 lines and ~4,086 test functions. This document catalogs the structural issues and prescribes a modernization plan.

## Current State

| Metric                                     | Value                          |
| ------------------------------------------ | ------------------------------ |
| Total test files                           | 138                            |
| Total test functions                       | 4,086                          |
| Total lines of test code                   | ~106,426                       |
| Files using `unittest.TestCase`            | 51 (37%)                       |
| Files using `@patch` decorators            | 59 (43%)                       |
| Files using `@pytest.mark.parametrize`     | 10 (7%)                        |
| Files mixing TestCase + pytest features    | 3                              |
| Files with 0 test functions                | 1 (`test_e2ee_integration.py`) |
| Skipped test files                         | 3                              |
| Files > 2,000 lines                        | 10                             |
| `_coverage.py` suffix (coverage gap fills) | 11                             |
| `_edge_cases.py` suffix                    | 8                              |

## Issue #1: Monolithic Test Files

Four files account for 21,076 lines (20% of the entire test suite):

| File                       | Lines | Tests | Lines/Test |
| -------------------------- | ----- | ----- | ---------- |
| `test_plugin_loader.py`    | 7,076 | 257   | 27.5       |
| `test_meshtastic_utils.py` | 5,797 | 189   | 30.7       |
| `test_main.py`             | 4,557 | 79    | 57.7       |
| `test_cli.py`              | 3,546 | 164   | 21.6       |

These are difficult to navigate, slow to edit, and encourage merge conflicts.

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

### Proposed decomposition: `test_meshtastic_utils.py` (5,797 lines)

Split into 10 domain files, folding in all 15 `test_meshtastic_utils_*` satellite files:

| File                                  | Content                                                                                     | Est. tests | Source classes + satellite files to absorb                                                                                                                                                                                                                                                                                                    |
| ------------------------------------- | ------------------------------------------------------------------------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `test_meshtastic_utils_messages.py`   | `on_meshtastic_message`, `send_text_reply`, reactions, replies, portnums                    | ~100+      | `TestMeshtasticUtils` (messages), `TestMessageProcessingEdgeCases`, `TestTextReplyFunctionality`; absorb `test_meshtastic_utils_message_paths.py`                                                                                                                                                                                             |
| `test_meshtastic_utils_connect.py`    | `connect_meshtastic`, serial/TCP/BLE connect, reconnect flag, startup drain, callback setup | ~100+      | `TestMeshtasticUtils` (connect), `TestConnectMeshtasticEdgeCases`, `TestReconnectingFlagLogic`; absorb `test_meshtastic_utils_connect_paths.py`, `test_meshtastic_utils_callback_lifecycle.py`, `test_meshtastic_utils_client_cleanup_coverage.py`, `test_meshtastic_utils_probe_coverage.py`, `test_meshtastic_utils_skew_drain_coverage.py` |
| `test_meshtastic_utils_reconnect.py`  | Reconnect function, backoff, cancellation                                                   | ~12        | Standalone reconnect tests; absorb `test_meshtastic_utils_reconnect.py`, `test_meshtastic_utils_reconnect_paths.py`, `test_meshtastic_utils_reconnect_bootstrap_coverage.py`                                                                                                                                                                  |
| `test_meshtastic_utils_disconnect.py` | `on_lost_meshtastic_connection`, BLE disconnect, event guards                               | ~20        | `TestConnectionLossHandling`; absorb `test_meshtastic_utils_event_guards_coverage.py`                                                                                                                                                                                                                                                         |
| `test_meshtastic_utils_metadata.py`   | `_get_device_metadata`, `_get_portnum_name`, `_get_packet_details`, node name refresh       | ~45        | `TestGetDeviceMetadata`, `TestGetPortnumName`, `TestGetPacketDetails`; absorb `test_meshtastic_utils_node_name_refresh.py`                                                                                                                                                                                                                    |
| `test_meshtastic_utils_ble.py`        | BLE-specific helpers, scan, discovery, exception classes                                    | ~35        | `TestBleHelperFunctions`, `TestBLEExceptionHandling`, `TestUncoveredMeshtasticUtilsPaths` (BLE portions)                                                                                                                                                                                                                                      |
| `test_meshtastic_utils_async.py`      | `_submit_coro`, `fire_and_forget`, `_make_awaitable`                                        | ~28        | `TestCoroutineSubmission`, `TestAsyncHelperUtilities`, `TestSubmitCoroActualImplementation`; absorb `test_meshtastic_utils_async_helpers.py`                                                                                                                                                                                                  |
| `test_meshtastic_utils_health.py`     | `check_connection`, health probes                                                           | ~25        | Absorb `test_meshtastic_utils_health.py` and health-related parts of coverage files                                                                                                                                                                                                                                                           |
| `test_meshtastic_utils_service.py`    | `is_running_as_service`, `serial_port_exists`                                               | ~9         | `TestServiceDetection`, `TestSerialPortDetection`                                                                                                                                                                                                                                                                                             |

## Issue #2: TestCase + Pytest Incompatibility

Three files mix `unittest.TestCase` with pytest-specific features, which is fragile and can cause subtle failures:

### `test_meshtastic_utils.py` (5,797 lines)

- 18 `unittest.TestCase` classes
- Uses `@pytest.fixture(autouse=True)` at module level (lines 89, 158)
- Uses `@pytest.mark.usefixtures(...)` on 3 classes
- Uses `@pytest.mark.parametrize` on a standalone function
- **Fix**: Convert to pure pytest style as part of decomposition

### `test_main.py` (4,557 lines)

- `class TestMain(unittest.TestCase)` at line 474
- Uses `@pytest.mark.parametrize` with `@patch` decorators on TestCase methods
- **Fix**: Convert to pure pytest style as part of decomposition

### `test_db_runtime_security.py` (1,379 lines)

- Has both `class TestDatabaseManager(unittest.TestCase)` and standalone `@pytest.mark.asyncio` functions
- **Fix**: Convert the TestCase class to pytest-style, merge with standalone functions

## Issue #3: Coverage Gap File Fragmentation

11 `_coverage.py` and 8 `_edge_cases.py` files exist as scattered supplements:

**Meshtastic utils satellite files (15 files):**

- `test_meshtastic_utils_coverage.py` (2,160 lines, 116 tests) - Largest supplement
- `test_meshtastic_utils_edge_cases.py` (1,434 lines, 44 tests)
- `test_meshtastic_utils_connect_paths.py` (1,793 lines, 46 tests)
- `test_meshtastic_utils_message_paths.py` (1,046 lines, 50 tests)
- Plus 11 more smaller files

**Other satellite files:**

- `test_meshtastic_utils_health.py`, `test_migrate_coverage.py`, `test_paths_coverage.py`, `test_core_utils_coverage.py`, `test_cli_health_check_coverage.py`, `test_cli_system_health_coverage.py`
- `test_main_shutdown_coverage.py`, `test_db_utils_name_sync_coverage.py`, `test_db_utils_close_manager_coverage.py`
- `test_db_runtime_del_coverage.py`, `test_db_utils_edge_cases.py`, `test_message_queue_additional_coverage.py`
- `test_message_queue_edge_cases.py`, `test_setup_utils_edge_cases.py`, `test_setup_utils_improvements.py`
- `test_setup_utils_execstart_improvements.py`, `test_cli_edge_cases.py`, `test_cli_targeted.py`
- `test_plugin_loader_edge_cases.py`

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

1. **Decompose `test_plugin_loader.py`** (7,076 → ~8 files) - Largest single file, most impact
2. **Decompose `test_meshtastic_utils.py`** (5,797 → ~9 files, absorbing 15 satellite files) - Folds in fragmentation
3. **Fix TestCase + pytest mix** in remaining files - Addresses fragility
4. **Clean up conftest.py** - Extract subsystems into separate modules
5. **Triage dead/skipped tests** - Clean up orphaned code
6. **Remove sys.path boilerplate** - Clean redundant imports

## Success Criteria

- All files < 2,000 lines
- No files mixing `unittest.TestCase` with pytest features
- No `_coverage.py` or `_edge_cases.py` files remaining
- All tests pass with same coverage
- No warnings or errors in test output
