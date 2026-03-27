"""
Message queue constants.

Contains configuration values for the message queue system including
delays, size limits, and water marks for queue management.
"""

__all__ = [
    "CONNECTION_ERROR_KEYWORDS",
    "CONNECTION_RETRY_SLEEP_SEC",
    "DEFAULT_MESSAGE_DELAY",
    "MAX_QUEUE_SIZE",
    "MINIMUM_MESSAGE_DELAY",
    "QUEUE_EXECUTOR_MAX_WORKERS",
    "QUEUE_FULL_LOG_INTERVAL_SEC",
    "QUEUE_HIGH_WATER_MARK",
    "QUEUE_LOG_THRESHOLD",
    "QUEUE_MEDIUM_WATER_MARK",
    "QUEUE_POLL_INTERVAL_SEC",
    "QUEUE_WAIT_RETRY_SLEEP_SEC",
    "TASK_SHUTDOWN_TIMEOUT_SEC",
]

from typing import Final

from mmrelay.constants.network import MINIMUM_MESSAGE_DELAY

# Message timing constants
DEFAULT_MESSAGE_DELAY: Final[float] = (
    MINIMUM_MESSAGE_DELAY + 0.5
)  # Keep 0.5s above firmware minimum to reduce dropped sends.

# Queue size management
MAX_QUEUE_SIZE: Final[int] = 500
QUEUE_HIGH_WATER_MARK: Final[int] = int(MAX_QUEUE_SIZE * 0.75)  # 75% of MAX_QUEUE_SIZE
QUEUE_MEDIUM_WATER_MARK: Final[int] = int(
    MAX_QUEUE_SIZE * 0.50
)  # 50% of MAX_QUEUE_SIZE

# Queue logging thresholds
QUEUE_LOG_THRESHOLD: Final[int] = 2  # Only log queue status when size >= this value

# Queue timing constants
TASK_SHUTDOWN_TIMEOUT_SEC: Final[float] = 1.0
QUEUE_FULL_LOG_INTERVAL_SEC: Final[float] = 5.0
QUEUE_WAIT_RETRY_SLEEP_SEC: Final[float] = 0.5
QUEUE_POLL_INTERVAL_SEC: Final[float] = 0.1
CONNECTION_RETRY_SLEEP_SEC: Final[float] = 1.0

# Queue executor
QUEUE_EXECUTOR_MAX_WORKERS: Final[int] = 1

# Connection error keywords for detection
# Note: Keywords are lowercase; normalize error messages with .lower() before checking
CONNECTION_ERROR_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "timeout",
        "refused",
        "reset",
        "broken",
        "closed",
        "unreachable",
        "network",
    }
)
