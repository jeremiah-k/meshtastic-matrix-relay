"""
Message queue constants.

Contains configuration values for the message queue system including
delays, size limits, and water marks for queue management.
"""

from typing import Final

# Message timing constants
DEFAULT_MESSAGE_DELAY: Final[float] = (
    2.5  # Set above the 2.0s firmware limit to prevent message dropping
)
MINIMUM_MESSAGE_DELAY: Final[float] = (
    2.1  # Minimum delay enforced to stay above firmware limit
)

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
