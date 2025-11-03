"""
Tests for async database utilities.

Tests async wrapper functions for all database operations using aiosqlite,
ensuring compatibility with sync versions and proper async behavior.
"""

import os

# Set PYTHONPATH for imports
import sys
import tempfile
import unittest
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay import async_db_utils


class TestAsyncDbUtils:
    """Test cases for async database utilities."""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Mock the db_path function
        import mmrelay.db_utils

        self.original_get_db_path = mmrelay.db_utils.get_db_path
        mmrelay.db_utils.get_db_path = lambda: self.db_path

        yield

        # Cleanup
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        # Restore original function
        mmrelay.db_utils.get_db_path = self.original_get_db_path

    @pytest.mark.asyncio
    async def test_async_initialize_database(self):
        """Test async database initialization."""
        await async_db_utils.async_initialize_database()

        # Verify database file was created
        assert os.path.exists(self.db_path)

        # Verify tables exist by connecting and querying
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as conn:
            # Check that all tables exist
            tables = await conn.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            table_names = [row[0] for row in tables]

            expected_tables = ["plugin_data", "longnames", "shortnames", "message_map"]
            for table in expected_tables:
                assert table in table_names

    @pytest.mark.asyncio
    async def test_async_plugin_data_operations(self):
        """Test async plugin data storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test storing plugin data
        test_data = {"key": "value", "number": 42}
        await async_db_utils.async_store_plugin_data("test_plugin", "node1", test_data)

        # Test retrieving plugin data for node
        retrieved_data = await async_db_utils.async_get_plugin_data_for_node(
            "test_plugin", "node1"
        )
        self.assertEqual(retrieved_data, [test_data])

        # Test retrieving all plugin data
        all_data = await async_db_utils.async_get_plugin_data("test_plugin")
        self.assertEqual(all_data, [test_data])

        # Test deleting plugin data
        await async_db_utils.async_delete_plugin_data("test_plugin", "node1")
        deleted_data = await async_db_utils.async_get_plugin_data_for_node(
            "test_plugin", "node1"
        )
        self.assertEqual(deleted_data, [])

    @pytest.mark.asyncio
    async def test_async_longname_operations(self):
        """Test async longname storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test saving longname
        await async_db_utils.async_save_longname("node1", "TestLongName")

        # Test retrieving longname
        retrieved_name = await async_db_utils.async_get_longname("node1")
        self.assertEqual(retrieved_name, "TestLongName")

        # Test non-existent node
        non_existent = await async_db_utils.async_get_longname("nonexistent")
        self.assertIsNone(non_existent)

    @pytest.mark.asyncio
    async def test_async_shortname_operations(self):
        """Test async shortname storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test saving shortname
        await async_db_utils.async_save_shortname("node1", "TN")

        # Test retrieving shortname
        retrieved_name = await async_db_utils.async_get_shortname("node1")
        self.assertEqual(retrieved_name, "TN")

        # Test non-existent node
        non_existent = await async_db_utils.async_get_shortname("nonexistent")
        self.assertIsNone(non_existent)

    @pytest.mark.asyncio
    async def test_async_message_map_operations(self):
        """Test async message map storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test storing message map
        await async_db_utils.async_store_message_map(
            meshtastic_id="msg1",
            matrix_event_id="$event1",
            matrix_room_id="!room1",
            meshtastic_text="Hello world",
            meshtastic_meshnet="testnet",
        )

        # Test retrieving by meshtastic ID
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "$event1")  # matrix_event_id
        self.assertEqual(result[1], "!room1")  # matrix_room_id
        self.assertEqual(result[2], "Hello world")  # meshtastic_text
        self.assertEqual(result[3], "testnet")  # meshtastic_meshnet

        # Test retrieving by matrix event ID
        result = await async_db_utils.async_get_message_map_by_matrix_event_id(
            "$event1"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "msg1")  # meshtastic_id
        self.assertEqual(result[1], "!room1")  # matrix_room_id
        self.assertEqual(result[2], "Hello world")  # meshtastic_text
        self.assertEqual(result[3], "testnet")  # meshtastic_meshnet

    @pytest.mark.asyncio
    async def test_async_message_map_edge_cases(self):
        """Test async message map edge cases and error handling."""
        await async_db_utils.async_initialize_database()

        # Test storing minimal message map
        await async_db_utils.async_store_message_map(
            meshtastic_id="msg2", matrix_event_id="$event2", matrix_room_id="!room2"
        )

        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg2")
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "$event2")
        self.assertEqual(result[1], "!room2")
        self.assertIsNone(result[2])  # meshtastic_text should be None
        self.assertIsNone(result[3])  # meshtastic_meshnet should be None

        # Test non-existent message
        non_existent = await async_db_utils.async_get_message_map_by_meshtastic_id(
            "nonexistent"
        )
        self.assertIsNone(non_existent)

    @pytest.mark.asyncio
    async def test_async_wipe_message_map(self):
        """Test async message map wiping."""
        await async_db_utils.async_initialize_database()

        # Add some test data
        await async_db_utils.async_store_message_map("msg1", "$event1", "!room1")
        await async_db_utils.async_store_message_map("msg2", "$event2", "!room2")

        # Verify data exists
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        self.assertIsNotNone(result)

        # Wipe the table
        await async_db_utils.async_wipe_message_map()

        # Verify data is gone
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        self.assertIsNone(result)

    @pytest.mark.asyncio
    async def test_async_prune_message_map(self):
        """Test async message map pruning."""
        await async_db_utils.async_initialize_database()

        # Add test data
        for i in range(10):
            await async_db_utils.async_store_message_map(
                f"msg{i}", f"$event{i}", f"!room{i}"
            )

        # Prune to keep only 5 messages
        await async_db_utils.async_prune_message_map(5)

        # Should only have 5 messages remaining (the newest ones)
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM message_map")
            count = (await cursor.fetchone())[0]
            self.assertEqual(count, 5)

    @pytest.mark.asyncio
    async def test_async_error_handling(self):
        """Test async error handling and rollback."""
        await async_db_utils.async_initialize_database()

        # Test handling of invalid JSON in plugin data
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as conn:
            # Insert invalid JSON directly
            await conn.execute(
                "INSERT INTO plugin_data (plugin_name, meshtastic_id, data) VALUES (?, ?, ?)",
                ("test_plugin", "node1", "invalid json {"),
            )
            await conn.commit()

        # Should handle invalid JSON gracefully
        retrieved_data = await async_db_utils.async_get_plugin_data_for_node(
            "test_plugin", "node1"
        )
        self.assertEqual(retrieved_data, [])  # Should return empty list on error

    @pytest.mark.asyncio
    async def test_async_database_connection_fallback(self):
        """Test fallback to direct connection when pooling is disabled."""
        # Mock config to disable pooling
        with patch("mmrelay.db_utils.config", {"database": {"pool_enabled": False}}):

            # Should still work with direct connection
            await async_db_utils.async_initialize_database()

            # Test basic operation
            await async_db_utils.async_save_longname("test_node", "TestName")
            retrieved = await async_db_utils.async_get_longname("test_node")
            self.assertEqual(retrieved, "TestName")


