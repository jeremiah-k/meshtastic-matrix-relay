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

# Default heartbeat interval for health checks
DEFAULT_HEARTBEAT_INTERVAL_SECS: Final[int] = 60  # seconds

# Connection retry backoff policy
CONNECTION_RETRY_BACKOFF_BASE: Final[int] = 2
CONNECTION_RETRY_BACKOFF_MAX_SECS: Final[int] = 60

# Matrix message limits
MATRIX_MESSAGE_FETCH_LIMIT: Final[int] = 100

# Matrix client timeouts
MATRIX_EARLY_SYNC_TIMEOUT: Final[int] = 2000  # milliseconds
MATRIX_MAIN_SYNC_TIMEOUT: Final[int] = 5000  # milliseconds
MATRIX_ROOM_SEND_TIMEOUT: Final[float] = 10.0  # seconds
MATRIX_TO_DEVICE_TIMEOUT: Final[float] = 10.0  # seconds
MATRIX_LOGIN_TIMEOUT: Final[float] = 30.0  # seconds
MATRIX_SYNC_OPERATION_TIMEOUT: Final[float] = 60.0  # seconds
# Initial Matrix sync retry policy.
# 0 means retry indefinitely (recommended for unattended service restarts).
MATRIX_INITIAL_SYNC_MAX_ATTEMPTS: Final[int] = 0
MATRIX_INITIAL_SYNC_RETRY_MAX_DELAY_SECS: Final[float] = 60.0

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

# ACK polling interval
ACK_POLL_INTERVAL_SECS: Final[float] = 0.1

# BLE timing constants
BLE_FUTURE_STALE_GRACE_SECS: Final[float] = 2.0
BLE_INTERFACE_CREATE_TIMEOUT_FLOOR_SECS: Final[float] = 90.0
BLE_DISCONNECT_MAX_RETRIES: Final[int] = 3
BLE_DISCONNECT_TIMEOUT_SECS: Final[float] = 3.0
BLE_DISCONNECT_SETTLE_SECS: Final[float] = 2.0
BLE_RETRY_DELAY_SECS: Final[float] = 0.5
# Cover full disconnect retry budget plus one final cleanup pass:
# retries * (disconnect + settle) + inter-retry sleeps + final (disconnect + settle)
STALE_DISCONNECT_TIMEOUT_SECS: Final[float] = (
    BLE_DISCONNECT_MAX_RETRIES
    * (BLE_DISCONNECT_TIMEOUT_SECS + BLE_DISCONNECT_SETTLE_SECS)
    + max(BLE_DISCONNECT_MAX_RETRIES - 1, 0) * BLE_RETRY_DELAY_SECS
    + (BLE_DISCONNECT_TIMEOUT_SECS + BLE_DISCONNECT_SETTLE_SECS)
)
HEALTH_PROBE_TRACK_GRACE_SECS: Final[float] = 60.0

# Maximum initial clock skew allowed when seeding from first packet (5 minutes)
# Used to prevent wildly wrong values from stale backlog packets
MAX_INITIAL_SKEW_SECS: Final[float] = 300.0

# Future/cancel timing
FUTURE_CANCEL_TIMEOUT_SECS: Final[float] = 0.2

# Plugin timeout
DEFAULT_PLUGIN_TIMEOUT_SECS: Final[float] = 5.0

# Close timeouts
MATRIX_CLIENT_CLOSE_TIMEOUT_SECS: Final[float] = 10.0
MESHTASTIC_CLOSE_TIMEOUT_SECS: Final[float] = 10.0
# Backward-compatible alias for legacy imports.
MESHTASTIC_CLOSE_TIMEOUT_SECONDS: Final[float] = MESHTASTIC_CLOSE_TIMEOUT_SECS

# Sync and retry timing
MATRIX_SYNC_RETRY_DELAY_SECS: Final[float] = 5.0
NODEDB_BACKOFF_INITIAL_SECS: Final[float] = 1.0
NODEDB_BACKOFF_MAX_SECS: Final[float] = 30.0
NODEDB_SHUTDOWN_TIMEOUT_SECS: Final[float] = 10.0

# Process check timeouts
PROCESS_CHECK_TIMEOUT_SECS: Final[float] = 5.0
PROCESS_CHECK_SHORT_TIMEOUT_SECS: Final[float] = 2.0

# HTTP status codes
HTTP_SERVER_ERROR_CODES: Final[tuple[int, ...]] = tuple(range(500, 600))
HTTP_STATUS_UNAUTHORIZED: Final[int] = 401
HTTP_STATUS_FORBIDDEN: Final[int] = 403

# Hostname validation limits
MAX_HOSTNAME_LENGTH: Final[int] = 253
MAX_HOSTNAME_LABEL_LENGTH: Final[int] = 63

# Meshtastic channel limits
MESHTASTIC_CHANNEL_MIN: Final[int] = 0
MESHTASTIC_CHANNEL_MAX: Final[int] = 7
