# Testing Guide

This guide covers testing patterns and best practices for the meshtastic-matrix-relay project.

First check for an existing virtual environment in `venv/`; if it does not exist, create one and run `. venv/bin/activate && pip install -e '.[dev,test,e2e]'` then continue.

## Type Checking

Run strict mypy type checking (after activating the venv):

```bash
python -m mypy src/ --strict
```

## Async Function Mocking Patterns

### Problem: RuntimeWarnings with AsyncMock

When testing functions that call async code via `asyncio.run()`, using `AsyncMock` can lead to RuntimeWarnings about unawaited coroutines:

```text
RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited
```

### Solution: Direct Return Value Mocking

**✅ CORRECT PATTERN** - Use regular `Mock` with direct return values:

```python
@patch("mmrelay.matrix_utils.logout_matrix_bot")
@patch("builtins.print")
def test_handle_auth_logout_success(self, mock_print, mock_logout):
    """Test successful logout."""
    # ✅ CORRECT: Direct return value for async function
    mock_logout.return_value = True

    result = handle_auth_logout(self.mock_args)

    self.assertEqual(result, 0)
```

**❌ INCORRECT PATTERN** - Avoid AsyncMock for functions called via asyncio.run():

```python
# ❌ DON'T DO THIS - causes RuntimeWarnings
mock_logout = AsyncMock(return_value=True)
```

### Exception Testing Pattern

For testing exception handling in async functions:

```python
@patch("mmrelay.matrix_utils.logout_matrix_bot")
@patch("builtins.print")
def test_handle_auth_logout_keyboard_interrupt(self, mock_print, mock_logout):
    """Test logout handles KeyboardInterrupt gracefully."""
    # ✅ CORRECT: Use side_effect to raise exceptions
    mock_logout.side_effect = KeyboardInterrupt()

    result = handle_auth_logout(self.mock_args)

    self.assertEqual(result, 1)
    mock_print.assert_any_call("\nLogout cancelled by user.")
```

### When to Use AsyncMock vs Regular Mock

| Scenario                                      | Use            | Pattern                                      |
| --------------------------------------------- | -------------- | -------------------------------------------- |
| Function calls async code via `asyncio.run()` | Regular `Mock` | `mock_func.return_value = result`            |
| Direct async function testing                 | `AsyncMock`    | `mock_func = AsyncMock(return_value=result)` |
| Exception in async context                    | Regular `Mock` | `mock_func.side_effect = Exception()`        |

## Standardized Async Patterns

Use standardized async testing patterns and avoid test-environment detection branches (for example, `MMRELAY_TESTING`).

### Key Principles

1. **No Test Environment Detection**: Code should behave consistently in test and production environments
2. **Use `asyncio.to_thread()`**: For running blocking operations in async context
3. **Consistent Mocking**: Use the patterns described above for all async function testing
4. **Global State Isolation**: Use `reset_meshtastic_globals` fixture for tests that modify global state

### Threading and Executor Patterns

Some code paths use `asyncio.to_thread()` or `loop.run_in_executor()`. In tests, real thread pools can hang or introduce nondeterminism. Prefer inline execution patterns:

1. **Inline executors**: Patch `asyncio.get_running_loop` with `_make_patched_get_running_loop()` (see `tests/test_main.py`) so `run_in_executor` executes synchronously via `InlineExecutorLoop`.
2. **Immediate shutdown events**: Use `_ImmediateEvent()` (from `tests/test_main.py`) to skip long-running loops that wait on shutdown events.
3. **Avoid KeyboardInterrupt control flow**: Do not raise `KeyboardInterrupt` inside async tasks; prefer explicit shutdown events for deterministic cleanup.
4. **Cleanup isolation**: When a test reaches `main()` cleanup, patch `shutdown_plugins` and `stop_message_queue` to no-ops unless the test is explicitly validating cleanup behavior.

### Legacy Pattern Cleanup

If you encounter code that still uses test-environment detection patterns:

```python
# ❌ Avoid
if os.getenv("MMRELAY_TESTING"):
    # Test-specific behavior
else:
    # Production behavior
```

Replace with consistent behavior:

