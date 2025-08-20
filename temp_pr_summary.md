# Add comprehensive environment variable support for Docker deployments

## Summary

Extends environment variable support beyond Matrix authentication to include Meshtastic connection settings, operational configuration, and system settings. This addresses Docker deployment needs where users want to configure the application without managing config.yaml files.

## Environment Variables Added

### Meshtastic Connection Settings (5 variables)

- `MMRELAY_MESHTASTIC_CONNECTION_TYPE` - Connection method (tcp/serial/ble)
- `MMRELAY_MESHTASTIC_HOST` - TCP host address
- `MMRELAY_MESHTASTIC_PORT` - TCP port number (1-65535)
- `MMRELAY_MESHTASTIC_SERIAL_PORT` - Serial device path
- `MMRELAY_MESHTASTIC_BLE_ADDRESS` - Bluetooth MAC address

### Meshtastic Operational Settings (3 variables)

- `MMRELAY_MESHTASTIC_BROADCAST_ENABLED` - Enable Matrix→Meshtastic messages (true/false)
- `MMRELAY_MESHTASTIC_MESHNET_NAME` - Display name for mesh network
- `MMRELAY_MESHTASTIC_MESSAGE_DELAY` - Message delay in seconds (minimum 2.0)

### System Configuration (3 variables)

- `MMRELAY_LOGGING_LEVEL` - Log level (DEBUG/INFO/WARNING/ERROR/CRITICAL)
- `MMRELAY_LOG_FILE` - Log file path (enables file logging when set)
- `MMRELAY_DATABASE_PATH` - SQLite database file path

## Implementation Details

### Configuration Loading

- Added `load_meshtastic_config_from_env()`, `load_logging_config_from_env()`, `load_database_config_from_env()`
- Added `apply_env_config_overrides()` to integrate with existing config loading
- Modified `load_config()` to apply environment variable overrides
- Supports environment-only configuration (no config.yaml required)

### Validation and Type Conversion

- Added helper functions `_convert_env_bool()`, `_convert_env_int()`, `_convert_env_float()`
- Range validation for numeric values (ports, delays)
- Enum validation for connection types and log levels
- Clear error messages for invalid values

### Precedence Order

1. Environment variables (highest priority)
2. credentials.json file (Matrix authentication only)
3. config.yaml sections (lowest priority)

## Testing

- Added 28 new test cases covering all environment variables
- Tests include validation, error handling, integration, and precedence scenarios
- All existing tests continue to pass
- Config module coverage increased from 30% to 67%

## Documentation Updates

- Updated `docs/DOCKER.md` with comprehensive environment variable documentation
- Added examples for TCP, serial, and BLE connection configurations
- Updated sample docker-compose files with commented examples of all new variables
- Included minimal config.yaml examples for environment-variable-first deployments

## Backward Compatibility

- All existing functionality preserved
- Existing config.yaml files continue to work unchanged
- Environment variables only override when explicitly set
- No breaking changes to existing APIs or behavior

## Files Modified

- `src/mmrelay/config.py` - Core environment variable loading and validation
- `tests/test_config.py` - Comprehensive test coverage
- `docs/DOCKER.md` - Updated documentation with examples
- `src/mmrelay/tools/sample-docker-compose*.yaml` - Added environment variable examples

This change enables Docker users to configure most commonly changed settings via environment variables while keeping complex configurations (plugins, room mappings) in config.yaml files.
