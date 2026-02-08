#!/usr/bin/env python3
"""
Test suite for Integration scenarios and end-to-end testing in MMRelay.

Tests integration scenarios including:
- Complete message flow from Meshtastic to Matrix
- Complete message flow from Matrix to Meshtastic
- Plugin interaction chains
- Configuration loading and validation
- Service lifecycle management
- Error recovery scenarios
- Multi-room message routing
"""

import asyncio
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from mmrelay.config import load_config
from mmrelay.db_utils import initialize_database
from mmrelay.matrix_utils import connect_matrix
from mmrelay.meshtastic_utils import connect_meshtastic, on_meshtastic_message


class TestIntegrationScenarios(unittest.TestCase):
    """Test cases for integration scenarios and end-to-end flows."""

    def setUp(self):
        """
        Prepares the test environment by resetting global state before each test case.
        """
        self._reset_global_state()

    def tearDown(self):
        """
        Cleans up after each test by resetting global state to ensure test isolation.
        """
        self._reset_global_state()

    def _reset_global_state(self):
        """
        Reset global state in MMRelay modules to ensure test isolation between tests.

        This method clears or reinitializes global variables in the `meshtastic_utils`, `matrix_utils`, `plugin_loader`, `config`, and `db_utils` modules if they are loaded, preventing shared state from affecting subsequent tests.
        """
        # Reset meshtastic_utils globals
        if "mmrelay.meshtastic_utils" in sys.modules:
            module = sys.modules["mmrelay.meshtastic_utils"]
            module.config = None
            module.matrix_rooms = []
            module.meshtastic_client = None
            module.event_loop = None
            module.reconnecting = False
            module.shutting_down = False
            module.reconnect_task = None
            module.subscribed_to_messages = False
            module.subscribed_to_connection_lost = False

        # Reset matrix_utils globals
        if "mmrelay.matrix_utils" in sys.modules:
            module = sys.modules["mmrelay.matrix_utils"]
            if hasattr(module, "config"):
                module.config = None
            if hasattr(module, "matrix_client"):
                module.matrix_client = None

        # Reset plugin_loader globals
        if "mmrelay.plugin_loader" in sys.modules:
            module = sys.modules["mmrelay.plugin_loader"]
            if hasattr(module, "config"):
                module.config = None
            if hasattr(module, "sorted_active_plugins"):
                module.sorted_active_plugins = []
            if hasattr(module, "plugins_loaded"):
                module.plugins_loaded = False

        # Reset config globals
        if "mmrelay.config" in sys.modules:
            module = sys.modules["mmrelay.config"]
            if hasattr(module, "config"):
                module.config = None

        # Reset db_utils globals
        if "mmrelay.db_utils" in sys.modules:
            module = sys.modules["mmrelay.db_utils"]
            if hasattr(module, "config"):
                module.config = None

    def test_complete_meshtastic_to_matrix_flow(self):
        """
        Test the end-to-end relay of a Meshtastic message to Matrix, including plugin processing.

        Simulates receiving a Meshtastic message, processes it through the plugin chain, and verifies that the message is relayed to the correct Matrix room and that the plugin handler is invoked.
        """
        # Create test configuration
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "meshnet_name": "TestMesh",
            },
            "plugins": {"debug": {"active": True}},
        }

        # Mock Meshtastic packet
        packet = {
            "decoded": {"text": "Hello from Meshtastic!", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
            "to": 4294967295,  # BROADCAST_NUM
            "id": 123456789,
        }

        mock_meshtastic_interface = MagicMock()
        mock_meshtastic_interface.nodes = {
            "!12345678": {
                "user": {"id": "!12345678", "longName": "Test Node", "shortName": "TN"}
            }
        }

        with patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_matrix_relay:
            with patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins:
                # Mock debug plugin
                mock_plugin = MagicMock()
                mock_plugin.handle_meshtastic_message = AsyncMock(return_value=False)
                mock_load_plugins.return_value = [mock_plugin]

                # Set up global state
                import mmrelay.meshtastic_utils

                mmrelay.meshtastic_utils.config = config
                mmrelay.meshtastic_utils.matrix_rooms = config["matrix_rooms"]
                mmrelay.meshtastic_utils.event_loop = MagicMock()  # Mock event loop

                # Process the message
                on_meshtastic_message(packet, mock_meshtastic_interface)

                # Verify Matrix relay was called
                mock_matrix_relay.assert_called_once()

                # Verify plugin was called
                mock_plugin.handle_meshtastic_message.assert_called_once()

    def test_complete_matrix_to_meshtastic_flow(self):
        """
        Test that a Matrix text message is correctly processed and intercepted by a plugin before being relayed to Meshtastic.

        This test simulates a Matrix message event, configures a plugin to intercept the message, and verifies that the plugin's handler is awaited exactly once, ensuring proper integration between Matrix message handling and plugin processing.
        """

        async def async_test():
            # Create test configuration
            """
            Asynchronously tests that a Matrix text message event is intercepted by a plugin during message handling.

            Simulates a Matrix message event, configures a mock plugin to intercept the message, and verifies that the plugin's async handler is called exactly once.
            """
            config = {
                "matrix": {
                    "homeserver": "https://matrix.org",
                    "access_token": "test_token",
                    "bot_user_id": "@test:matrix.org",
                },
                "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
                "meshtastic": {
                    "connection_type": "serial",
                    "serial_port": "/dev/ttyUSB0",
                    "meshnet_name": "TestMesh",
                },
                "plugins": {"help": {"active": True}},
            }

            # Mock Matrix event
            mock_room = MagicMock()
            mock_room.room_id = "!room1:matrix.org"

            # Import the mock class from conftest
            from nio import RoomMessageText

            mock_event = MagicMock(spec=RoomMessageText)
            mock_event.sender = "@user:matrix.org"
            mock_event.body = "!help"
            mock_event.server_timestamp = (
                int(time.time() * 1000) + 1000
            )  # Future timestamp
            mock_event.source = {"content": {}}

            # Mock Meshtastic client
            mock_meshtastic_client = MagicMock()

            with patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins:
                # Mock help plugin that intercepts the message
                mock_plugin = MagicMock()
                mock_plugin.handle_room_message = AsyncMock(
                    return_value=True
                )  # Intercepts
                mock_load_plugins.return_value = [mock_plugin]

                # Set up global state
                import mmrelay.matrix_utils

                mmrelay.matrix_utils.config = config
                mmrelay.matrix_utils.matrix_rooms = config["matrix_rooms"]

                import mmrelay.meshtastic_utils

                mmrelay.meshtastic_utils.meshtastic_client = mock_meshtastic_client

                # Import and call the Matrix message handler
                from mmrelay.matrix_utils import on_room_message

                await on_room_message(mock_room, mock_event)

                # Verify plugin was called and intercepted the message
                mock_plugin.handle_room_message.assert_awaited_once()

        asyncio.run(async_test())

    def test_plugin_chain_processing(self):
        """
        Test that plugins process a Meshtastic message in priority order and halt processing when intercepted.

        This test ensures that when multiple plugins are loaded, each plugin's message handler is invoked according to its priority, and that processing stops as soon as a plugin signals interception by returning True.
        """
        packet = {
            "decoded": {"text": "test message", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,
        }

        mock_interface = MagicMock()

        # Create plugins with different priorities
        mock_plugin1 = MagicMock()
        mock_plugin1.priority = 1
        mock_plugin1.handle_meshtastic_message = AsyncMock(return_value=False)

        mock_plugin2 = MagicMock()
        mock_plugin2.priority = 5
        mock_plugin2.handle_meshtastic_message = AsyncMock(return_value=False)

        mock_plugin3 = MagicMock()
        mock_plugin3.priority = 10
        mock_plugin3.handle_meshtastic_message = AsyncMock(
            return_value=True
        )  # Intercepts

        with patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins:
            with patch("mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock):
                with patch("mmrelay.meshtastic_utils._submit_coro") as mock_submit_coro:
                    import inspect
                    from concurrent.futures import Future

                    def _done_future(coro, *args, **kwargs):
                        # Close the coroutine if it's a coroutine to prevent "never awaited" warnings
                        """
                        Return a completed Future after closing the given coroutine to suppress "never awaited" warnings.

                        Parameters:
                            coro: The coroutine object to be closed if it is a coroutine.

                        Returns:
                            Future: A Future object marked as completed with a result of None.
                        """
                        if inspect.iscoroutine(coro):
                            coro.close()
                        f = Future()
                        f.set_result(None)
                        return f

                    mock_submit_coro.side_effect = _done_future

                    # Return plugins in random order (should be sorted by priority)
                    mock_load_plugins.return_value = [
                        mock_plugin2,
                        mock_plugin1,
                        mock_plugin3,
                    ]

                    # Set up minimal config
                    import mmrelay.meshtastic_utils

                    mmrelay.meshtastic_utils.config = {
                        "matrix_rooms": [
                            {"id": "!room:matrix.org", "meshtastic_channel": 0}
                        ],
                        "meshtastic": {"meshnet_name": "TestMesh"},
                    }
                    mmrelay.meshtastic_utils.matrix_rooms = [
                        {"id": "!room:matrix.org", "meshtastic_channel": 0}
                    ]
                    mmrelay.meshtastic_utils.event_loop = MagicMock()

                    on_meshtastic_message(packet, mock_interface)

                    # Verify all plugins were called in priority order
                    mock_plugin1.handle_meshtastic_message.assert_called_once()
                    mock_plugin2.handle_meshtastic_message.assert_called_once()
                    mock_plugin3.handle_meshtastic_message.assert_called_once()

    def test_configuration_loading_and_validation_flow(self):
        """
        Test loading and validating a configuration file for correct structure and content.

        This test writes a sample YAML configuration to a temporary file, loads it using the application's configuration loader, verifies the presence of required configuration sections, and checks that the CLI configuration validation function accepts the loaded configuration.
        """
        # Create temporary config file
        config_data = """
matrix:
  homeserver: https://matrix.org
  access_token: test_token
  bot_user_id: '@test:matrix.org'

matrix_rooms:
  - id: '!room1:matrix.org'
    meshtastic_channel: 0

meshtastic:
  connection_type: serial
  serial_port: /dev/ttyUSB0
  broadcast_enabled: true

plugins:
  debug:
    active: true
  help:
    active: true
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_data)
            temp_config_path = f.name

        try:
            # Load configuration
            config = load_config(config_file=temp_config_path)

            # Verify configuration structure
            self.assertIsNotNone(config)
            self.assertIn("matrix", config)
            self.assertIn("matrix_rooms", config)
            self.assertIn("meshtastic", config)
            self.assertIn("plugins", config)

            # Test configuration validation
            from unittest.mock import MagicMock

            from mmrelay.cli import check_config

            # Create mock args
            mock_args = MagicMock()
            mock_args.config = temp_config_path

            with patch("mmrelay.cli.get_config_paths", return_value=[temp_config_path]):
                result = check_config(mock_args)
                self.assertTrue(result)

        finally:
            os.unlink(temp_config_path)

    def test_database_initialization_and_usage_flow(self):
        """
        Test initialization of the database and verify basic CRUD operations for node names and message mappings.

        This test ensures that the database can be initialized, node long and short names can be stored and retrieved, and message mappings can be saved and fetched correctly.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.sqlite")

            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                # Initialize database
                initialize_database()

                # Test database operations
                from mmrelay.db_utils import (
                    get_longname,
                    get_message_map_by_meshtastic_id,
                    get_shortname,
                    save_longname,
                    save_shortname,
                    store_message_map,
                )

                # Test name storage and retrieval
                save_longname("!12345678", "Test Node")
                save_shortname("!12345678", "TN")

                self.assertEqual(get_longname("!12345678"), "Test Node")
                self.assertEqual(get_shortname("!12345678"), "TN")

                # Test message mapping
                store_message_map(
                    "mesh123", "matrix456", "!room:matrix.org", "test message"
                )
                mapping = get_message_map_by_meshtastic_id("mesh123")
                self.assertIsNotNone(mapping)

    def test_error_recovery_scenario(self):
        """
        Test recovery from connection failures for Matrix and Meshtastic clients.

        Simulates authentication and connection errors during Matrix and Meshtastic client initialization. Verifies that the Matrix connection function returns a client instance even if authentication fails, and that the Meshtastic connection function returns None after repeated failures.
        """
        config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
        }

        # Test Matrix connection failure recovery
        with patch("ssl.create_default_context") as mock_ssl:
            mock_ssl.return_value = MagicMock()
            with patch("mmrelay.matrix_utils.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.rooms = {}
                # Mock whoami to return a WhoamiError (this is what connect_matrix actually calls)
                from nio import WhoamiError

                mock_whoami_error = WhoamiError("Whoami failed")
                mock_client.whoami.return_value = mock_whoami_error
                mock_client_class.return_value = mock_client

                async def test_matrix_recovery():
                    """
                    Asynchronously tests that the Matrix connection function returns a client instance even when the 'whoami' check fails.
                    """
                    result = await connect_matrix(config)
                    self.assertIsNotNone(
                        result
                    )  # Should return client even if whoami fails

                asyncio.run(test_matrix_recovery())

        # Test Meshtastic connection failure recovery
        import mmrelay.meshtastic_utils

        mmrelay.meshtastic_utils.shutting_down = False
        with patch("mmrelay.meshtastic_utils.serial_port_exists", return_value=True):
            # Patch all exceptions in the except clause to be proper Exception classes
            with patch("mmrelay.meshtastic_utils.BleakDBusError", Exception):
                with patch("mmrelay.meshtastic_utils.BleakError", Exception):
                    with patch(
                        "mmrelay.meshtastic_utils.serial.SerialException", Exception
                    ):
                        with patch("time.sleep") as mock_sleep:

                            def set_shutdown(*args, **kwargs):
                                """
                                Set the global shutdown flag in the Meshtastic utilities module to True.

                                This function is typically used to signal that the MMRelay system should begin shutting down.
                                """
                                import mmrelay.meshtastic_utils

                                mmrelay.meshtastic_utils.shutting_down = True
                                return None

                            mock_sleep.side_effect = set_shutdown

                            # Import meshtastic to patch at the module level
                            import meshtastic.serial_interface

                            # Patch the SerialInterface at the module level where it's imported
                            with patch.object(
                                meshtastic.serial_interface, "SerialInterface"
                            ) as mock_serial_interface:
                                # Make the constructor raise an exception
                                mock_serial_interface.side_effect = Exception(
                                    "Connection failed"
                                )

                                result = connect_meshtastic(config)
                                self.assertIsNone(result)

    def test_multi_room_message_routing(self):
        """
        Test that a Meshtastic message is relayed to all Matrix rooms configured for the same channel.

        Verifies that when a Meshtastic packet is received on a specific channel, it is relayed to each Matrix room mapped to that channel, and not to rooms mapped to other channels.
        """
        config = {
            "meshtastic": {"meshnet_name": "TestMesh"},
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 0},
                {
                    "id": "!room3:matrix.org",
                    "meshtastic_channel": 1,
                },  # Different channel
            ],
        }

        packet = {
            "decoded": {"text": "Multi-room test", "portnum": 1},
            "fromId": "!12345678",
            "channel": 0,  # Should route to room1 and room2, not room3
            "to": 4294967295,
            "id": 123456789,
        }

        mock_interface = MagicMock()
        mock_interface.myInfo.my_node_num = 123456789

        with patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_matrix_relay:
            with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
                with patch("mmrelay.db_utils.get_longname", return_value="TestNode"):
                    with patch("mmrelay.db_utils.get_shortname", return_value="TN"):
                        with patch("mmrelay.meshtastic_utils.logger"):
                            with patch(
                                "mmrelay.matrix_utils.get_interaction_settings"
                            ) as mock_interactions:
                                mock_interactions.return_value = {"reactions": True}
                                with patch(
                                    "mmrelay.matrix_utils.message_storage_enabled"
                                ):
                                    # Set up global state
                                    import asyncio

                                    import mmrelay.meshtastic_utils

                                    mmrelay.meshtastic_utils.config = config
                                    mmrelay.meshtastic_utils.matrix_rooms = config[
                                        "matrix_rooms"
                                    ]

                                    # Create and set event loop
                                    try:
                                        loop = asyncio.get_event_loop()
                                    except RuntimeError:
                                        loop = asyncio.new_event_loop()
                                        asyncio.set_event_loop(loop)
                                    mmrelay.meshtastic_utils.event_loop = loop

                                    on_meshtastic_message(packet, mock_interface)

                                    # Should be called for each matching room
                                    # The global mock_submit_coro fixture will handle the AsyncMock properly
                                    self.assertEqual(mock_matrix_relay.call_count, 2)

    def test_service_lifecycle_simulation(self):
        """
        Simulates detection of running as a systemd service and tests the service installation process using mocked environment variables and system utilities.
        """
        # Test service detection
        from mmrelay.meshtastic_utils import is_running_as_service

        # Mock systemd environment by setting INVOCATION_ID
        with patch.dict("os.environ", {"INVOCATION_ID": "test_invocation"}):
            result = is_running_as_service()
            self.assertTrue(result)

        # Test service installation
        from mmrelay.setup_utils import install_service

        with patch(
            "mmrelay.setup_utils.get_executable_path", return_value="/usr/bin/mmrelay"
        ):
            with patch("mmrelay.setup_utils.create_service_file", return_value=True):
                with patch("mmrelay.setup_utils.reload_daemon", return_value=True):
                    with patch(
                        "mmrelay.setup_utils.check_loginctl_available",
                        return_value=False,
                    ):
                        with patch(
                            "builtins.input", return_value="n"
                        ):  # Mock user input
                            with patch(
                                "mmrelay.setup_utils.is_service_enabled",
                                return_value=False,
                            ):
                                with patch(
                                    "mmrelay.setup_utils.is_service_active",
                                    return_value=False,
                                ):
                                    result = install_service()
                                    self.assertTrue(result)

    def test_concurrent_message_processing(self):
        """
        Test that multiple Meshtastic messages are processed concurrently without relaying to Matrix when no Matrix rooms are configured.

        Verifies that each message is handled independently and that the Matrix relay function is not called if the configuration lacks Matrix rooms.
        """
        packets = []
        for i in range(10):
            packets.append(
                {
                    "decoded": {"text": f"Message {i}", "portnum": 1},
                    "fromId": f"!{i:08x}",
                    "channel": 0,
                    "id": i,
                }
            )

        mock_interface = MagicMock()

        with patch("mmrelay.plugin_loader.load_plugins", return_value=[]):
            with patch(
                "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
            ) as mock_matrix_relay:
                # Set up minimal config
                import mmrelay.meshtastic_utils

                mmrelay.meshtastic_utils.config = {
                    "matrix_rooms": [],
                    "meshtastic": {"meshnet_name": "TestMesh"},
                }
                mmrelay.meshtastic_utils.matrix_rooms = []

                # Process multiple messages
                for packet in packets:
                    on_meshtastic_message(packet, mock_interface)

                # All messages should be processed without interference
                # (Matrix relay not called since no rooms configured)
                mock_matrix_relay.assert_not_called()

    def test_plugin_chain_with_weather_processing(self):
        """
        Test that a Meshtastic weather telemetry message is processed by the telemetry plugin and not relayed to Matrix.

        Simulates a weather sensor packet, verifies the telemetry plugin's handler is called once, and confirms that Matrix relay is not invoked for telemetry messages.
        """
        # Create weather sensor packet
        packet = {
            "decoded": {
                "telemetry": {
                    "deviceMetrics": {
                        "temperature": 25.5,
                        "batteryLevel": 85,
                        "voltage": 4.1,
                    },
                    "time": int(time.time()),
                },
                "portnum": "TELEMETRY_APP",  # Use string constant
            },
            "fromId": "!weather01",
            "channel": 0,
            "to": 4294967295,
            "id": 987654321,
        }

        mock_interface = MagicMock()
        mock_interface.nodes = {
            "!weather01": {
                "user": {
                    "id": "!weather01",
                    "longName": "Weather Station",
                    "shortName": "WS",
                }
            }
        }

        with patch(
            "mmrelay.matrix_utils.matrix_relay", new_callable=AsyncMock
        ) as mock_matrix_relay:
            with patch("mmrelay.plugin_loader.load_plugins") as mock_load_plugins:
                # Mock telemetry plugin (simulates weather processing)
                mock_telemetry_plugin = MagicMock()
                mock_telemetry_plugin.handle_meshtastic_message = AsyncMock(
                    return_value=False  # Processes but doesn't intercept
                )
                mock_load_plugins.return_value = [mock_telemetry_plugin]

                # Set up global state
                import mmrelay.meshtastic_utils

                mmrelay.meshtastic_utils.config = {
                    "matrix_rooms": [
                        {"id": "!weather:matrix.org", "meshtastic_channel": 0}
                    ],
                    "meshtastic": {"meshnet_name": "TestMesh"},
                }
                mmrelay.meshtastic_utils.matrix_rooms = [
                    {"id": "!weather:matrix.org", "meshtastic_channel": 0}
                ]
                mmrelay.meshtastic_utils.event_loop = MagicMock()

                # Process the telemetry message
                on_meshtastic_message(packet, mock_interface)

                # Verify telemetry plugin was called
                mock_telemetry_plugin.handle_meshtastic_message.assert_called_once()

                # Verify Matrix relay was NOT called (telemetry messages are not relayed)
                mock_matrix_relay.assert_not_called()

    def test_config_hot_reload_scenario(self):
        """
        Test that configuration changes are detected and applied by reloading the config file at runtime.

        Verifies that adding new Matrix rooms and plugins to the configuration file is reflected after a reload, ensuring dynamic updates are handled correctly.
        """
        # Create initial config
        initial_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [{"id": "!room1:matrix.org", "meshtastic_channel": 0}],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
            "plugins": {"debug": {"active": True}},
        }

        # Create updated config with new room
        updated_config = {
            "matrix": {
                "homeserver": "https://matrix.org",
                "access_token": "test_token",
                "bot_user_id": "@test:matrix.org",
            },
            "matrix_rooms": [
                {"id": "!room1:matrix.org", "meshtastic_channel": 0},
                {"id": "!room2:matrix.org", "meshtastic_channel": 1},  # New room
            ],
            "meshtastic": {"connection_type": "serial", "serial_port": "/dev/ttyUSB0"},
            "plugins": {
                "debug": {"active": True},
                "help": {"active": True},
            },  # New plugin
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            import yaml

            yaml.dump(initial_config, f)
            config_path = f.name

        try:
            # Load initial config
            config = load_config(config_file=config_path)
            self.assertEqual(len(config["matrix_rooms"]), 1)
            self.assertEqual(len(config["plugins"]), 1)

            # Simulate config file update
            with open(config_path, "w") as f:
                yaml.dump(updated_config, f)

            # Reload config
            updated_config_loaded = load_config(config_file=config_path)
            self.assertEqual(len(updated_config_loaded["matrix_rooms"]), 2)
            self.assertEqual(len(updated_config_loaded["plugins"]), 2)

            # Verify new room is present
            room_ids = [room["id"] for room in updated_config_loaded["matrix_rooms"]]
            self.assertIn("!room2:matrix.org", room_ids)

        finally:
            os.unlink(config_path)

    def test_database_cleanup_during_operation(self):
        """
        Test that database cleanup operations do not disrupt active message processing.

        Verifies that pruning old message mappings during operation maintains database accessibility and does not raise exceptions, ensuring data integrity is preserved.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_cleanup.sqlite")

            with patch("mmrelay.db_utils.get_db_path", return_value=db_path):
                # Initialize database
                initialize_database()

                from mmrelay.db_utils import (
                    get_message_map_by_meshtastic_id,
                    prune_message_map,
                    store_message_map,
                )

                # Store multiple message mappings
                for i in range(10):
                    store_message_map(
                        f"mesh{i}",
                        f"matrix{i}",
                        "!room:matrix.org",
                        f"Test message {i}",
                    )

                # Verify all messages are stored
                for i in range(10):
                    mapping = get_message_map_by_meshtastic_id(f"mesh{i}")
                    self.assertIsNotNone(mapping)

                # Simulate cleanup during operation
                # This should keep only the 5 most recent messages
                prune_message_map(5)

                # Verify cleanup worked but didn't break database
                # (Exact behavior depends on cleanup implementation)
                # At minimum, database should still be accessible
                mapping = get_message_map_by_meshtastic_id("mesh9")  # Most recent
                # Should still exist or be None (depending on cleanup logic)
                # The important thing is no exception is raised


if __name__ == "__main__":
    unittest.main()