```python
# ✅ Preferred
async def function_that_works_everywhere():
    # Same logic for test and production
    return await asyncio.to_thread(blocking_operation)
```

## Test Organization

### Test File Structure

For new tests, prefer pytest functions for better parametrization and fixture support:

```python
import pytest
from unittest.mock import MagicMock, patch

@pytest.mark.parametrize(
    "input_value, expected",
    [
        (1, 2),
        (2, 4),
    ],
)
def test_function_parametrized(input_value, expected):
    """Test function with parametrized inputs."""
    result = function_under_test(input_value)
    assert result == expected

def test_specific_behavior():
    """Test specific behavior with descriptive name."""
    # Arrange
    # Act
    # Assert
```

Legacy tests may use unittest.TestCase:

```python
import unittest
from unittest.mock import MagicMock, mock_open, patch

class TestFeatureName(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        # Initialize common test data

    def test_specific_behavior(self):
        """Test specific behavior with descriptive name."""
        # Arrange
        # Act
        # Assert
```

### Matrix Test Layout and Patch Targets

Matrix tests are maintained as split domain files. Keep that structure stable.

- Add tests to the existing domain file for the behavior under test.
- Do not recreate catch-all Matrix test files (for example, a new omnibus `test_matrix_utils*.py` that mixes unrelated domains).
- Keep connect/bootstrap, credentials, room mapping, relay/event, and prefix behavior in separate files.

Patch target policy for Matrix tests:

- Default to patching through `mmrelay.matrix_utils.*` (facade patching).
- Patch `mmrelay.matrix.<module>.*` directly only when the test is explicitly about source-module lookup/type behavior.

Source-module to test-file mapping:

- `mmrelay.matrix.sync_bootstrap` (`connect_matrix`, sync/bootstrap ordering): `tests/test_matrix_utils_connect.py`, `tests/test_matrix_utils_connect_credentials.py`, `tests/test_matrix_utils_connect_e2ee.py`, `tests/test_matrix_utils_connect_rooms.py`, `tests/test_matrix_utils_connect_sync.py`
- `mmrelay.matrix.credentials`: `tests/test_matrix_utils_connect_credentials.py`, `tests/test_matrix_utils_auth_credentials.py`
- `mmrelay.matrix.events` (`on_room_message`, invite/member handlers): `tests/test_matrix_utils_relay.py`, `tests/test_matrix_utils_invite.py`
- `mmrelay.matrix.prefixes`: `tests/test_matrix_utils_core.py` (behavioral policy tests), `tests/test_prefix_customization.py` (format/compatibility scenarios)
- `mmrelay.matrix.command_bridge`: `tests/test_command_bridge_channel_validation.py`, `tests/test_matrix_utils_detection.py`

Matrix/Meshtastic global-state reset policy:

- Use `reset_matrix_utils_globals` when a test mutates Matrix facade globals (`matrix_client`, `matrix_rooms`, `bot_user_id`, related startup state).
- Use `reset_meshtastic_globals` when a test mutates Meshtastic globals, executors, or reconnect/shutdown flags.
- For cross-boundary tests that mutate both sides, use both fixtures.

### Global State Management

For tests that interact with meshtastic utilities or plugins that maintain global state, use the `reset_meshtastic_globals` fixture to ensure proper test isolation:

```python
import pytest

@pytest.mark.usefixtures("reset_meshtastic_globals")
class TestMyPlugin(unittest.TestCase):
    """Test plugin with proper global state isolation."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_args = MagicMock()
        # Global state will be reset before each test

    def test_plugin_behavior(self):
        """Test plugin behavior without global state interference."""
        # Test implementation
        pass
```

The `reset_meshtastic_globals` fixture automatically:

- Resets module-level globals in `mmrelay.meshtastic_utils`
- Clears config, logger, meshtastic_client, and event_loop references
- Resets state flags (reconnecting, shutting_down, etc.)
- Ensures clean test isolation between test runs

### Async Mock Cleanup

The project uses a cleanup fixture in `tests/conftest.py` to handle AsyncMock cleanup for certain test modules. If you're writing tests that don't need AsyncMock warnings suppressed, ensure your test module is not in the `asyncmock_patterns` list:

