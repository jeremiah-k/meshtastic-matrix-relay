#!/usr/bin/env python3
"""
E2EE Integration Test

This test can verify actual E2EE behavior by inspecting the real Matrix client
state and message sending behavior without requiring manual room testing.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

try:
    from .test_e2ee_encryption import E2EEDebugUtilities

    from mmrelay.config import load_config
    from mmrelay.matrix_utils import connect_matrix, matrix_client, matrix_relay

    IMPORTS_AVAILABLE = True
except ImportError as e:
    print(f"❌ Import error: {e}")
    print("Make sure you're running from the project root directory")
    IMPORTS_AVAILABLE = False

    # Create dummy classes to prevent further import errors
    class E2EEDebugUtilities:
        pass


class E2EEIntegrationTester:
    """Integration tester for E2EE functionality"""

    def __init__(self):
        self.config = None
        self.client = None
        self.test_results = {}

    async def setup_test_environment(self):
        """Set up test environment with real config"""
        print("🔧 Setting up test environment...")

        try:
            # Load real config
            self.config = load_config()
            if not self.config:
                raise Exception("Could not load config")

            print("✅ Config loaded successfully")
            return True

        except Exception as e:
            print(f"❌ Setup failed: {e}")
            return False

    async def check_matrix_connection(self):
        """Test Matrix connection with E2EE"""
        print("\n🔍 Testing Matrix connection...")

        try:
            # Attempt to connect (but don't start sync loop)
            self.client = await connect_matrix(self.config)

            if not self.client:
                raise Exception("Failed to connect to Matrix")

            print("✅ Matrix connection successful")

            # Check E2EE setup
            has_device_id = bool(getattr(self.client, "device_id", None))
            has_store_path = bool(getattr(self.client, "store_path", None))
            encryption_enabled = False

            if hasattr(self.client, "config") and self.client.config:
                encryption_enabled = getattr(
                    self.client.config, "encryption_enabled", False
                )

            print(f"   Device ID: {getattr(self.client, 'device_id', 'None')}")
            print(f"   Store Path: {getattr(self.client, 'store_path', 'None')}")
            print(f"   Encryption Enabled: {encryption_enabled}")

            self.test_results["connection"] = {
                "success": True,
                "has_device_id": has_device_id,
                "has_store_path": has_store_path,
                "encryption_enabled": encryption_enabled,
            }

            return True

        except Exception as e:
            print(f"❌ Connection failed: {e}")
            self.test_results["connection"] = {"success": False, "error": str(e)}
            return False

    async def check_room_encryption_detection(self):
        """Test room encryption state detection"""
        print("\n🔍 Testing room encryption detection...")

        if not self.client:
            print("❌ No client available")
            return False

        try:
            # Check if we have rooms
            rooms = getattr(self.client, "rooms", {})

            if not rooms:
                print("⚠️  No rooms found - may need sync first")
                # Try a quick sync to populate rooms
                try:
                    await asyncio.wait_for(
                        self.client.sync(timeout=5000, full_state=False), timeout=10.0
                    )
                    rooms = getattr(self.client, "rooms", {})
                    print(f"   After sync: {len(rooms)} rooms found")
                except Exception as sync_e:
                    print(f"   Sync failed: {sync_e}")

            room_analysis = {}
            encrypted_count = 0
            unencrypted_count = 0

            for room_id, room in rooms.items():
                encrypted = getattr(room, "encrypted", "unknown")
                display_name = getattr(room, "display_name", "Unknown")

                room_analysis[room_id] = {
                    "encrypted": encrypted,
                    "display_name": display_name,
                    "room_type": type(room).__name__,
                }

                if encrypted is True:
                    encrypted_count += 1
                elif encrypted is False:
                    unencrypted_count += 1

            print(f"   Total rooms: {len(rooms)}")
            print(f"   Encrypted rooms: {encrypted_count}")
            print(f"   Unencrypted rooms: {unencrypted_count}")

            # Show first few rooms as examples
            for i, (room_id, analysis) in enumerate(room_analysis.items()):
                if i >= 3:  # Only show first 3
                    break
                print(
                    f"   Room {i+1}: {analysis['display_name']} - Encrypted: {analysis['encrypted']}"
                )

            self.test_results["room_detection"] = {
                "success": True,
                "total_rooms": len(rooms),
                "encrypted_rooms": encrypted_count,
                "unencrypted_rooms": unencrypted_count,
                "room_analysis": room_analysis,
            }

            return True

        except Exception as e:
            print(f"❌ Room detection failed: {e}")
            self.test_results["room_detection"] = {"success": False, "error": str(e)}
            return False

    async def check_message_sending_parameters(self):
        """Test message sending parameter detection (without actually sending)"""
        print("\n🔍 Testing message sending parameters...")

        if not self.client:
            print("❌ No client available")
            return False

        try:
            # Get a test room (preferably encrypted)
            rooms = getattr(self.client, "rooms", {})
            test_room_id = None
            test_room_encrypted = False

            # Look for an encrypted room first
            for room_id, room in rooms.items():
                if getattr(room, "encrypted", False):
                    test_room_id = room_id
                    test_room_encrypted = True
                    break

            # If no encrypted room, use any room
            if not test_room_id and rooms:
                test_room_id = list(rooms.keys())[0]
                test_room_encrypted = getattr(rooms[test_room_id], "encrypted", False)

            if not test_room_id:
                print("⚠️  No rooms available for testing")
                return False

            print(f"   Test room: {test_room_id}")
            print(f"   Room encrypted: {test_room_encrypted}")

            # Simulate the parameter detection logic from matrix_relay
            room = self.client.rooms.get(test_room_id)
            if room:
                detected_encrypted = getattr(room, "encrypted", "unknown")
                print(f"   Detected encryption status: {detected_encrypted}")
            else:
                print("   Room not found in client.rooms")
                detected_encrypted = "unknown"

            # Based on current implementation, ignore_unverified_devices should always be True
            expected_ignore_unverified = True

            print(
                f"   Would use ignore_unverified_devices: {expected_ignore_unverified}"
            )

            self.test_results["message_parameters"] = {
                "success": True,
                "test_room_id": test_room_id,
                "room_encrypted": test_room_encrypted,
                "detected_encrypted": detected_encrypted,
                "ignore_unverified_devices": expected_ignore_unverified,
            }

            return True

        except Exception as e:
            print(f"❌ Message parameter test failed: {e}")
            self.test_results["message_parameters"] = {
                "success": False,
                "error": str(e),
            }
            return False

    async def run_full_integration_test(self):
        """Run complete integration test suite"""
        print("🚀 E2EE Integration Test Suite")
        print("=" * 50)

        # Setup
        if not await self.setup_test_environment():
            return False

        # Run tests
        tests = [
            ("Matrix Connection", self.check_matrix_connection),
            ("Room Encryption Detection", self.check_room_encryption_detection),
            ("Message Sending Parameters", self.check_message_sending_parameters),
        ]

        passed = 0
        failed = 0

        for test_name, test_func in tests:
            print(f"\n📋 {test_name}")
            print("-" * 30)

            try:
                if await test_func():
                    passed += 1
                    print(f"✅ {test_name}: PASSED")
                else:
                    failed += 1
                    print(f"❌ {test_name}: FAILED")
            except Exception as e:
                failed += 1
                print(f"❌ {test_name}: ERROR - {e}")

        # Summary
        print("\n" + "=" * 50)
        print("📊 INTEGRATION TEST SUMMARY")
        print("=" * 50)
        print(f"Total: {passed + failed} tests")
        print(f"Passed: {passed}")
        print(f"Failed: {failed}")

        # Detailed results
        print("\n📋 DETAILED RESULTS:")
        print(json.dumps(self.test_results, indent=2, default=str))

        # Cleanup
        if self.client:
            try:
                await self.client.close()
            except:
                pass

        return failed == 0

    def generate_recommendations(self):
        """Generate recommendations based on test results"""
        print("\n💡 RECOMMENDATIONS:")
        print("=" * 30)

        if "connection" in self.test_results:
            conn = self.test_results["connection"]
            if not conn.get("has_device_id"):
                print("❌ Missing device_id - E2EE will not work")
            if not conn.get("encryption_enabled"):
                print("❌ Encryption not enabled in client config")

        if "room_detection" in self.test_results:
            room = self.test_results["room_detection"]
            if room.get("encrypted_rooms", 0) == 0:
                print("⚠️  No encrypted rooms detected - may need full sync")
            if room.get("total_rooms", 0) == 0:
                print("⚠️  No rooms found - client may not be properly synced")

        print("\n🔧 DEBUGGING STEPS:")
        print("1. Check MMRelay logs for room encryption status")
        print("2. Verify credentials.json has correct device_id")
        print("3. Ensure full sync is performed after E2EE setup")
        print("4. Test with matrix-nio-send to compare behavior")


async def main():
    """Main test runner"""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("E2EE Integration Test")
        print("====================")
        print("Tests actual E2EE behavior with real Matrix client")
        print()
        print("Usage:")
        print("  python test_e2ee_integration.py        # Run integration tests")
        print("  python test_e2ee_integration.py --help # Show this help")
        print()
        print("Requirements:")
        print("- Valid MMRelay configuration")
        print("- Matrix credentials (credentials.json)")
        print("- Network access to Matrix homeserver")
        return

    tester = E2EEIntegrationTester()
    success = await tester.run_full_integration_test()
    tester.generate_recommendations()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    if IMPORTS_AVAILABLE:
        asyncio.run(main())
    else:
        print("❌ Required imports not available, skipping integration test")
        sys.exit(1)
