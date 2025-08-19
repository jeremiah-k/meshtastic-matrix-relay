# Fix RuntimeWarning and add test coverage

## Bug Fix

**Fixed RuntimeWarning about unawaited 'reconnect' coroutine**
- Replace `_submit_coro(reconnect(), event_loop)` with `event_loop.create_task(reconnect())`
- Add safety check for closed event loops with `not event_loop.is_closed()`
- Use standard asyncio pattern for proper coroutine task management
- Eliminates test warning while maintaining identical functionality

## Test Coverage Additions

**CLI Utilities (`tests/test_cli_utils.py`)** - 268 lines
- Complete test suite for CLI command registry and utility functions
- Tests command lookup, validation, deprecation warnings, and message templates
- Covers all functions in `cli_utils.py` which was previously untested

**Main Application (`tests/test_main.py`)** - 119 lines
- Tests for application banner functionality and state management
- Database configuration handling (both current and legacy formats)
- Proper async mocking for main application flow

**Matrix Utilities (`tests/test_matrix_utils.py`)** - 152 lines
- Message formatting and truncation variable tests
- Prefix format validation with proper error handling
- Message storage configuration logic tests
- Basic functionality verification for utility functions

**Meshtastic Utilities (`tests/test_meshtastic_utils.py`)** - 41 lines
- Coroutine submission with non-coroutine inputs
- Text reply functionality with edge cases
- Enhanced BLE connection test setup

## Technical Details

- **Bug fix**: Addresses root cause of RuntimeWarning in test environments
- **Test patterns**: All new tests follow existing codebase conventions
- **Coverage focus**: Tests verify actual functionality rather than superficial metrics
- **Async handling**: Proper async/await mocking patterns implemented

**Total**: 580 lines of new test coverage plus critical bug fix