```python
# In conftest.py
asyncmock_patterns = [
    "test_async_patterns",
    "test_matrix_utils_edge_cases",
    "test_mesh_relay_plugin",
    "test_map_plugin",
    "test_meshtastic_utils",
    "test_base_plugin",
    "test_telemetry_plugin",
    "test_performance_stress",
    "test_main",
    "test_health_plugin",
    "test_error_boundaries",
    "test_integration_scenarios",
    "test_help_plugin",
    "test_ping_plugin",
    "test_nodes_plugin",
]
```

## Coverage and Quality

### Running Tests with Coverage

```bash
# Run specific test module
python -m pytest tests/test_cli.py -v --cov --tb=short

# Run all tests with coverage
python -m pytest -v --cov --junitxml=junit.xml -o junit_family=legacy --timeout=60
```

### Code Quality Checks

```bash
# Run trunk check to fix formatting and linting issues
.trunk/trunk check --fix --all

# Check specific files
.trunk/trunk check --fix tests/test_cli.py
```

## Best Practices

### 1. Treat Warnings as Errors

**⚠️ CRITICAL**: All test warnings must be eliminated, not ignored. Warnings indicate underlying problems that can hide real issues and cause flaky tests.

- **RuntimeWarnings about unawaited coroutines**: Fix by using proper Mock patterns (see above)
- **DeprecationWarnings**: Update code to use non-deprecated APIs
- **Any other warnings**: Investigate and fix the root cause

Do not suppress warnings; fix the underlying issue. Warnings in tests often indicate:

- Incorrect mocking patterns
- Resource leaks
- API misuse
- Configuration problems

Recommended pytest configuration (picked up by CI):

```ini
# pytest.ini (repo root)
[pytest]
addopts = -W error
filterwarnings =
    error
    # Allow narrowly scoped ignores for noisy third-party libs only if necessary:
    # ignore:.*some benign 3rd-party warning.*:DeprecationWarning:third_party_pkg
```

### 2. Testing Code That Uses Custom Loggers

The project uses a custom logger configuration (`log_utils.get_logger`) that manages its own handlers. This creates a conflict with `unittest.TestCase.assertLogs()`.

> **Note**: `mmrelay.cli._get_logger` is a wrapper around `log_utils.get_logger` that adds CLI-specific configuration. The import of the function under test must happen _inside_ the test function, after mock setup, to ensure the function uses the mocked logger rather than caching a reference to the real one at module load time.

**Problem**: `assertLogs()` works by attaching a `_CapturingHandler` to the target logger (handler-based capture, not propagation-based). The conflict is that `_configure_logger` replaces the logger's handlers when called, which removes the capturing handler that `assertLogs()` attached, causing logs to be lost.

**✅ CORRECT PATTERN** - Mock `_get_logger` to return a controllable logger:

```python
@patch("mmrelay.cli._get_logger")
@patch("mmrelay.cli.os.path.exists")
def test_validation_logs_warning(self, mock_exists, mock_get_logger):
    """Test that validation logs appropriate warnings."""
    import logging
    import uuid

    # Create a uniquely named test logger to avoid handler leakage across tests
    logger_name = f"mmrelay.test.{self._testMethodName}.{uuid.uuid4().hex[:8]}"
    mock_logger = logging.getLogger(logger_name)
    mock_logger.handlers.clear()
    mock_logger.setLevel(logging.DEBUG)
    # propagate=True only needed if function_under_test creates child loggers
    # (e.g., "mmrelay.test.<id>.submodule") that should propagate to parent
    mock_logger.propagate = True
    mock_get_logger.return_value = mock_logger

    # Import after mock setup to ensure function uses the mocked logger
    from mmrelay.cli import function_under_test

    config_path = "/tmp/mmrelay-config.yaml"
    with self.assertLogs(logger_name, level="WARNING"):
        result = function_under_test(config_path)

    self.assertFalse(result)
```

**❌ INCORRECT PATTERN** - Setting `propagate=True` on the configured logger:

