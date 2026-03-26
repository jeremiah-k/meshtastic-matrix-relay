"""
Test constants for consistent values across test files.

Contains test-specific values used in multiple test files to ensure
consistency and make tests easier to maintain.
"""

from mmrelay.constants.queue import DEFAULT_MESSAGE_DELAY

# Test message delay values for message queue testing
# These are intentionally different from production to test edge cases
TEST_MESSAGE_DELAY_LOW = 0.1  # Faster than minimum, for performance testing
TEST_MESSAGE_DELAY_WARNING_THRESHOLD = 1.0  # Below minimum to trigger warnings
TEST_MESSAGE_DELAY_NEGATIVE = -1.0  # Invalid value edge case
TEST_MESSAGE_DELAY_NORMAL = DEFAULT_MESSAGE_DELAY  # Reference production default (2.5)
TEST_MESSAGE_DELAY_HIGH = 3.0  # Above default for testing higher delays
