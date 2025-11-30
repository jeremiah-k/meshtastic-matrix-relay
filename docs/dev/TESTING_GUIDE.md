# Testing Guide

This guide covers testing patterns and best practices for the meshtastic-matrix-relay project.

First check for an existing virtual environment in venv/ if it does not exist, create one and run `. venv/bin/activate && pip install -r requirements.txt -r requirements-e2e.txt` then continue.

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

The project has standardized async testing patterns and no longer requires test environment detection (e.g., `MMRELAY_TESTING` environment variable).

### Key Principles

1. **No Test Environment Detection**: Code should behave consistently in test and production environments
2. **Use `asyncio.to_thread()`**: For running blocking operations in async context
3. **Consistent Mocking**: Use the patterns described above for all async function testing
4. **Global State Isolation**: Use `reset_meshtastic_globals` fixture for tests that modify global state

### Migration from Old Patterns

If you encounter code using old test environment detection patterns:

```python
# ❌ OLD PATTERN - No longer needed
if os.getenv("MMRELAY_TESTING"):
    # Test-specific behavior
else:
    # Production behavior
```

Replace with consistent behavior:

```python
# ✅ NEW PATTERN - Consistent behavior
def function_that_works_everywhere():
    # Same logic for test and production
    return asyncio.to_thread(blocking_operation)
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
    "test_matrix_utils",
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
python -m pytest -v --cov --junitxml=junit.xml -o junit_family=legacy
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

### 2. Descriptive Test Names

- Use descriptive test method names that explain the scenario
- Include expected behavior in the name

### 3. Arrange-Act-Assert Pattern

- **Arrange**: Set up test data and mocks
- **Act**: Execute the code under test
- **Assert**: Verify the expected behavior

### 4. Mock at the Right Level

- Mock external dependencies, not internal logic
- Mock at the boundary of your system under test

### 5. Test Error Conditions

- Test both success and failure scenarios
- Test exception handling and edge cases
- Consider adding explicit patterns for asserting log messages on failures in async paths
  Example:
  ```python
  # Ensure logger name matches the component under test (e.g., "MessageQueue")
  with self.assertLogs("mmrelay", level="ERROR") as cm:
      result = some_async_wrapper(self.mock_args)
      self.assertIn("expected failure detail", "\n".join(cm.output))
  ```

### 6. Avoid Test Interdependence

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

## References

- [unittest.mock documentation](https://docs.python.org/3/library/unittest.mock.html)
- [pytest documentation](https://docs.pytest.org/)
- [AsyncMock best practices](https://docs.python.org/3/library/unittest.mock.html#unittest.mock.AsyncMock)
