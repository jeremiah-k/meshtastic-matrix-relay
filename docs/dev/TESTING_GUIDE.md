# Testing Guide

This guide covers testing patterns and best practices for the meshtastic-matrix-relay project.

## Async Function Mocking Patterns

### Problem: RuntimeWarnings with AsyncMock

When testing functions that call async code via `asyncio.run()`, using `AsyncMock` can lead to RuntimeWarnings about unawaited coroutines:

```
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

## Test Organization

### Test File Structure

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

### Async Mock Cleanup

The project uses a cleanup fixture in `tests/conftest.py` to handle AsyncMock cleanup for certain test modules. If you're writing tests that don't need AsyncMock warnings suppressed, ensure your test module is not in the `asyncmock_patterns` list:

```python
# In conftest.py
asyncmock_patterns = [
    "test_matrix_utils",
    "test_e2ee_unified",
    "test_help_plugin",
    "test_ping_plugin",
    "test_nodes_plugin",
    # "test_cli",  # ✅ Removed - warnings properly fixed
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

### 1. Descriptive Test Names

- Use descriptive test method names that explain the scenario
- Include expected behavior in the name

### 2. Arrange-Act-Assert Pattern

- **Arrange**: Set up test data and mocks
- **Act**: Execute the code under test
- **Assert**: Verify the expected behavior

### 3. Mock at the Right Level

- Mock external dependencies, not internal logic
- Mock at the boundary of your system under test

### 4. Test Error Conditions

- Test both success and failure scenarios
- Test exception handling and edge cases

### 5. Avoid Test Interdependence

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

## Troubleshooting

### RuntimeWarnings About Unawaited Coroutines

If you see warnings like:

```
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

## References

- [unittest.mock documentation](https://docs.python.org/3/library/unittest.mock.html)
- [pytest documentation](https://docs.pytest.org/)
- [AsyncMock best practices](https://docs.python.org/3/library/unittest.mock.html#unittest.mock.AsyncMock)
