"""
Message queue constants.

Contains configuration values for the message queue system including
delays, size limits, and water marks for queue management.
"""

# Message timing constants
DEFAULT_MESSAGE_DELAY = (
    2.5  # Set above the 2.0s firmware limit to prevent message dropping
)
MINIMUM_MESSAGE_DELAY = 2.5  # Minimum delay enforced to stay above firmware limit

# Queue size management
MAX_QUEUE_SIZE = 500
QUEUE_HIGH_WATER_MARK = int(MAX_QUEUE_SIZE * 0.75)  # 75% of MAX_QUEUE_SIZE
QUEUE_MEDIUM_WATER_MARK = int(MAX_QUEUE_SIZE * 0.50)  # 50% of MAX_QUEUE_SIZE

# Queue logging thresholds
QUEUE_LOG_THRESHOLD = 2  # Only log queue status when size >= this value