class TestAsyncDbUtilsIntegration(unittest.TestCase):
    """Integration tests for async database utilities."""

    def setUp(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        patcher = patch("mmrelay.async_db_utils.get_db_path")
        self.mock_get_db_path = patcher.start()
        self.mock_get_db_path.return_value = self.db_path

    def tearDown(self):
        """Clean up test environment."""
        self.mock_get_db_path.stop()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    @pytest.mark.asyncio
    async def test_complex_workflow(self):
        """Test a complex workflow using multiple async operations."""
        await async_db_utils.async_initialize_database()

        # Setup: Save some names
        await async_db_utils.async_save_longname("node1", "Alice")
        await async_db_utils.async_save_shortname("node1", "A")
        await async_db_utils.async_save_longname("node2", "Bob")
        await async_db_utils.async_save_shortname("node2", "B")

        # Store some plugin data
        plugin_data = {"settings": {"theme": "dark", "notifications": True}}
        await async_db_utils.async_store_plugin_data("ui_plugin", "node1", plugin_data)

        # Store some message mappings
        await async_db_utils.async_store_message_map(
            "msg1", "$event1", "!room1", "Hello from Alice"
        )
        await async_db_utils.async_store_message_map(
            "msg2", "$event2", "!room1", "Hi from Bob"
        )

        # Verify all data is correctly stored and retrievable
        alice_long = await async_db_utils.async_get_longname("node1")
        self.assertEqual(alice_long, "Alice")

        bob_short = await async_db_utils.async_get_shortname("node2")
        self.assertEqual(bob_short, "B")

        alice_plugin = await async_db_utils.async_get_plugin_data_for_node(
            "ui_plugin", "node1"
        )
        self.assertEqual(alice_plugin, [plugin_data])

        alice_msg = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        self.assertEqual(alice_msg[2], "Hello from Alice")

        # Test cleanup
        await async_db_utils.async_delete_plugin_data("ui_plugin", "node1")
        deleted_plugin = await async_db_utils.async_get_plugin_data_for_node(
            "ui_plugin", "node1"
        )
        self.assertEqual(deleted_plugin, [])


if __name__ == "__main__":
    unittest.main()
