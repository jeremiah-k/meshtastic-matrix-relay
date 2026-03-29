"""
Test constants for consistent values across test files.

Contains test-specific values used in multiple test files to ensure
consistency and make tests easier to maintain.
"""

from typing import Final

from mmrelay.constants.app import (
    CONFIG_FILENAME,
    CREDENTIALS_FILENAME,
    DATABASE_FILENAME,
)
from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY

# Re-export production constants for test convenience
# These are imported from production to avoid duplication
TEST_CONFIG_FILENAME: Final[str] = CONFIG_FILENAME
TEST_CREDENTIALS_FILENAME: Final[str] = CREDENTIALS_FILENAME
TEST_DATABASE_FILENAME: Final[str] = DATABASE_FILENAME

# Test-specific path fixtures (not in production)
TEST_SERIAL_PORT: Final[str] = "/dev/ttyUSB0"
TEST_CONFIG_PATH: Final[str] = "/path/to/config.yaml"
TEST_HOME_CONFIG_PATH: Final[str] = "/home/user/.mmrelay/config.yaml"

# Matrix homeserver URLs
TEST_MATRIX_HOMESERVER: Final[str] = "https://matrix.org"
TEST_MATRIX_HOMESERVER_EXAMPLE: Final[str] = "https://matrix.example.org"

# Matrix user IDs
TEST_BOT_USER_ID: Final[str] = "@bot:matrix.org"
TEST_BOT_USER_ID_EXAMPLE: Final[str] = "@bot:example.org"
TEST_USER_ID: Final[str] = "@user:matrix.org"

# Matrix room IDs
TEST_ROOM_ID: Final[str] = "!room:matrix.org"
TEST_ROOM_ID_1: Final[str] = "!room1:matrix.org"
TEST_ROOM_ID_2: Final[str] = "!room2:matrix.org"

# Matrix event IDs
TEST_EVENT_ID: Final[str] = "$event123"

# Test message delay values for message queue testing
# These are intentionally different from production to test edge cases
TEST_MESSAGE_DELAY_LOW: Final[float] = (
    0.1  # Faster than minimum, for performance testing
)
TEST_MESSAGE_DELAY_WARNING_THRESHOLD: Final[float] = (
    1.0  # Below minimum to trigger warnings
)
TEST_MESSAGE_DELAY_NEGATIVE: Final[float] = -1.0  # Invalid value edge case
TEST_MESSAGE_DELAY_NORMAL: Final[float] = (
    DEFAULT_MESSAGE_DELAY  # Reference production default
)
TEST_MESSAGE_DELAY_HIGH: Final[float] = 3.0  # Above default for testing higher delays

# Test timing/delay values
TEST_SHORT_DELAY: Final[float] = 0.1
TEST_HALF_SECOND: Final[float] = 0.5
TEST_NETWORK_TIMEOUT: Final[float] = 5.0
TEST_PLUGIN_TIMEOUT: Final[float] = 5.0
TEST_GIT_TIMEOUT: Final[float] = 120.0
TEST_LONG_TIMEOUT: Final[float] = 10.0

# Test SQL snippets for database testing
TEST_SQL_CREATE_TABLE: Final[str] = (
    "CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)"
)
TEST_SQL_INSERT_VALUE: Final[str] = "INSERT INTO test (value) VALUES (?)"
TEST_SQL_SELECT_ONE: Final[str] = "SELECT 1"
TEST_SQL_COUNT_TEST: Final[str] = "SELECT COUNT(*) FROM test"
TEST_SQL_CREATE_TABLE_SIMPLE: Final[str] = "CREATE TABLE test_table (id INTEGER)"

# Test Meshtastic IDs
TEST_MESHTASTIC_ID: Final[str] = "!a1b2c3d4"
TEST_MESHTASTIC_ID_1: Final[str] = "!11111111"
TEST_MESHTASTIC_ID_2: Final[str] = "!22222222"
TEST_MESHTASTIC_ID_3: Final[str] = "!33333333"

# Test packet/node IDs
TEST_PACKET_ID: Final[int] = 12345
TEST_NODE_NUM: Final[int] = 67890
TEST_PACKET_FROM_ID: Final[int] = 123456789

# Test BLE MAC address
TEST_BLE_MAC: Final[str] = "AA:BB:CC:DD:EE:FF"

# Test coordinates (NYC and SF)
TEST_LAT_NYC: Final[float] = 40.7128
TEST_LON_NYC: Final[float] = -74.0060
TEST_LAT_SF: Final[float] = 37.7749
TEST_LON_SF: Final[float] = -122.4194
