# MMRelay Constants Organization

This document describes the organized constants structure implemented in MMRelay to eliminate magic numbers and hardcoded strings throughout the codebase.

## Overview

All constants are organized in the `src/mmrelay/constants/` package, structured by functional area similar to Android/Kotlin resource organization. This provides:

- **Centralized Management**: All constants in discoverable locations
- **Functional Organization**: Constants grouped by purpose
- **Easy Maintenance**: Clear structure for finding and updating constants
- **No Magic Numbers**: Eliminated hardcoded values throughout codebase
- **Consistent Imports**: Standardized import patterns

## Package Structure

```text
src/mmrelay/constants/
├── __init__.py          # Re-exports commonly used constants
├── app.py              # Application metadata & platform constants
├── queue.py            # Message queue configuration constants
├── formats.py          # Message format templates & port constants
├── network.py          # Network/connection constants
├── database.py         # Database-related constants
└── messages.py         # User-facing strings & logging constants
```

## Constants by Category

### Application Metadata (`constants/app.py`)

```python
APP_NAME = "mmrelay"
APP_AUTHOR = None
APP_DISPLAY_NAME = "M<>M Relay"
WINDOWS_PLATFORM = "win32"
```

### Message Queue (`constants/queue.py`)

```python
DEFAULT_MESSAGE_DELAY = 2.0  # Firmware-enforced minimum delay
MAX_QUEUE_SIZE = 500
QUEUE_HIGH_WATER_MARK = int(MAX_QUEUE_SIZE * 0.75)   # 75% of MAX_QUEUE_SIZE
QUEUE_MEDIUM_WATER_MARK = int(MAX_QUEUE_SIZE * 0.50) # 50% of MAX_QUEUE_SIZE
```

### Message Formats (`constants/formats.py`)

```python
DEFAULT_MESHTASTIC_PREFIX = "{display5}[M]: "
DEFAULT_MATRIX_PREFIX = "[{long}/{mesh}]: "
TEXT_MESSAGE_APP = "TEXT_MESSAGE_APP"
DETECTION_SENSOR_APP = "DETECTION_SENSOR_APP"
EMOJI_FLAG_VALUE = 1
DEFAULT_CHANNEL = 0
```

### Network & Connection (`constants/network.py`)

```python
CONNECTION_TYPE_TCP = "tcp"
CONNECTION_TYPE_SERIAL = "serial"
CONNECTION_TYPE_BLE = "ble"
CONNECTION_TYPE_NETWORK = "network"  # Legacy alias
DEFAULT_BACKOFF_TIME = 10  # seconds
INFINITE_RETRIES = 0
MINIMUM_MESSAGE_DELAY = 2.0
ERRNO_BAD_FILE_DESCRIPTOR = 9
SYSTEMD_INIT_SYSTEM = "systemd"
MILLISECONDS_PER_SECOND = 1000
```

### Database (`constants/database.py`)

```python
DEFAULT_MSGS_TO_KEEP = 500
DEFAULT_MAX_DATA_ROWS_PER_NODE_BASE = 100
DEFAULT_MAX_DATA_ROWS_PER_NODE_MESH_RELAY = 50
PROGRESS_TOTAL_STEPS = 100
PROGRESS_COMPLETE = 100
DEFAULT_TEXT_TRUNCATION_LENGTH = 50
DEFAULT_DISTANCE_KM_FALLBACK = 1000
DEFAULT_RADIUS_KM = 5
```

### Messages & Logging (`constants/messages.py`)

```python
DEFAULT_LOG_SIZE_MB = 5
DEFAULT_LOG_BACKUP_COUNT = 1
LOG_SIZE_BYTES_MULTIPLIER = 1024 * 1024
PORTNUM_NUMERIC_VALUE = 1
DEFAULT_CHANNEL_VALUE = 0

COMPONENT_LOGGERS = {
    "matrix_nio": ["nio", "nio.client", "nio.http", "nio.crypto"],
    "bleak": ["bleak", "bleak.backends"],
    "meshtastic": ["meshtastic", "meshtastic.serial_interface", ...],
}

LOG_LEVEL_STYLES = {
    "DEBUG": {"color": "cyan"},
    "INFO": {"color": "green"},
    "WARNING": {"color": "yellow"},
    "ERROR": {"color": "red"},
    "CRITICAL": {"color": "red", "bold": True},
}
```

## Usage Patterns

### Convenient Re-exports

```python
# Import commonly used constants from main package
from mmrelay.constants import APP_NAME, DEFAULT_MESSAGE_DELAY, MAX_QUEUE_SIZE
```

### Specific Module Imports

```python
# Import from specific modules for clarity
from mmrelay.constants.app import APP_DISPLAY_NAME, WINDOWS_PLATFORM
from mmrelay.constants.formats import TEXT_MESSAGE_APP, DETECTION_SENSOR_APP
from mmrelay.constants.network import CONNECTION_TYPE_TCP, DEFAULT_BACKOFF_TIME
```

### Multiple Constants from Same Module

```python
from mmrelay.constants.queue import (
    DEFAULT_MESSAGE_DELAY,
    MAX_QUEUE_SIZE,
    QUEUE_HIGH_WATER_MARK,
    QUEUE_MEDIUM_WATER_MARK,
)
```

## Benefits

1. **Maintainability**: Easy to find and update related constants
2. **Consistency**: Standardized values across the entire codebase
3. **Documentation**: Constants serve as self-documenting code
4. **Type Safety**: Centralized definitions prevent typos
5. **Refactoring**: Easy to change values in one place
6. **Testing**: Consistent test values and easier mocking

## Migration Notes

All existing hardcoded values have been extracted to appropriate constant modules. The original functionality is preserved - this is purely an organizational improvement.

Files updated with constant imports:

- Core modules: `main.py`, `config.py`, `matrix_utils.py`, `meshtastic_utils.py`, etc.
- Message queue: `message_queue.py`
- Logging: `log_utils.py`
- Setup utilities: `setup_utils.py`
- Plugins: `base_plugin.py`, `mesh_relay_plugin.py`, `drop_plugin.py`, `weather_plugin.py`
- Tests: `test_message_queue.py`, `test_message_queue_edge_cases.py`

All 595 tests continue to pass, ensuring no functionality was broken during the extraction process.