```python
# ❌ This won't work - _configure_logger clears handlers and sets propagate=False
logger = logging.getLogger("mmrelay.cli")
logger.propagate = True  # Gets reset when _get_logger is called

config_path = "/tmp/mmrelay-config.yaml"
with self.assertLogs("mmrelay.cli", level="WARNING"):
    result = function_under_test(config_path)  # Logs are lost
```

**Future Consideration**: This pattern is a workaround for the current logger architecture. A future refactoring could:

- Make logger propagation configurable for testing
- Use dependency injection for loggers instead of module-level caching
- Provide a test fixture that automatically mocks `_get_logger`

### 3. Descriptive Test Names

- Use descriptive test method names that explain the scenario
- Include expected behavior in the name

### 4. Arrange-Act-Assert Pattern

- **Arrange**: Set up test data and mocks
- **Act**: Execute the code under test
- **Assert**: Verify the expected behavior

### 5. Mock at the Right Level

- Mock external dependencies, not internal logic
- Mock at the boundary of your system under test

### 6. Test Error Conditions

- Test both success and failure scenarios
- Test exception handling and edge cases
- Consider adding explicit patterns for asserting log messages on failures in async paths
  Example:
  ```python
  # some_async_wrapper calls async code via asyncio.run()
  # Ensure logger name matches the component under test (e.g., "mmrelay.MessageQueue")
  with self.assertLogs("mmrelay.MessageQueue", level="ERROR") as cm:
      result = some_async_wrapper(self.mock_args)
      self.assertIn("expected failure detail", "\n".join(cm.output))
  ```

### 7. Avoid Test Interdependence

- Each test should be independent
- Use `setUp()` and `tearDown()` for common initialization

## Common Patterns

### File System Mocking

```python
@patch("builtins.open", new_callable=mock_open, read_data="test data")
@patch("os.path.exists", return_value=True)
def test_file_operations(self, mock_exists, mock_file):
    # Test file operations
    pass
```

### Environment Variable Mocking

```python
@patch.dict(os.environ, {"TEST_VAR": "test_value"})
def test_environment_dependent_code(self):
    # Test code that depends on environment variables
    pass
```

### Print Output Testing

```python
@patch("builtins.print")
def test_output_messages(self, mock_print):
    # Execute code that prints
    function_that_prints()

    # Verify specific messages were printed
    mock_print.assert_any_call("Expected message")
```

### Exception Handling in Plugins

When testing plugin exception handling, ensure you test both network-level exceptions and data parsing exceptions:

```python
@patch("mmrelay.plugins.weather_plugin.requests.get")
def test_weather_plugin_requests_exception(self, mock_get):
    """Test weather plugin handles requests exceptions properly."""
    # Mock network-level failure
    mock_get.side_effect = requests.exceptions.ConnectionError("Network error")

    plugin = Plugin()
    result = plugin.generate_forecast(40.7128, -74.0060)

    self.assertEqual(result, "Error fetching weather data.")

@patch("mmrelay.plugins.weather_plugin.requests.get")
def test_weather_plugin_attribute_error_fallback(self, mock_get):
    """Test weather plugin handles AttributeError during response processing."""
    # Mock response that raises AttributeError on raise_for_status
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = AttributeError("Some error")
    mock_response.raise_for_status.__module__ = "requests"  # Make it look like requests exception
    mock_get.return_value = mock_response

    plugin = Plugin()
    result = plugin.generate_forecast(40.7128, -74.0060)

    self.assertEqual(result, "Error fetching weather data.")
```

**Key Patterns for Exception Testing:**

1. **Network Exceptions**: Test `requests.exceptions.RequestException` and subclasses
2. **Attribute Errors**: Test cases where response objects might not have expected attributes
3. **Data Parsing Errors**: Test malformed JSON responses and missing data fields
4. **Multiple Exception Types**: Use tuple catching for related exceptions:

```python
# ✅ GOOD: Catch related exceptions together
except (requests.exceptions.RequestException, AttributeError):
    self.logger.exception("Error fetching weather data")
    return "Error fetching weather data."

# ✅ GOOD: Handle parsing errors specifically
except (KeyError, IndexError, TypeError, ValueError, AttributeError):
    self.logger.exception("Malformed weather data")
    return "Error parsing weather data."
```

## Troubleshooting

### RuntimeWarnings About Unawaited Coroutines

