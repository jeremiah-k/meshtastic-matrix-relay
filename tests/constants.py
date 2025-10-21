"""
Test constants for consistent values across test files.

Contains mock values, test data, and other constants used in multiple test files
to ensure consistency and make tests easier to maintain.
"""

# Mock timestamps and IDs
MOCK_SERVER_TIMESTAMP = 1234567890
MOCK_BOT_START_TIME = 1234567880
MOCK_EVENT_ID = "$event123"
MOCK_ROOM_ID = "!room:matrix.org"
MOCK_BOT_USER_ID = "@bot:matrix.org"
MOCK_CONTENT_URI = "mxc://matrix.org/test123"

# Test configuration values
TEST_MESHTASTIC_CHANNEL = 0
TEST_MSGS_TO_KEEP_DEFAULT = 500
TEST_MSGS_TO_KEEP_LEGACY = 100
TEST_MSGS_TO_KEEP_NEW = 200
TEST_REPLY_ID = 12345
TEST_PORTNUM = 1

# Test message formatting
TEST_PREFIX_FORMAT_DISPLAY5 = "{display5}[M]: "
TEST_PREFIX_FORMAT_DISPLAY3 = "[{display3}]: "
TEST_PREFIX_FORMAT_MATRIX = "[{long3}/{mesh}]: "

# Test truncation values
TEST_TRUNCATE_BYTES_SMALL = 10
TEST_TRUNCATE_BYTES_MEDIUM = 20
TEST_TRUNCATE_BYTES_LARGE = 50

# Test display name lengths
TEST_DISPLAY_LENGTH_1 = 1
TEST_DISPLAY_LENGTH_3 = 3
TEST_DISPLAY_LENGTH_5 = 5
TEST_DISPLAY_LENGTH_10 = 10
TEST_DISPLAY_LENGTH_20 = 20

# Test E2EE configuration
TEST_E2EE_STORE_PATH = "/test/store"
TEST_JSON_INDENT = 2

# Test room configurations
TEST_ROOM_CONFIG = {"meshtastic_channel": TEST_MESHTASTIC_CHANNEL}

# Test matrix rooms configuration
TEST_MATRIX_ROOMS = [
    {"id": MOCK_ROOM_ID, "meshtastic_channel": TEST_MESHTASTIC_CHANNEL}
]

# Test matrix configuration
TEST_MATRIX_CONFIG = {"bot_user_id": MOCK_BOT_USER_ID}

# Test meshtastic configuration with prefix
TEST_MESHTASTIC_CONFIG_WITH_PREFIX = {
    "prefix_enabled": True,
    "prefix_format": TEST_PREFIX_FORMAT_DISPLAY5,
    "message_interactions": {"reactions": False, "replies": False},
}

# Test message delay values for message queue testing
TEST_MESSAGE_DELAY_LOW = 0.1
TEST_MESSAGE_DELAY_WARNING_THRESHOLD = 1.0
TEST_MESSAGE_DELAY_NEGATIVE = -1.0
TEST_MESSAGE_DELAY_NORMAL = 2.5
TEST_MESSAGE_DELAY_HIGH = 3.0

# Test warning message constants
TEST_LOW_DELAY_WARNING_PREFIX = "Message delay {delay}s is at or below {minimum}s"
TEST_RECOMMENDED_DELAY_SUFFIX = "2.1s or higher is recommended"
TEST_RUNTIME_WARNING_PREFIX = "[Runtime] Messages sent"
