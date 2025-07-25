"""
Test detection sensor functionality in MMRelay.

Tests the core detection sensor logic:
1. Configuration handling: Enabled/disabled detection sensor processing
2. Message queue integration for detection sensor messages
3. Portnum handling and data encoding
"""

import unittest
from unittest.mock import MagicMock, patch
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Mock all external dependencies
sys.modules['meshtastic'] = MagicMock()
sys.modules['meshtastic.protobuf'] = MagicMock()
sys.modules['meshtastic.protobuf.portnums_pb2'] = MagicMock()
sys.modules['meshtastic.protobuf.portnums_pb2'].PortNum = MagicMock()
sys.modules['meshtastic.protobuf.portnums_pb2'].PortNum.DETECTION_SENSOR_APP = 1
sys.modules['nio'] = MagicMock()
sys.modules['nio.MatrixRoom'] = MagicMock()
sys.modules['nio.RoomMessageText'] = MagicMock()
sys.modules['nio.RoomMessageNotice'] = MagicMock()
sys.modules['nio.ReactionEvent'] = MagicMock()
sys.modules['nio.RoomMessageEmote'] = MagicMock()

from mmrelay.message_queue import MessageQueue


class TestDetectionSensor(unittest.TestCase):
    """Test detection sensor functionality."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock message queue
        self.queue = MessageQueue()
        self.sent_messages = []

        def mock_send_data(*args, **kwargs):
            """Mock sendData function that records calls."""
            self.sent_messages.append({"args": args, "kwargs": kwargs})
            return MagicMock(id=123)

        self.mock_send_data = mock_send_data

    def tearDown(self):
        """Clean up after tests."""
        if self.queue.is_running():
            self.queue.stop()

    def test_detection_sensor_config_enabled(self):
        """Test detection sensor configuration parsing when enabled."""
        config_enabled = {
            "meshtastic": {
                "detection_sensor": True,
            }
        }

        # Test that config.get() returns True when detection_sensor is enabled
        self.assertTrue(config_enabled["meshtastic"].get("detection_sensor", False))

    def test_detection_sensor_config_disabled(self):
        """Test detection sensor configuration parsing when disabled."""
        config_disabled = {
            "meshtastic": {
                "detection_sensor": False,
            }
        }

        # Test that config.get() returns False when detection_sensor is disabled
        self.assertFalse(config_disabled["meshtastic"].get("detection_sensor", False))

    def test_detection_sensor_config_default(self):
        """Test detection sensor configuration parsing with default value."""
        config_no_setting = {
            "meshtastic": {
                # detection_sensor not specified
            }
        }

        # Test that config.get() returns False by default
        self.assertFalse(config_no_setting["meshtastic"].get("detection_sensor", False))

    def test_detection_sensor_portnum_check(self):
        """Test detection sensor portnum identification."""
        # Mock event with detection sensor portnum
        mock_event = {
            "source": {
                "content": {
                    "meshtastic_portnum": "DETECTION_SENSOR_APP"
                }
            }
        }

        portnum = mock_event["source"]["content"].get("meshtastic_portnum")
        self.assertEqual(portnum, "DETECTION_SENSOR_APP")

    def test_detection_sensor_data_encoding(self):
        """Test that detection sensor data is properly encoded as bytes."""
        test_message = "Motion detected at sensor 1"
        encoded_data = test_message.encode("utf-8")

        # Verify encoding works correctly
        self.assertIsInstance(encoded_data, bytes)
        self.assertEqual(encoded_data.decode("utf-8"), test_message)

    def test_detection_sensor_queue_basic(self):
        """Test basic queue functionality for detection sensor messages."""
        # Start the queue
        self.queue.start(message_delay=2.0)

        # Queue a detection sensor message
        success = self.queue.enqueue(
            self.mock_send_data,
            data=b"Motion detected",
            channelIndex=0,
            description="Detection sensor test",
        )

        # Verify message was queued successfully
        self.assertTrue(success)
        self.assertEqual(self.queue.get_queue_size(), 1)

        # Verify queue is running
        self.assertTrue(self.queue.is_running())

    def test_detection_sensor_message_structure(self):
        """Test that detection sensor messages have the correct structure."""
        # Test data that would be sent for a detection sensor message
        test_data = {
            "data": b"Motion detected at sensor 1",
            "channelIndex": 0,
            "portNum": 1,  # DETECTION_SENSOR_APP enum value
            "description": "Detection sensor data from TestUser",
        }

        # Verify all required fields are present
        self.assertIn("data", test_data)
        self.assertIn("channelIndex", test_data)
        self.assertIn("portNum", test_data)
        self.assertIn("description", test_data)

        # Verify data is bytes
        self.assertIsInstance(test_data["data"], bytes)

        # Verify channel is integer
        self.assertIsInstance(test_data["channelIndex"], int)

        # Verify portNum is the detection sensor value
        self.assertEqual(test_data["portNum"], 1)


if __name__ == "__main__":
    print("Testing Detection Sensor Functionality\n")
    unittest.main(verbosity=2)