If you see warnings like:

```text
RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited
```

**Solution**: Replace `AsyncMock` with regular `Mock` and use `return_value` or `side_effect`:

```python
# ❌ Causes warnings
mock_func = AsyncMock(return_value=True)

# ✅ No warnings
mock_func.return_value = True
```

### Test Discovery Issues

Ensure test files:

- Start with `test_` prefix
- Are in the `tests/` directory
- Import the modules being tested correctly

### Mock Not Being Called

Common issues:

- Wrong import path in `@patch`
- Mock applied in wrong order (decorators apply bottom-to-top)
- Function not actually calling the mocked dependency

### Patch Decorator Argument Order

When stacking `@patch` decorators, the innermost decorator (closest to the
function) provides the first mock argument:

```python
@patch("mmrelay.module.outer_dependency")
@patch("mmrelay.module.inner_dependency")
def test_example(mock_inner, mock_outer):
    # mock_inner -> inner_dependency
    # mock_outer -> outer_dependency
    pass
```

### Meshtastic Message Handler Early-Exit Guards

The `on_meshtastic_message` handler (in `mmrelay.meshtastic_utils`) returns
early if the interface lacks `myInfo` or if a packet is directed to another
node. When tests expect relays or plugin handling, set
`interface.myInfo.my_node_num` and ensure `packet["to"]` is either
`BROADCAST_NUM` or the relay node id.

### Parametrized Tests with Patch Decorators

When using `@pytest.mark.parametrize` with `@patch` decorators in `unittest.TestCase` classes, the parametrized arguments may not be passed correctly, causing `TypeError: missing 1 required positional argument`.

**Problem**: pytest.mark.parametrize injects its arguments after `self` in unittest.TestCase methods, which shifts the positions of @patch injected mocks. This causes mock_logger to receive the url value, leading to TypeError.

```python
# ❌ PROBLEMATIC - May cause TypeError about missing arguments
@pytest.mark.parametrize("url", ["", "   "])
@patch("mmrelay.module._some_function")
@patch("mmrelay.module.logger")
def test_clone_or_update_repo_invalid_url(self, mock_logger, mock_some_func, url):
    # This may fail with "missing 1 required positional argument: 'url'"
    pass
```

**Solution**: Use separate test methods instead of parametrization:

```python
# ✅ CORRECT - Separate test methods avoid decorator conflicts
@patch("mmrelay.module._some_function")
@patch("mmrelay.module.logger")
def test_clone_or_update_repo_invalid_url_empty(self, mock_logger, mock_some_func):
    """Test clone with empty URL."""
    ref = {"type": "branch", "value": "main"}
    result = clone_or_update_repo("", ref, "/tmp")
    self.assertFalse(result)

@patch("mmrelay.module._some_function")
@patch("mmrelay.module.logger")
def test_clone_or_update_repo_invalid_url_whitespace(self, mock_logger, mock_some_func):
    """Test clone with whitespace-only URL."""
    ref = {"type": "branch", "value": "main"}
    result = clone_or_update_repo("   ", ref, "/tmp")
    self.assertFalse(result)
```

**Note**: Parametrized tests work fine with pytest functions (not unittest.TestCase), as shown in the Test Organization section.

## Integration Testing

Integration tests are required for behavior that spans module boundaries (for example, connection lifecycle + retry logic + global state handling), even when unit tests exist for each helper.

### When to add an integration test

Add or extend integration tests when a change:

1. Changes control flow across components (`main` + `meshtastic_utils`, `db_utils` + `db_runtime`, plugin loader + scheduler, etc.).
2. Introduces/reworks retry, timeout, or shutdown behavior.
3. Fixes a production regression that required multiple moving parts to reproduce.
4. Adds CI shell-harness behavior in `scripts/ci/run-mmrelay-meshtasticd-integration.sh`.

### Where integration tests should live

- Python integration scenarios: `tests/test_integration_scenarios.py` or a focused `tests/test_*integration*.py` module.
- Meshtastic connection/recovery integration paths: keep scenario-style tests close to `tests/test_meshtastic_utils_connect_paths.py` and add a higher-level scenario in `tests/test_integration_scenarios.py` when the bug crosses module boundaries.
- Shell/CI integration harness updates: `scripts/ci/run-mmrelay-meshtasticd-integration.sh` (only for behavior that must be validated in the runtime harness).

