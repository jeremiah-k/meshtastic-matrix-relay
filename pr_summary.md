# Test Coverage Improvements

This PR addresses test coverage gaps and resolves test failures across the codebase.

## Summary

- **+926 lines added, -98 lines removed** across 12 files
- **404 new lines** in `tests/test_cli.py` - comprehensive CLI function tests
- **435 new lines** in `tests/test_main.py` - main.py test coverage and async fixes
- **22 new lines** in `tests/conftest.py` - custom_data_dir fixture for test isolation

## Test Coverage Added

### CLI Functions (`tests/test_cli.py`)
- Added 4 new test classes with comprehensive coverage:
  - `TestCLIValidationFunctions` - credentials validation, E2EE dependencies, matrix authentication
  - `TestCLISubcommandHandlers` - config, auth, and service command dispatching
  - `TestE2EEConfigurationFunctions` - E2EE config validation scenarios
  - `TestE2EEAnalysisFunctions` - E2EE setup analysis and status checking
  - `TestE2EEPrintFunctions` - print function testing with proper mocking

### Main Function (`tests/test_main.py`)
- Added 2 new test classes:
  - `TestRunMainFunction` - run_main function with various scenarios
  - `TestMainAsyncFunction` - async main function initialization and event loop setup
- Fixed coroutine 'never awaited' warnings with proper cleanup mocks
- Added comprehensive edge case testing

## Test Fixes

### Python 3.10 Compatibility
- Fixed print function mocking conflict in `test_print_environment_summary_linux`
- Replaced `builtins.__import__` mock with `patch.dict('sys.modules')` approach
- Resolved namespace resolution issues between import and print mocking

### Async/Await Warnings
- Added proper coroutine cleanup in all asyncio.run mocks
- Implemented `mock_run_with_cleanup` pattern to close coroutines before returning
- Eliminated all "coroutine was never awaited" warnings

### Test Isolation
- Added `reset_custom_data_dir` pytest fixture to prevent test interference
- Fixed custom_data_dir permission issues in test environment
- Improved mock assertions for functions called multiple times

## Code Quality

### Formatting
- Applied trunk formatting across all modified files
- Fixed docstring formatting and spacing consistency
- Improved import organization and removed unused imports

### Mocking Improvements
- Enhanced mocking strategies for cross-version compatibility
- Better handling of module-level mocks vs function-level mocks
- More precise mock assertions using `assert_any_call` where appropriate

## Files Modified

- `tests/test_cli.py` (+404 lines) - Comprehensive CLI function tests
- `tests/test_main.py` (+435 lines) - Main function coverage and async fixes
- `tests/conftest.py` (+22 lines) - Test isolation fixture
- `tests/test_cli_utils.py` (+42/-42 lines) - Formatting and assertion updates
- `tests/test_config_checker.py` (+8/-8 lines) - Minor formatting improvements
- `tests/test_e2ee_unified.py` (+47/-47 lines) - Docstring and formatting updates
- `tests/test_matrix_utils.py` (-2 lines) - Removed unused imports
- `src/mmrelay/cli.py` (+8/-8 lines) - Minor formatting adjustments
- `src/mmrelay/e2ee_utils.py` (+35/-35 lines) - Code formatting improvements
- `src/mmrelay/matrix_utils.py` (+8/-8 lines) - Docstring formatting
- `src/mmrelay/meshtastic_utils.py` (+6/-6 lines) - Docstring formatting
- `docs/E2EE.md` (-7 lines) - Removed extra whitespace

## Test Results
- All tests pass across Python 3.10, 3.11, and 3.12
- Eliminated all test warnings and failures
- Improved test reliability and maintainability
