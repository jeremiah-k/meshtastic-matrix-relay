"""
Tests for async database utilities.

Tests async wrapper functions for all database operations using aiosqlite,
ensuring compatibility with sync versions and proper async behavior.
"""

import os

# Set PYTHONPATH for imports
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay import async_db_utils

pytestmark = pytest.mark.asyncio


class TestAsyncDbUtils:
    """Test cases for async database utilities."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Mock db_path function
        import mmrelay.db_utils

        self.original_get_db_path = mmrelay.db_utils.get_db_path
        mmrelay.db_utils.get_db_path = lambda: self.db_path

        yield

        # Cleanup
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        # Restore original function
        import mmrelay.db_utils

        mmrelay.db_utils.get_db_path = self.original_get_db_path

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

        pass

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
        assert retrieved_data == test_data

        # Test retrieving all plugin data
        all_data = await async_db_utils.async_get_plugin_data("test_plugin")
        assert all_data == [test_data]

        # Test deleting plugin data
        await async_db_utils.async_delete_plugin_data("test_plugin", "node1")
        deleted_data = await async_db_utils.async_get_plugin_data_for_node(
            "test_plugin", "node1"
        )
        assert deleted_data is None

        pass

    async def test_async_longname_operations(self):
        """Test async longname storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test saving longname
        await async_db_utils.async_save_longname("node1", "TestLongName")

        # Test retrieving longname
        retrieved_name = await async_db_utils.async_get_longname("node1")
        assert retrieved_name == "TestLongName"

        # Test non-existent node
        non_existent = await async_db_utils.async_get_longname("nonexistent")
        assert non_existent is None

        pass

    async def test_async_shortname_operations(self):
        """Test async shortname storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test saving shortname
        await async_db_utils.async_save_shortname("node1", "TN")

        # Test retrieving shortname
        retrieved_name = await async_db_utils.async_get_shortname("node1")
        assert retrieved_name == "TN"

        # Test non-existent node
        non_existent = await async_db_utils.async_get_shortname("nonexistent")
        assert non_existent is None

        pass

    async def test_async_message_map_operations(self):
        """Test async message map storage and retrieval."""
        await async_db_utils.async_initialize_database()

        # Test storing message map
        await async_db_utils.async_store_message_map(
            meshtastic_id="msg1",
            matrix_event_id="$event1",
            matrix_room_id="!room1",
            meshtastic_text="Hello World",
            meshtastic_meshnet="testnet",
        )

        # Test retrieval by meshtastic_id
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        assert result is not None
        assert result[0] == "$event1"
        assert result[1] == "!room1"
        assert result[2] == "Hello World"
        assert result[3] == "testnet"

        # Test retrieval by matrix_event_id
        result = await async_db_utils.async_get_message_map_by_matrix_event_id(
            "$event1"
        )
        assert result is not None
        assert result[0] == "msg1"
        assert result[1] == "!room1"
        assert result[2] == "Hello World"
        assert result[3] == "testnet"

        pass

    async def test_async_wipe_message_map(self):
        """Test async message map wipe functionality."""
        await async_db_utils.async_initialize_database()

        # Add a test entry
        await async_db_utils.async_store_message_map(
            meshtastic_id="msg1", matrix_event_id="$event1", matrix_room_id="!room1"
        )

        # Verify entry exists
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        assert result is not None

        # Wipe the table
        await async_db_utils.async_wipe_message_map()

        # Verify entry is gone
        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        assert result is None

        pass

    async def test_async_prune_message_map(self):
        """Test async message map pruning functionality."""
        await async_db_utils.async_initialize_database()

        # Add 10 messages
        for i in range(10):
            await async_db_utils.async_store_message_map(
                meshtastic_id=f"msg{i}",
                matrix_event_id=f"$event{i}",
                matrix_room_id="!room1",
            )

        # Prune to keep only 5
        await async_db_utils.async_prune_message_map(5)

        # Should only have 5 remaining (newest ones)
        import aiosqlite

        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM message_map")
            count = (await cursor.fetchone())[0]
            assert count == 5

        pass


@pytest.mark.usefixtures("mock_event_loop")
@pytest.mark.usefixtures("mock_event_loop")
class TestAsyncDbUtilsIntegration:
    """Integration tests for async database utilities."""

    @pytest.fixture(autouse=True)
    def setup_method(self):
        """Set up test environment."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
        self.temp_db.close()
        self.db_path = self.temp_db.name

        # Mock db_path function
        import mmrelay.db_utils

        self.original_get_db_path = mmrelay.db_utils.get_db_path
        mmrelay.db_utils.get_db_path = lambda: self.db_path

        yield

        # Cleanup
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
        # Restore original function
        import mmrelay.db_utils

        mmrelay.db_utils.get_db_path = self.original_get_db_path

    @pytest.mark.asyncio
    async def test_complex_workflow(self):
        """Test complex workflow with multiple operations."""
        await async_db_utils.async_initialize_database()

        # Store node information
        await async_db_utils.async_save_longname("node1", "TestNode")
        await async_db_utils.async_save_shortname("node1", "TN")

        # Store plugin data
        await async_db_utils.async_store_plugin_data("weather", "node1", {"temp": 72})

        # Store message
        await async_db_utils.async_store_message_map(
            meshtastic_id="msg1",
            matrix_event_id="$event1",
            matrix_room_id="!room1",
            meshtastic_text="Weather update",
        )

        # Verify all data is retrievable
        longname_result = await async_db_utils.async_get_longname("node1")
        assert longname_result == "TestNode"

        shortname_result = await async_db_utils.async_get_shortname("node1")
        assert shortname_result == "TN"

        plugin_data_result = await async_db_utils.async_get_plugin_data_for_node(
            "weather", "node1"
        )
        assert plugin_data_result == {"temp": 72}

        result = await async_db_utils.async_get_message_map_by_meshtastic_id("msg1")
        assert result is not None
        assert result[2] == "Weather update"

        pass