### Marking integration tests

Tag integration tests with `@pytest.mark.integration` so they can be selected or
excluded quickly:

```python
import pytest


@pytest.mark.integration
def test_connection_lifecycle_with_retry():
    ...
```

Useful commands:

- Run only integration tests: `python -m pytest -m integration -v --timeout=60`
- Skip integration tests during fast local cycles:
  `python -m pytest -m "not integration" -v --timeout=60`

### Integration test design rules

1. Keep tests deterministic: no real BLE hardware, no external network calls, no wall-clock sleeps without patching/mocking.
2. Bound execution time: use explicit retry limits and timeout settings in test configs.
3. Assert observable outcomes, not just internal calls:
   - Returned client/state
   - Global state cleanup
   - Retry/backoff stop conditions
   - Expected log/warning messages for operator visibility
4. Isolate global state:
   - Use `reset_meshtastic_globals` when touching `mmrelay.meshtastic_utils`.
   - In scenario tests that mutate executor/future state, call
     `mmrelay.meshtastic_utils.shutdown_shared_executors()` and then reset any
     remaining module-level future/executor references as needed.
5. Keep integration scope minimal:
   - Mock external dependencies (Meshtastic library objects, filesystem/network boundaries).
   - Exercise real orchestration code between modules.

### CI harness integration checks

When updating `scripts/ci/run-mmrelay-meshtasticd-integration.sh`:

1. Add assertions for both positive and negative outcomes (for example, stale rows removed and live rows preserved).
2. Ensure failures are attributed to the correct test block (`start_test` before precondition probes).
3. Keep helper functions defensive and bounded (timeouts, process-liveness checks, tail-log output on failure).
4. Prefer explicit table/column allowlists for SQL identifier interpolation in test helpers.

### Integration test checklist for PRs

Before merge, confirm:

- A production bug repro has at least one integration-level regression test.
- New retry/timeout logic has a test for success path and a test for failure path.
- Shutdown/cleanup paths are covered where background tasks or worker executors are involved.
- Targeted integration tests were run locally (plus targeted lint/type checks).

## Matrix Facade Testing

The `mmrelay.matrix_utils` module serves as a facade that re-exports functions from decomposed submodules under `mmrelay.matrix/`. Tests for Matrix behavior must follow these rules to stay compatible with the facade architecture.

### Where to write new Matrix tests

Write tests into the appropriate split domain files:

| File                                             | Domain                                      |
| ------------------------------------------------ | ------------------------------------------- |
| `tests/test_matrix_utils_invite.py`              | Room invite handling and alias matching     |
| `tests/test_matrix_utils_auth_login.py`          | Login flow and discovery                    |
| `tests/test_matrix_utils_auth_credentials.py`    | Credential loading and storage              |
| `tests/test_matrix_utils_auth_logout.py`         | Logout and cleanup                          |
| `tests/test_matrix_utils_auth_e2ee.py`           | E2EE setup and decryption                   |
| `tests/test_matrix_utils_core.py`                | Prefixes, config parsing, general utilities |
| `tests/test_matrix_utils_room.py`                | Room mapping and discovery                  |
| `tests/test_matrix_utils_error_handling.py`      | Error paths across Matrix operations        |
| `tests/test_matrix_utils_edge_cases.py`          | Boundary conditions and unusual inputs      |
| `tests/test_matrix_utils_bot.py`                 | Bot lifecycle and identity                  |
| `tests/test_matrix_utils_errors.py`              | Error classification and reporting          |
| `tests/test_matrix_utils_relay.py`               | Message relay and retry logic               |
| `tests/test_matrix_utils_media.py`               | Image upload and media handling             |
| `tests/test_matrix_utils_replies.py`             | Reply formatting and threading              |
| `tests/test_matrix_utils_detection.py`           | Detection sensor packet handling            |
| `tests/test_matrix_utils_connect.py`             | General connect/bootstrap/config behavior   |
| `tests/test_matrix_utils_connect_sync.py`        | Initial sync and retry behavior             |
| `tests/test_matrix_utils_connect_credentials.py` | Credentials reload/save during connect      |
| `tests/test_matrix_utils_connect_rooms.py`       | Room/alias/displayname setup during connect |
| `tests/test_matrix_utils_connect_e2ee.py`        | E2EE/device/whoami setup during connect     |

