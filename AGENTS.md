# Agent Guidelines for Meshtastic Matrix Relay

## Interactive Feedback Protocol

**CRITICAL**: Always call interactive feedback at the end of every response, never end a turn. We always have work and are never finished. Wait for more input before proceeding.

## Build/Lint/Test Commands

- **Run all tests**: `pytest`
- **Run single test**: `pytest tests/test_specific_file.py::test_function_name`
- **Run with coverage**: `pytest --cov=src/mmrelay --cov-report=term-missing`
- **Lint code**: `trunk check --fix --all` or `ruff check src/`
- **Format code**: `black src/ && isort src/`
- **Type checking**: `mypy src/` (if mypy installed)

## Code Style Guidelines

- **Imports**: Use isort with black profile (already configured)
- **Formatting**: Black formatter with 88-character line length
- **Types**: Use type hints consistently (Python 3.10+)
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Error handling**: Use specific exceptions, avoid bare except clauses
- **Logging**: Use structured logging via log_utils.get_logger()
- **Async**: Use asyncio patterns, avoid sync calls in async contexts
- **Testing**: Use pytest with async support, mark tests appropriately

## Testing Guidelines (from TESTING_GUIDE.md)

- **Async Mocking**: Use regular `Mock` with `return_value` for functions called via `asyncio.run()`, not `AsyncMock`
- **Warning Handling**: Treat all warnings as errors - fix underlying issues, don't suppress
- **Test Organization**: Use Arrange-Act-Assert pattern, descriptive test names, independent tests
- **Mock Patterns**: Mock external dependencies, not internal logic; use proper patch paths
- **Coverage**: Run tests with `pytest --cov --tb=short` and ensure no warnings

## E2EE Guidelines (from E2EE.md)

- **Windows Limitation**: E2EE is not supported on Windows due to python-olm library limitations
- **Configuration**: E2EE enabled via `matrix.e2ee.enabled: true` in config.yaml, respects config settings
- **Installation**: Use `pipx install 'mmrelay[e2e]'` for E2EE support on Linux/macOS
- **Authentication**: Use `mmrelay auth login` for proper E2EE credential setup
- **File Locations**: Credentials in `~/.mmrelay/credentials.json`, store in `~/.mmrelay/store/`
- **Troubleshooting**: "Failed to decrypt" errors are normal temporary behavior, keys sync automatically

## Inno Setup Guidelines (from INNO_SETUP_GUIDE.md)

- **Critical Warning**: Always ask for feedback before committing changes to `scripts/mmrelay.iss`
- **Pascal Syntax**: String handling differs from other languages, understand procedures vs functions
- **String Escaping**: Prefer double quotes for YAML; use single quotes for Windows paths/secrets (escape internal ' as '')
- **Testing**: Test locally with Inno Setup ISCC before pushing to CI
- **Common Errors**: "Type mismatch" from using procedures as functions, fix with proper variable assignment
