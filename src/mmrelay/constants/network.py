"""
Network and connection constants.

Contains timeout values, retry limits, connection types, and other
network-related configuration constants.
"""

from typing import Final

# Connection types
CONNECTION_TYPE_TCP: Final[str] = "tcp"
CONNECTION_TYPE_SERIAL: Final[str] = "serial"
CONNECTION_TYPE_BLE: Final[str] = "ble"
CONNECTION_TYPE_NETWORK: Final[str] = (
    "network"  # DEPRECATED: Legacy alias for tcp, use CONNECTION_TYPE_TCP instead
)

# Configuration keys for connection settings
CONFIG_KEY_BLE_ADDRESS: Final[str] = "ble_address"
CONFIG_KEY_SERIAL_PORT: Final[str] = "serial_port"
CONFIG_KEY_HOST: Final[str] = "host"
CONFIG_KEY_CONNECTION_TYPE: Final[str] = "connection_type"
CONFIG_KEY_TIMEOUT: Final[str] = "timeout"
CONFIG_KEY_PORT: Final[str] = "port"

# Meshtastic TCP defaults
DEFAULT_TCP_PORT: Final[int] = 4403

# Connection retry and timing
DEFAULT_BACKOFF_TIME: Final[int] = 10  # seconds
DEFAULT_RETRY_ATTEMPTS: Final[int] = 1
INFINITE_RETRIES: Final[int] = 0  # 0 means infinite retries
MINIMUM_MESSAGE_DELAY: Final[float] = 2.0  # Minimum delay for message queue fallback
RECOMMENDED_MINIMUM_DELAY: Final[float] = (
    2.1  # Recommended minimum delay (MINIMUM_MESSAGE_DELAY + 0.1)
)

# Meshtastic client timeout (for getMetadata and other operations)
DEFAULT_MESHTASTIC_TIMEOUT: Final[int] = 300  # seconds

# Timeout for individual Meshtastic operations (e.g., getMetadata, getMyNodeInfo)
DEFAULT_MESHTASTIC_OPERATION_TIMEOUT: Final[int] = 30  # seconds

# Initial delay before starting the first connection health check
INITIAL_HEALTH_CHECK_DELAY: Final[int] = 5  # seconds

# Matrix client timeouts
MATRIX_EARLY_SYNC_TIMEOUT: Final[int] = 2000  # milliseconds
MATRIX_MAIN_SYNC_TIMEOUT: Final[int] = 5000  # milliseconds
MATRIX_ROOM_SEND_TIMEOUT: Final[float] = 10.0  # seconds
MATRIX_TO_DEVICE_TIMEOUT: Final[float] = 10.0  # seconds
MATRIX_LOGIN_TIMEOUT: Final[float] = 30.0  # seconds
MATRIX_SYNC_OPERATION_TIMEOUT: Final[float] = 60.0  # seconds

# BLE-specific constants
BLE_FUTURE_WATCHDOG_SECS: Final[float] = 120.0
BLE_TIMEOUT_RESET_THRESHOLD: Final[int] = 3
BLE_SCAN_TIMEOUT_SECS: Final[float] = 4.0
BLE_TROUBLESHOOTING_GUIDANCE: Final[str] = (
    "Try: 1) Restarting BlueZ: 'sudo systemctl restart bluetooth', "
    "2) Manually disconnecting device: 'bluetoothctl disconnect {ble_address}', "
    "3) Rebooting your machine"
)
MAX_TIMEOUT_RETRIES_INFINITE: Final[int] = 5

# Error codes
ERRNO_BAD_FILE_DESCRIPTOR: Final[int] = 9

# System detection
SYSTEMCTL_FALLBACK: Final[str] = "/usr/bin/systemctl"
SYSTEMD_INIT_SYSTEM: Final[str] = "systemd"

# Time conversion
MILLISECONDS_PER_SECOND: Final[int] = 1000

# Metadata probe watchdog timeout (same as BLE pattern)
METADATA_WATCHDOG_SECS: Final[float] = 30.0

# Timeout for BLE connect() operation watchdog
BLE_CONNECT_TIMEOUT_SECS: Final[float] = 30.0

# Maximum orphaned workers before entering degraded state
# When executor recovery cycles orphan this many workers, we stop
# silently recovering and require explicit reconnect/restart.
EXECUTOR_ORPHAN_THRESHOLD: Final[int] = 5