If no existing file matches, create a new one following the `test_matrix_utils_<domain>.py` naming convention.

### Matrix Test File Organization

All Matrix tests live in split domain files under `tests/`. Each file targets a specific area of Matrix functionality:

**Rules:**

1. **Do NOT recreate legacy catch-all Matrix test files** — no new `test_matrix_utils.py` or `test_matrix_utils_auth.py`
2. **New Matrix tests must go into the appropriate split domain file** — extend the file whose domain matches the behavior under test
3. **Prefer existing split files before creating new ones**
4. **If a domain file grows too large, split it by subdomain** — e.g. `test_matrix_utils_relay_send.py`, `test_matrix_utils_relay_formatting.py` — rather than creating a new misc/catch-all file

### File Boundary Guidance

- **`auth_login.py`** — login flow, user discovery, authentication-specific behavior
- **`connect_*` files** — `connect_matrix` orchestration, bootstrap, sync, credentials reload, room setup, E2EE/device initialization during connection
- **`relay.py`** — message relay, retry logic, `on_room_message` handler, message formatting, mapping/storage, reply/quote behavior, meshnet/prefix relay
- **`room.py`** — room mapping and discovery (separate from connect-time room setup)
- **`auth_credentials.py`** — credential loading and storage (separate from connect-time credential reload)
- **`auth_e2ee.py`** — E2EE setup and decryption logic (separate from connect-time E2EE bootstrapping)

If unsure where a new test belongs, follow the function's source module: tests for `mmrelay.matrix.relay` go in `test_matrix_utils_relay.py`, tests for `mmrelay.matrix.sync_bootstrap` go in `test_matrix_utils_connect*.py`, etc.

### Patch targets

By default, patch Matrix functions through the facade:

```python
@patch("mmrelay.matrix_utils.send_reply_to_meshtastic")
@patch("mmrelay.matrix_utils.matrix_client", new_callable=AsyncMock)
async def test_something(mock_client, mock_send):
    ...
```

Patch a source submodule directly **only** when the test is specifically verifying source-module lookup behavior (e.g., testing that a submodule imports `RoomSendError` from nio directly rather than through the facade).

Rationale: the decomposed submodules call each other through the facade (`facade.func_name`), so patches on `mmrelay.matrix_utils.X` intercept the actual call path.

Note: The `asyncmock_patterns` list in `conftest.py` contains **filename-prefix patterns** used for GC cleanup (e.g., `test_matrix_utils_connect` matches `test_matrix_utils_connect.py`, `test_matrix_utils_connect_sync.py`, etc.). Entries should NOT be removed just because legacy monolith files were deleted — they match by prefix, not exact filename.

### nio mock classes

`tests/conftest.py` mocks `sys.modules["nio"]` with a `MagicMock()`. Any new nio import used in source code (e.g., `RoomSendError`, `InviteMemberEvent`) requires a corresponding mock class in conftest. Without it, `isinstance()` checks in source will raise `TypeError`.

### Shared state

The facade owns global state (`matrix_client`, `matrix_rooms`, `bot_user_id`, `config`, `logger`, etc.).

- **`reset_matrix_utils_globals`** restores: `matrix_client`, `matrix_rooms`, `bot_user_id`. Use this in fixtures/teardown to reset Matrix-side globals.
- **`reset_meshtastic_globals`** restores Meshtastic-side globals.
- **NOT reset by `reset_matrix_utils_globals`**: `config` and `logger` are intentionally preserved. If a test mutates `config` or `logger`, restore them manually in teardown or via a dedicated fixture.

## References

- [unittest.mock documentation](https://docs.python.org/3/library/unittest.mock.html)
- [pytest documentation](https://docs.pytest.org/)
- [AsyncMock best practices](https://docs.python.org/3/library/unittest.mock.html#unittest.mock.AsyncMock)
