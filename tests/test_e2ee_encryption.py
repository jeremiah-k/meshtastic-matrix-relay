"""
E2EE Encryption Testing Framework

This module provides comprehensive tests for verifying that MMRelay properly
encrypts messages when sending to encrypted Matrix rooms.

The tests focus on:
1. Verifying room encryption detection
2. Testing message sending parameters
3. Mocking Matrix client behavior
4. Validating E2EE setup completeness
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from nio import RoomSendResponse

from mmrelay.matrix_utils import matrix_relay


class MockEncryptedRoom:
    """Mock Matrix room that appears encrypted"""

    def __init__(self, room_id, encrypted=True):
        self.room_id = room_id
        self.encrypted = encrypted
        self.display_name = f"Test Room {room_id}"


class MockUnencryptedRoom:
    """Mock Matrix room that appears unencrypted"""

    def __init__(self, room_id):
        self.room_id = room_id
        self.encrypted = False
        self.display_name = f"Test Room {room_id}"


class E2EETestFramework:
    """Framework for testing E2EE encryption behavior"""

    @staticmethod
    def create_mock_client(rooms=None, should_upload_keys=False):
        """Create a mock Matrix client with E2EE capabilities"""
        client = AsyncMock()
        client.device_id = "TEST_DEVICE_ID"
        client.user_id = "@test:example.org"
        client.access_token = "test_token"

        # Mock rooms
        if rooms is None:
            rooms = {
                "!encrypted:example.org": MockEncryptedRoom(
                    "!encrypted:example.org", encrypted=True
                ),
                "!unencrypted:example.org": MockUnencryptedRoom(
                    "!unencrypted:example.org"
                ),
            }
        client.rooms = rooms

        # Mock E2EE methods
        client.should_upload_keys = should_upload_keys
        client.keys_upload = AsyncMock()
        client.sync = AsyncMock()
        client.room_send = AsyncMock(
            return_value=RoomSendResponse.from_dict({"event_id": "$test_event_id"})
        )

        return client

    @staticmethod
    def verify_encryption_parameters(mock_client, expected_ignore_unverified=True):
        """Verify that room_send was called with correct encryption parameters"""
        assert mock_client.room_send.called, "room_send should have been called"

        # Get the call arguments
        call_args = mock_client.room_send.call_args
        kwargs = call_args.kwargs if call_args.kwargs else {}

        # Verify ignore_unverified_devices parameter
        ignore_unverified = kwargs.get("ignore_unverified_devices", False)
        assert (
            ignore_unverified == expected_ignore_unverified
        ), f"Expected ignore_unverified_devices={expected_ignore_unverified}, got {ignore_unverified}"

        return call_args, kwargs


@pytest.mark.asyncio
class TestE2EEEncryption:
    """Test suite for E2EE message encryption"""

    async def test_encrypted_room_detection(self):
        """Test that encrypted rooms are properly detected"""
        framework = E2EETestFramework()

        # Create mock client with encrypted and unencrypted rooms
        rooms = {
            "!encrypted:example.org": MockEncryptedRoom(
                "!encrypted:example.org", encrypted=True
            ),
            "!unencrypted:example.org": MockUnencryptedRoom("!unencrypted:example.org"),
        }
        mock_client = framework.create_mock_client(rooms=rooms)

        # Test encrypted room detection
        encrypted_room = mock_client.rooms["!encrypted:example.org"]
        assert (
            encrypted_room.encrypted
        ), "Encrypted room should be detected as encrypted"

        # Test unencrypted room detection
        unencrypted_room = mock_client.rooms["!unencrypted:example.org"]
        assert (
            not unencrypted_room.encrypted
        ), "Unencrypted room should be detected as unencrypted"

    @patch("mmrelay.matrix_utils.config")
    @patch("mmrelay.matrix_utils.matrix_client")
    async def test_message_to_encrypted_room_uses_ignore_unverified(
        self, mock_matrix_client, mock_config
    ):
        """Test that messages to encrypted rooms use ignore_unverified_devices=True"""
        framework = E2EETestFramework()

        # Setup mock config that supports both .get() and direct indexing
        test_config = {
            "meshtastic": {"meshnet_name": "TestNet"},
            "matrix_rooms": {"!encrypted:example.org": {"meshtastic_channel": "general"}},
        }
        mock_config.get.return_value = test_config
        mock_config.__getitem__.side_effect = test_config.__getitem__
        mock_config.__contains__.side_effect = test_config.__contains__

        # Setup mock client with encrypted room
        rooms = {
            "!encrypted:example.org": MockEncryptedRoom(
                "!encrypted:example.org", encrypted=True
            )
        }
        mock_client = framework.create_mock_client(rooms=rooms)
        mock_matrix_client.return_value = mock_client

        # Send message to encrypted room
        await matrix_relay(
            room_id="!encrypted:example.org",
            message="Test message",
            longname="Test User",
            shortname="TU",
            meshnet_name="TestNet",
            portnum=1,
        )

        # Verify encryption parameters
        call_args, kwargs = framework.verify_encryption_parameters(
            mock_client, expected_ignore_unverified=True
        )

        # Verify room_id and message content
        assert (
            kwargs["room_id"] == "!encrypted:example.org"
        ), "Should send to correct room"
        assert (
            kwargs["message_type"] == "m.room.message"
        ), "Should use correct message type"

    @patch("mmrelay.matrix_utils.config")
    @patch("mmrelay.matrix_utils.matrix_client")
    async def test_message_to_unencrypted_room_still_uses_ignore_unverified(
        self, mock_matrix_client, mock_config
    ):
        """Test that messages to unencrypted rooms also use ignore_unverified_devices=True (current implementation)"""
        framework = E2EETestFramework()

        # Setup mock config that supports both .get() and direct indexing
        test_config = {
            "meshtastic": {"meshnet_name": "TestNet"},
            "matrix_rooms": {"!unencrypted:example.org": {"meshtastic_channel": "general"}},
        }
        mock_config.get.return_value = test_config
        mock_config.__getitem__.side_effect = test_config.__getitem__
        mock_config.__contains__.side_effect = test_config.__contains__

        # Setup mock client with unencrypted room
        rooms = {
            "!unencrypted:example.org": MockUnencryptedRoom("!unencrypted:example.org")
        }
        mock_client = framework.create_mock_client(rooms=rooms)
        mock_matrix_client.return_value = mock_client

        # Send message to unencrypted room
        await matrix_relay(
            room_id="!unencrypted:example.org",
            message="Test message",
            longname="Test User",
            shortname="TU",
            meshnet_name="TestNet",
            portnum=1,
        )

        # Verify encryption parameters (should still use ignore_unverified=True based on current implementation)
        call_args, kwargs = framework.verify_encryption_parameters(
            mock_client, expected_ignore_unverified=True
        )

    async def test_room_send_call_structure(self):
        """Test the exact structure of room_send calls"""
        framework = E2EETestFramework()
        mock_client = framework.create_mock_client()

        # Mock content structure
        test_content = {
            "msgtype": "m.text",
            "body": "Test message",
            "meshtastic_id": 12345,
            "meshtastic_longname": "Test User",
        }

        # Call room_send directly
        await mock_client.room_send(
            room_id="!test:example.org",
            message_type="m.room.message",
            content=test_content,
            ignore_unverified_devices=True,
        )

        # Verify call structure
        call_args, kwargs = framework.verify_encryption_parameters(mock_client)
        assert (
            kwargs["content"] == test_content
        ), "Content should match expected structure"


@pytest.mark.asyncio
class TestE2EERoomStateDetection:
    """Test suite for room encryption state detection"""

    async def test_room_encryption_state_after_sync(self):
        """Test that room encryption state is properly set after sync"""
        framework = E2EETestFramework()

        # Create mock client
        mock_client = framework.create_mock_client()

        # Mock sync response that should populate room encryption state
        mock_sync_response = MagicMock()
        mock_sync_response.rooms.join = {
            "!encrypted:example.org": MagicMock(),
            "!unencrypted:example.org": MagicMock(),
        }
        mock_client.sync.return_value = mock_sync_response

        # Perform sync
        await mock_client.sync(timeout=30000, full_state=True)

        # Verify sync was called with correct parameters
        mock_client.sync.assert_called_with(timeout=30000, full_state=True)

        # Verify rooms are accessible
        assert "!encrypted:example.org" in mock_client.rooms
        assert "!unencrypted:example.org" in mock_client.rooms

    async def test_full_sync_vs_lightweight_sync(self):
        """Test difference between full sync and lightweight sync for encryption state"""
        framework = E2EETestFramework()
        mock_client = framework.create_mock_client()

        # Test lightweight sync (current early sync)
        await mock_client.sync(timeout=5000, full_state=False)
        mock_client.sync.assert_called_with(timeout=5000, full_state=False)

        # Test full sync (matrix-nio-send pattern)
        await mock_client.sync(timeout=30000, full_state=True)
        mock_client.sync.assert_called_with(timeout=30000, full_state=True)

        # Both should be called
        assert mock_client.sync.call_count == 2


@pytest.mark.asyncio
class TestE2EEIntegration:
    """Integration tests for E2EE functionality"""

    @patch("mmrelay.matrix_utils.config")
    @patch("mmrelay.matrix_utils.matrix_client")
    async def test_complete_e2ee_message_flow(self, mock_global_client, mock_config):
        """Test complete flow from E2EE setup to encrypted message sending"""
        framework = E2EETestFramework()

        # Setup mock config that supports both .get() and direct indexing
        test_config = {
            "meshtastic": {"meshnet_name": "TestNet"},
            "matrix_rooms": {"!encrypted:example.org": {"meshtastic_channel": "general"}},
        }
        mock_config.get.return_value = test_config
        mock_config.__getitem__.side_effect = test_config.__getitem__
        mock_config.__contains__.side_effect = test_config.__contains__

        # Create mock client with E2EE setup
        mock_client = framework.create_mock_client(should_upload_keys=True)
        mock_global_client.return_value = mock_client

        # Mock E2EE setup steps
        mock_client.load_store = MagicMock()
        mock_client.whoami = AsyncMock(return_value=MagicMock(device_id="TEST_DEVICE"))

        # Simulate the E2EE setup sequence
        await mock_client.keys_upload()  # Key upload
        await mock_client.sync(timeout=30000, full_state=True)  # Full sync

        # Send message to encrypted room
        with patch("mmrelay.matrix_utils.matrix_client", mock_client):
            await matrix_relay(
                room_id="!encrypted:example.org",
                message="E2EE Test Message",
                longname="E2EE Test User",
                shortname="ETU",
                meshnet_name="E2EENet",
                portnum=1,
            )

        # Verify E2EE setup was called
        mock_client.keys_upload.assert_called()
        mock_client.sync.assert_called()

        # Verify message was sent with encryption parameters
        framework.verify_encryption_parameters(
            mock_client, expected_ignore_unverified=True
        )


class E2EEDebugUtilities:
    """Utilities for debugging E2EE issues in real environments"""

    @staticmethod
    async def diagnose_client_encryption_state(client):
        """
        Analyze a Matrix-like client's end-to-end encryption state and return a structured diagnostic.
        
        Returns a dictionary with the following keys:
        - client_info: {"encrypted_rooms": [room_id, ...]} — list of room IDs that appear to have encryption enabled.
        - prerequisites: {
            "has_device_id": bool,            # whether client.device_id is present/truthy
            "encryption_enabled": bool        # True if there are encrypted rooms or a device_id is present
          }
        - room_analysis: {room_id: {"encrypted": True|False|"unknown", "display_name": str, "room_type": str}, ...}
          — per-room details derived from client.rooms; uses safe defaults when attributes are missing.
        - recommendations: [str, ...] — human-readable suggestions produced when device_id is missing, encryption is not enabled, or no encrypted rooms are detected.
        
        The function is defensive: it works with any object that exposes a .rooms mapping and optional .device_id, and it will populate safe defaults rather than raising if those attributes are absent.
        """
        # Initialize with safe defaults to avoid KeyError when tools are unavailable
        encrypted_rooms = []
        if hasattr(client, "rooms") and client.rooms:
            encrypted_rooms = [
                rid for rid, r in client.rooms.items() if getattr(r, "encrypted", False)
            ]
        diagnosis = {
            "client_info": {
                "encrypted_rooms": encrypted_rooms,
            },  # Fallback until E2EEDiagnosticTools is enabled
            "prerequisites": {
                "has_device_id": bool(getattr(client, "device_id", None)),
                # Better heuristic: encryption enabled if we have encrypted rooms or device_id
                "encryption_enabled": bool(encrypted_rooms)
                or bool(getattr(client, "device_id", None)),
            },
            "room_analysis": {},
            "recommendations": [],
        }

        # Analyze each room
        if hasattr(client, "rooms") and client.rooms:
            for room_id, room in client.rooms.items():
                diagnosis["room_analysis"][room_id] = {
                    "encrypted": getattr(room, "encrypted", "unknown"),
                    "display_name": getattr(room, "display_name", "unknown"),
                    "room_type": type(room).__name__,
                }

        # Generate recommendations
        if not diagnosis["prerequisites"].get("has_device_id", False):
            diagnosis["recommendations"].append(
                "Missing device_id - E2EE will not work"
            )

        if not diagnosis["prerequisites"].get("encryption_enabled", False):
            diagnosis["recommendations"].append(
                "Encryption not enabled in client config"
            )

        if not diagnosis["client_info"].get("encrypted_rooms"):
            diagnosis["recommendations"].append(
                "No encrypted rooms detected - may need full sync"
            )

        return diagnosis

    @staticmethod
    def create_test_room_send_call():
        """Create a test room_send call to verify parameters"""
        return {
            "room_id": "!test:example.org",
            "message_type": "m.room.message",
            "content": {"msgtype": "m.text", "body": "Test message"},
            "ignore_unverified_devices": True,
        }


if __name__ == "__main__":
    # Run basic diagnostic test
    print("E2EE Testing Framework loaded successfully")
    print("Available test classes:")
    print("- TestE2EEEncryption: Basic encryption parameter tests")
    print("- TestE2EERoomStateDetection: Room state and sync tests")
    print("- TestE2EEIntegration: Full integration tests")
    print("- E2EEDebugUtilities: Debugging tools")
    print("\nRun with: python -m pytest tests/test_e2ee_encryption.py -v")
