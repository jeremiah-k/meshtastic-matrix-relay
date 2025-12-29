# Agent Guidelines for Meshtastic Matrix Relay

## Build/Lint/Test Commands

- **Run with coverage**: `python -m pytest -v --cov --tb=short`
- **Run specific test**: `python -m pytest tests/test_filename.py -v --cov --tb=short`
- **Run all tests with coverage**: `python -m pytest -v --cov --junitxml=junit.xml -o junit_family=legacy`
- **Run tests with a timeout**: `python -m pytest -v --timeout=60` (to prevent tests from hanging indefinitely)
- **Lint code**: `.trunk/trunk check --fix --all` (do not need to run each time, it wastes time)

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

- **Read the testing guide in full**: Follow existing patterns (Located in docs/dev/TESTING_GUIDE.md)
- **Set up a venv**: Create a virtual environment in venv/ and `pip install -r requirements.txt -r requirements-e2e.txt
- **Async Mocking**: Use regular `Mock` with `return_value` for functions called via `asyncio.run()`, not `AsyncMock`
- **Warning Handling**: Treat all warnings as errors - fix underlying issues, don't suppress
- **Test Organization**: Use Arrange-Act-Assert pattern, descriptive test names, independent tests
- **Mock Patterns**: Mock external dependencies, not internal logic; use proper patch paths
- **Coverage**: Run tests with `pytest --cov=src/mmrelay --cov-report=term-missing --tb=short` and ensure no warnings

## Inno Setup Guidelines (from INNO_SETUP_GUIDE.md)

- **Critical Warning**: Always ask for feedback before committing changes to `scripts/mmrelay.iss`
- **Pascal Syntax**: String handling differs from other languages, understand procedures vs functions
- **String Escaping**: Prefer double quotes for YAML; use single quotes for Windows paths/secrets (escape internal ' as '')
- **Testing**: Test locally with Inno Setup ISCC before pushing to CI
- **Common Errors**: "Type mismatch" from using procedures as functions, fix with proper variable assignment
